"""Snapshot-based legacy import. Source databases stay intact; optional material migration follows a verified commit."""
from pathlib import Path
from datetime import datetime
from uuid import uuid4
import os
import json
import sqlite3
import hashlib
import shutil
import re
from contextlib import contextmanager,closing

from native_files import plain_path,cancelled,path_is_within
from workspace_manager import copy_verified

SCHEMA='''CREATE TABLE IF NOT EXISTS legacy_import_runs(
 id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,source TEXT NOT NULL,
 report TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
 CREATE TABLE IF NOT EXISTS legacy_project_origins(
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),run_id TEXT NOT NULL REFERENCES legacy_import_runs(id),
 old_id INTEGER NOT NULL,old_status TEXT NOT NULL,old_batch TEXT NOT NULL);'''
SKIP={'desktop_settings','workflow_settings','workflow_first_run','intake_chat_state',
      'workflow_archive_roots','legacy_import_runs','legacy_project_origins'}
MARKERS={'zip_approval_migrations','delivery_event_migrations'}
PATH_COLUMNS={'path','source_path','target_path','archive_path','destination','source','target','source_root','group_path'}
IDS={'project_id':'projects','batch_id':'batches','message_id':'messages','zip_id':'zips',
     'work_file_id':'work_files','version_id':'material_versions','source_version_id':'material_versions',
     'submission_id':'material_submissions','profile_id':'material_profiles','receipt_id':'intake_receipts',
     'conversation_id':'intake_conversations','turn_id':'intake_turns','event_id':'delivery_events',
     'archive_id':'project_delivery_archives','source_id':'zip_sources','original_source_id':'zip_sources'}
JSON_LIST_IDS={'project_ids':'projects','candidate_ids':'projects','batch_ids':'batches',
               'created_project_ids':'projects','version_ids':'material_versions'}

def quote(name):return '"'+name.replace('"','""')+'"'
def digest(path):
    from native_files import ZipLease
    from workspace_manager import _hash_stream
    with ZipLease(path,movable=False) as lease:return _hash_stream(lease.stream)[0]
@contextmanager
def read_db(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');yield c
    finally:c.close()
def snapshot(source,target,cancel_event=None):
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    with read_db(source) as old,closing(sqlite3.connect(target)) as new:old.backup(new,pages=256,progress=lambda *args:cancelled(cancel_event),sleep=.05)
def tables(c):return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


class LegacyImporter:
    def __init__(self,service):
        self.service=service;self.db=service.db
        with self.db.connect() as c:c.executescript(SCHEMA)

    def discover(self,roots=None,*,cancel_event=None,progress=None):
        default_search=roots is None
        if roots is None:
            from portable_update import read_index
            known=read_index()
            home=Path.home();roots=[home/'Desktop',home/'Downloads',Path(__file__).resolve().parent/'data']+[Path(p)/'data' for p in known.get('locations',[])]
            for env in ('APPDATA','LOCALAPPDATA'):
                base=Path(os.environ.get(env,home))
                roots.extend([base/'WorkBox',base/'workbox',base/'Programs'/'WorkBox'])
            if os.name=='nt':
                import winreg
                for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                    for key in (r'Software\Microsoft\Windows\CurrentVersion\Uninstall',r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                        try:
                            with winreg.OpenKey(hive,key) as parent:
                                for i in range(winreg.QueryInfoKey(parent)[0]):
                                    try:
                                        with winreg.OpenKey(parent,winreg.EnumKey(parent,i)) as child:
                                            name=winreg.QueryValueEx(child,'DisplayName')[0]
                                            if 'workbox' in str(name).casefold():roots.append(Path(winreg.QueryValueEx(child,'InstallLocation')[0]))
                                    except OSError:pass
                        except OSError:pass
        found=[];seen=set();examined=0
        if default_search:
            with self.db.connect() as c:
                n=c.execute('SELECT COUNT(*) FROM projects p WHERE NOT EXISTS(SELECT 1 FROM production_tasks t WHERE t.project_id=p.id)').fetchone()[0]
            if n:found.append(dict(path='__current__',projects=n,batches=0,current_inventory=True))
        for root in roots:
            root=Path(root)
            if not root.exists():continue
            if root.is_file():candidates=[root]
            else:
                candidates=[];stack=[(root,0)]
                while stack and examined<12000:
                    directory,depth=stack.pop();cancelled(cancel_event)
                    try:
                        for p in directory.iterdir():
                            examined+=1
                            cancelled(cancel_event)
                            if examined>12000:break
                            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:continue
                            if p.is_file() and p.name.casefold()=='workbox.db':candidates.append(p)
                            elif p.is_dir() and depth<4 and p.name not in {'.git','.venv','node_modules','_internal','.local','旧版导入'}:stack.append((p,depth+1))
                    except OSError:continue
            for p in candidates:
                cancelled(cancel_event)
                try:
                    p=plain_path(p)
                    if p==self.db.path or str(p).casefold() in seen:continue
                    with read_db(p) as c:
                        if not {'projects','batches','messages'}.issubset(tables(c)):continue
                        counts={t:c.execute('SELECT COUNT(*) FROM '+quote(t)).fetchone()[0] for t in ('projects','batches')}
                    seen.add(str(p).casefold());found.append(dict(path=str(p),**counts))
                except (OSError,ValueError,sqlite3.Error):continue
        return found

    def prepare(self,path,*,material_root=None,cancel_event=None,progress=None):
        source=plain_path(path)
        if source.is_dir():
            choices=self.discover([source],cancel_event=cancel_event)
            if len(choices)!=1:raise ValueError('该文件夹找到'+str(len(choices))+'份旧数据，请选择具体的 workbox.db。')
            source=Path(choices[0]['path'])
        if source==self.db.path or source.samefile(self.db.path):raise ValueError('这是当前正在使用的数据。请点自动查找，可把尚未进入总表的旧记录补入。')
        token=uuid4().hex;stage=self.db.path.parent/'旧版导入'/'预览'/token;stage.mkdir(parents=True)
        original=stage/'旧版原始数据.db';snapshot(source,original,cancel_event)
        fingerprint=digest(original)
        with self.db.connect() as c:
            previous=c.execute('SELECT report FROM legacy_import_runs WHERE fingerprint=?',(fingerprint,)).fetchone()
        if previous:return dict(already_imported=True,report=json.loads(previous[0]))
        with read_db(original) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('旧数据库检查未通过，未改动任何数据。')
            original_tables=tables(c)
            if not {'projects','messages','batches'}.issubset(original_tables):raise ValueError('这不是支持的 BOX 数据库。')
        normalized=stage/'核对用副本.db';shutil.copy2(original,normalized)
        from desktop_service import DesktopService
        old=DesktopService(normalized)
        with old.db.connect() as c,self.db.connect() as target:
            unknown=original_tables-tables(target)-SKIP
            if any(c.execute('SELECT COUNT(*) FROM '+quote(t)).fetchone()[0] for t in unknown):raise ValueError('这份旧数据含当前版本不支持的记录，请保留原件并联系维护者。')
            data={t:[dict(r) for r in c.execute('SELECT * FROM '+quote(t))] for t in tables(c) if t not in SKIP}
            for t,rows in data.items():
                if not rows:continue
                target_cols={r['name'] for r in target.execute('PRAGMA table_info('+quote(t)+')')}
                if set(rows[0])-target_cols:raise ValueError('旧版字段与当前版本不兼容，未开始导入。')
            settings={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM workflow_settings')}
            collisions=sum(r['name'] in {x[0] for x in target.execute('SELECT name FROM batches')} for r in data['batches'])
            project_duplicates=sum(r['name'] in {x[0] for x in target.execute('SELECT name FROM projects')} for r in data['projects'])
        roots=[]
        if material_root:
            extra=plain_path(material_root)
            if extra==extra.parent or not extra.is_dir():raise ValueError('请选择具体的材料文件夹，不要选择整个磁盘。')
            roots.append(extra)
        if source.name.casefold()=='workbox.db' and source.parent!=source.parent.parent:
            roots.append(source.parent)
            # A moved portable BOX may keep its newer default library beside
            # data/. Its recorded absolute paths still name the old location.
            if source.parent.name.casefold()=='data':roots.append(source.parent.parent/'材料库')
        for key in ('work_root','archive_root'):
            if settings.get(key):roots.append(Path(settings[key]))
        roots.extend(Path(r[k]) for t in ('project_workspaces','collection_batch_folders','project_delivery_archives') for r in data.get(t,[]) for k in ('path',) if r.get(k))
        roots=[p for p in roots if p.is_absolute() and p.is_dir() and p!=p.parent and not path_is_within(stage,p)]
        roots=sorted(set(roots),key=lambda p:len(p.parts));selected=[]
        for p in roots:
            if not any(path_is_within(p,parent) for parent in selected):selected.append(p)
        files={};directories={};count=0;total=0
        excluded=self.db.path.parent/'旧版导入'
        for n,root in enumerate(selected):
            root=plain_path(root);directories[str(root)]=f'材料/{n+1}-{root.name}'
            stack=[root]
            while stack:
                cancelled(cancel_event);directory=stack.pop()
                for p in directory.iterdir():
                    if path_is_within(p,excluded):continue
                    if p==source or p.name in {source.name+'-wal',source.name+'-shm',source.name+'.session.lock'}:continue
                    p=plain_path(p)
                    if p.is_dir():stack.append(p)
                    elif p.is_file():
                        stat=p.stat();count+=1;total+=stat.st_size
                        if count>50000 or total>100*1024**3:raise ValueError('材料范围过大，请选择更具体的旧 BOX 文件夹。')
                        files[str(p)]=dict(relative=directories[str(root)]+'/'+p.relative_to(root).as_posix(),size=stat.st_size,mtime=stat.st_mtime_ns)
        mandatory=[];expected={}
        for table in ('work_files','material_versions','collected_materials','zips'):
            for r in data.get(table,[]):
                p=r.get('path')
                if p:
                    mandatory.append(p)
                    if r.get('sha256') and table in ('work_files','material_versions','zips'):expected.setdefault(str(Path(p)).casefold(),[]).append(r['sha256'])
        for archive in data.get('project_delivery_archives',[]):
            for item in json.loads(archive['manifest']):
                relative=Path(item['path'])
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('旧版归档记录包含不安全的路径，未开始导入。')
                p=str(Path(archive['path'])/relative);mandatory.append(p)
                expected.setdefault(p.casefold(),[]).append(item['sha256'])
        missing=[];aliases={}
        for raw in dict.fromkeys(mandatory):
            cancelled(cancel_event);p=Path(raw)
            if not p.is_absolute():raise ValueError('旧数据引用的材料不是完整路径，请保留原数据并联系维护者。')
            if not p.is_file():
                candidates=[];best=0
                for candidate in files:
                    score=0
                    for a,b in zip(reversed(Path(candidate).parts),reversed(p.parts)):
                        if a.casefold()!=b.casefold():break
                        score+=1
                    if score>best:best=score;candidates=[candidate]
                    elif score==best and score:candidates.append(candidate)
                checks=expected.get(str(p).casefold(),[])
                if checks:candidates=[q for q in candidates if digest(q) in checks]
                elif best<2:candidates=[]
                if len(candidates)==1:aliases[raw]=candidates[0];continue
                missing.append(raw);continue
            p=plain_path(p)
            if str(p) not in files:
                stat=p.stat();files[str(p)]=dict(relative='材料/其他/'+hashlib.sha256(str(p).encode()).hexdigest()[:12]+'/'+p.name,size=stat.st_size,mtime=stat.st_mtime_ns)
        plan=dict(token=token,source=str(source),stage=str(stage),fingerprint=fingerprint,
                  normalized_sha256=digest(normalized),
                  counts={t:len(r) for t,r in data.items()},file_count=len(files),bytes=sum(x['size'] for x in files.values()),
                  files=files,directories=directories,missing=missing,expected=expected,aliases=aliases,
                  material_paths=sorted({aliases.get(raw,str(plain_path(raw))) for raw in mandatory if raw not in missing}),
                  directory_refs=list(dict.fromkeys([r[k] for t in ('project_workspaces','collection_batch_folders','project_delivery_archives') for r in data.get(t,[]) for k in ('path','group_path','source_root') if r.get(k)])),
                  batch_collisions=collisions,project_duplicates=project_duplicates)
        (stage/'核对结果.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        return plan

    def import_plan(self,plan,*,allow_partial=False,transfer_mode="copy",cancel_event=None,progress=None):
        if transfer_mode not in ('copy','move'):raise ValueError('请选择复制或迁移。')
        if plan.get('already_imported'):return plan['report']
        if plan.get('current_inventory'):return self.import_current(plan,allow_partial=allow_partial,cancel_event=cancel_event,progress=progress)
        if not re.fullmatch('[0-9a-f]{32}',str(plan.get('token',''))):raise ValueError('导入编号无效，请重新扫描。')
        stage=plain_path(plan['stage']);saved=json.loads((stage/'核对结果.json').read_text('utf-8'))
        if stage!=(self.db.path.parent/'旧版导入'/'预览'/plan['token']).resolve():raise ValueError('导入预览位置无效，请重新扫描。')
        if saved!=plan:raise ValueError('核对清单已变化，请重新扫描。')
        if digest(stage/'旧版原始数据.db')!=plan['fingerprint'] or digest(stage/'核对用副本.db')!=plan['normalized_sha256']:raise ValueError('核对数据已变化，请重新扫描。')
        with self.db.connect() as c:
            prior=c.execute('SELECT report FROM legacy_import_runs WHERE fingerprint=?',(plan['fingerprint'],)).fetchone()
        if prior:return json.loads(prior[0])
        if transfer_mode=='move':
            from legacy_migration import check_move_source
            check_move_source(self,plan)
            if plan['missing']:raise ValueError('迁移需要材料齐全；请补充材料位置，或改选复制后导入现有内容。')
        if plan['missing'] and not allow_partial:raise ValueError('有材料缺失，尚不能完整导入。请先找回材料，或明确选择仅导入现有内容。')
        if shutil.disk_usage(self.db.path.parent).free<plan['bytes']*3+source_size(self.db.path):raise ValueError('保存位置的可用空间不足，请先腾出空间再导入。')
        token=plan['token'];run=self.db.path.parent/'旧版导入'/'导入记录'/token;run.mkdir(parents=True,exist_ok=True)
        backup=run/'导入前新版.db';snapshot(self.db.path,backup,cancel_event)
        shutil.copy2(stage/'旧版原始数据.db',run/'旧版原始数据.db')
        path_map={};verified={}
        for root,relative in plan['directories'].items():
            target=plain_path(run/relative);target.mkdir(parents=True,exist_ok=True);path_map[root]=str(target)
        for raw,meta in plan['files'].items():
            cancelled(cancel_event);p=plain_path(raw);stat=p.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(meta['size'],meta['mtime']):raise ValueError('材料在核对后发生变化，请关闭旧BOX并重新扫描。')
            target=plain_path(run/meta['relative']);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                if digest(target)!=digest(p):raise ValueError('导入暂存副本不一致，请重新扫描。')
                sha=digest(target)
            else:sha=copy_verified(p,target,cancel_event=cancel_event,progress=progress)['sha256']
            if plan['expected'].get(raw.casefold()) and sha not in plan['expected'][raw.casefold()]:raise ValueError('旧材料与历史校验值不同，已停止导入：'+p.name)
            path_map[raw]=str(target);verified[raw]=sha
        for i,raw in enumerate(plan['missing']):path_map[raw]=str(run/'缺失材料'/f'{i+1}-{Path(raw).name}')
        for raw,found in plan['aliases'].items():path_map[raw]=path_map[found]
        for raw in plan['directory_refs']:
            if raw in path_map:continue
            candidates=set()
            for old_path,target in list(path_map.items()):
                try:
                    relative=Path(old_path).relative_to(Path(raw))
                    if not relative.parts:continue
                    parent=Path(target)
                    for _ in relative.parts:parent=parent.parent
                    candidates.add(str(parent))
                except ValueError:continue
            fallback=run/'原文件夹'/hashlib.sha256(raw.encode()).hexdigest()[:12]/(Path(raw).name or '旧目录')
            if len(candidates)==1:destination=Path(candidates.pop())
            else:destination=fallback
            if not destination.resolve().is_relative_to(run.resolve()):destination=fallback
            destination.mkdir(parents=True,exist_ok=True);path_map[raw]=str(destination)
        report=dict(run_id=token,source=plan['source'],backup=str(backup),original=str(run/'旧版原始数据.db'),
                    files=len(plan['files']),missing=list(plan['missing']),complete=not plan['missing'],
                    batches=plan['counts'].get('batches',0),projects=plan['counts'].get('projects',0),
                    reviews=plan['counts'].get('material_reviews',0),submissions=plan['counts'].get('material_submission_items',0),
                    batch_collisions=plan['batch_collisions'],project_duplicates=plan['project_duplicates'])
        report['transfer_mode']=transfer_mode
        report['transfer_items']=[dict(source=raw,target=path_map[raw],size=plan['files'][raw]['size'],mtime=plan['files'][raw]['mtime'],sha256=verified[raw],state='pending',reason='') for raw in plan['material_paths'] if raw in verified]
        report['source_fingerprint']=plan['fingerprint']
        report['retained_other_files']=len(plan['files'])-len(report['transfer_items'])
        self._merge(stage/'核对用副本.db',path_map,plan,report,cancel_event)
        if transfer_mode=='move':
            from legacy_migration import finish_transfer
            return finish_transfer(self,report,cancel_event=cancel_event,progress=progress)
        try:(run/'导入报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:report['report_file_warning']='报告文件未写出，可从以前的导入结果查看。'
        return report

    def _merge(self,source,path_map,plan,report,cancel_event):
        token=plan['token'];prefix='旧版-'+token[:8]+'-'
        with read_db(source) as old,self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');c.execute('PRAGMA defer_foreign_keys=ON')
            data={t:[dict(r) for r in old.execute('SELECT * FROM '+quote(t))] for t in tables(old) if t not in SKIP and t in tables(c)}
            info={t:[dict(r) for r in old.execute('PRAGMA table_info('+quote(t)+')')] for t in data}
            maps={};reuse=set();names={};batch_names={}
            for t,rows in data.items():
                pk=[r for r in info[t] if r['pk']];maps[t]={}
                if len(pk)!=1:continue
                col=pk[0]['name'];foreign={r['from'] for r in old.execute('PRAGMA foreign_key_list('+quote(t)+')')}
                if col in foreign:continue
                maximum=c.execute('SELECT COALESCE(MAX('+quote(col)+'),0) FROM '+quote(t)).fetchone()[0] if pk[0]['type'].upper()=='INTEGER' else 0
                for r in rows:
                    value=r[col]
                    if t in MARKERS:
                        maps[t][value]=value
                        if c.execute('SELECT 1 FROM '+quote(t)+' WHERE '+quote(col)+'=?',(value,)).fetchone():reuse.add((t,value))
                        continue
                    if t in ('material_profiles','production_commissions'):
                        existing=c.execute('SELECT * FROM '+quote(t)+' WHERE name=?',(r['name'],)).fetchone()
                        if existing and (t=='production_commissions' or all(existing[k]==r[k] for k in ('kinds','aliases','mode','active'))):
                            maps[t][value]=existing[col];reuse.add((t,value));continue
                    if pk[0]['type'].upper()=='INTEGER':maximum+=1;maps[t][value]=maximum
                    else:maps[t][value]=prefix+str(value)
            def remap(table,value):return maps.get(table,{}).get(value,value) if value is not None else None
            ordered=sorted(path_map,key=len,reverse=True)
            def moved(value):
                if not isinstance(value,str):return value
                for p in ordered:
                    normalized=os.path.normcase(os.path.normpath(value));parent=os.path.normcase(os.path.normpath(p))
                    if normalized==parent:return path_map[p]
                    if normalized.startswith(parent.rstrip('\\/')+os.sep):return str(Path(path_map[p])/Path(value).relative_to(p))
                return value
            def json_value(value,key='',context=''):
                if isinstance(value,dict):
                    id_table={'project':'projects','file':'work_files','zip':'zips','materials':'material_versions','receipts':'projects'}.get(key,context)
                    if 'batch_id' in value and 'name' in value:id_table='projects'
                    return {k:remap(id_table,v) if k=='id' and isinstance(v,int) and id_table else json_value(v,k,id_table) for k,v in value.items()}
                if isinstance(value,list):return [remap(JSON_LIST_IDS[key],v) if key in JSON_LIST_IDS and isinstance(v,int) else json_value(v,key,context) for v in value]
                if key in IDS and isinstance(value,int):return remap(IDS[key],value)
                if key in ('package','package_name','preset') and isinstance(value,str):return names.get(value,value)
                if key in ('token','import_token') and isinstance(value,str):return prefix+value
                return moved(value)
            # Reserve collision-free display names before rewriting JSON and task packages.
            for t in ('batches','material_profiles'):
                used={r[0].casefold() for r in c.execute('SELECT name FROM '+quote(t))}
                for r in data.get(t,[]):
                    if (t,r['id']) in reuse:continue
                    original=r['name'];label=original;n=1
                    while label.casefold() in used:n+=1;label=original[:65 if t=='batches' else 27]+f'（旧版{n}）'
                    used.add(label.casefold())
                    if t=='material_profiles':names[original]=label
                    else:batch_names[r['id']]=label
            for t,rows in data.items():
                fks={r['from']:r['table'] for r in old.execute('PRAGMA foreign_key_list('+quote(t)+')')}
                pk=[r['name'] for r in info[t] if r['pk']]
                for original in rows:
                    cancelled(cancel_event)
                    if len(pk)==1 and (t,original[pk[0]]) in reuse:continue
                    if t=='collected_materials':
                        missing={os.path.normcase(os.path.normpath(p)) for p in plan['missing']}
                        version=next((v for v in data.get('material_versions',[]) if v['id']==original['version_id']),None)
                        if os.path.normcase(os.path.normpath(original['path'])) in missing or version and os.path.normcase(os.path.normpath(version['path'])) in missing:continue
                    r=dict(original)
                    for col,value in list(r.items()):
                        if col in fks:r[col]=remap(fks[col],value)
                        elif len(pk)==1 and col==pk[0]:r[col]=remap(t,value)
                        elif col in IDS:r[col]=remap(IDS[col],value)
                        if col in PATH_COLUMNS:r[col]=moved(r[col])
                        elif isinstance(value,str) and (col.endswith('_json') or col in {'metadata','manifest','candidate_ids','project_ids','before_json','after_json'}):
                            try:r[col]=json.dumps(json_value(json.loads(value),col),ensure_ascii=False)
                            except (ValueError,TypeError):pass
                    if t=='batches':r['name']=batch_names.get(original['id'],r['name'])
                    if t=='material_profiles':r['name']=names.get(original['name'],original['name']);r['position']=r['position']+10000
                    if t=='production_tasks':r['package_name']=names.get(r['package_name'],r['package_name'])
                    if t=='intake_turns':r['token']=prefix+original['token']
                    if t=='intake_receipts':r['fingerprint']=hashlib.sha256((prefix+original['fingerprint']).encode()).hexdigest()
                    if t=='delivery_events' and r.get('legacy_source_key'):r['legacy_source_key']=prefix+r['legacy_source_key']
                    if t=='zip_sources':
                        from snapshot_manager import SnapshotManager
                        r['enabled']=0;r['source_key']=SnapshotManager._key(Path(r['source_path']))
                    if t=='managed_transfers':r['state']='complete'
                    if t in ('zip_snapshot_attempts','project_delivery_archive_attempts'):r['state']='registered'
                    columns=list(r);c.execute('INSERT INTO '+quote(t)+'('+','.join(map(quote,columns))+') VALUES ('+','.join('?' for _ in columns)+')',[r[k] for k in columns])
            report['batch_ids']=[remap('batches',r['id']) for r in data['batches']]
            report['project_ids']=[remap('projects',r['id']) for r in data['projects']]
            report['legacy_states']=[dict(name=p['name'],status=p['status'],created_at=p['created_at'],
                batch=next(b['name'] for b in data['batches'] if b['id']==p['batch_id']),
                archived=any(d['project_id']==p['id'] and d.get('archived_at') for d in data.get('project_details',[])),
                deleted=any(d['project_id']==p['id'] for d in data.get('project_deletions',[]))) for p in data['projects']]
            c.execute('INSERT INTO legacy_import_runs(id,fingerprint,source,report) VALUES (?,?,?,?)',
                      (token,plan['fingerprint'],plan['source'],json.dumps(report,ensure_ascii=False)))
            c.execute('INSERT OR IGNORE INTO workflow_archive_roots(path) VALUES (?)',(str(self.db.path.parent/'旧版导入'/'导入记录'/token),))
            for p in data['projects']:
                pid=remap('projects',p['id']);batch=next(b['name'] for b in data['batches'] if b['id']==p['batch_id'])
                c.execute('INSERT INTO legacy_project_origins VALUES (?,?,?,?,?)',(pid,token,p['id'],p['status'],batch))
                if not c.execute('SELECT 1 FROM production_tasks WHERE project_id=?',(pid,)).fetchone():
                    self._adopt_inventory(c,pid,token,remap('batches',p['batch_id']),cancel_event)
            errors=c.execute('PRAGMA foreign_key_check').fetchall()
            if errors:raise ValueError('旧版记录关联校验失败，已撤销本次数据写入。')

    def _adopt_inventory(self,c,pid,token,bid,cancel_event,overrides=None):
        """Legacy statuses stay facts; new check/submission marks are never invented."""
        overrides=overrides or {}
        rows=[dict(r) for r in c.execute('SELECT * FROM work_files WHERE project_id=? AND id IN (SELECT MAX(id) FROM work_files WHERE project_id=? GROUP BY path)',(pid,pid))]
        if not rows:
            for z in c.execute('SELECT z.* FROM zips z JOIN zip_project_links l ON l.zip_id=z.id WHERE l.project_id=?',(pid,)).fetchall():
                if Path(overrides.get(z['path'],z['path'])).is_file():
                    fid=c.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns,created_at) VALUES (?,?,?,?,?,?,?,?)',
                        (pid,z['path'],z['path'],z['filename'],z['size'],z['sha256'],z['mtime_ns'],z['created_at'])).lastrowid
                    rows.append(dict(c.execute('SELECT * FROM work_files WHERE id=?',(fid,)).fetchone()))
        for row in rows:row['path']=overrides.get(row['path'],row['path'])
        from production import infer_kind
        from material_templates import PRESETS
        kinds=[];bindings=[]
        for row in rows:
            kind=infer_kind(row['filename'],PRESETS['14件＋双视频']) or row['filename']
            while kind in kinds:kind=Path(kind).stem+'（旧版）'+Path(kind).suffix
            kinds.append(kind);bindings.append((kind,row))
        self.service.production._set_requirements(c,pid,kinds,'旧版材料' if kinds else '旧版待整理','','旧版导入保留实际材料清单')
        c.execute('INSERT OR IGNORE INTO batch_processing_modes VALUES (?,?)',(bid,'collect'))
        from workspace_manager import _folder_name
        folder=self.db.path.parent/'旧版导入'/'导入记录'/token/'旧版分类收录'/f'批次-{bid}'
        work=folder/f'WB-{pid}';work.mkdir(parents=True,exist_ok=True)
        c.execute('INSERT OR IGNORE INTO collection_batch_folders VALUES (?,?,?)',(bid,'旧版材料',str(folder)))
        c.execute('INSERT OR IGNORE INTO project_workspaces VALUES (?,?,?,?)',(pid,str(work),str(folder),bid))
        versions={}
        for kind,row in bindings:
            cancelled(cancel_event)
            if not Path(row['path']).is_file():continue
            target=self.db.path.parent/'旧版导入'/'导入记录'/token/'保留版本'/str(pid)/str(row['id'])/_folder_name(row['filename'])
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():meta=copy_verified(row['path'],target,cancel_event=cancel_event)
            else:meta=dict(sha256=digest(target),size=target.stat().st_size)
            vid=c.execute('INSERT INTO material_versions(project_id,kind,work_file_id,path,filename,sha256,size,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (pid,kind,row['id'],str(target),row['filename'],meta['sha256'],meta['size'],row['created_at'])).lastrowid
            pretty=work/_folder_name(kind)
            if not pretty.exists():copy_verified(target,pretty,cancel_event=cancel_event)
            c.execute('INSERT INTO collected_materials VALUES (?,?,?,?,?,?)',(pid,kind,vid,str(pretty),'旧版材料',row['created_at']))
            versions[kind]=vid
        from material_templates import dependencies
        for kind,vid in versions.items():
            for parent in dependencies(kinds,kind):c.execute('INSERT INTO material_version_inputs VALUES (?,?,?)',(vid,parent,versions.get(parent)))

    def resume_migration(self,run_id,*,cancel_event=None,progress=None):
        from legacy_migration import finish_transfer
        with self.db.connect() as c:
            row=c.execute('SELECT report FROM legacy_import_runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise ValueError('没有找到这次导入，请重新核对。')
        report=json.loads(row[0])
        if report.get('current_inventory') or not report.get('transfer_items'):raise ValueError('这份记录没有可迁移的外部旧材料。')
        if not report['complete']:raise ValueError('这次导入有缺失，请先补齐材料；暂不移除原材料。')
        report['transfer_mode']='move'
        return finish_transfer(self,report,cancel_event=cancel_event,progress=progress)

    def reports(self):
        with self.db.connect() as c:return [json.loads(r[0]) for r in c.execute('SELECT report FROM legacy_import_runs ORDER BY created_at DESC')]

    def prepare_current(self,*,material_root=None,cancel_event=None,progress=None):
        with self.db.connect() as c:
            projects=[dict(r) for r in c.execute('SELECT * FROM projects p WHERE NOT EXISTS(SELECT 1 FROM production_tasks t WHERE t.project_id=p.id)')]
            if not projects:raise ValueError('当前BOX的旧记录都已进入总表，无需重复整理。')
            ids={p['id'] for p in projects};records=[dict(r) for r in c.execute('SELECT * FROM work_files WHERE id IN (SELECT MAX(id) FROM work_files GROUP BY project_id,path)') if r['project_id'] in ids]
            records.extend(dict(r) for r in c.execute('SELECT z.*,l.project_id AS owner_id FROM zips z JOIN zip_project_links l ON l.zip_id=z.id') if r['owner_id'] in ids)
        candidates=[]
        if material_root:
            from folder_intake import FolderIntake
            candidates=FolderIntake(self.service)._scan(plain_path(material_root),cancel_event)
        files={};overrides={};missing=[]
        for row in records:
            cancelled(cancel_event);p=Path(row['path'])
            if not p.is_file():
                found=[q for q in candidates if q.name.casefold()==p.name.casefold() and digest(q)==row['sha256']]
                if found:overrides[str(p)]=str(found[0]);p=found[0]
                else:missing.append(str(p));continue
            stat=p.stat();files[str(p)]=dict(size=stat.st_size,mtime=stat.st_mtime_ns,sha256=row['sha256'])
        token=uuid4().hex;stage=self.db.path.parent/'旧版导入'/'预览'/token;stage.mkdir(parents=True)
        fingerprint='current-'+hashlib.sha256(json.dumps(projects,sort_keys=True).encode()).hexdigest()
        plan=dict(current_inventory=True,token=token,stage=str(stage),source='当前 BOX 中未进入总表的旧记录',fingerprint=fingerprint,
            projects=projects,files=files,overrides=overrides,missing=list(dict.fromkeys(missing)),file_count=len(files),bytes=sum(r['size'] for r in files.values()),
            counts=dict(projects=len(projects),batches=len({p['batch_id'] for p in projects})),directories={},batch_collisions=0,project_duplicates=0)
        (stage/'核对结果.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8');return plan

    def import_current(self,plan,*,allow_partial=False,cancel_event=None,progress=None):
        token=plan['token']
        if not re.fullmatch('[a-f0-9]{32}',str(token)):raise ValueError('核对编号无效。')
        stage=self.db.path.parent/'旧版导入'/'预览'/token
        if json.loads((stage/'核对结果.json').read_text('utf-8'))!=plan:raise ValueError('核对清单已变化，请重新查找。')
        with self.db.connect() as c:
            previous=c.execute('SELECT report FROM legacy_import_runs WHERE fingerprint=?',(plan['fingerprint'],)).fetchone()
        if previous:return json.loads(previous[0])
        if plan['missing'] and not allow_partial:raise ValueError('仍有材料缺失，请补充位置或明确选择导入现有内容。')
        for path,meta in plan['files'].items():
            cancelled(cancel_event);p=plain_path(path);s=p.stat()
            if (s.st_size,s.st_mtime_ns)!=(meta['size'],meta['mtime']) or digest(p)!=meta['sha256']:raise ValueError('旧材料在核对后发生变化，请重新查找。')
        run=self.db.path.parent/'旧版导入'/'导入记录'/token;run.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(run).free < plan['bytes']*3+source_size(self.db.path):raise ValueError('剩余空间不足，请释放空间后重试。')
        backup=run/'整理前完整数据.db';snapshot(self.db.path,backup,cancel_event)
        report=dict(run_id=token,current_inventory=True,source=plan['source'],backup=str(backup),original=str(backup),complete=not plan['missing'],
            missing=plan['missing'],files=plan['file_count'],projects=len(plan['projects']),batches=plan['counts']['batches'],reviews=0,submissions=0,
            batch_collisions=0,project_duplicates=0,project_ids=[r['id'] for r in plan['projects']],batch_ids=list({r['batch_id'] for r in plan['projects']}),legacy_states=[])
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for old in plan['projects']:
                now=c.execute('SELECT * FROM projects WHERE id=?',(old['id'],)).fetchone()
                if not now or dict(now)!=old or c.execute('SELECT 1 FROM production_tasks WHERE project_id=?',(old['id'],)).fetchone():raise ValueError('旧记录已经变化，请重新查找。')
            c.execute('INSERT INTO legacy_import_runs(id,fingerprint,source,report) VALUES (?,?,?,?)',(token,plan['fingerprint'],plan['source'],'{}'))
            for old in plan['projects']:
                cancelled(cancel_event);pid=old['id'];batch=c.execute('SELECT name FROM batches WHERE id=?',(old['batch_id'],)).fetchone()[0]
                c.execute('INSERT INTO legacy_project_origins VALUES (?,?,?,?,?)',(pid,token,pid,old['status'],batch))
                self._adopt_inventory(c,pid,token,old['batch_id'],cancel_event,plan['overrides'])
                detail=c.execute('SELECT archived_at FROM project_details WHERE project_id=?',(pid,)).fetchone()
                report['legacy_states'].append(dict(name=old['name'],status=old['status'],batch=batch,created_at=old['created_at'],archived=bool(detail and detail[0]),deleted=bool(c.execute('SELECT 1 FROM project_deletions WHERE project_id=?',(pid,)).fetchone())))
            c.execute('UPDATE legacy_import_runs SET report=? WHERE id=?',(json.dumps(report,ensure_ascii=False),token))
            c.execute('INSERT OR IGNORE INTO workflow_archive_roots(path) VALUES (?)',(str(run),))
        try:(run/'导入报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
        return report

def source_size(path):return Path(path).stat().st_size*3
