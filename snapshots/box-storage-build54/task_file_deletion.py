"""Explicit task-copy deletion with shared-file protection and crash recovery."""
from pathlib import Path
from contextlib import nullcontext
from uuid import uuid4
import json,hashlib,os
from native_files import plain_path,ZipLease,cancelled

SCHEMA='''CREATE TABLE IF NOT EXISTS task_file_deletions(
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),token TEXT NOT NULL,
 removed_count INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));'''
DESTINATIONS={'work_files':['path'],'material_versions':['path'],'collected_materials':['path'],
              'original_zip_receipts':['path'],'collection_retirements':['path','history_path']}


def candidates(store,ids):
    """Only database-owned copies, never directory trees or arbitrary source files."""
    owned={};shared=set();sources=set();marks=','.join('?' for _ in ids)
    with store.db.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        settings=store.service.workspaces.settings()
        roots=[plain_path(settings['work_root']),plain_path(settings['archive_root']),*map(plain_path,store.service.workspaces.archive_roots())]
        from storage_management import internal_roots
        roots.extend(p.parent for p in internal_roots(store.service))
        for path, in c.execute('SELECT path FROM project_workspaces'):roots.append(plain_path(path))
        for path, in c.execute('SELECT path FROM collection_batch_folders'):roots.append(plain_path(path))
        for path, in c.execute('SELECT directory FROM task_raw_rules WHERE directory!=\'\''):roots.append(plain_path(path))
        for path, in c.execute('SELECT directory FROM task_storage_preferences'):roots.append(plain_path(path))
        for leaf in ['材料历史','ZIP归档','材料目录整理','工作缓存']:roots.append(plain_path(store.db.path.parent/leaf))
        for table,columns in DESTINATIONS.items():
            if table not in tables:continue
            for column in columns:
                for row in c.execute('SELECT project_id,"'+column+'" FROM "'+table+'" WHERE "'+column+'" IS NOT NULL'):
                    if not row[1]:continue
                    p=plain_path(row[1])
                    if row[0] in ids:
                        if any(p.is_relative_to(root) and p!=root for root in roots) and p.is_file():owned.setdefault(p,set()).add(row[0])
                    else:shared.add(p)
        # A copy used as input by another task is shared too; an outside source
        # never becomes a deletion candidate merely because it was dragged in.
        for table in tables:
            columns={r[1] for r in c.execute('PRAGMA table_info("'+table+'")')}
            if 'source_path' in columns and 'project_id' in columns:
                for pid,path in c.execute('SELECT project_id,source_path FROM "'+table+'" WHERE source_path IS NOT NULL'):
                    if path and pid not in ids:
                        try:shared.add(plain_path(path,allow_network=True))
                        except ValueError:pass
        for path, in c.execute('''SELECT z.path FROM zips z JOIN zip_project_links l ON l.zip_id=z.id
            WHERE l.project_id NOT IN ('''+marks+')',ids):
            try:shared.add(plain_path(path))
            except ValueError:pass
        for path, in c.execute('''SELECT source.path FROM material_version_inputs i
            JOIN material_versions source ON source.id=i.source_version_id
            JOIN material_versions consumer ON consumer.id=i.version_id
            WHERE consumer.project_id NOT IN ('''+marks+')',ids):
            shared.add(plain_path(path))
    return owned,shared


def _journal_dir(store):
    p=plain_path(store.db.path.parent/'task-delete-journals');p.mkdir(exist_ok=True);return p


def _checked_file(item,token,index):
    target=plain_path(item['path']);stage=plain_path(item['stage'])
    if stage!=target.with_name('.box-delete-'+token+'-'+str(index)):raise ValueError('删除暂存记录无效。')
    return target,stage


def recover(store):
    root=plain_path(store.db.path.parent/'task-delete-journals')
    if not root.exists():return
    for manifest in root.glob('*.json'):
        data=json.loads(manifest.read_text(encoding='utf-8'));token=data['token']
        if manifest.stem!=token or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('删除恢复记录无效。')
        with store.db.connect() as c:committed=bool(c.execute('SELECT 1 FROM task_file_deletions WHERE token=?',(token,)).fetchone())
        for i,item in enumerate(data['files']):
            target,stage=_checked_file(item,token,i)
            if not stage.exists():continue
            with ZipLease(stage,movable=True) as lease:
                if hashlib.file_digest(lease.stream,'sha256').hexdigest()!=item['sha256']:raise ValueError('删除暂存文件发生变化，已保留。')
                if committed:lease.remove_after_verified_commit()
                elif not target.exists():lease.rename_to(target)
                else:raise ValueError('原位置已有新文件，删除恢复副本已保留。')
        manifest.unlink()


def delete(store,ids,*,cancel_event=None,progress=None):
    if not ids or any(type(pid) is not int or pid<=0 for pid in ids):raise ValueError('请选择有效的软件任务。')
    ids=list(dict.fromkeys(ids))
    with getattr(store.db,'_operation_lock',nullcontext()):
        recover(store);owned,shared=candidates(store,ids);token=uuid4().hex;files=[]
        for path,pids in sorted(owned.items()):
            if path in shared:continue
            cancelled(cancel_event)
            with ZipLease(path,movable=False) as lease:sha=hashlib.file_digest(lease.stream,'sha256').hexdigest()
            files.append(dict(path=str(path),stage=str(path.with_name('.box-delete-'+token+'-'+str(len(files)))),sha256=sha,project_ids=sorted(pids)))
        manifest=_journal_dir(store)/(token+'.json')
        payload=json.dumps(dict(token=token,files=files),ensure_ascii=False)
        with manifest.open('x',encoding='utf-8') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        try:
            for i,item in enumerate(files):
                cancelled(cancel_event);target,stage=_checked_file(item,token,i)
                with ZipLease(target,movable=True) as lease:
                    if hashlib.file_digest(lease.stream,'sha256').hexdigest()!=item['sha256']:raise ValueError('材料在删除前发生变化，请重新确认。')
                    lease.rename_to(stage)
            result=store._change_deleted_tasks(ids,True,cancel_event,files_token=token,
                file_counts={pid:sum(pid in f['project_ids'] for f in files) for pid in ids})
        except BaseException:
            recover(store);raise
        # Once task deletion commits, cleanup is replayable after interruption.
        try:recover(store)
        except OSError:result['cleanup_pending']=True
        result.update(deleted_file_count=len(files),preserved_shared_files=len(set(owned)&shared))
        return result
