"""Durable intake receipts and resumable raw ZIP copies."""
import json
from pathlib import Path
from uuid import uuid4
from concurrent.futures import CancelledError
from native_files import ZipLease,cancelled

SCHEMA='''CREATE TABLE IF NOT EXISTS intake_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,token TEXT NOT NULL,result_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
 CREATE INDEX IF NOT EXISTS intake_history_token ON intake_history(token,id);
 CREATE TABLE IF NOT EXISTS raw_intake_journal(token TEXT PRIMARY KEY,result_json TEXT NOT NULL);'''


def save(store,result,*,connection=None):
    if not result.get('import_token'):result['import_token']='zip-'+uuid4().hex
    def write(c):
        payload=json.dumps(result)
        c.execute('INSERT INTO intake_history(token,result_json) VALUES (?,?)',(result['import_token'],payload))
        if result.get('raw_zip_intake'):
            c.execute('INSERT INTO raw_intake_journal(token,result_json) VALUES (?,?) ON CONFLICT(token) DO UPDATE SET result_json=excluded.result_json',(result['import_token'],payload))
    if connection is not None:write(connection)
    else:
        with store.db.connect() as c:write(c)
    return result


def latest(store):
    with store.db.connect() as c:
        r=c.execute('SELECT result_json FROM intake_history ORDER BY id DESC LIMIT 1').fetchone()
        if not r:r=c.execute('SELECT result_json FROM production_imports ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
        result=json.loads(r[0]) if r else None
    if result and not result.get('raw_zip_intake'):
        from intake_link import import_receipt
        return import_receipt(store,result['import_token'])
    return view(store,result) if result else None


def load_raw(store,token):
    with store.db.connect() as c:
        r=c.execute('SELECT result_json FROM raw_intake_journal WHERE token=?',(token,)).fetchone()
        if not r:r=c.execute('SELECT result_json FROM intake_history WHERE token=? ORDER BY id DESC LIMIT 1',(token,)).fetchone()
    result=json.loads(r[0]) if r else None
    return result if result and result.get('raw_zip_intake') else None


def zip_ids(store,pid):
    with store.db.connect() as c:return [r[0] for r in c.execute('SELECT id FROM original_zip_receipts WHERE project_id=? ORDER BY id',(pid,))]


def summarize(result):
    files=result.get('files',[])
    result['copied_count']=sum(f['state']=='complete' for f in files)
    result['paused_count']=sum(f['state']=='paused' for f in files)
    result['superseded_count']=sum(f['state']=='superseded' for f in files)
    result['pending_count']=sum(f['state'] not in ('complete','paused','superseded') for f in files)
    result['project_ids']=list(dict.fromkeys([p for p in result.get('created_project_ids',[]) if p]+[f['project_id'] for f in files if f['project_id']]))
    result['intake_errors']=[dict(path=f['source_path'],error=f['error']) for f in files if f['state'] not in ('complete','paused','superseded')]
    return result


def view(store,result):
    from retry_intake import paused
    result=json.loads(json.dumps(result))
    with store.db.connect() as c:
        for f in result['files']:
            if f['state'] not in ('complete','superseded') and paused(c,f['project_id']):
                f.update(state='paused',error='任务已撤销或归档，暂停重试；恢复任务后可继续。')
            elif f['state']=='paused':f.update(state='failed',error='任务已恢复，可以继续重试。')
    return summarize(result)


def pending_raw(store):
    with store.db.connect() as c:rows=c.execute('SELECT token,result_json FROM intake_history WHERE id IN (SELECT MAX(id) FROM intake_history GROUP BY token) ORDER BY id DESC').fetchall()
    result=[]
    for row in rows:
        r=json.loads(row['result_json'])
        if r.get('raw_zip_intake'):
            count=sum(f['project_id'] and f['state'] not in ('complete','paused','superseded') for f in view(store,r)['files'])
            if count:result.append(dict(token=row['token'],pending_count=count))
    return result


def failed_file(path,error,*,pid=0,name=None,digest='',expected=None):
    return dict(project_id=pid,name=name or Path(path).stem,kind='ZIP原包',source_path=str(path),source_sha256=digest,
        saved_path='',state='failed',error=str(error),expected_zip_ids=expected or [])


def receive_original(service,paths,**kwargs):
    from original_zip_intake import OriginalZipIntake
    from raw_intake_engine import begin
    plan=OriginalZipIntake(service).prepare(paths,kwargs.pop('project_id',None))
    if plan['groups']:return dict(material_conflicts=plan['groups'],material_plan=plan)
    return begin(service,plan,{},cancel_event=kwargs.get('cancel_event'),progress=kwargs.get('progress'))


def retry_raw(store,token,**kwargs):
    from raw_intake_engine import retry
    return retry(store,token,**kwargs)
