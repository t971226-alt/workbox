"""Batch material checklist. Review and submission always refer to a saved version."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from native_files import ZipLease, cancelled, plain_path
from workspace_manager import _hash_stream, copy_verified
from material_templates import dependencies, package_for


SCHEMA = '''
CREATE TABLE IF NOT EXISTS production_commissions (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS production_batches (
 batch_id INTEGER PRIMARY KEY REFERENCES batches(id), commission_id INTEGER REFERENCES production_commissions(id),
 label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_tasks (
 project_id INTEGER PRIMARY KEY REFERENCES projects(id), package_name TEXT NOT NULL, category TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS material_requirements (
 project_id INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, position INTEGER NOT NULL,
 PRIMARY KEY(project_id,kind)
);
CREATE TABLE IF NOT EXISTS requirement_changes (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS production_imports (
 token TEXT PRIMARY KEY, source_text TEXT NOT NULL, result_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS production_import_files (
 token TEXT NOT NULL REFERENCES production_imports(token), project_id INTEGER NOT NULL REFERENCES projects(id),
 source_path TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(token,project_id,source_path)
);
CREATE TABLE IF NOT EXISTS material_version_inputs (
 version_id INTEGER NOT NULL REFERENCES material_versions(id), kind TEXT NOT NULL,
 source_version_id INTEGER REFERENCES material_versions(id), PRIMARY KEY(version_id,kind)
);
CREATE TABLE IF NOT EXISTS original_zip_receipts (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), path TEXT NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), UNIQUE(project_id,path,sha256)
);
CREATE TABLE IF NOT EXISTS production_kinds (
 batch_id INTEGER NOT NULL REFERENCES batches(id), kind TEXT NOT NULL,
 position INTEGER NOT NULL, PRIMARY KEY(batch_id,kind)
);
CREATE TABLE IF NOT EXISTS material_versions (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL, work_file_id INTEGER REFERENCES work_files(id),
 path TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_material_current ON material_versions(project_id,kind,id DESC);
CREATE TABLE IF NOT EXISTS material_reviews (
 id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES material_versions(id),
 passed INTEGER NOT NULL CHECK(passed IN (0,1)), note TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS material_submissions (
 id INTEGER PRIMARY KEY, note TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS material_submission_items (
 submission_id INTEGER NOT NULL REFERENCES material_submissions(id),
 version_id INTEGER NOT NULL REFERENCES material_versions(id),
 PRIMARY KEY(submission_id,version_id)
);
CREATE TABLE IF NOT EXISTS material_submission_voids (
 submission_id INTEGER NOT NULL REFERENCES material_submissions(id),
 version_id INTEGER NOT NULL REFERENCES material_versions(id),
 note TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(submission_id,version_id)
);
'''


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def infer_kind(filename, kinds, project_name=None):
    from material_naming import candidates
    matches=candidates(filename,kinds,project_name=project_name)
    return matches[0][1] if len({kind for _,kind in matches})==1 else None




def source_digest(path):
    import hashlib
    try:
        with open(path,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
    except OSError:return ''


class Production:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)
            from material_collection import SCHEMA as COLLECTION_SCHEMA
            connection.executescript(COLLECTION_SCHEMA)
            from intake_history import SCHEMA as HISTORY_SCHEMA
            connection.executescript(HISTORY_SCHEMA)
            from collection_retirement import SCHEMA as RETIRE_SCHEMA
            connection.executescript(RETIRE_SCHEMA)
            from task_file_deletion import SCHEMA as DELETE_SCHEMA
            connection.executescript(DELETE_SCHEMA)
            from intake_storage import SCHEMA as STORAGE_SCHEMA
            connection.executescript(STORAGE_SCHEMA)
            # Migrate the previous batch-wide model only for tasks without their own snapshot.
            connection.execute('BEGIN IMMEDIATE')
            if 'collected_at' not in {r['name'] for r in connection.execute('PRAGMA table_info(collected_materials)')}:
                connection.execute("ALTER TABLE collected_materials ADD COLUMN collected_at TEXT NOT NULL DEFAULT ''")
            columns={r['name'] for r in connection.execute('PRAGMA table_info(production_import_files)')}
            for name,definition in {'source_sha256':"TEXT NOT NULL DEFAULT ''",'saved_path':"TEXT NOT NULL DEFAULT ''",'saved_sha256':"TEXT NOT NULL DEFAULT ''",'saved_version_id':'INTEGER','expected_version_id':'INTEGER NOT NULL DEFAULT -1','saved_at':"TEXT NOT NULL DEFAULT ''"}.items():
                if name not in columns:connection.execute(f'ALTER TABLE production_import_files ADD COLUMN {name} {definition}')
            connection.execute("UPDATE collected_materials SET collected_at=(SELECT created_at FROM material_versions WHERE id=version_id) WHERE collected_at=''")
            ids = [r[0] for r in connection.execute('''SELECT p.id FROM projects p
                WHERE EXISTS(SELECT 1 FROM production_kinds k WHERE k.batch_id=p.batch_id)
                AND NOT EXISTS(SELECT 1 FROM production_tasks t WHERE t.project_id=p.id)''')]
            for pid in ids:
                bid = connection.execute('SELECT batch_id FROM projects WHERE id=?', (pid,)).fetchone()[0]
                self._set_requirements(connection, pid, self._kinds(connection, bid), '自定义', '', '旧批次清单迁移')

        from version_overwrite import SCHEMA as DISCARD_SCHEMA, finalize as discard
        with self.db.connect() as c:c.executescript(DISCARD_SCHEMA)
        discard(self)
        from collection_retirement import finalize
        finalize(self)

    @staticmethod
    def _kinds(connection, batch_id):
        return [r[0] for r in connection.execute(
            'SELECT kind FROM production_kinds WHERE batch_id=? ORDER BY position', (batch_id,))]

    def kinds(self, batch_id):
        with self.db.connect() as connection:
            default = self._kinds(connection, batch_id)
            if default:
                return default
            return list(dict.fromkeys(r[0] for r in connection.execute('''SELECT k.kind FROM material_requirements k
                JOIN projects p ON p.id=k.project_id WHERE p.batch_id=? ORDER BY p.id,k.position''', (batch_id,))))

    def managed(self, project_id):
        with self.db.connect() as connection:
            return bool(connection.execute('SELECT 1 FROM production_tasks WHERE project_id=?', (project_id,)).fetchone())

    def deleted_rows(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('''SELECT p.id,p.name,b.name AS batch_name,x.deleted_at,
                EXISTS(SELECT 1 FROM task_file_deletions f WHERE f.project_id=p.id) AS materials_deleted
                FROM project_deletions x JOIN projects p ON p.id=x.project_id
                JOIN production_tasks t ON t.project_id=p.id JOIN batches b ON b.id=p.batch_id
                ORDER BY x.deleted_at DESC,p.id DESC''')]

    def delete_tasks(self, project_ids, *, confirmed=False, delete_materials=False, cancel_event=None, progress=None):
        if confirmed is not True:raise ValueError('请先确认删除所选任务。')
        if delete_materials:
            from task_file_deletion import delete
            return delete(self,list(project_ids),cancel_event=cancel_event,progress=progress)
        return self._change_deleted_tasks(project_ids,True,cancel_event)

    def restore_tasks(self, project_ids, *, cancel_event=None, progress=None):
        return self._change_deleted_tasks(project_ids,False,cancel_event)

    def _change_deleted_tasks(self, project_ids, deleted, cancel_event, *, files_token=None,file_counts=None):
        from contextlib import nullcontext
        ids=list(project_ids)
        if not ids or any(type(pid) is not int or pid<=0 for pid in ids):raise ValueError('请选择有效的软件任务。')
        ids=list(dict.fromkeys(ids));changed=0
        with getattr(self.db,'_operation_lock',nullcontext()),self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for pid in ids:
                cancelled(cancel_event)
                row=c.execute('''SELECT p.id,d.archived_at,x.deleted_at,x.previous_archived_at
                    FROM projects p JOIN production_tasks t ON t.project_id=p.id
                    LEFT JOIN project_details d ON d.project_id=p.id
                    LEFT JOIN project_deletions x ON x.project_id=p.id WHERE p.id=?''',(pid,)).fetchone()
                if not row:raise ValueError('任务不存在，请刷新列表后重试。')
                if deleted and files_token:
                    c.execute('INSERT OR REPLACE INTO task_file_deletions(project_id,token,removed_count) VALUES (?,?,?)',(pid,files_token,file_counts.get(pid,0)))
                if deleted and row['deleted_at'] is None:
                    c.execute('INSERT INTO project_deletions(project_id,previous_archived_at) VALUES (?,?)',(pid,row['archived_at']))
                    c.execute('INSERT OR IGNORE INTO project_details(project_id) VALUES (?)',(pid,))
                    c.execute("UPDATE project_details SET archived_at=COALESCE(archived_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE project_id=?",(pid,));changed+=1
                elif not deleted and row['deleted_at'] is not None:
                    c.execute('UPDATE project_details SET archived_at=? WHERE project_id=?',(row['previous_archived_at'],pid))
                    c.execute('DELETE FROM project_deletions WHERE project_id=?',(pid,));changed+=1
            cancelled(cancel_event)
        return {('deleted_count' if deleted else 'restored_count'):changed}

    def _task_kinds(self, connection, project_id):
        return [r[0] for r in connection.execute(
            'SELECT kind FROM material_requirements WHERE project_id=? ORDER BY position', (project_id,))]

    def task_kinds(self, project_id):
        with self.db.connect() as connection:
            return self._task_kinds(connection, project_id)

    def _set_requirements(self, connection, pid, kinds, package, category, reason):
        before = dict(kinds=self._task_kinds(connection, pid))
        task = connection.execute('SELECT package_name,category FROM production_tasks WHERE project_id=?', (pid,)).fetchone()
        if task:
            before.update(dict(task))
        after = dict(kinds=list(kinds), package_name=package, category=category)
        connection.execute('INSERT INTO production_tasks VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET package_name=excluded.package_name,category=excluded.category',
                           (pid, package, category))
        self.service.classifications.snapshot(connection, pid, package, kinds)
        connection.execute('DELETE FROM material_requirements WHERE project_id=?', (pid,))
        connection.executemany('INSERT INTO material_requirements VALUES (?,?,?)', [(pid, k, i) for i, k in enumerate(kinds)])
        if before != after:
            connection.execute('INSERT INTO requirement_changes(project_id,before_json,after_json,reason) VALUES (?,?,?,?)',
                               (pid, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), reason))

    def configure_task(self, project_id, kinds, package=None, category='', reason=''):
        kinds = self._clean(kinds, '材料类型', 40) if kinds else []
        if not isinstance(category, str) or len(category) > 100 or not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('分类或变更原因过长。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.db._assert_projects_editable(connection, project_id)
            if not reason.strip():
                raise ValueError('请填写本次设置或调整材料要求的原因。')
            self._set_requirements(connection, project_id, kinds, package or package_for(kinds), category.strip(), reason.strip())
            connection.execute('DELETE FROM automatic_material_tasks WHERE project_id=?', (project_id,))
            self._sync(connection, project_id)
            from material_collection import publish
            publish(connection, self, project_id)

    def batches(self, commission=None, period=None):
        from datetime import datetime, timedelta, timezone
        with self.db.connect() as connection:
            rows = [dict(r) for r in connection.execute('''SELECT b.*,COALESCE(c.name,'') AS commission,
                COALESCE(pb.label,b.name) AS label FROM batches b LEFT JOIN production_batches pb ON pb.batch_id=b.id
                LEFT JOIN production_commissions c ON c.id=pb.commission_id ORDER BY b.id DESC''')]
        today = datetime.now(timezone(timedelta(hours=8))).date()
        def in_period(row):
            date = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).date()
            return (period is None or period == '今天' and date == today or period == '昨天' and date == today-timedelta(days=1)
                    or period == '最近7天' and today-timedelta(days=6) <= date <= today)
        return [r for r in rows if (commission is None or r['commission'] == commission) and in_period(r)]

    def file_candidates(self, filename, projects):
        stem = normalized(Path(filename).stem)
        matched = []
        with self.db.connect() as connection:
            for p in projects:
                kinds = self._task_kinds(connection, p['id'])
                if (not kinds or connection.execute('SELECT 1 FROM automatic_material_tasks WHERE project_id=?', (p['id'],)).fetchone()) and connection.execute('SELECT 1 FROM production_tasks WHERE project_id=?', (p['id'],)).fetchone():
                    from material_collection import ALL_KINDS
                    kinds = ALL_KINDS
                from material_naming import candidates
                from file_families import task_families,compatible
                snap=connection.execute('SELECT aliases FROM task_profile_snapshots WHERE project_id=?',(p['id'],)).fetchone()
                aliases=json.loads(snap[0]) if snap else {}
                from name_patterns import read,norm
                patterns=read(connection,'task_name_patterns','project_id',p['id'])
                if patterns:
                    from file_families import infer
                    base=norm(p['name']);value=norm(Path(filename).stem)
                    if (value==base or value.startswith(base+' ')) and infer(filename,kinds,task_families(connection,p['id']),aliases,p['name'],patterns):matched.append(p);continue
                    kinds=[k for k in kinds if k not in patterns]
                families=task_families(connection,p['id'])
                virtual=[Path(k).stem+Path(filename).suffix for k in kinds if compatible(k,filename,families)]
                adapted={Path(k).stem+Path(filename).suffix:aliases.get(k,[]) for k in kinds if compatible(k,filename,families)}
                if any(normalized(name)==normalized(p['name']) for name,kind in candidates(filename,virtual,adapted,p['name'])):
                    matched.append(p)
        return matched

    @staticmethod
    def _clean(values, label, maximum):
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= maximum:
            raise ValueError(f'{label}需要 1 至 {maximum} 项。')
        if any(not isinstance(v, str) or not v.strip() or len(v.strip()) > 100 for v in values):
            raise ValueError(f'{label}每项需要 1 至 100 个字符。')
        cleaned = [v.strip() for v in values]
        if len({normalized(v) for v in cleaned}) != len(cleaned):
            raise ValueError(f'{label}不能重复。')
        return cleaned

    def create_batch(self, name, names, kinds, *, commission='', category='', package=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('批次名称需要 1 至 100 个字符。')
        names = self._clean(names, '项目名称', 500)
        kinds = self._clean(kinds, '材料类型', 40) if kinds else []
        if any(not isinstance(v, str) or len(v.strip()) > 100 for v in (commission, category)):
            raise ValueError('委托项目和软件分类最多100字符。')
        commission, category = commission.strip(), category.strip()
        if len((commission + ' · ' if commission else '') + name.strip()) > 100:
            raise ValueError('委托项目与批次组合名称不能超过100字符。')
        saved_name = name.strip()
        if commission:
            from datetime import datetime, timedelta, timezone
            day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            saved_name = day + ' · ' + commission + ' · ' + name.strip()
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM batches WHERE name=?', (saved_name,)).fetchone():
                raise ValueError('批次名称已存在；同名重做请使用新的批次名称。')
            batch_id = connection.execute('INSERT INTO batches(name) VALUES (?)', (saved_name,)).lastrowid
            raw = '\n'.join(names)
            message_id = connection.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)',
                                            (batch_id, raw)).lastrowid
            ids = [connection.execute('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                                      (message_id, batch_id, n)).lastrowid for n in names]
            connection.executemany('INSERT INTO production_kinds VALUES (?,?,?)',
                                   [(batch_id, k, i) for i, k in enumerate(kinds)])
            self._batch_metadata(connection, batch_id, commission, name)
            for pid in ids:
                self._set_requirements(connection, pid, kinds, package or package_for(kinds), category, '新建批次')
            mode='collect'
            connection.execute('INSERT OR IGNORE INTO batch_processing_modes VALUES (?,?)',(batch_id,mode))
        result = dict(batch_id=batch_id, project_ids=ids)
        # A directory failure must not discard the user's newly saved name list.
        return self.service._attach_workspaces(result)

    def configure(self, batch_id, kinds):
        kinds = self._clean(kinds, '材料类型', 20)
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM batches WHERE id=?', (batch_id,)).fetchone():
                raise ValueError('批次不存在。')
            if connection.execute('''SELECT 1 FROM material_versions v JOIN projects p ON p.id=v.project_id
                WHERE p.batch_id=?''', (batch_id,)).fetchone():
                raise ValueError('已有材料版本，不能更改必需类型；重新制作请新建批次。')
            connection.execute('DELETE FROM production_kinds WHERE batch_id=?', (batch_id,))
            connection.executemany('INSERT INTO production_kinds VALUES (?,?,?)',
                                   [(batch_id, k, i) for i, k in enumerate(kinds)])
            for row in connection.execute('SELECT id FROM projects WHERE batch_id=?', (batch_id,)).fetchall():
                if not connection.execute('SELECT 1 FROM production_tasks WHERE project_id=?', (row['id'],)).fetchone():
                    self._set_requirements(connection, row['id'], kinds, package_for(kinds), '', '启用批次默认材料要求')

    @staticmethod
    def _batch_metadata(connection, bid, commission, label):
        cid = None
        if commission:
            connection.execute('INSERT OR IGNORE INTO production_commissions(name) VALUES (?)', (commission,))
            cid = connection.execute('SELECT id FROM production_commissions WHERE name=?', (commission,)).fetchone()[0]
        connection.execute('INSERT INTO production_batches VALUES (?,?,?)', (bid, cid, label))

    def import_plan(self, rows, *, token, source_text='', cancel_event=None, progress=None):
        """Metadata is atomic across all projects/batches; source files copy only after confirmation."""
        from requirement_intake import validate_rows
        rows = validate_rows(rows)
        from file_families import validate as validate_families
        from name_patterns import validate as validate_patterns
        for r in rows:validate_families(r['kinds'],r.get('family_map',{}));validate_patterns(r['kinds'],r.get('name_patterns',{}))
        with self.db.connect() as connection:
            previous = connection.execute('SELECT result_json FROM production_imports WHERE token=?', (token,)).fetchone()
        if previous:
            return self.retry_import_files(token, cancel_event=cancel_event, progress=progress)
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT result_json FROM production_imports WHERE token=?', (token,)).fetchone()
            if previous:
                return json.loads(previous[0])
            batches, ids, copies, bindings, created_ids = {}, [], [], [], []
            for row in rows:
                cancelled(cancel_event)
                if row.get('target_project_id') is not None:
                    pid=row['target_project_id'];self.db._assert_projects_editable(connection,pid)
                    if not connection.execute('SELECT 1 FROM production_tasks WHERE project_id=?',(pid,)).fetchone():
                        raise ValueError('对应材料任务不存在，请重新核对。')
                    if row.get('initialize_empty_task'):
                        if self._task_kinds(connection,pid) or not connection.execute('SELECT 1 FROM original_zip_receipts WHERE project_id=?',(pid,)).fetchone():raise ValueError('原包任务已有材料要求，请重新核对。')
                        profile=next((p for p in self.service.classifications.profiles(True) if p['name']==row['package'] and p['kinds']==row['kinds']),None)
                        by_files=row['package']=='自定义' and bool(row['kinds']) and set(row['kinds'])==set(row['source_kinds'].values())
                        transient=row.get('confirmed_transient_profile')==dict(name=row['package'],kinds=row['kinds']) and bool(row['kinds']) and not any(p['name']==row['package'] for p in self.service.classifications.profiles(True))
                        if not profile and not by_files and not transient:raise ValueError('材料规则已变化，请重新识别。')
                        self._set_requirements(connection,pid,row['kinds'],row['package'],row['category'],'用户确认解压材料，补充原包任务的材料要求')
                        for k,f in row.get('family_map',{}).items():connection.execute('INSERT OR REPLACE INTO task_file_families VALUES (?,?,?)',(pid,k,f))
                        for k,p in row.get('name_patterns',{}).items():connection.execute('INSERT OR REPLACE INTO task_name_patterns VALUES (?,?,?)',(pid,k,json.dumps(p,ensure_ascii=False)))
                    if row.get('extra_kinds'):
                        if self._task_kinds(connection,pid)!=row.get('extend_base'):raise ValueError('任务材料清单已变化，请重新确认新增项。')
                        for i,k in enumerate(row['extra_kinds'],len(row['extend_base'])):connection.execute('INSERT INTO material_requirements VALUES (?,?,?)',(pid,k,i))
                        for k in row['extra_kinds']:
                            if k in row.get('family_map',{}):connection.execute('INSERT INTO task_file_families VALUES (?,?,?)',(pid,k,row['family_map'][k]))
                            if k in row.get('name_patterns',{}):connection.execute('INSERT INTO task_name_patterns VALUES (?,?,?)',(pid,k,json.dumps(row['name_patterns'][k],ensure_ascii=False)))
                        connection.execute('INSERT INTO requirement_changes(project_id,before_json,after_json,reason) VALUES (?,?,?,?)',(pid,json.dumps(dict(kinds=row['extend_base']),ensure_ascii=False),json.dumps(dict(kinds=self._task_kinds(connection,pid)),ensure_ascii=False),'用户确认补充本次新增材料；通用规则保留'))
                    if any(k not in self._task_kinds(connection,pid) for k in row['source_kinds'].values()):
                        raise ValueError('已有任务的材料清单已变化，请重新核对。')
                    if row.get('replace_current'):
                        for k in row['source_kinds'].values():
                            if (self._current(connection,pid,k).get('id') or 0)!=row.get('replace_expected',{}).get(k,0):
                                raise ValueError('已有材料在确认后变化，请重新核对；未直接覆盖。')
                    ids.append(pid);copies.extend((pid,p) for p in row['source_files'])
                    if row.get('storage'):
                        from intake_storage import bind
                        bind(self.service,connection,pid,row['storage'],row['storage_names'])
                    bindings.extend(dict(project_id=pid,path=p,kind=k,replace_current=bool(row.get('replace_current'))) for p,k in row['source_kinds'].items())
                    continue
                key = (normalized(row['commission']), normalized(row['batch']))
                if key not in batches:
                    from datetime import datetime, timedelta, timezone
                    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
                    name = day + ' · ' + (row['commission'] + ' · ' if row['commission'] else '') + row['batch']
                    if connection.execute('SELECT 1 FROM batches WHERE name=?', (name,)).fetchone():
                        raise ValueError(f'批次「{name}」已存在，请在预览中改用新的批次名称。')
                    bid = connection.execute('INSERT INTO batches(name) VALUES (?)', (name,)).lastrowid
                    mid = connection.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)',
                        (bid, source_text.strip()[:200000] or '需求导入')).lastrowid
                    self._batch_metadata(connection, bid, row['commission'], row['batch'])
                    batches[key] = (bid, mid)
                bid, mid = batches[key]
                pid = connection.execute('INSERT INTO projects(batch_id,message_id,name) VALUES (?,?,?)', (bid, mid, row['name'])).lastrowid
                self._set_requirements(connection, pid, row['kinds'], row['package'], row['category'], '核对需求后导入')
                for k,f in row.get('family_map',{}).items():connection.execute('INSERT OR REPLACE INTO task_file_families VALUES (?,?,?)',(pid,k,f))
                for k,p in row.get('name_patterns',{}).items():connection.execute('INSERT OR REPLACE INTO task_name_patterns VALUES (?,?,?)',(pid,k,json.dumps(p,ensure_ascii=False)))
                if row.get('snapshot_aliases') is not None:connection.execute('INSERT OR REPLACE INTO task_profile_snapshots VALUES (?,NULL,?)',(pid,json.dumps(row['snapshot_aliases'],ensure_ascii=False)))
                ids.append(pid)
                created_ids.append(pid)
                if row.get('storage'):
                    from intake_storage import bind
                    bind(self.service,connection,pid,row['storage'],row['storage_names'])
                copies.extend((pid, path) for path in row.get('source_files', []))
                bindings.extend(dict(project_id=pid,path=p,kind=k) for p,k in row['source_kinds'].items())
            from name_patterns import bind_type
            for binding in bindings:bind_type(connection,binding['project_id'],binding['kind'],Path(binding['path']).name)
            for bid, mid in batches.values():
                packages=[r[0] for r in connection.execute('SELECT t.package_name FROM production_tasks t JOIN projects p ON p.id=t.project_id WHERE p.batch_id=?',(bid,))]
                modes={r['processing_mode'] for r in rows if r.get('processing_mode') and not r.get('target_project_id') and batches.get((normalized(r['commission']),normalized(r['batch'])),(None,))[0]==bid}
                if len(modes)>1:raise ValueError('同一新批次请选择一致的处理方式。')
                mode='collect'
                connection.execute('INSERT OR IGNORE INTO batch_processing_modes VALUES (?,?)',(bid,mode))
            result = dict(batch_ids=[b[0] for b in batches.values()], project_ids=ids, created_project_ids=created_ids, intake_errors=[], copied_count=0, import_token=token, material_bindings=bindings)
            connection.execute('INSERT INTO production_imports VALUES (?,?,?,strftime(\'%Y-%m-%dT%H:%M:%fZ\',\'now\'))',
                               (token, source_text[:200000], json.dumps(result)))
            connection.executemany('INSERT OR IGNORE INTO production_import_files(token,project_id,source_path,source_sha256,expected_version_id) VALUES (?,?,?,?,?)',
                                   [(token,pid,path,source_digest(path),self._current(connection,pid,next((b['kind'] for b in bindings if b['project_id']==pid and b['path']==path),None) or self.match_kind(connection,pid,Path(path).name)).get('id') or 0) for pid,path in copies])
            from intake_history import save
            save(self,result,connection=connection)
        result = self.service._attach_workspaces(result)
        with self.db.connect() as connection:
            connection.execute('UPDATE production_imports SET result_json=? WHERE token=?', (json.dumps(result), token))
        return self.retry_import_files(token, cancel_event=cancel_event, progress=progress)

    def pending_imports(self):
        from retry_intake import pending_imports
        from intake_history import pending_raw
        return pending_imports(self)+pending_raw(self)

    def retry_import_files(self, token, *, project_id=None, source_path=None, accepted_source=None, cancel_event=None, progress=None):
        from retry_intake import retry_import_files
        from intake_history import load_raw,retry_raw
        if load_raw(self,token) is not None:return retry_raw(self,token,project_id=project_id,source_path=source_path,accepted_source=accepted_source,cancel_event=cancel_event,progress=progress)
        from contextlib import nullcontext
        with getattr(self.db,'_operation_lock',nullcontext()):
            return retry_import_files(self, token, project_id=project_id, source_path=source_path,
                accepted_source=accepted_source, cancel_event=cancel_event, progress=progress)

    def check_import_conflicts(self, rows):
        from requirement_intake import validate_rows
        from datetime import datetime, timedelta, timezone
        rows = validate_rows(rows)
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        with self.db.connect() as connection:
            for row in rows:
                name = day + ' · ' + (row['commission'] + ' · ' if row['commission'] else '') + row['batch']
                if connection.execute('SELECT 1 FROM batches WHERE name=?', (name,)).fetchone():
                    raise ValueError(f'批次「{row["batch"]}」已存在，请修改预览中的批次名称。')

    def _current(self, connection, project_id, kind):
        row = connection.execute('''SELECT v.*,
            (SELECT passed FROM material_reviews WHERE version_id=v.id ORDER BY id DESC LIMIT 1) AS passed,
            (SELECT note FROM material_reviews WHERE version_id=v.id ORDER BY id DESC LIMIT 1) AS review_note,
            (SELECT created_at FROM material_reviews WHERE version_id=v.id ORDER BY id DESC LIMIT 1) AS reviewed_at,
            (SELECT s.id FROM material_submission_items i JOIN material_submissions s ON s.id=i.submission_id
             WHERE i.version_id=v.id AND NOT EXISTS(SELECT 1 FROM material_submission_voids x
                WHERE x.submission_id=s.id AND x.version_id=v.id)
             ORDER BY s.id DESC LIMIT 1) AS submission_id,
            (SELECT COUNT(*) FROM material_versions h WHERE h.project_id=v.project_id AND h.kind=v.kind) AS version_number
            FROM material_versions v WHERE project_id=? AND kind=? ORDER BY id DESC LIMIT 1''',
            (project_id, kind)).fetchone()
        if not row:
            return dict(kind=kind, state='待生成', id=None)
        result = dict(row)
        required = self._task_kinds(connection, project_id)
        invalid = False
        for source in dependencies(required, kind):
            latest = connection.execute('SELECT MAX(id) FROM material_versions WHERE project_id=? AND kind=?', (project_id, source)).fetchone()[0]
            saved = connection.execute('SELECT source_version_id FROM material_version_inputs WHERE version_id=? AND kind=?', (result['id'], source)).fetchone()
            if not saved or saved[0] != latest:
                invalid = True
        result['dependency_invalid'] = invalid
        if invalid:
            result['passed'] = None
        result['state'] = ('已提交' if result['submission_id'] else '检查通过' if result['passed'] == 1
                           else '需修改' if result['passed'] == 0 else '待检查')
        if invalid:
            result['state'] = '待更新'
        result['submitted_at'] = None
        if result['submission_id']:
            result['submitted_at'] = connection.execute('SELECT created_at FROM material_submissions WHERE id=?',
                                                       (result['submission_id'],)).fetchone()[0]
        return result

    def _project(self, connection, pid):
        row = connection.execute('''SELECT p.*,b.name AS batch_name FROM projects p
            JOIN batches b ON b.id=p.batch_id WHERE p.id=?''', (pid,)).fetchone()
        if not row:
            raise ValueError('项目不存在。')
        result = dict(row)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_project_origins'").fetchone():
            origin=connection.execute('SELECT old_status,old_batch FROM legacy_project_origins WHERE project_id=?',(pid,)).fetchone()
            if origin:result['legacy_status']=origin['old_status'];result['legacy_batch']=origin['old_batch']
        metadata = connection.execute('''SELECT t.*,COALESCE(c.name,'') AS commission FROM production_tasks t
            JOIN projects p ON p.id=t.project_id LEFT JOIN production_batches b ON b.batch_id=p.batch_id
            LEFT JOIN production_commissions c ON c.id=b.commission_id WHERE t.project_id=?''', (pid,)).fetchone()
        result.update(dict(metadata) if metadata else dict(package_name='未启用清单', category='', commission=''))
        from file_families import task_families
        result['family_map']=task_families(connection,pid)
        from name_patterns import read
        result['name_patterns']=read(connection,'task_name_patterns','project_id',pid)
        mode=connection.execute('SELECT mode FROM batch_processing_modes WHERE batch_id=?',(result['batch_id'],)).fetchone()
        result['processing_mode']=mode[0] if mode else 'review'
        result['materials'] = [self._current(connection, pid, k) for k in self._task_kinds(connection, pid)]
        states = [m['state'] for m in result['materials']]
        if not states:
            state = '要求待确认' if metadata else '未启用清单'
        elif '待更新' in states:
            state = '待更新'
        elif all(s == '已提交' for s in states):
            state = '已提交'
        elif '已提交' in states:
            state = '部分提交'
        elif '待生成' in states:
            state = '材料未齐'
        elif '需修改' in states:
            state = '需修改'
        elif '待检查' in states:
            state = '待检查'
        else:
            state = '待提交'
        result['production_state'] = state
        result['required_count'] = len(states)
        result['received_count'] = sum(bool(m['id']) for m in result['materials'])
        result['reviewed_count'] = sum(m.get('passed') == 1 for m in result['materials'])
        result['submitted_count'] = sum(m['state'] == '已提交' for m in result['materials'])
        receipts = {r['kind']: dict(r) for r in connection.execute('SELECT * FROM collected_materials WHERE project_id=?', (pid,))}
        from collection_integrity import issue
        for m in result['materials']:m['collection_issue']=issue(self,connection,pid,m)
        result['collection_count']=sum(bool(m['id'] and not m['collection_issue'] ) for m in result['materials'])
        result['collection_state'] = ('已收齐' if result['required_count'] and result['collection_count'] == result['required_count']
                                      else '部分收录' if result['collection_count'] else '未收录')
        result['collection_folder'] = str(Path(next(iter(receipts.values()))['path']).parent) if receipts else ''
        result['collected_at'] = max((receipts[m['kind']]['collected_at'] for m in result['materials']), default='') if result['collection_state'] == '已收齐' else ''
        result['checked_at'] = max((m.get('reviewed_at') or '' for m in result['materials']), default='') if states and result['reviewed_count'] == len(states) else ''
        result['completed_submission_at'] = max((m.get('submitted_at') or '' for m in result['materials']), default='') if states and result['submitted_count'] == len(states) else ''
        if any(m.get('collection_issue') for m in result['materials'] if m['id']):result['production_state']='材料副本异常'
        elif result['processing_mode']=='collect':
            result['production_state']='已收纳' if result['collection_state']=='已收齐' else '部分收纳' if result['collection_count'] else '待收纳'
        from intake_link import enrich_row
        enrich_row(self,connection,result,receipts)
        result['historical_processing_mode']=result['processing_mode']
        result['historical_production_state']=result['production_state']
        result['processing_mode']='collect'
        for m in result['materials']:
            m['historical_state']=m['state']
            m['state']='文件异常' if m.get('collection_issue') else '已收录' if m.get('collection_saved') else '未收录'
        last=connection.execute('SELECT MAX(created_at) FROM material_versions WHERE project_id=?',(pid,)).fetchone()[0]
        result['last_received_at']=max([last or '']+[z['created_at'] for z in result.get('original_zips',[])])
        from task_query import status
        result['production_state']=status(result)
        return result

    def rows(self, batch_id=None):
        with self.db.connect() as connection:
            ids = [r[0] for r in connection.execute('''SELECT p.id FROM projects p
                JOIN production_tasks t ON t.project_id=p.id
                LEFT JOIN project_details d ON d.project_id=p.id
                LEFT JOIN project_deletions x ON x.project_id=p.id
                WHERE (? IS NULL OR p.batch_id=?) AND d.archived_at IS NULL AND x.deleted_at IS NULL ORDER BY p.id''', (batch_id, batch_id))]
            return [self._project(connection, pid) for pid in ids]

    @staticmethod
    def local_date(value):
        from datetime import datetime, timezone, timedelta
        if not value:
            return ''
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone(timedelta(hours=8))).date().isoformat()

    def statistics(self, day, monthly=False):
        key = day[:7] if monthly else day[:10]
        fields = dict(added='created_at', collected='collected_at', checked='checked_at', submitted='completed_submission_at')
        rows = self.rows()
        return {label: sum(self.local_date(row.get(field))[:len(key)] == key for row in rows) for label, field in fields.items()}

    def storage_statistics(self, day, monthly=False):
        key=day[:7] if monthly else day[:10]
        return sum(r['processing_mode']=='collect' and r['collection_state']=='已收齐' and self.local_date(r['collected_at'])[:len(key)]==key for r in self.rows())

    def set_batch_mode(self,batch_id,mode):
        from classification_rules import MODES
        if mode not in MODES:raise ValueError('处理方式无效。')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            ids=[r[0] for r in c.execute('''SELECT p.id FROM projects p JOIN production_tasks t ON t.project_id=p.id
                LEFT JOIN project_details d ON d.project_id=p.id LEFT JOIN project_deletions x ON x.project_id=p.id
                WHERE p.batch_id=? AND d.archived_at IS NULL AND x.deleted_at IS NULL''',(batch_id,))]
            if not ids:raise ValueError('请先选择已有材料任务的批次。')
            for pid in ids:self.db._assert_projects_editable(c,pid)
            c.execute('INSERT INTO batch_processing_modes VALUES (?,?) ON CONFLICT(batch_id) DO UPDATE SET mode=excluded.mode',(batch_id,mode))
            for pid in ids:self._sync(c,pid)

    def _assert_review_mode(self,c,pid):
        row=c.execute('SELECT m.mode FROM projects p JOIN batch_processing_modes m ON m.batch_id=p.batch_id WHERE p.id=?',(pid,)).fetchone()
        if row and row[0]=='collect':raise ValueError('本批次为统计整理已完成材料，请先切换到统计初始未提交材料。')

    def match_kind(self,c,pid,filename):
        name=c.execute('SELECT name FROM projects WHERE id=?',(pid,)).fetchone()
        kinds=self._task_kinds(c,pid)
        snap=c.execute('SELECT aliases FROM task_profile_snapshots WHERE project_id=?',(pid,)).fetchone()
        aliases=json.loads(snap[0]) if snap else {}
        from file_families import infer,task_families
        from name_patterns import read
        return infer(filename,kinds,task_families(c,pid),aliases,name[0] if name else None,read(c,'task_name_patterns','project_id',pid))

    def collection_migration_needed(self):
        with self.db.connect() as connection:
            rows = connection.execute('''SELECT c.path AS actual,f.path AS expected,c.category FROM collected_materials c JOIN projects p ON p.id=c.project_id
                JOIN material_requirements r ON r.project_id=c.project_id AND r.kind=c.kind
                LEFT JOIN collection_batch_folders f ON f.batch_id=p.batch_id AND f.category=c.category
                ''').fetchall()
            root=Path(self.service.workspaces.settings()['work_root'])
            custom={r[0] for r in connection.execute('SELECT c.path FROM collected_materials c JOIN task_storage_preferences p ON p.project_id=c.project_id')}
            return any((not r['expected'] or not Path(r['actual']).is_relative_to(Path(r['expected'])) or not Path(r['expected']).is_relative_to(root/r['category'])) for r in rows if r['actual'] not in custom)

    def refresh_collections(self, *, cancel_event=None, progress=None):
        from material_collection import publish
        from native_files import cancelled
        with self.db.connect() as connection:
            ids = [r[0] for r in connection.execute('SELECT DISTINCT project_id FROM collected_materials')]
        for pid in ids:
            cancelled(cancel_event)
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                publish(connection, self, pid, cancel_event, progress)
        return dict(reorganized=len(ids))

    def export_master(self, target):
        from task_query import status,missing,receipt_time
        def safe(value):
            value=str('' if value is None else value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        with open(target,'w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['任务编号','软件名称','建表日期','所属批次','分类规则','已收录','要求数量','收纳状态','缺失材料','最近收录时间','收齐时间','保存位置'])
            for row in self.rows():
                writer.writerow([safe(v) for v in [f"WB-{row['id']:06d}",row['name'],self.local_date(row['created_at']),row['batch_name'],row['package_name'],row['collection_count'],row['required_count'],status(row),'、'.join(m['kind'] for m in missing(row)),receipt_time(row),row['collected_at'],row['collection_folder']]])

    def _sync(self, connection, project_id):
        task = self._project(connection, project_id)
        # Storage is a receipt record; it must not manufacture delivery evidence.
        if task['processing_mode'] == 'collect':
            connection.execute("UPDATE projects SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (project_id,))
            return
        state = task['production_state']
        connection.execute('''UPDATE projects SET status=?,delivered_zip_id=NULL,
            delivered_at=CASE WHEN ?='已提交' THEN COALESCE(delivered_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) ELSE NULL END,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?''',
            ('已交付' if state == '已提交' else '待交付', state, project_id))

    def record_work_file(self, connection, file_row, *, kind=None, cancel_event=None, progress=None, replace_current=False, expected_version_id=None):
        """Called within file intake transaction; explicit kind beats suffix inference."""
        pid = file_row['project_id']
        from material_collection import prepare, publish
        kind = prepare(connection, self, file_row, kind)
        project = connection.execute('SELECT batch_id,name FROM projects WHERE id=?', (pid,)).fetchone()
        kinds = self._task_kinds(connection, pid)
        if not kinds:
            return None
        if kind is None:
            kind = self.match_kind(connection,pid,file_row['filename'])
            if kind is None:
                return None
        if kind not in kinds:
            raise ValueError('该材料类型不在本批次清单中。')
        from file_families import compatible,task_families
        if not compatible(kind,file_row['filename'],task_families(connection,pid)):raise ValueError('文件类型不符合该材料要求，原文件未转换。')
        current = self._current(connection, pid, kind)
        if replace_current and (current.get('id') or 0) != expected_version_id:
            raise ValueError('已有版本在确认后发生变化，请重新拖入核对；未直接覆盖。')
        if current.get('sha256') == file_row['sha256'] and current.get('size') == file_row['size']:
            try:
                self._verify(current, cancel_event=cancel_event)
            except (ValueError,OSError):
                # Recapture the explicitly reimported original if its immutable
                # archive was damaged. Preserve the old record for traceability.
                pass
            else:
                publish(connection, self, pid, cancel_event, progress, repair_kind=kind)
                return current['id']
        # A version is a copy outside the editable work directory, so old versions survive edits.
        target = plain_path(Path(self.service.workspaces.settings()['archive_root']) /
                            '材料版本' / f'WB-{pid:06d}' / uuid4().hex / file_row['filename'])
        target.parent.mkdir(parents=True, exist_ok=False)
        metadata = copy_verified(file_row['path'], target, cancel_event=cancel_event, progress=progress)
        if (metadata['sha256'], metadata['size']) != (file_row['sha256'], file_row['size']):
            raise ValueError('材料在收录过程中变化，请重新收录。')
        from name_patterns import bind_type
        bind_type(connection,pid,kind,file_row['filename'])
        version_id = connection.execute('''INSERT INTO material_versions
            (project_id,kind,work_file_id,path,filename,sha256,size) VALUES (?,?,?,?,?,?,?)''',
            (pid, kind, file_row['id'], str(target), file_row['filename'], metadata['sha256'], metadata['size'])).lastrowid
        for source in dependencies(kinds, kind):
            source_id = connection.execute('SELECT MAX(id) FROM material_versions WHERE project_id=? AND kind=?', (pid, source)).fetchone()[0]
            connection.execute('INSERT INTO material_version_inputs VALUES (?,?,?)', (version_id, source, source_id))
        publish(connection, self, pid, cancel_event, progress, repair_kind=kind, discard_old_kind=kind if replace_current else None, source=file_row['source_path'])
        if replace_current:
            from version_overwrite import retire_material
            retire_material(connection,self,current,source=file_row['source_path'])
        self._sync(connection, pid)
        return version_id

    def receive(self, project_id, kind, source, *, cancel_event=None, progress=None, import_token=None, replace_current=False, expected_version_id=None):
        if kind not in self.task_kinds(project_id):
            raise ValueError('请先配置材料类型。')
        return self.service.workspaces.import_file(source, project_id, cancel_event=cancel_event, progress=progress,
            routing=dict(manual=True, basis='材料表明确选择项目与类型', material_kind=kind,import_token=import_token,import_source_path=str(source),replace_current=replace_current,expected_version_id=expected_version_id))

    def bind(self, work_file_id, kind, *, cancel_event=None, progress=None):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM work_files WHERE id=?', (work_file_id,)).fetchone()
            if not row:
                raise ValueError('工作文件不存在。')
            self.db._assert_projects_editable(connection, row['project_id'])
            return self.record_work_file(connection, row, kind=kind, cancel_event=cancel_event, progress=progress)

    def classify_files(self, project_id, *, cancel_event=None, progress=None):
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
            files = [dict(r) for r in connection.execute('''SELECT * FROM work_files f WHERE project_id=? AND id=(
                SELECT MAX(h.id) FROM work_files h WHERE h.project_id=f.project_id AND h.path=f.path) ORDER BY id''', (project_id,))]
        count = 0
        for file in files:
            cancelled(cancel_event)
            if not Path(file['path']).is_file():continue
            project = self.service.get_project(project_id)
            with self.db.connect() as connection:
                kind = self.match_kind(connection,project_id,file['filename'])
            if kind:
                self.bind(file['id'], kind, cancel_event=cancel_event, progress=progress)
                count += 1
        return dict(classified_count=count)

    @staticmethod
    def _verify(row, *, cancel_event=None):
        cancelled(cancel_event)
        with ZipLease(row['path'], movable=False) as lease:
            if _hash_stream(lease.stream, cancel_event=cancel_event) != (row['sha256'], row['size']):
                raise ValueError('版本归档已变化，请重新收录并检查，未保存确认。')

    def _assert_current(self, connection, version_id):
        row = connection.execute('SELECT * FROM material_versions WHERE id=?', (version_id,)).fetchone()
        if not row:
            raise ValueError('材料版本不存在。')
        self.db._assert_projects_editable(connection, row['project_id'])
        current = self._current(connection, row['project_id'], row['kind'])
        if row['kind'] not in self._task_kinds(connection, row['project_id']):
            raise ValueError('此材料已不属于本次要求，历史保留，但不能作为当前材料提交。')
        if current.get('dependency_invalid'):
            raise ValueError('上游Word已更新，请重新生成并收录对应PDF。')
        if current['id'] != version_id:
            raise ValueError('材料已更新，请刷新后检查最新版本。')
        work = connection.execute('SELECT path FROM work_files WHERE id=?', (current['work_file_id'],)).fetchone()
        if work and Path(work['path']).is_file():
            with ZipLease(work['path'], movable=False) as lease:
                if _hash_stream(lease.stream) != (current['sha256'], current['size']):
                    raise ValueError('工作文件已重新生成，请等待稳定后刷新，再检查新版本。')
        from collection_integrity import issue
        problem=issue(self,connection,current['project_id'],current,force=True)
        if problem:raise ValueError(problem)
        return current

    def review(self, version_id, passed, note='', *, cancel_event=None, progress=None):
        if type(passed) is not bool or not isinstance(note, str) or len(note) > 1000:
            raise ValueError('检查结果或备注无效。')
        if not passed and not note.strip():
            raise ValueError('请填写需要修改的原因。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self._assert_current(connection, version_id)
            self._assert_review_mode(connection,current['project_id'])
            if current['submission_id']:
                raise ValueError('该版本已提交，请先撤销误记，或收录新版本。')
            self._verify(current, cancel_event=cancel_event)
            connection.execute('INSERT INTO material_reviews(version_id,passed,note) VALUES (?,?,?)',
                               (version_id, int(passed), note.strip()))
            self._sync(connection, current['project_id'])

    def review_many(self,version_ids,passed=True,note='',*,cancel_event=None,progress=None):
        if not isinstance(version_ids,(list,tuple)) or not version_ids or len(version_ids)>10000 or any(type(v) is not int or v<1 for v in version_ids) or len(set(version_ids))!=len(version_ids):raise ValueError('请选择有效且不重复的材料版本。')
        if type(passed) is not bool or not isinstance(note,str) or len(note)>1000 or (not passed and not note.strip()):raise ValueError('检查结果或修改原因无效。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');currents=[self._assert_current(connection,vid) for vid in version_ids]
            for current in currents:
                self._assert_review_mode(connection,current['project_id'])
                if current['submission_id']:raise ValueError('所选材料包含已提交版本，请先取消勾选或撤销误记。')
                self._verify(current,cancel_event=cancel_event)
            cancelled(cancel_event)
            pending=[r for r in currents if r.get('passed')!=int(passed) or (note.strip() and r.get('review_note')!=note.strip())]
            connection.executemany('INSERT INTO material_reviews(version_id,passed,note) VALUES (?,?,?)',[(r['id'],int(passed),note.strip()) for r in pending])
            for pid in {r['project_id'] for r in pending}:cancelled(cancel_event);self._sync(connection,pid)
            return dict(reviewed_count=len(pending))

    def submit(self, version_ids, note='', *, cancel_event=None, progress=None):
        if not isinstance(version_ids, (list, tuple)) or not version_ids or len(version_ids) > 10000:
            raise ValueError('请选择需要提交的材料。')
        if any(type(v) is not int or v < 1 for v in version_ids) or len(set(version_ids)) != len(version_ids):
            raise ValueError('材料版本编号无效或重复。')
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError('提交备注不能超过 1000 字符。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            currents = [self._assert_current(connection, vid) for vid in version_ids]
            for row in currents:
                self._assert_review_mode(connection,row['project_id'])
                if row['passed'] != 1:
                    raise ValueError(f"{row['kind']}尚未检查通过，未记录本次提交。")
                self._verify(row, cancel_event=cancel_event)
            pending = [r for r in currents if not r['submission_id']]
            if not pending:
                return dict(submitted_count=0)
            cancelled(cancel_event)
            sid = connection.execute('INSERT INTO material_submissions(note) VALUES (?)', (note.strip(),)).lastrowid
            connection.executemany('INSERT INTO material_submission_items VALUES (?,?)', [(sid, r['id']) for r in pending])
            for pid in {r['project_id'] for r in pending}:
                self._sync(connection, pid)
            return dict(submission_id=sid, submitted_count=len(pending))

    def submit_projects(self, project_ids, expected_versions, *, cancel_event=None, progress=None):
        """The UI passes its displayed versions, preventing stale checkbox submissions."""
        if not project_ids or len(set(project_ids)) != len(project_ids):
            raise ValueError('请勾选制作任务。')
        # submit revalidates current versions in its write transaction.
        with self.db.connect() as connection:
            ids = []
            for pid in project_ids:
                self.db._assert_projects_editable(connection, pid)
                project = self._project(connection, pid)
                if not project['materials'] or any(not m['id'] for m in project['materials']):
                    raise ValueError(f"{project['name']}材料未齐，不能整项提交。")
                ids.extend(m['id'] for m in project['materials'])
            if sorted(ids) != sorted(expected_versions):
                raise ValueError('材料清单已变化，请刷新后重新勾选。')
        return self.submit(ids, cancel_event=cancel_event, progress=progress)

    def void_submission(self, submission_id, note, *, version_ids=None, cancel_event=None, progress=None):
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise ValueError('请填写撤销原因，最多 1000 字符。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('''SELECT v.project_id,v.id FROM material_versions v
                JOIN material_submission_items i ON i.version_id=v.id WHERE i.submission_id=?''', (submission_id,)).fetchall()
            if not rows:
                raise ValueError('提交记录不存在。')
            if version_ids is not None:
                if not version_ids or not set(version_ids) <= {r['id'] for r in rows}:
                    raise ValueError('材料不属于所选提交记录。')
                rows = [r for r in rows if r['id'] in version_ids]
            for row in rows:
                self.db._assert_projects_editable(connection, row[0])
            cancelled(cancel_event)
            connection.executemany('''INSERT OR IGNORE INTO material_submission_voids(submission_id,version_id,note)
                VALUES (?,?,?)''', [(submission_id, row['id'], note.strip()) for row in rows])
            for row in rows:
                self._sync(connection, row[0])

    def history(self, project_id, kind):
        with self.db.connect() as connection:
            versions = [dict(r) for r in connection.execute('''SELECT * FROM material_versions
                WHERE project_id=? AND kind=? ORDER BY id DESC''', (project_id, kind))]
            for version in versions:
                version['reviews'] = [dict(r) for r in connection.execute(
                    'SELECT * FROM material_reviews WHERE version_id=? ORDER BY id DESC', (version['id'],))]
                version['submissions'] = [dict(r) for r in connection.execute('''SELECT s.*,x.note AS void_reason,
                    x.created_at AS voided_at FROM material_submissions s JOIN material_submission_items i ON i.submission_id=s.id
                    LEFT JOIN material_submission_voids x ON x.submission_id=s.id AND x.version_id=i.version_id
                    WHERE i.version_id=? ORDER BY s.id DESC''', (version['id'],))]
            return versions

    def scan(self, *, cancel_event=None, progress=None):
        """Notice regenerated contents at registered work paths; only changed bytes reset review."""
        with self.db.connect() as connection:
            rows = [dict(r) for r in connection.execute('''SELECT v.*,f.path AS work_path FROM material_versions v
                JOIN work_files f ON f.id=v.work_file_id JOIN projects p ON p.id=v.project_id
                LEFT JOIN project_details d ON d.project_id=p.id
                LEFT JOIN project_deletions x ON x.project_id=p.id
                WHERE d.archived_at IS NULL AND x.deleted_at IS NULL
                AND EXISTS(SELECT 1 FROM material_requirements k WHERE k.project_id=v.project_id AND k.kind=v.kind) AND v.id=(
                    SELECT MAX(h.id) FROM material_versions h WHERE h.project_id=v.project_id AND h.kind=v.kind)''')]
        from collection_retirement import finalize
        errors, changed = finalize(self), 0
        previous_errors = getattr(self, '_scan_errors', {})
        current_errors = {}
        for row in rows:
            cancelled(cancel_event)
            from collection_integrity import issue
            with self.db.connect() as c:problem=issue(self,c,row['project_id'],row)
            if problem:
                problem_key='collection:'+str(row['id']);current_errors[problem_key]=problem
                if previous_errors.get(problem_key)!=problem:errors.append(dict(source_path=row['path'],error=problem))
            try:
                with ZipLease(row['work_path'], movable=False) as lease:
                    digest, size = _hash_stream(lease.stream, cancel_event=cancel_event)
                if (digest, size) == (row['sha256'], row['size']):
                    continue
                # Persist bytes/stat across two observations, avoiding partially written output.
                identity_key = f"{row['id']}:{digest}:{size}"
                observed = getattr(self, '_observed', {})
                from time import monotonic
                now = monotonic()
                since = observed.get(identity_key)
                if since is None:
                    observed = {k: v for k, v in observed.items() if not k.startswith(str(row['id']) + ':')}
                    observed[identity_key] = now
                    self._observed = observed
                    continue
                if now - since < self.service.workspaces.settings()['stable_seconds']:
                    continue
                self.receive(row['project_id'], row['kind'], row['work_path'], cancel_event=cancel_event, progress=progress)
                self._observed.pop(identity_key, None)
                changed += 1
            except (OSError, ValueError) as error:
                message = str(error)
                current_errors[row['work_path']] = message
                if previous_errors.get(row['work_path']) != message:
                    errors.append(dict(source_path=row['work_path'], error=message))
        self._scan_errors = current_errors
        return dict(changed=changed, errors=errors)

    def export(self, batch_id, target):
        def safe(value):
            value = str(value or '')
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        with open(target, 'w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['批次', '任务编号', '名称', '整体进度', '材料类型', '版本', '材料状态', '文件名', '检查备注', '检查时间', '提交时间', '委托项目', '软件分类', '本次套餐', '收录结果', '已收录数量', '要求数量', '收录文件夹'])
            for row in self.rows(batch_id):
                for m in row['materials'] or [dict(kind='', state=row['production_state'])]:
                    writer.writerow([safe(v) for v in [row['batch_name'], row['id'], row['name'], row['production_state'],
                        m['kind'], m.get('version_number'), m['state'], m.get('filename'), m.get('review_note'),
                        m.get('reviewed_at'), m.get('submitted_at'), row['commission'], row['category'], row['package_name'],
                        row['collection_state'], row['collection_count'], row['required_count'], row['collection_folder']]])
