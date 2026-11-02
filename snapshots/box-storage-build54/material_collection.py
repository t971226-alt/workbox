"""Automatic receipt classification and batch collection folders, separate from review/submission."""
from pathlib import Path
import json
from uuid import uuid4
import hashlib
import stat
from zipfile import ZipFile

from material_templates import PRESETS
from native_files import plain_path, ZipLease, cancelled
from workspace_manager import copy_verified, _hash_stream
from managed_archive import segment


SCHEMA = '''
CREATE TABLE IF NOT EXISTS automatic_material_tasks(project_id INTEGER PRIMARY KEY REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS collected_materials(
 project_id INTEGER NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,
 version_id INTEGER NOT NULL REFERENCES material_versions(id),path TEXT NOT NULL,category TEXT NOT NULL,
 collected_at TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(project_id,kind));
CREATE TABLE IF NOT EXISTS collection_batch_folders(
 batch_id INTEGER NOT NULL REFERENCES batches(id),category TEXT NOT NULL,
 path TEXT NOT NULL UNIQUE COLLATE NOCASE,PRIMARY KEY(batch_id,category));
'''
ALL_KINDS = list(dict.fromkeys([*PRESETS['14件＋双视频']]))


def has_material_members(files, projects):
    from production import infer_kind
    return any(Path(file).suffix.casefold() == '.docx' or any(
        infer_kind(Path(file).name, ALL_KINDS, project['name']) for project in projects) for file in files)


def prepare(connection, production, file_row, kind=None):
    from production import infer_kind
    pid = file_row['project_id']
    task = connection.execute('SELECT * FROM production_tasks WHERE project_id=?', (pid,)).fetchone()
    if not task:
        return kind
    name = connection.execute('SELECT name FROM projects WHERE id=?', (pid,)).fetchone()[0]
    inferred = infer_kind(file_row['filename'], ALL_KINDS, name)
    current = production._task_kinds(connection, pid)
    automatic = connection.execute('SELECT 1 FROM automatic_material_tasks WHERE project_id=?', (pid,)).fetchone()
    # Revisions of an already known role keep that task's own requirement
    # snapshot, even when the reusable classification has since been edited.
    if current and automatic:
        known=kind or production.match_kind(connection,pid,file_row['filename'])
        if known:return known
    if not current or automatic:
        if not inferred and Path(file_row['filename']).suffix.casefold() == '.docx':
            inferred = '软件说明书.docx'
        if inferred:
            seen = {r[0] for r in connection.execute('SELECT kind FROM material_versions WHERE project_id=?', (pid,))}
            seen.add(inferred)
            if seen - set(PRESETS['五件材料']):
                package = '14件＋双视频'
            elif seen - {'软件说明书.docx'}:
                package = '五件材料'
            else:
                package = '单手册 Word'
            profile=connection.execute('SELECT kinds FROM material_profiles WHERE name=? AND active=1',(package,)).fetchone()
            required=json.loads(profile['kinds']) if profile else []
            if not profile or not seen.issubset(required):
                package='自定义';required=list(dict.fromkeys([*current,*sorted(seen)]))
            production._set_requirements(connection, pid, required, package, task['category'], '按已匹配材料自动识别本次类型')
            connection.execute('INSERT OR IGNORE INTO automatic_material_tasks VALUES (?)', (pid,))
            return kind or inferred
    return kind


def category_for(package):
    if package == '单手册 Word':
        return '单Word'
    if package == '五件材料':
        return '五件材料'
    if package == '桌面14件':
        return '14件材料'
    if package == '14件＋双视频':
        return '14件＋视频'
    return '其他材料'


def publish(connection, production, pid, cancel_event=None, progress=None, *, repair_kind=None):
    row = connection.execute('''SELECT p.*,b.name AS batch_name,t.package_name FROM projects p
        JOIN batches b ON b.id=p.batch_id JOIN production_tasks t ON t.project_id=p.id WHERE p.id=?''', (pid,)).fetchone()
    if not row:
        return
    root = plain_path(Path(production.service.workspaces.settings()['work_root']))
    from intake_storage import get as storage_settings
    storage=storage_settings(connection,pid)
    if storage:
        category='独立收纳';batch_dir=None;single_word=False;directory=plain_path(storage['directory'])
    else:
        category = category_for(row['package_name']) if row['package_name'] in PRESETS else segment(row['package_name'],maximum=40) if row['package_name'] not in ('自定义','待确认','未启用清单') else '其他材料'
        folder = connection.execute('SELECT path FROM collection_batch_folders WHERE batch_id=? AND category=?', (row['batch_id'], category)).fetchone()
        if folder and plain_path(folder['path']).is_relative_to(root / category):
            batch_dir = plain_path(folder['path'])
        else:
            names = connection.execute('SELECT name FROM projects WHERE batch_id=? ORDER BY id', (row['batch_id'],)).fetchall()
            base = root / category / (segment(names[0]['name'], maximum=70) + f'-{len(names)}件')
            batch_dir, index = base, 1
            while batch_dir.exists() or connection.execute('SELECT 1 FROM collection_batch_folders WHERE path=?', (str(batch_dir),)).fetchone():
                index += 1
                batch_dir = base.with_name(base.name + f'（{index}）')
            connection.execute('INSERT INTO collection_batch_folders VALUES (?,?,?) ON CONFLICT(batch_id,category) DO UPDATE SET path=excluded.path',
                               (row['batch_id'], category, str(batch_dir)))
        single_word = category == '单Word' and production._task_kinds(connection,pid) == ['软件说明书.docx']
        previous=connection.execute('SELECT path FROM collected_materials WHERE project_id=? LIMIT 1',(pid,)).fetchone()
        from intake_storage import available_directory
        directory=plain_path(previous[0]).parent if previous else batch_dir if single_word else available_directory(connection,pid,batch_dir/segment(row['name']))
    directory.mkdir(parents=True, exist_ok=True)
    for kind in production._task_kinds(connection, pid):
        version = production._current(connection, pid, kind)
        if not version['id']:
            continue
        cancelled(cancel_event)
        old = connection.execute('SELECT * FROM collected_materials WHERE project_id=? AND kind=?', (pid, kind)).fetchone()
        filename = segment(row['name'], maximum=120) + '.docx' if single_word else segment(row['name'], maximum=120) + '_' + segment(kind, maximum=110)
        actual_suffix=Path(version['path']).suffix
        if storage:
            name=storage['filenames'].get(kind)
            if not name and old:
                # Receiving one material must not rename unrelated existing copies.
                target=plain_path(old['path']);filename=target.name
            else:
                from intake_storage import basename
                filename=(name or basename(row['name'],kind,1))+actual_suffix
        else:
            filename=str(Path(filename).with_suffix(actual_suffix))

        if not Path(filename).suffix:
            filename += Path(version['path']).suffix
        target = plain_path(directory / filename) if not (storage and not storage['filenames'].get(kind) and old) else plain_path(old['path'])
        # Distinguish same normalized filename among separate tasks, without overwriting.
        occupied = connection.execute('SELECT project_id FROM collected_materials WHERE path=? COLLATE NOCASE AND (project_id!=? OR kind!=?)', (str(target), pid,kind)).fetchone()
        if occupied:
            from intake_storage import task_time
            suffix='_'+segment(Path(kind).stem,maximum=60) if occupied['project_id']==pid else '_'+task_time(connection,pid)
            target = target.with_name(target.stem + suffix + target.suffix)
        if old and old['version_id']==version['id'] and old['path']==str(target) and not target.exists() and repair_kind is not None and kind!=repair_kind:
            continue
        if old and old['version_id'] == version['id'] and old['path'] == str(target) and target.is_file():
            with ZipLease(target, movable=False) as lease:
                intact = _hash_stream(lease.stream) == (version['sha256'], version['size'])
            if intact or kind != repair_kind:
                # Keep unrelated modified files visible as an issue; receiving
                # another kind never silently repairs or overwrites their bytes.
                continue
        if target.exists():
            # A checked/edited collection copy can itself be dropped back into BOX.
            # Reuse it when it already contains this version, without removing its source handle.
            with ZipLease(target, movable=False) as lease:
                same = _hash_stream(lease.stream) == (version['sha256'], version['size'])
            if not same:
                target = target.with_name(target.stem + '_' + version['sha256'][:8] + target.suffix)
        if target.exists():
            with ZipLease(target, movable=False) as lease:
                if _hash_stream(lease.stream) != (version['sha256'], version['size']):
                    target = target.with_name(target.stem + '_' + uuid4().hex[:8] + target.suffix)
        if not target.exists():
            copied = copy_verified(version['path'], target, cancel_event=cancel_event, progress=progress)
            if copied['sha256'] != version['sha256']:
                raise ValueError('分类副本核对失败。')
        if old and old['path'] != str(target):
            previous = plain_path(old['path'])
            if (previous.is_relative_to(root) or storage) and previous.is_file():
                history = plain_path(production.db.path.parent / '材料历史' / f'WB{pid:06d}' / uuid4().hex / previous.name)
                history.parent.mkdir(parents=True, exist_ok=True)
                # Retain a verified history copy before removing only this managed export.
                saved = copy_verified(previous, history)
                # Queue only inside the same transaction as the new publication pointer.
                # Files are retired after commit; rollback leaves the old path usable.
                connection.execute('INSERT INTO collection_retirements(project_id,path,root,history_path,sha256,size) VALUES (?,?,?,?,?,?)',
                    (pid,str(previous),str(previous.parent if storage else root),str(history),saved['sha256'],saved['size']))
        received_at = old['collected_at'] if old and old['version_id'] == version['id'] else connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
        connection.execute('''INSERT INTO collected_materials(project_id,kind,version_id,path,category,collected_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_id,kind) DO UPDATE SET version_id=excluded.version_id,path=excluded.path,category=excluded.category,collected_at=excluded.collected_at''',
            (pid, kind, version['id'], str(target), category, received_at))


def unpack_material_zip(service, path, *, cancel_event=None):
    """Read regular ZIP entries into a bounded staging area; never execute any entry."""
    path = plain_path(path, allow_network=True)
    if path.suffix.casefold()=='.7z':
        from archive_support import unpack_7z
        return unpack_7z(service,path,cancel_event=cancel_event)
    with ZipLease(path, movable=False) as lease:
        digest, _ = _hash_stream(lease.stream, cancel_event=cancel_event)
        with ZipFile(lease.stream) as archive:
            members = archive.infolist()
            if len(members) > 5000 or sum(i.file_size for i in members) > 1024**3:
                raise ValueError('材料包过大，请分批收录。')
            seen = set()
            for item in members:
                parts = item.filename.replace('\\', '/').split('/')
                mode = item.external_attr >> 16
                if (any(p in ('.', '..') or ':' in p for p in parts) or item.filename.startswith(('/', '\\'))
                        or stat.S_ISLNK(mode) or item.flag_bits & 1 or item.file_size > 256*1024**2):
                    raise ValueError('材料包含危险路径、特殊文件或不支持的成员。')
                key = item.filename.replace('\\', '/').casefold()
                if key in seen:
                    raise ValueError('材料包内存在重复文件名。')
                seen.add(key)
            from storage_management import staging_root,hold
            root = plain_path(staging_root(service) / digest[:24]);hold(root)
            files = []
            for item in members:
                cancelled(cancel_event)
                if item.is_dir():
                    continue
                target = plain_path(root.joinpath(*item.filename.replace('\\', '/').split('/')))
                if not target.is_relative_to(root):
                    raise ValueError('材料超出收件目录。')
                target.parent.mkdir(parents=True, exist_ok=True)
                # Read verifies CRC; publish complete members atomically.
                content = archive.read(item)
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(content).digest():
                        raise ValueError('既有材料收件缓存已变化。')
                else:
                    temporary = target.with_name('.' + uuid4().hex + '.part')
                    temporary.write_bytes(content)
                    temporary.replace(target)
                files.append(target)
    return files
