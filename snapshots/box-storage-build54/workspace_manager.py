"""本地项目目录与保留原件的文件收录。"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import stat
import struct
import uuid
from collections import OrderedDict
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from native_files import ZipLease, cancelled, identity, plain_path, _windows_path
from intake_log import FileIntakeLog
from first_run import initialize as initialize_first_run, setup_required as read_setup_required, complete as complete_first_run
from workflow_policy import DEFAULTS as POLICY_DEFAULTS, read_policy, validate as validate_policy


SCHEMA = '''
CREATE TABLE IF NOT EXISTS workflow_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_archive_roots (
    path TEXT PRIMARY KEY COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS project_workspaces (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id),
    path TEXT NOT NULL COLLATE NOCASE UNIQUE,
    group_path TEXT NOT NULL, batch_id INTEGER NOT NULL REFERENCES batches(id)
);
CREATE TABLE IF NOT EXISTS work_files (
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
    source_path TEXT NOT NULL, path TEXT NOT NULL COLLATE NOCASE,
    filename TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(project_id,path,sha256)
);
CREATE INDEX IF NOT EXISTS idx_work_files_project_hash ON work_files(project_id,sha256);
'''


def _path(value, *, allow_network=False):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('请选择本机有效路径。')
    # 在转为绝对路径前检查，避免把网络或设备路径折叠为本机路径。
    if not allow_network and str(value).startswith(('\\\\', '//')):
        raise ValueError('不接受网络路径或设备路径。')
    value = os.path.expandvars(str(value).strip())
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    path = plain_path(value, allow_network=allow_network)
    if os.name == 'nt' and not allow_network:
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        api.GetDriveTypeW.restype = ctypes.c_uint
        if api.GetDriveTypeW(path.anchor) == 4:
            raise ValueError('不接受映射的网络磁盘，请选择本机目录。')
    return path


def _hash_stream(stream, *, cancel_event=None, progress=None):
    before = os.fstat(stream.fileno())
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('请选择本机普通文件。')
    stream.seek(0)
    digest, size = hashlib.sha256(), 0
    while True:
        cancelled(cancel_event)
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        size += len(block)
        if progress:
            progress(size, before.st_size)
    cancelled(cancel_event)
    if size != before.st_size or identity(before) != identity(os.fstat(stream.fileno())):
        raise RuntimeError('读取期间文件发生变化，未完成收录。')
    return digest.hexdigest(), size


def _zip_structure(stream, cancel_event=None):
    """只核对中央目录和本地成员结构，不解压或声称通过 CRC 校验。"""
    try:
        stream.seek(0)
        if stream.read(4) not in (b'PK\x03\x04', b'PK\x05\x06'):
            raise ValueError('没有可识别的 ZIP 文件头。')
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            ranges = []
            for entry in entries:
                cancelled(cancel_event)
                offset = entry.header_offset
                if offset < 0 or offset + 30 > archive.start_dir:
                    raise ValueError('ZIP 成员偏移无效。')
                stream.seek(offset)
                header = stream.read(30)
                if len(header) != 30 or header[:4] != b'PK\x03\x04':
                    raise ValueError('ZIP 成员头不完整。')
                name_size, extra_size = struct.unpack_from('<HH', header, 26)
                local_name = stream.read(name_size)
                flags, method = struct.unpack_from('<HH', header, 6)
                try:
                    decoded_name = local_name.decode('utf-8' if flags & 0x800 else 'cp437')
                except UnicodeDecodeError as error:
                    raise ValueError('ZIP 成员文件名编码无效。') from error
                if decoded_name != entry.orig_filename or method != entry.compress_type:
                    raise ValueError('ZIP 本地成员与中央目录不一致。')
                end = offset + 30 + name_size + extra_size + entry.compress_size
                if end > archive.start_dir:
                    raise ValueError('ZIP 成员数据超出中央目录边界。')
                ranges.append((offset, end))
            ranges.sort()
            if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
                raise ValueError('ZIP 成员数据存在重叠。')
    except (BadZipFile, EOFError, struct.error) as error:
        raise ValueError('ZIP 中央目录或成员结构不完整。') from error
    finally:
        stream.seek(0)


def copy_verified(source, target, *, cancel_event=None, progress=None, require_zip=False, require_archive=False):
    """稳定读取原件，校验副本，再以无覆盖的原子改名发布。原件始终保留。"""
    source, target = _path(source, allow_network=True), _path(target)
    # Keep ordinary validated paths in records; extend only filesystem calls so
    # deeply nested material/history folders work without a system-wide setting.
    target_io = Path(_windows_path(target)) if os.name == 'nt' else target
    cancelled(cancel_event)
    source_io = Path(_windows_path(source)) if os.name == 'nt' else source
    if not source_io.is_file():
        raise ValueError('请选择本机普通文件，不能收录目录。')
    if source == target or target_io.exists():
        raise FileExistsError('目标已存在，不覆盖已有文件。')
    target_io.parent.mkdir(parents=True, exist_ok=True)
    _path(target.parent)
    temporary = target.parent / ('.' + uuid.uuid4().hex + '.partial')
    temporary_io = Path(_windows_path(temporary)) if os.name == 'nt' else temporary
    try:
        with ZipLease(source, movable=False) as lease:
            before = os.fstat(lease.stream.fileno())
            if require_zip:
                _zip_structure(lease.stream, cancel_event)
            if require_archive:
                from archive_support import validate_stream
                validate_stream(lease.stream,source,cancel_event)
            streams = lease.named_streams()
            digest, size = hashlib.sha256(), 0
            lease.stream.seek(0)
            with temporary_io.open('xb') as output:
                while True:
                    cancelled(cancel_event)
                    block = lease.stream.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                    if progress:
                        progress(size, before.st_size)
                output.flush()
                os.fsync(output.fileno())
            cancelled(cancel_event)
            if size != before.st_size or identity(before) != identity(os.fstat(lease.stream.fileno())):
                raise RuntimeError('读取期间文件发生变化，副本未发布。')
            for name, metadata in streams.items():
                with open(str(temporary_io) + ':' + name, 'xb') as stream:
                    stream.write(metadata)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.utime(temporary_io, ns=(before.st_atime_ns, before.st_mtime_ns))
            with ZipLease(temporary, movable=False) as copied:
                copied_digest, copied_size = _hash_stream(copied.stream, cancel_event=cancel_event)
                if (copied_digest, copied_size) != (digest.hexdigest(), size):
                    raise RuntimeError('副本校验不一致，未完成收录。')
                if copied.named_streams() != streams:
                    raise RuntimeError('文件附加信息未能完整保留，副本未发布。')
            cancelled(cancel_event)
            _path(target)
            # Windows 的 rename 不会替换已有目标；同目录发布保证原子性。
            os.rename(temporary_io, target_io)
            return dict(path=str(target), source_path=str(source), filename=source.name,
                        size=size, sha256=digest.hexdigest(), mtime_ns=target_io.stat().st_mtime_ns,
                        identity=identity(target_io.stat()))
    finally:
        if temporary_io.exists():
            temporary_io.unlink()


def _folder_name(name):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip().rstrip('. ')
    if not value:
        value = '未命名项目'
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    return value[:160].rstrip('. ')


class WorkspaceManager:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as connection:
            initialize_first_run(connection)
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)
        self.intake_log = FileIntakeLog(self.db)
        # 默认归档可能已在首次配置前使用；升级时也保留已保存的当前目录。
        roots = {str(self.db.path.parent / 'ZIP归档'), self.settings()['archive_root']}
        with self.db.connect() as connection:
            connection.executemany('INSERT OR IGNORE INTO workflow_archive_roots(path) VALUES (?)',
                                   [(str(Path(os.path.abspath(root))),) for root in sorted(roots)])

    def setup_required(self):
        with self.db.connect() as connection:
            return read_setup_required(connection)

    def settings(self):
        defaults = dict(work_root=str(self.db.path.parent / '工作项目'),
                        archive_root=str(self.db.path.parent / 'ZIP归档'), stable_seconds=10,
                        **POLICY_DEFAULTS)
        with self.db.connect() as connection:
            if read_setup_required(connection):
                # Fresh installations must suggest a directory that the save
                # validator accepts. Stored preferences always override this.
                data_folder=self.db.path.parent
                if data_folder.name.casefold()=='data':defaults['work_root']=str(data_folder.parent/'材料库')
                elif data_folder.name:defaults['work_root']=str(data_folder.with_name(data_folder.name+'-材料库'))
            for row in connection.execute('SELECT key,value FROM workflow_settings'):
                if row['key'] in defaults:
                    defaults[row['key']] = json.loads(row['value'])
        return defaults

    def archive_roots(self):
        """供自动扫描持续排除当前和历史归档目录，不迁移或删除历史内容。"""
        with self.db.connect() as connection:
            return [row['path'] for row in connection.execute('SELECT path FROM workflow_archive_roots ORDER BY rowid')]

    @staticmethod
    def _overlap(left, right):
        return left == right or left.is_relative_to(right) or right.is_relative_to(left)

    def configure(self, work_root, archive_root, stable_seconds=10, *, auto_create_projects=None,
                  auto_deliver_zip=None, zip_delivery_min_files=None, complete_setup=False, _create_archive=True):
        if type(complete_setup) is not bool:
            raise ValueError('首次设置完成标记必须是布尔值。')
        policy_changes = dict(auto_create_projects=auto_create_projects, auto_deliver_zip=auto_deliver_zip,
                              zip_delivery_min_files=zip_delivery_min_files)
        validate_policy({key: POLICY_DEFAULTS[key] if value is None else value
                         for key, value in policy_changes.items()})
        previous_work=self.settings()['work_root']
        work, archive = _path(work_root), _path(archive_root)
        if work == Path(work.anchor) or archive == Path(archive.anchor):
            raise ValueError('请选择专用文件夹，不能直接使用磁盘根目录。')
        if complete_setup:
            self._validate_material_root(work)
        if any(self._overlap(work,self.db.path.parent/name) for name in ('工作缓存','材料历史','材料目录整理')):
            raise ValueError('材料保存目录不能与 BOX 的内部工作缓存、版本历史或整理备份目录重叠。')
        if self._overlap(work, archive):
            raise ValueError('工作目录与 ZIP 归档目录必须独立，不能相同或互相包含。')
        if type(stable_seconds) is not int or not 5 <= stable_seconds <= 300:
            raise ValueError('稳定等待时间应为 5 至 300 秒。')
        settings = dict(work_root=str(work), archive_root=str(archive), stable_seconds=stable_seconds)
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            read_setup_required(connection)
            policy = read_policy(connection)
            policy.update({key: value for key, value in policy_changes.items() if value is not None})
            settings.update(policy)
            history = [Path(row['path']) for row in connection.execute('SELECT path FROM workflow_archive_roots')]
            if any(self._overlap(work, root) for root in history):
                raise ValueError('工作目录不能与当前或历史 ZIP 归档目录相同或互相包含。')
            for row in connection.execute('SELECT path,group_path FROM project_workspaces'):
                if work == Path(row['path']) or work.is_relative_to(Path(row['path'])):
                    raise ValueError('工作根目录不能等于或位于已有项目工作目录内部。')
                if any(self._overlap(archive, Path(row[key])) for key in ('path', 'group_path')):
                    raise ValueError('ZIP 归档目录不能与已登记的项目工作目录或分组目录相同或互相包含。')
            for root in ((work, archive) if _create_archive else (work,)):
                root.mkdir(parents=True, exist_ok=True)
                _path(root)
            connection.execute('INSERT OR IGNORE INTO workflow_archive_roots(path) VALUES (?)', (str(archive),))
            connection.executemany('INSERT INTO workflow_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                   [(key, json.dumps(value, ensure_ascii=False)) for key, value in settings.items()])
            from storage_management import remember_roots
            remember_roots(connection,previous_work,work)
            if complete_setup:
                complete_first_run(connection)
        return settings

    def _validate_material_root(self, work):
        if work == Path(work.anchor):
            raise ValueError('请选择专用文件夹，不能直接使用磁盘根目录。')
        from storage_management import INTERNAL,LEGACY_INTERNAL
        if any(p.casefold() in (INTERNAL.casefold(),LEGACY_INTERNAL.casefold()) for p in work.parts):raise ValueError('请选择材料保存主目录，不能使用 BOX 内部自动管理的工作文件夹作为材料主目录。')
        protected = [self.db.path.parent / name for name in ('工作缓存', '材料历史', '材料目录整理')]
        if any(work == root or work.is_relative_to(root) for root in protected):
            raise ValueError('材料保存目录不能与 BOX 的内部工作缓存、版本历史或整理备份目录重叠。')
        if self._overlap(work, self.db.path.parent):
            raise ValueError('材料保存目录不能与 BOX 数据目录相同或互相包含。')
        import sys
        if getattr(sys, 'frozen', False) and self._overlap(work, Path(sys.executable).resolve().parent):
            raise ValueError('材料保存目录不能与 BOX 程序目录相同或互相包含。')

    def configure_materials(self, work_root, stable_seconds=None, **policy):
        """The current workflow asks for one root; keep legacy archive references intact."""
        self._validate_material_root(_path(work_root))
        current = self.settings()
        return self.configure(work_root, current['archive_root'],
                              current['stable_seconds'] if stable_seconds is None else stable_seconds,
                              _create_archive=False, **policy)

    def get_workspace(self, project_id):
        with self.db.connect() as connection:
            row = connection.execute('SELECT w.*,p.name FROM project_workspaces w JOIN projects p ON p.id=w.project_id WHERE w.project_id=?', (project_id,)).fetchone()
            return dict(row) if row else None

    def list_workspaces(self, include_archived=False):
        with self.db.connect() as connection:
            sql = 'SELECT w.*,p.name FROM project_workspaces w JOIN projects p ON p.id=w.project_id LEFT JOIN project_details d ON d.project_id=p.id'
            if not include_archived:
                sql += ' WHERE d.archived_at IS NULL'
            return [dict(row) for row in connection.execute(sql + ' ORDER BY p.id')]

    @staticmethod
    def _allocate(parent, name, created):
        parent = _path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        base, suffix = _folder_name(name), 1
        while True:
            candidate = _path(parent / (base if suffix == 1 else f'{base} ({suffix})'))
            if not candidate.is_relative_to(parent):
                raise ValueError('目录超出指定工作目录。')
            try:
                candidate.mkdir()
                created.append(candidate)
                return candidate
            except FileExistsError:
                suffix += 1

    def ensure_projects(self, project_ids):
        if not project_ids or len(project_ids) > 500 or any(type(pid) is not int or pid < 1 for pid in project_ids):
            raise ValueError('请选择 1 至 500 个有效项目。')
        ids = list(dict.fromkeys(project_ids))
        groups, result, created = OrderedDict(), {}, []
        try:
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                for pid in ids:
                    row = connection.execute('SELECT id,name,batch_id FROM projects WHERE id=?', (pid,)).fetchone()
                    if row is None:
                        raise KeyError('项目不存在。')
                    managed=connection.execute("SELECT 1 FROM sqlite_master WHERE name='production_tasks'").fetchone() and connection.execute('SELECT 1 FROM production_tasks WHERE project_id=?',(pid,)).fetchone()
                    groups.setdefault((row['batch_id'],bool(managed)), []).append(dict(row))
                for (batch_id,managed), rows in groups.items():
                    from storage_management import work_root as internal_work_root
                    root = _path(internal_work_root(self.service) if managed else Path(self.settings()['work_root'])/'旧版材料')
                    existing = connection.execute('SELECT * FROM project_workspaces WHERE batch_id=? ORDER BY project_id LIMIT 1', (batch_id,)).fetchone()
                    if existing and not _path(existing['group_path']).is_relative_to(root):existing=None
                    group = _path(existing['group_path']) if existing else None
                    multiple = len(rows) > 1
                    for row in rows:
                        registered = connection.execute('SELECT project_id,path,group_path FROM project_workspaces WHERE project_id=?', (row['id'],)).fetchone()
                        if registered:
                            _path(registered['path'])
                            result[row['id']] = dict(registered)
                            continue
                        if multiple and group is None:
                            group = self._allocate(root, rows[0]['name'], created)
                        path = self._allocate(group if multiple or existing else root, row['name'], created)
                        if group is None:
                            group = path
                        connection.execute('INSERT INTO project_workspaces(project_id,path,group_path,batch_id) VALUES (?,?,?,?)',
                                           (row['id'], str(path), str(group), batch_id))
                        result[row['id']] = dict(project_id=row['id'], path=str(path), group_path=str(group))
            return [result[pid] for pid in ids]
        except BaseException:
            for path in reversed(created):
                try:
                    path.rmdir()
                except (OSError, ValueError):
                    pass
            raise

    def import_file(self,source,project_id,**kwargs):
        try:return self._import_file(source,project_id,**kwargs)
        finally:
            if hasattr(self.service,'production'):
                from collection_retirement import finalize
                try:finalize(self.service.production)
                except Exception as error:self.service.production._retirement_error=str(error)

    def _import_file(self, source, project_id, *, cancel_event=None, progress=None, routing=None, relative_path=None):
        if type(project_id) is not int or project_id < 1:
            raise ValueError('请先明确选择文件所属项目。')
        source = _path(source, allow_network=True)
        if not source.is_file():
            raise ValueError('请选择本机普通文件，不能收录目录。')
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
        workspace = self.get_workspace(project_id) or self.ensure_projects([project_id])[0]
        directory = _path(workspace['path'])
        directory.mkdir(parents=True, exist_ok=True)
        relative = Path(relative_path) if relative_path is not None else None
        if relative is not None:
            if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                    or relative.name != source.name or any(':' in part for part in relative.parts)):
                raise ValueError('收录相对路径无效，不能越出项目工作目录或改写文件名。')
            parent = _path(directory / relative.parent)
            if not parent.is_relative_to(directory):
                raise ValueError('收录相对路径超出项目工作目录。')
            parent.mkdir(parents=True, exist_ok=True)
            _path(parent)
        else:
            parent = directory
        own_copy = None
        try:
            with ZipLease(source, movable=False) as lease:
                digest, size = _hash_stream(lease.stream, cancel_event=cancel_event)
                with self.db.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    self.db._assert_projects_editable(connection, project_id)
                    project_name = connection.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()[0]
                    def record(file_row, status):
                        decision = routing or {}
                        self.intake_log.append(source, status, project_id=project_id, project_name=project_name,
                            target_path=file_row['path'], sha256=digest, work_file_id=file_row['id'],
                            basis=decision.get('basis', '调用方指定项目'),
                            candidate_ids=decision.get('candidate_ids', ()), manual=decision.get('manual'),
                            connection=connection)
                        if hasattr(self.service, 'production'):
                            version_id=self.service.production.record_work_file(connection, file_row,
                                kind=decision.get('material_kind'), cancel_event=cancel_event, progress=progress)
                            token=decision.get('import_token')
                            if token and version_id:
                                attempt=connection.execute('SELECT * FROM production_import_files WHERE token=? AND project_id=? AND source_path=?',
                                    (token,project_id,decision['import_source_path'])).fetchone()
                                version=connection.execute('SELECT * FROM material_versions WHERE id=?',(version_id,)).fetchone()
                                if not attempt or attempt['source_sha256']!=version['sha256']:raise ValueError('来源与已确认收录记录不一致，未提交本次材料。')
                                connection.execute("""UPDATE production_import_files SET state='complete',error='',saved_path=?,saved_sha256=?,saved_version_id=?,
                                    saved_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE token=? AND project_id=? AND source_path=?""",
                                    (version['path'],version['sha256'],version_id,token,project_id,decision['import_source_path']))
                    for row in connection.execute('SELECT * FROM work_files WHERE project_id=? AND sha256=? AND size=? ORDER BY id', (project_id, digest, size)):
                        saved = _path(row['path'])
                        if Path(row['source_path']).name != source.name:
                            continue
                        if relative is not None and saved.parent != parent:
                            continue
                        if not saved.is_file():
                            continue
                        with ZipLease(saved, movable=False) as saved_lease:
                            if _hash_stream(saved_lease.stream, cancel_event=cancel_event) == (digest, size):
                                record(row, 'duplicate')
                                self.db._mark_work_file_pending(connection, project_id, row['id'], duplicate=True)
                                return dict(file=dict(row), duplicate=True, source_preserved=True)
                    if source.is_relative_to(directory):
                        metadata = dict(path=str(source), source_path=str(source), filename=source.name, size=size,
                                        sha256=digest, mtime_ns=source.stat().st_mtime_ns)
                    else:
                        target = parent / source.name
                        if target.exists():
                            target = parent / f'{source.stem} ({digest[:8]}){source.suffix}'
                        index = 2
                        while target.exists():
                            target = parent / f'{source.stem} ({digest[:8]}) ({index}){source.suffix}'
                            index += 1
                        metadata = copy_verified(source, target, cancel_event=cancel_event, progress=progress)
                        own_copy = metadata
                    cancelled(cancel_event)
                    file_id = connection.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?,?)',
                                                 (project_id, metadata['source_path'], metadata['path'], metadata['filename'], metadata['size'], metadata['sha256'], metadata['mtime_ns'])).lastrowid
                    file_row = dict(connection.execute('SELECT * FROM work_files WHERE id=?', (file_id,)).fetchone())
                    record(file_row, 'copied')
                    self.db._mark_work_file_pending(connection, project_id, file_id)
                return dict(file=file_row, duplicate=False, source_preserved=True)
        except BaseException:
            if own_copy is not None:
                try:
                    with ZipLease(own_copy['path'], movable=True) as copied:
                        if identity(copied.initial) == own_copy['identity']:
                            copied.remove_after_verified_commit()
                except (OSError, ValueError):
                    # 文件已经被外部替换或占用时保留现场，不删除不属于本次操作的文件。
                    pass
            raise
