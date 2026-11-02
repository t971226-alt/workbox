"""Verified, resumable storage consolidation; business versions and facts stay intact."""
from pathlib import Path
from uuid import uuid4
import json
import os
import shutil
import sqlite3
from contextlib import closing
from native_files import plain_path,cancelled,ZipLease
from workspace_manager import copy_verified,_hash_stream
from portable_update import write_json


def _jobs(service):return service.db.path.parent/'材料目录整理'

def _same_volume(source,target):return source.stat().st_dev==target.parent.stat().st_dev

def _pending(service):
    root=_jobs(service)
    if root.exists():
        for p in sorted(root.glob('*/plan.json')):
            plan=json.loads(p.read_text('utf-8'))
            if plan['state']!='complete':return p,plan
    return None

def _candidates(service):
    root=plain_path(service.workspaces.settings()['work_root']);moves=[];reserved=set()
    with service.db.connect() as c:
        known=c.execute('SELECT path,category FROM collection_batch_folders').fetchall()
    legacy=root/'分类收录'
    legacy_layout=any(Path(r['path']).is_relative_to(legacy/r['category']) for r in known)
    def add(source,target):
        source=plain_path(source);target=plain_path(target)
        if not source.exists() or source==target:return
        if any(source==Path(m['source']) or source.is_relative_to(Path(m['source'])) for m in moves):return
        if source==root or not source.is_relative_to(root):return
        base=target;i=2
        while target.exists() or str(target).casefold() in reserved:
            target=base.with_name(base.stem+f'（{i}）'+base.suffix) if source.is_file() else base.with_name(base.name+f'（{i}）');i+=1
        if target.is_relative_to(source):raise ValueError('整理目标不能位于原文件夹内。')
        reserved.add(str(target).casefold());moves.append(dict(source=str(source),target=str(target),mode='',ready=False))
    # Reuse the verified backup, rename and reference-rewrite transaction for
    # the previous reserved working-directory names. Never overwrite a target.
    from storage_management import INTERNAL,STAGING,WORK,LEGACY_INTERNAL,LEGACY_STAGING,LEGACY_WORK
    old_internal=root/LEGACY_INTERNAL
    if old_internal.is_dir():
        for source in old_internal.iterdir():
            target=root/INTERNAL/{LEGACY_STAGING:STAGING,LEGACY_WORK:WORK}.get(source.name,source.name)
            if source.is_dir() and target.is_dir():
                for child in source.iterdir():add(child,target/child.name)
            else:add(source,target)
    if legacy_layout and legacy.is_dir():
        for category in sorted(legacy.iterdir()):
            destination=(root/category.name if category.name!='历史版本' else service.db.path.parent/'材料历史/旧分类版本') if category.is_dir() else root/'旧版材料/分类收录附加文件'/category.name
            if destination.exists() and category.is_dir():
                for child in sorted(category.iterdir()):add(child,destination/child.name)
            else:add(category,destination)
    with service.db.connect() as c:
        rows=c.execute('SELECT w.group_path,w.path,EXISTS(SELECT 1 FROM production_tasks t WHERE t.project_id=w.project_id) managed FROM project_workspaces w').fetchall()
    grouped={}
    for row in rows:
        group=plain_path(row['group_path'])
        if not group.is_relative_to(root) or group==root or group.is_relative_to(legacy):continue
        if not row['managed'] and group.is_relative_to(root/'旧版材料'):continue
        grouped.setdefault(group,[]).append(bool(row['managed']))
    for group,managed in sorted(grouped.items(),key=lambda pair:len(pair[0].parts)):
        from storage_management import work_root as internal_work_root
        target=(internal_work_root(service) if all(managed) else root/'旧版材料')/group.name
        add(group,target)
    return moves


def needs_unification(service):return bool(_pending(service) or _candidates(service))

def _manifest(path,cancel_event=None):
    path=plain_path(path);files=[path] if path.is_file() else []
    if path.is_dir():
        for directory,dirs,names in os.walk(path,followlinks=False):
            plain_path(directory)
            for name in dirs:plain_path(Path(directory)/name)
            files.extend(plain_path(Path(directory)/name) for name in names)
    result={}
    for file in sorted(files):
        cancelled(cancel_event)
        with ZipLease(file,movable=False) as lease:sha,size=_hash_stream(lease.stream,cancel_event=cancel_event)
        result['.' if file==path else str(file.relative_to(path))]=dict(sha256=sha,size=size)
    return result

def _copy_tree(source,target,manifest,cancel_event):
    if source.is_dir():target.mkdir(parents=True,exist_ok=True)
    for relative,expected in manifest.items():
        cancelled(cancel_event);src=source if relative=='.' else source/relative;dst=target if relative=='.' else target/relative
        dst.parent.mkdir(parents=True,exist_ok=True)
        if dst.exists():
            with ZipLease(dst,movable=False) as lease:actual=_hash_stream(lease.stream,cancel_event=cancel_event)
            if actual!=(expected['sha256'],expected['size']):raise ValueError('整理备份中的文件发生变化，原文件保留。')
        else:
            copied=copy_verified(src,dst,cancel_event=cancel_event)
            if (copied['sha256'],copied['size'])!=(expected['sha256'],expected['size']):raise ValueError('备份校验失败，原文件保留。')

def _rewrite_references(connection,moves):
    mappings=sorted([(m['source'].replace('\\','/'),Path(m['target'])) for m in moves],key=lambda m:len(m[0]),reverse=True)
    def path(value):
        if not isinstance(value,str):return value
        normalized=value.replace('\\','/')
        for old,new in mappings:
            if normalized.casefold()==old.casefold():return str(new)
            if normalized.casefold().startswith(old.casefold()+'/'):return str(new/normalized[len(old)+1:])
        return value
    def nested(value):
        if isinstance(value,str):return path(value)
        if isinstance(value,list):return [nested(v) for v in value]
        if isinstance(value,dict):
            human={'raw_text','source_text','text','note','message','reason','name','label','category','package','package_name','kind','filename'}
            return {path(k):(v if k in human else nested(v)) for k,v in value.items()}
        return value
    def quote(name):return '"'+name.replace('"','""')+'"'
    changed=0
    tables=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns=[r[1] for r in connection.execute('PRAGMA table_info('+quote(table)+')')]
        for row in connection.execute('SELECT rowid,* FROM '+quote(table)).fetchall():
            updates={}
            for name,value in zip(columns,row[1:]):
                if not isinstance(value,str):continue
                path_column=name=='path' or name.endswith('_path') or name.endswith('_root') or name in ('source','destination','directory','target','source_key')
                replacement=path(value) if path_column else value
                if name=='source_key' and replacement!=value:replacement=os.path.normcase(replacement).casefold()
                metadata=name in ('value','result_json','files_json','plan_json','payload_json','metadata_json','metadata')
                if metadata and replacement==value and value[:1] in ('{','[','"'):
                    try:
                        decoded=json.loads(value);mapped=nested(decoded)
                        if mapped!=decoded:replacement=json.dumps(mapped,ensure_ascii=False)
                    except (ValueError,TypeError):pass
                if replacement!=value:updates[name]=replacement
            if updates:
                connection.execute('UPDATE '+quote(table)+' SET '+','.join(quote(k)+'=?' for k in updates)+' WHERE rowid=?',(*updates.values(),row[0]));changed+=1
    return changed

def _validate_plan(service,journal,plan,root):
    job=plain_path(journal.parent);base=plain_path(_jobs(service))
    token=plan.get('token','')
    if job.parent!=base or job.name!=token or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('整理记录位置无效。')
    if plan.get('state') not in ('pending','paths_updated','complete'):raise ValueError('整理状态无效。')
    targets=set()
    for move in plan['moves']:
        source=plain_path(move['source']);target=plain_path(move['target']);backup=plain_path(move['backup'])
        if source==root or not source.is_relative_to(root) or target==source or target.is_relative_to(source):raise ValueError('原目录超出整理范围。')
        allowed=(root,service.db.path.parent/'工作缓存',service.db.path.parent/'材料历史')
        if not any(target.is_relative_to(p) and target!=p for p in allowed) or not backup.is_relative_to(job/'原目录备份'):raise ValueError('整理目标超出指定范围。')
        if target in targets:raise ValueError('整理目标重复。')
        targets.add(target)
        for relative,meta in move['files'].items():
            value=Path(relative)
            if relative!='.' and (value.is_absolute() or '..' in value.parts or ':' in relative or '\x00' in relative):raise ValueError('备份文件路径无效。')
            if len(meta['sha256'])!=64 or any(c not in '0123456789abcdef' for c in meta['sha256']) or type(meta['size']) is not int or meta['size']<0:raise ValueError('备份文件校验信息无效。')


def unify_storage(service,*,cancel_event=None,progress=None):
    pending=_pending(service);root=plain_path(service.workspaces.settings()['work_root'])
    if pending:journal,plan=pending
    else:
        moves=_candidates(service)
        if not moves:return dict(storage_unified=True,moved_directories=0)
        token=uuid4().hex;job=_jobs(service)/token;job.mkdir(parents=True);journal=job/'plan.json'
        for i,move in enumerate(moves):
            move['files']=_manifest(Path(move['source']),cancel_event);move['backup']=str(job/'原目录备份'/str(i))
        required=sum(meta['size'] for m in moves for meta in m['files'].values())*2+service.db.path.stat().st_size*3+16*1024**2
        if shutil.disk_usage(job).free<required:raise ValueError('整理备份空间不足，未移动原材料。')
        with closing(sqlite3.connect(service.db.path)) as c,closing(sqlite3.connect(job/'整理前.db')) as b:c.backup(b)
        plan=dict(token=token,root=str(root),state='pending',moves=moves);write_json(journal,plan)
    if Path(plan['root'])!=root:raise ValueError('材料保存目录已变化，请先完成上次整理。')
    _validate_plan(service,journal,plan,root)
    with service.db.connect() as c:
        applied=c.execute("SELECT value FROM workflow_settings WHERE key='storage_layout_job'").fetchone()
        applied=bool(applied and json.loads(applied[0])==plan['token'])
    if not applied:
        try:
            # Verify independent copies before moving any live directory.
            for move in plan['moves']:
                source=plain_path(move['source']);target=plain_path(move['target'])
                if not source.exists() and target.exists() and move['ready']:continue
                if _manifest(source,cancel_event)!=move['files']:raise ValueError('原目录在整理期间发生变化，文件保留，请核对后重试。')
                _copy_tree(source,Path(move['backup']),move['files'],cancel_event)
            for i,move in enumerate(plan['moves']):
                cancelled(cancel_event);source=plain_path(move['source']);target=plain_path(move['target'])
                if move['ready'] and target.exists():
                    if move['mode']=='copy':_copy_tree(source,target,move['files'],cancel_event)
                    if _manifest(target,cancel_event)!=move['files']:raise ValueError('整理目标已变化，文件保留。')
                    continue
                if target.exists():raise ValueError('整理目标已存在，停止以避免覆盖。')
                if _manifest(source,cancel_event)!=move['files']:raise ValueError('来源目录已变化，文件保留。')
                target.parent.mkdir(parents=True,exist_ok=True)
                # Journal the intended rename before the atomic filesystem step.
                move['mode']='rename' if _same_volume(source,target) else 'copy'
                move['ready']=True;write_json(journal,plan)
                if move['mode']=='rename':source.replace(target)
                else:_copy_tree(source,target,move['files'],cancel_event)
                if _manifest(target,cancel_event)!=move['files']:raise ValueError('整理后文件校验失败，备份保留。')
                if progress:progress(i+1,len(plan['moves']))
            with service.db.connect() as c:
                c.execute('BEGIN IMMEDIATE');changed=_rewrite_references(c,plan['moves'])
                c.execute("INSERT INTO workflow_settings(key,value) VALUES ('storage_layout_job',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(plan['token']),))
            applied=True;plan['changed_reference_rows']=changed;plan['state']='paths_updated';write_json(journal,plan)
        except BaseException:
            if not applied:
                for move in reversed(plan['moves']):
                    source=Path(move['source']);target=Path(move['target'])
                    if move['mode']=='rename' and move['ready'] and target.exists() and not source.exists():
                        source.parent.mkdir(parents=True,exist_ok=True);target.replace(source);move['ready']=False
                write_json(journal,plan)
            raise
    # Referenced current/old versions remain intact; re-publish only into their types.
    from storage_management import LEGACY_INTERNAL
    working_names_only=all(Path(m['source']).is_relative_to(root/LEGACY_INTERNAL) for m in plan['moves'])
    if not working_names_only:
        service.production.refresh_collections(cancel_event=cancel_event,progress=progress)
    retained=[]
    for move in plan['moves']:
        if move['mode']!='copy':continue
        source=plain_path(move['source'])
        for relative,expected in move['files'].items():
            file=source if relative=='.' else source/relative
            if not file.exists():continue
            try:
                with ZipLease(file,movable=True) as lease:
                    if _hash_stream(lease.stream)==(expected['sha256'],expected['size']):lease.remove_after_verified_commit()
                    else:retained.append(str(file))
            except OSError:retained.append(str(file))
        if source.is_dir():
            for directory,dirs,files in os.walk(source,topdown=False):
                try:Path(directory).rmdir()
                except OSError:pass
    from storage_management import LEGACY_INTERNAL
    for legacy in (root/'分类收录',root/LEGACY_INTERNAL):
        if legacy.is_dir():
            for directory,dirs,files in os.walk(legacy,topdown=False):
                try:Path(directory).rmdir()
                except OSError:pass
    plan.update(state='complete',retained=retained);write_json(journal,plan)
    return dict(storage_unified=True,moved_directories=len(plan['moves']),storage_backup=str(journal.parent),retained_old_files=retained)
