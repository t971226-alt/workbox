"""One durable plan for raw ZIP tasks, copies, receipts, and replay."""
import json
from pathlib import Path
from contextlib import ExitStack,nullcontext
from datetime import datetime
from concurrent.futures import CancelledError
from native_files import ZipLease,cancelled,plain_path
from workspace_manager import _hash_stream
from archive_support import validate_stream
from production import source_digest,normalized
import intake_history as history
import intake_link


def _ids(c,pid):
    return [r[0] for r in c.execute('SELECT id FROM original_zip_receipts WHERE project_id=? ORDER BY id',(pid,))]


def _new_task(store,c,row,token,batches):
    key=(row.get('commission',''),row.get('processing_mode','review'))
    if key not in batches:
        label=datetime.now().strftime('%m-%d %H:%M:%S 原包收录-')+token[-8:]+'-'+str(len(batches)+1)
        bid=c.execute('INSERT INTO batches(name) VALUES (?)',(label,)).lastrowid
        mid=c.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)',(bid,'ZIP 原包收录，包内材料尚未核对')).lastrowid
        store._batch_metadata(c,bid,key[0],label)
        c.execute('INSERT INTO batch_processing_modes VALUES (?,?)',(bid,key[1]))
        batches[key]=(bid,mid)
    bid,mid=batches[key]
    pid=c.execute('INSERT INTO projects(batch_id,message_id,name) VALUES (?,?,?)',(bid,mid,row['name'])).lastrowid
    kinds=[m['kind'] for m in row.get('materials',[])]
    store._set_requirements(c,pid,kinds,row.get('package_name','原包待核对'),row.get('category',''),'确认压缩包原件收录；复制前登记可恢复任务')
    return pid


def begin(service,plan,decisions,*,cancel_event=None,progress=None):
    store=service.production;token='zip-'+plan['token']
    with getattr(store.db,'_operation_lock',nullcontext()):
        old=history.load_raw(store,token)
        if old is not None:return history.view(store,old)
        from original_zip_intake import OriginalZipIntake
        intake=OriginalZipIntake(service);selected={}
        for g in plan['groups']:
            d=decisions.get(g['key'],{});pid=d.get('project_id')
            if d.get('action') not in ('new','overwrite','replace','skip') or pid not in [r['id'] for r in g['candidates']]:raise ValueError('请明确选择替换或新建任务。')
            current=intake.rows([r['id'] for r in g['candidates']])
            if intake.snapshot(current)!=g['snapshot']:raise ValueError('任务或原包已变化，请重新拖入并核对。')
            selected[g['key']]=dict(d,row=next(r for r in current if r['id']==pid))
        validated=[];invalid=[];choices=[]
        with ExitStack() as stack:
            for e in plan['entries']:
                cancelled(cancel_event)
                if any(selected[k]['action']=='skip' for k in e['groups']):continue
                try:
                    lease=stack.enter_context(ZipLease(e['path'],movable=False))
                    if source_digest(e['path'])!=e['sha256']:raise ValueError('ZIP 来源已变化，请重新拖入并核对。')
                    validate_stream(lease.stream,e['path'],cancel_event)
                except CancelledError:raise
                except (OSError,ValueError) as error:
                    invalid.append(history.failed_file(e['path'],error,digest=e['sha256']));continue
                if e['groups']:
                    validated.append((e,e['groups'],[]));continue
                if e.get('targets') is not None:
                    validated.append((e,[],e['targets']));continue
                routing=service.file_routing(e['path'])
                if plan.get('project_id') is not None:
                    validated.append((e,[],[dict(project_id=plan['project_id'])]));continue
                if routing.get('requires_choice') or len(routing['candidates'])>1:
                    choices.append(dict(intake_choice=e['path'],routing=routing,zip_mode='original'));continue
                if routing['candidates']:
                    validated.append((e,[],[dict(project_id=routing['candidates'][0]['id'])]));continue
                names=[Path(e['path']).stem]
                if Path(e['path']).suffix.casefold()=='.zip' and service.is_bundle_candidate(Path(e['path']).name):
                    from bundle_analyzer import analyze_bundle
                    bundle=analyze_bundle(e['path'],service.list_projects(archived=None,deleted=None))
                    if bundle['state']=='matched':
                        validated.append((e,[],[dict(project_id=p) for p in bundle['project_ids']]));continue
                    names=bundle.get('provision_names',[])
                    if not names:
                        choices.append(dict(intake_choice=e['path'],routing=dict(candidates=service.list_projects(),requires_choice=True,basis=bundle['reason']),zip_mode='original'));continue
                if not service.workspaces.settings()['auto_create_projects']:
                    choices.append(dict(intake_choice=e['path'],routing=dict(candidates=service.list_projects(),requires_choice=True,basis='请先建立或选择项目'),zip_mode='original'));continue
                validated.append((e,[],[dict(name=n,processing_mode=service.get_setting('default_processing_mode','review') or 'review') for n in names]))
            if not validated and len(plan['entries'])==1:
                if invalid:raise ValueError(invalid[0]['error'])
                if choices:return choices[0]
            incoming={}
            for e,keys,targets in validated:
                for target in targets:
                    if target.get('project_id'):continue
                    key=normalized(target['name']);previous=incoming.get(key)
                    if previous and previous!=e['sha256']:raise ValueError('本批包含同名软件的不同 ZIP，请分别收录并选择替换或新建。')
                    incoming[key]=e['sha256']
            result=dict(raw_zip_intake=True,import_token=token,files=list(invalid),created_project_ids=[],routing_choices=choices)
            # No copy or task creation precedes this durable, atomic metadata boundary.
            with store.db.connect() as c:
                c.execute('BEGIN IMMEDIATE');batches={};new_ids={}
                if plan.get('archive_rule'):
                    from intake_rule_options import save_archive_rule
                    save_archive_rule(c,plan['archive_rule'])
                for e,keys,targets in validated:
                    assigned=[]
                    for key in keys:
                        d=selected[key];pid=d['project_id'];store.db._assert_projects_editable(c,pid)
                        if d['action']=='new':
                            if key not in new_ids:
                                new_ids[key]=_new_task(store,c,d['row'],token,batches);result['created_project_ids'].append(new_ids[key])
                            pid=new_ids[key]
                        assigned.append(pid)
                    for target in targets:
                        if target.get('project_id'):
                            pid=target['project_id'];store.db._assert_projects_editable(c,pid)
                        else:
                            if not target['name'].strip() or len(target['name'])>200:raise ValueError('软件名称需要 1 至 200 个字符。')
                            key=('incoming',normalized(target['name']))
                            if key not in new_ids:
                                new_ids[key]=_new_task(store,c,target,token,batches);result['created_project_ids'].append(new_ids[key])
                            pid=new_ids[key]
                        assigned.append(pid)
                    for pid in dict.fromkeys(assigned):
                        row=c.execute('SELECT name FROM projects WHERE id=?',(pid,)).fetchone()
                        output=None
                        if e.get('storage'):
                            from intake_storage import bind
                            directory=bind(service,c,pid,e['storage'],{'__original__':e['storage']['base']})
                            output=dict(directory=directory,filename=e['storage']['base']+Path(e['path']).suffix)
                        if plan.get('archive_rule'):
                            from intake_rule_options import bind_raw_rule
                            bind_raw_rule(service,c,pid,row['name'],plan['archive_rule'])
                        replacing=[selected[k] for k in keys if selected[k]['action']=='replace' and selected[k]['project_id']==pid]
                        if replacing:
                            expected=sorted(z['id'] for z in replacing[0]['row'].get('original_zips',[]))
                            if _ids(c,pid)!=expected:raise ValueError('原包在确认后发生变化，请重新核对；未直接覆盖。')
                            if any(f.get('replace_current') and f['project_id']==pid for f in result['files']):
                                raise ValueError('直接覆盖时，每个软件请选择一份原包；多份压缩包请分别收录。')
                        f=history.failed_file(e['path'],'等待保存',pid=pid,name=row['name'],digest=e['sha256'],expected=_ids(c,pid));f['state']='pending'
                        f['replace_current']=bool(replacing)
                        if output:f['output']=output
                        if plan.get('library_original'):f['library_original']=True
                        result['files'].append(f)
                history.save(store,history.summarize(result),connection=c)
        return retry(store,token,cancel_event=cancel_event,progress=progress)


def _valid(path,digest):
    try:
        with ZipLease(path,movable=False) as lease:
            validate_stream(lease.stream,path)
            return _hash_stream(lease.stream)[0]==digest
    except (OSError,ValueError):return False


def _recover(store,f):
    """Recover build-23 split commits by content, without changing newer receipts."""
    with store.db.connect() as c:
        rows=c.execute('SELECT * FROM original_zip_receipts WHERE project_id=? AND sha256=? ORDER BY id',(f['project_id'],f['source_sha256'])).fetchall()
    for r in rows:
        if r['id'] not in f.get('expected_zip_ids',[]) and _valid(r['path'],f['source_sha256']):
            f.update(state='complete',error='',saved_path=r['path'],saved_sha256=r['sha256'],receipt_id=r['id'],recovered_receipt=True);return True
    return False


def _orphan_copy(store,f):
    """Locate only verified deterministic destinations of this approved copy."""
    workspace=store.service.workspaces.get_workspace(f['project_id'])
    if not workspace:return None
    root=plain_path(workspace['path'])
    if f.get('library_original'):
        from intake_rule_options import raw_directory
        root=raw_directory(store.service,f['project_id'],f['name'])
    if f.get('output'):root=plain_path(f['output']['directory'])
    name=f.get('output',{}).get('filename') or Path(f['source_path']).name;digest=f['source_sha256']
    if not digest:return None
    candidates=[root/name]
    versions=root/'ZIP收件版本'
    if versions.is_dir():candidates.extend(p/name for p in versions.glob(digest[:16]+'*') if p.is_dir())
    for p in candidates:
        try:
            p=plain_path(p)
            if p.is_relative_to(root) and _valid(p,digest):return str(p)
        except (ValueError,OSError):pass
    return None


def _share_recovered_baseline(store,result,f):
    ids=history.zip_ids(store,f['project_id'])
    own={item['receipt_id'] for item in result['files'] if item['project_id']==f['project_id'] and item.get('receipt_id')}
    for item in result['files']:
        if item['project_id']==f['project_id'] and item['state']!='complete' and set(ids)-set(item.get('expected_zip_ids',[]))<=own:
            item['expected_zip_ids']=ids


def _commit(store,result,f,path,digest):
    # The physical copy is verified again under a lease by the caller.
    with store.db.connect() as c:
        c.execute('BEGIN IMMEDIATE');store.db._assert_projects_editable(c,f['project_id'])
        if _ids(c,f['project_id'])!=f.get('expected_zip_ids',[]):raise ValueError('原包记录在保存期间变化，请重新核对。')
        previous=c.execute('SELECT * FROM original_zip_receipts WHERE project_id=? ORDER BY id DESC LIMIT 1',(f['project_id'],)).fetchone()
        r=intake_link.register_original_zip(store.service,dict(project_id=f['project_id'],working_zip_path=path),f['source_path'],connection=c)
        f.update(r['files'][0]);f['saved_sha256']=digest
        f['receipt_id']=c.execute('SELECT id FROM original_zip_receipts WHERE project_id=? AND path=? AND sha256=?',(f['project_id'],path,digest)).fetchone()[0]
        if f.get('replace_current') and previous and previous['id']!=f['receipt_id']:
            from version_overwrite import retire_raw
            retire_raw(c,previous,source=f['source_path'])
        for other in result['files']:
            if other['project_id']==f['project_id'] and other['state']!='complete':other['expected_zip_ids']=_ids(c,f['project_id'])
        history.save(store,history.summarize(result),connection=c)


def retry(store,token,*,project_id=None,source_path=None,accepted_source=None,cancel_event=None,progress=None):
    from retry_intake import paused
    with getattr(store.db,'_operation_lock',nullcontext()):
        result=history.load_raw(store,token)
        if result is None:raise ValueError('原包收录记录不存在。')
        for index in range(len(result['files'])):
            f=result['files'][index]
            if f['state'] in ('complete','superseded'):continue
            pid,path=f['project_id'],f['source_path']
            if not pid:continue
            if project_id is not None and pid!=project_id:continue
            if source_path is not None and path!=source_path:continue
            cancelled(cancel_event)
            with store.db.connect() as c:
                if paused(c,pid):continue
                store.db._assert_projects_editable(c,pid)
            before=json.loads(json.dumps(result))
            try:
                if _recover(store,f):
                    _share_recovered_baseline(store,result,f)
                    history.save(store,history.summarize(result));continue
                if history.zip_ids(store,pid)!=f.get('expected_zip_ids',[]):
                    f.update(state='superseded',error='任务已有其他原包，旧失败项停止重试；请重新拖入并选择替换。');history.save(store,history.summarize(result));continue
                recovered=_orphan_copy(store,f) if not accepted_source or accepted_source==f['source_sha256'] else None
                if recovered:
                    with ZipLease(recovered,movable=False):
                        _commit(store,result,f,recovered,f['source_sha256'])
                    continue
                digest=source_digest(path)
                if not digest:raise OSError('源 ZIP 不存在或无法读取。')
                if digest!=f.get('source_sha256'):
                    if not (project_id==pid and source_path==path and accepted_source==digest):
                        f.update(state='source_changed',error='源 ZIP 内容变化，请核对并确认当前文件后重试。');history.save(store,history.summarize(result));continue
                    f['source_sha256']=digest
                with ZipLease(path,movable=False):
                    if source_digest(path)!=digest:raise ValueError('源 ZIP 已变化，请重新核对。')
                    store.service.workspaces.ensure_projects([pid]);workspace=store.service.workspaces.get_workspace(pid)
                    directory=workspace['path']
                    if f.get('output'):
                        directory=plain_path(f['output']['directory']);directory.mkdir(parents=True,exist_ok=True)
                    elif f.get('library_original'):
                        from intake_rule_options import raw_directory
                        directory=raw_directory(store.service,pid,f['name'],create=True)
                    options=dict(filename=f['output']['filename']) if f.get('output') else {}
                    copied=store.service._working_zip_copy(Path(path),directory,cancel_event=cancel_event,progress=progress,**options)
                    with ZipLease(copied['path'],movable=False) as lease:
                        if _hash_stream(lease.stream)[0]!=digest:raise ValueError('原包副本校验失败，请保留现场。')
                        _commit(store,result,f,copied['path'],digest)
            except Exception as error:
                # A failed commit must also roll back its in-memory completion and baselines.
                result=before;f=result['files'][index];f.update(state='failed',error=str(error))
                history.save(store,history.summarize(result))
                if isinstance(error,CancelledError):raise
        result=history.summarize(result)
        items=[]
        for f in result['files']:
            if f['state']=='complete':items.append(dict(raw_zip_intake=True,working_zip_path=f['saved_path'],files=[f],project_ids=[f['project_id']],created_project_ids=[]))
            else:items.append(dict(zip_original_error=f['error'],source_path=f['source_path']))
        items.extend(result.get('routing_choices',[]))
        if len(items)>1:result['zip_original_results']=items
        elif items and items[0].get('working_zip_path'):result['working_zip_path']=items[0]['working_zip_path']
        history.save(store,result)
        from version_overwrite import finalize
        finalize(store)
        return history.view(store,result)
