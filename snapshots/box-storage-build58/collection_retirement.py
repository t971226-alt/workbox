"""Commit-ordered retirement of managed exports. Pending work survives restart."""
from pathlib import Path
from contextlib import nullcontext
from native_files import ZipLease,plain_path
from workspace_manager import _hash_stream

SCHEMA='''CREATE TABLE IF NOT EXISTS collection_retirements(
 id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL,path TEXT NOT NULL,root TEXT NOT NULL,
 history_path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '');'''


def finalize(store):
    errors=[]
    with getattr(store.db,'_operation_lock',nullcontext()),store.db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute("SELECT * FROM collection_retirements WHERE state='pending' ORDER BY id").fetchall():
            try:
                path=plain_path(row['path']);history=plain_path(row['history_path'])
                if not path.is_relative_to(plain_path(row['root'])) or not history.is_relative_to(store.db.path.parent/'材料历史'):
                    raise ValueError('历史整理路径不在登记范围，保留文件。')
                # Never remove a currently referenced file or a user input.
                protected=any(c.execute(f'SELECT 1 FROM {table} WHERE {field}=? COLLATE NOCASE LIMIT 1',(str(path),)).fetchone() for table,field in
                    [('collected_materials','path'),('work_files','path'),('work_files','source_path'),('material_versions','path'),
                     ('production_import_files','source_path'),('production_import_files','saved_path'),('original_zip_receipts','path')])
                if protected:
                    c.execute("UPDATE collection_retirements SET state='retained',error='仍被记录或输入引用，保留文件' WHERE id=?",(row['id'],));continue
                with ZipLease(history,movable=False) as saved:
                    if _hash_stream(saved.stream)!=(row['sha256'],row['size']):raise ValueError('历史副本无法通过校验，保留原文件。')
                    if path.exists():
                        with ZipLease(path,movable=True) as old:
                            if _hash_stream(old.stream)!=(row['sha256'],row['size']):raise ValueError('待整理副本已经变化，保留文件。')
                            old.remove_after_verified_commit()
                c.execute("UPDATE collection_retirements SET state='complete',error='' WHERE id=?",(row['id'],))
            except (OSError,ValueError) as error:
                if row['error']!=str(error):errors.append(dict(source_path=row['path'],error=str(error)))
                c.execute('UPDATE collection_retirements SET error=? WHERE id=?',(str(error),row['id']))
    return errors
