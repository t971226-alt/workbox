"""Retry only active tasks and preserve the identity of each intake attempt."""
import json
from pathlib import Path
from concurrent.futures import CancelledError
from native_files import cancelled,ZipLease
from production import source_digest


def paused(c,pid):
    return bool(c.execute('SELECT 1 FROM project_deletions WHERE project_id=?',(pid,)).fetchone() or
        c.execute('SELECT 1 FROM project_details WHERE project_id=? AND archived_at IS NOT NULL',(pid,)).fetchone())


def superseded(store,c,row,result):
    if row['state']=='superseded':return True
    if row['state']=='complete':return False
    kind=next((b['kind'] for b in result.get('material_bindings',[]) if b['project_id']==row['project_id'] and b['path']==row['source_path']),None) or store.match_kind(c,row['project_id'],Path(row['source_path']).name)
    current=store._current(c,row['project_id'],kind) if kind else None
    return bool(current and current['id'] and current['id']!=row['expected_version_id'] and current['sha256']!=row['source_sha256'])


def pending_imports(store):
    with store.db.connect() as c:
        imports=c.execute('SELECT token,result_json FROM production_imports ORDER BY created_at DESC,rowid DESC').fetchall();result=[]
        for imported in imports:
            plan=json.loads(imported['result_json'])
            rows=c.execute("SELECT * FROM production_import_files WHERE token=? AND state NOT IN ('complete','superseded')",(imported['token'],)).fetchall()
            count=sum(not paused(c,r['project_id']) and not superseded(store,c,r,plan) for r in rows)
            if count:result.append(dict(token=imported['token'],pending_count=count))
        return result


def retry_import_files(store,token,*,project_id=None,source_path=None,accepted_source=None,cancel_event=None,progress=None):
    with store.db.connect() as c:
        imported=c.execute('SELECT result_json FROM production_imports WHERE token=?',(token,)).fetchone()
        if not imported:raise ValueError('导入记录不存在。')
        result=json.loads(imported[0])
        pending=[dict(r) for r in c.execute("SELECT * FROM production_import_files WHERE token=? AND state!='complete'",(token,))]
    if project_id is not None:pending=[r for r in pending if r['project_id']==project_id]
    if source_path is not None:pending=[r for r in pending if r['source_path']==source_path]
    def kind(row):
        return next((b['kind'] for b in result.get('material_bindings',[]) if b['project_id']==row['project_id'] and b['path']==row['source_path']),None)
    pending.sort(key=lambda r:Path(kind(r) or r['source_path']).suffix.casefold()=='.pdf')
    result.pop('workspace_error',None);result['intake_errors']=[]
    for row in pending:
        pid,path=row['project_id'],row['source_path'];key=(token,pid,path)
        try:
            cancelled(cancel_event)
            with store.db.connect() as c:
                if paused(c,pid):continue
                if superseded(store,c,row,result):
                    c.execute("UPDATE production_import_files SET state='superseded',error=? WHERE token=? AND project_id=? AND source_path=?",('已有新版材料，旧失败项已停止重试；如需使用旧文件，请重新拖入并选择替换。',*key));continue
            digest=source_digest(path)
            if not digest:raise OSError('源文件不存在或无法读取，请重新选择来源。')
            if not row['source_sha256'] or digest!=row['source_sha256']:
                # Approval names exactly one file and the hash displayed by the UI.
                approved=(project_id==pid and source_path==path and accepted_source==digest)
                if not approved:
                    message='源文件内容已变化，请在收录结果中核对并确认后重试。' if row['source_sha256'] else '旧记录没有源文件校验信息，请在收录结果中核对并确认后重试。'
                    with store.db.connect() as c:c.execute("UPDATE production_import_files SET state='source_changed',error=? WHERE token=? AND project_id=? AND source_path=?",(message,*key))
                    result['intake_errors'].append(dict(path=path,error=message));continue
                with store.db.connect() as c:c.execute('UPDATE production_import_files SET source_sha256=? WHERE token=? AND project_id=? AND source_path=?',(digest,*key))
            with ZipLease(path,movable=False):
                if source_digest(path)!=digest:raise ValueError('来源在等待期间变化，请重新核对后重试。')
                attached=store.service._attach_workspaces(dict(project_ids=[pid]))
                if attached.get('workspace_error'):raise OSError(attached['workspace_error'])
                with store.db.connect() as c:material_kind=kind(row) or store.match_kind(c,pid,Path(path).name)
                if material_kind:
                    binding=next((b for b in result.get('material_bindings',[]) if b['project_id']==pid and b['path']==path),{})
                    store.receive(pid,material_kind,path,cancel_event=cancel_event,progress=progress,import_token=token,
                        replace_current=binding.get('replace_current',False),expected_version_id=row['expected_version_id'])
                else:store.service.receive_file(path,pid,manual=True,cancel_event=cancel_event,progress=progress)
                with store.db.connect() as c:
                    completed=c.execute('SELECT state FROM production_import_files WHERE token=? AND project_id=? AND source_path=?',key).fetchone()
                    if completed and completed['state']=='complete':continue
                    material_kind=material_kind or store.match_kind(c,pid,Path(path).name)
                    v=store._current(c,pid,material_kind) if material_kind else None
                    if not v or not v['id'] or v['sha256']!=digest:
                        raise ValueError('来源在收录期间变化或无法核对保存版本，请重新核对材料。')
                    # Immutable version path, never the mutable latest published location.
                    c.execute("""UPDATE production_import_files SET state='complete',error='',saved_path=?,saved_sha256=?,saved_version_id=?,
                        saved_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE token=? AND project_id=? AND source_path=?""",
                        (v['path'],v['sha256'],v['id'],*key))
        except Exception as error:
            result['intake_errors'].append(dict(path=path,error=str(error)))
            with store.db.connect() as c:c.execute("UPDATE production_import_files SET state='failed',error=? WHERE token=? AND project_id=? AND source_path=?",(str(error),*key))
            if isinstance(error,CancelledError):break
    with store.db.connect() as c:
        remaining=[dict(r) for r in c.execute('SELECT * FROM production_import_files WHERE token=?',(token,))]
        result['copied_count']=sum(r['state']=='complete' for r in remaining)
        result['superseded_count']=sum(r['state']=='superseded' for r in remaining)
        result['paused_count']=sum(r['state'] not in ('complete','superseded') and paused(c,r['project_id']) for r in remaining)
        result['pending_count']=len(remaining)-result['copied_count']-result['paused_count']-result['superseded_count']
        c.execute('UPDATE production_imports SET result_json=? WHERE token=?',(json.dumps(result),token))
    from intake_history import save
    save(store,result)
    return result
