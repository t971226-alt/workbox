"""Explicit destructive replacement, queued only with the new version's commit."""
from pathlib import Path
import json
from contextlib import nullcontext
from native_files import ZipLease, plain_path
from workspace_manager import _hash_stream

SCHEMA = '''CREATE TABLE IF NOT EXISTS version_discards(
 id INTEGER PRIMARY KEY, path TEXT NOT NULL, root TEXT NOT NULL,
 sha256 TEXT NOT NULL, size INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '');'''


def queue(c, path, digest, size, *, source):
    path = plain_path(path)
    if path == plain_path(source, allow_network=True):
        return  # Even a managed file may be the original explicitly dragged in.
    c.execute('INSERT INTO version_discards(path,root,sha256,size) VALUES (?,?,?,?)',
              (str(path), str(path.parent), digest, size))


def retire_material(c, store, previous, *, source):
    if not previous.get('id'):
        return
    vid = previous['id']
    # Preserve older versions and their records. A dependency on discarded bytes
    # becomes unavailable, rather than incorrectly referring to the new Word.
    c.execute('UPDATE material_version_inputs SET source_version_id=NULL WHERE source_version_id=?', (vid,))
    for table in ('material_version_inputs', 'material_reviews', 'material_submission_voids', 'material_submission_items'):
        c.execute(f'DELETE FROM {table} WHERE version_id=?', (vid,))
    c.execute("UPDATE production_import_files SET saved_path='',saved_version_id=NULL,error='此版本已按用户选择直接覆盖，无法恢复' WHERE saved_version_id=?", (vid,))
    c.execute('DELETE FROM material_versions WHERE id=?', (vid,))
    queue(c, previous['path'], previous['sha256'], previous['size'], source=source)
    if previous.get('work_file_id'):
        row = c.execute('SELECT * FROM work_files WHERE id=?', (previous['work_file_id'],)).fetchone()
        if row:
            queue(c, row['path'], row['sha256'], row['size'], source=source)


def retire_raw(c, previous, *, source):
    path = plain_path(previous['path'])
    # No read/delete of source originals; hashes are checked again after commit.
    if path.exists():
        with ZipLease(path, movable=False) as lease:
            digest, size = _hash_stream(lease.stream)
        if digest != previous['sha256']:
            raise ValueError('旧原包已在外部变化，请重新核对后收录。')
        queue(c, path, digest, size, source=source)
    c.execute('DELETE FROM original_zip_receipts WHERE id=?', (previous['id'],))


def finalize(store):
    """Only delete unreferenced, unchanged managed copies after DB commit."""
    errors = []
    with getattr(store.db, '_operation_lock', nullcontext()), store.db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for r in c.execute("SELECT * FROM version_discards WHERE state='pending' ORDER BY id").fetchall():
            try:
                path = plain_path(r['path'])
                if path.parent != plain_path(r['root']):
                    raise ValueError('待覆盖文件超出登记位置，保留文件。')
                protected = any(c.execute(f'SELECT 1 FROM {t} WHERE {f}=? COLLATE NOCASE LIMIT 1', (str(path),)).fetchone()
                    for t, f in [('material_versions','path'), ('collected_materials','path'),
                                 ('original_zip_receipts','path'), ('work_files','source_path'),
                                 ('production_import_files','source_path'), ('zips','path')])
                # Raw originals have a durable journal rather than work_files.
                protected = protected or any(
                    str(plain_path(f['source_path'],allow_network=True)).casefold()==str(path).casefold()
                    for journal in c.execute('SELECT result_json FROM raw_intake_journal')
                    for f in json.loads(journal[0]).get('files',[]) if f.get('source_path'))
                protected = protected or c.execute('''SELECT 1 FROM work_files w JOIN material_versions v
                    ON v.work_file_id=w.id WHERE w.path=? COLLATE NOCASE''', (str(path),)).fetchone()
                if protected:
                    c.execute("UPDATE version_discards SET state='retained',error='原始输入或其他版本仍在使用，保留文件' WHERE id=?", (r['id'],))
                    continue
                if path.exists():
                    with ZipLease(path, movable=True) as lease:
                        if _hash_stream(lease.stream) != (r['sha256'], r['size']):
                            raise ValueError('待覆盖副本已在外部变化，保留文件。')
                        lease.remove_after_verified_commit()
                c.execute("UPDATE version_discards SET state='complete',error='' WHERE id=?", (r['id'],))
                # Empty version folders only; never recursively remove a directory.
                try: path.parent.rmdir()
                except OSError: pass
            except (OSError, ValueError) as error:
                errors.append(dict(source_path=r['path'], error=str(error)))
                c.execute('UPDATE version_discards SET error=? WHERE id=?', (str(error), r['id']))
    return errors
