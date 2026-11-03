"""Interactive duplicate-material decisions, kept separate from explicit backend imports."""
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from concurrent.futures import CancelledError

from native_files import cancelled, plain_path, path_is_within
from production import infer_kind, normalized
from material_collection import unpack_material_zip, has_material_members


class DuplicateIntake:
    def __init__(self, service):
        self.service = service

    def _projects(self, ids):
        with self.service.db.connect() as c:
            return [self.service.production._project(c, pid) for pid in ids if c.execute(
                'SELECT 1 FROM production_tasks WHERE project_id=?', (pid,)).fetchone()]

    def _entry(self, path, candidates, *, item=None, material_kind=None):
        rows = [r for r in candidates if not r.get('archived_at') and not r.get('deleted_at')]
        return dict(source_path=str(path), project_ids=[r['id'] for r in rows],
                    folder_item=item, material_kind=material_kind)

    def _entries(self, path, project_id, material_kind, folder_items, cancel_event):
        source = plain_path(path, allow_network=True)
        active = {p['id']: p for p in self.service.list_projects()}
        if folder_items:
            return [self._entry(i['source_path'], [active[project_id]], item=i, material_kind=material_kind) for i in folder_items]
        if source.is_dir():
            from folder_intake import FolderIntake
            intake = FolderIntake(self.service)
            roots = [Path(self.service.workspaces.settings()['work_root']), *map(Path, self.service.workspaces.archive_roots())]
            if any(path_is_within(source, p) or path_is_within(p, source) for p in roots):
                return [dict(source_path=str(source), project_ids=[], opaque=True)]
            files = intake._scan(source, cancel_event)
            plans = intake._plan(source, files, project_id, project_id is not None)
            entries = []
            for plan in plans:
                route = plan['routing']
                candidates = route['candidates']
                protected = any(p.get('archived_at') or p.get('deleted_at') for p in candidates)
                item = dict(source_path=str(plan['source']), root_path=str(source),
                            relative_path=str(plan['relative_path']), source_relative_path=str(plan['source'].relative_to(source)))
                entry = self._entry(plan['source'], candidates, item=item)
                entry['protected'] = protected or (route.get('requires_choice') and not candidates)
                entries.append(entry)
            return entries
        routing = self.service.file_routing(source)
        candidates = [active[project_id]] if project_id is not None else routing['candidates']
        if source.suffix.casefold() == '.zip' and not candidates:
            stem = normalized(source.stem)
            candidates = [p for p in active.values() if self.service.production.managed(p['id']) and
                (stem == normalized(p['name']) or any(stem.startswith(normalized(p['name']) + s) for s in ('_', '-', ' ')))]
        if routing.get('requires_choice') and project_id is None:
            return [dict(source_path=str(source), project_ids=[], opaque=True)]
        managed = self._projects([p['id'] for p in candidates])
        from file_families import infer
        if source.suffix.casefold() == '.zip' and managed and not any(
                infer(source.name, [m['kind'] for m in p['materials']],p.get('family_map'),project_name=p['name'],patterns=p.get('name_patterns')) for p in managed):
            # A matching material package is inspected in bounded staging, never executed.
            files = unpack_material_zip(self.service, source, cancel_event=cancel_event)
            if not has_material_members(files, candidates):
                return [dict(source_path=str(source), project_ids=[], opaque=True)]
            entries = []
            for file in files:
                matched = self.service.file_routing(file)['candidates']
                if not matched:
                    matched = [p for p in candidates if normalized(p['name']) in {normalized(v) for v in file.parts[:-1]}] or candidates
                entries.append(self._entry(file, matched))
            return entries
        return [self._entry(source, candidates, material_kind=material_kind)]

    def _has_existing(self, entry, row):
        filename = Path(entry['source_path']).name
        from file_families import infer
        kind = entry.get('material_kind') or infer(filename,[m['kind'] for m in row['materials']],row.get('family_map'),project_name=row['name'],patterns=row.get('name_patterns'))
        existing=next((m for m in row['materials'] if m['kind']==kind and m['id']),None)
        if existing:
            from production import source_digest
            try:
                if not existing.get('collection_issue') and existing.get('collection_saved') and source_digest(entry['source_path'])==existing['sha256']:return False
            except OSError:pass
            return True
        with self.service.db.connect() as c:
            return any(normalized(Path(r['source_path']).name) == normalized(filename) or normalized(r['filename']) == normalized(filename)
                       for r in c.execute('SELECT filename,source_path FROM work_files WHERE project_id=?', (row['id'],)))

    @staticmethod
    def _snapshot(rows):
        return {str(r['id']): dict(name=r['name'], batch_id=r['batch_id'], package=r['package_name'],
                    category=r['category'], commission=r['commission'], state=r['production_state'],
                    family_map=r.get('family_map',{}),name_patterns=r.get('name_patterns',{}),
                    reviewed=r['reviewed_count'], submitted=r['submitted_count'],
                    versions=[(m['kind'], m['id']) for m in r['materials']]) for r in rows}

    def prepare(self, paths, *, project_id=None, material_kind=None, folder_items=None, cancel_event=None):
        if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= 100:
            raise ValueError('一次请选择1至100个文件或文件夹。')
        entries = []
        for path in paths:
            cancelled(cancel_event)
            entries.extend(self._entries(path, project_id, material_kind, folder_items, cancel_event))
        groups = {}
        for entry in entries:
            cancelled(cancel_event)
            if entry.get('opaque') or entry.get('protected'):
                continue
            rows = self._projects(entry['project_ids'])
            names = {normalized(r['name']) for r in rows}
            if len(names) != 1 or len(rows) != len(entry['project_ids']):
                continue
            key = next(iter(names))
            entry['group'] = key
            group = groups.setdefault(key, dict(key=key, name=rows[0]['name'], candidates={}, files=[], conflict=False))
            group['candidates'].update({r['id']: r for r in rows})
            group['files'].append(entry['source_path'])
            group['conflict'] |= len(rows)>1 or any(self._has_existing(entry, r) for r in rows)
        conflicts = []
        for group in groups.values():
            if group.pop('conflict'):
                group['candidates'] = sorted(group['candidates'].values(), key=lambda r: r['id'], reverse=True)
                group['snapshot'] = self._snapshot(group['candidates'])
                conflicts.append(group)
        return dict(token=uuid4().hex, paths=[str(p) for p in paths], entries=entries, groups=conflicts,
                    project_id=project_id, material_kind=material_kind, folder_items=folder_items)

    def receive(self, paths, *, project_id=None, material_kind=None, folder_items=None, cancel_event=None, progress=None):
        plan = self.prepare(paths, project_id=project_id, material_kind=material_kind, folder_items=folder_items, cancel_event=cancel_event)
        if plan['groups']:
            return dict(material_conflicts=plan['groups'], material_plan=plan)
        results = []
        for path in paths:
            cancelled(cancel_event)
            try:
                if material_kind and project_id is not None:
                    result = self.service.production.receive(project_id, material_kind, path, cancel_event=cancel_event, progress=progress)
                else:
                    result = self.service.receive_path(path, project_id, manual=True if project_id is not None else None,
                        folder_items=folder_items, cancel_event=cancel_event, progress=progress)
            except CancelledError:
                raise
            except Exception as error:
                if len(paths) == 1:
                    raise
                result = self.failure(path, error)
            results.append(result)
        return results[0] if len(results) == 1 else self.combine(results, paths)

    @staticmethod
    def failure(path, error):
        return dict(kind='folder_result', source_path=str(path), copied=0, duplicate=0, watching=0, failed=1,
                    needs_choice=0, cancelled=False, items=[dict(source_path=str(path), status='failed', message=str(error))],
                    project_ids=[], created_project_ids=[])

    @staticmethod
    def combine(results, paths):
        total = dict(kind='folder_result', source_path=str(paths[0]), copied=0, duplicate=0, watching=0,
                     failed=0, needs_choice=0, cancelled=False, items=[], project_ids=[], created_project_ids=[])
        for result, path in zip(results, paths):
            if result.get('kind') == 'folder_result':
                for field in ('copied', 'duplicate', 'watching', 'failed', 'needs_choice'):
                    total[field] += result[field]
                total['items'].extend(result['items'])
                total['project_ids'].extend(result.get('project_ids', []))
                total['created_project_ids'].extend(result.get('created_project_ids', []))
                total['cancelled'] |= result.get('cancelled', False)
            elif result.get('intake_choice'):
                source = Path(path)
                total['needs_choice'] += 1
                total['items'].append(dict(status='needs_choice', retry_path=str(source), source_path=str(source), root_path=str(source.parent),
                    relative_path=source.name, source_relative_path=source.name,
                    candidate_ids=[p['id'] for p in result['routing']['candidates']], basis=result['routing']['basis']))
            else:
                status = 'watching' if result.get('watching') else 'duplicate' if result.get('duplicate') else 'copied'
                total[status] += 1
                pid = result.get('file', {}).get('project_id') or result.get('project_id')
                if pid:
                    total['project_ids'].append(pid)
                total['created_project_ids'].extend(result.get('created_project_ids', []))
                total['items'].append(dict(source_path=str(path), status=status))
        return total

    def resolve(self, plan, decisions, *, cancel_event=None, progress=None):
        # Validate every decision and source before creating even one new task.
        selected = {}
        for group in plan['groups']:
            decision = decisions.get(group['key'], {})
            pid, action = decision.get('project_id'), decision.get('action')
            if action not in ('new', 'overwrite', 'replace', 'skip') or pid not in [p['id'] for p in group['candidates']]:
                raise ValueError('请选择新建、替换或跳过。')
            with self.service.db.connect() as c:
                self.service.db._assert_projects_editable(c, pid)
            current = self._projects([p['id'] for p in group['candidates']])
            if self._snapshot(current) != group['snapshot']:
                fresh = self.prepare(plan['paths'], project_id=plan['project_id'], material_kind=plan['material_kind'],
                    folder_items=plan['folder_items'], cancel_event=cancel_event)
                if not fresh['groups']:
                    raise ValueError('材料或任务已变化，请重新拖入后核对。')
                return dict(material_conflicts=fresh['groups'], material_plan=fresh)
            selected[group['key']] = dict(action=action, row=next(p for p in current if p['id'] == pid), project_id=pid)
        for entry in plan['entries']:
            cancelled(cancel_event)
            if not plain_path(entry['source_path'], allow_network=True).exists():
                raise ValueError('来源已移动或删除，请重新拖入材料。')
        new = [(key, value) for key, value in selected.items() if value['action'] == 'new']
        imported = {}
        if new:
            from requirement_intake import proposal
            label = datetime.now().strftime('%m-%d %H:%M:%S 重做-') + plan['token'][:6]
            rows = [dict(proposal(v['row']['name'], commission=v['row']['commission'], batch=label, category=v['row']['category']),
                         kinds=[m['kind'] for m in v['row']['materials']], package=v['row']['package_name']) for key, v in new]
            imported = self.service.production.import_plan(rows, token=plan['token'], cancel_event=cancel_event, progress=progress)
            for (key, value), pid in zip(new, imported['project_ids']):
                value['project_id'] = pid
        results, paths = [], []
        # PDFs describe the Word version delivered in this same operation,
        # even when the archive's member order puts PDF first.
        for entry in sorted(plan['entries'], key=lambda e: Path(e['source_path']).suffix.casefold() == '.pdf'):
            cancelled(cancel_event)
            path = entry['source_path']
            choice = selected.get(entry.get('group'))
            pid = choice['project_id'] if choice else entry['project_ids'][0] if len(entry.get('project_ids', [])) == 1 and not entry.get('protected') else None
            try:
                if choice and choice['action']=='skip':
                    result=dict(kind='folder_result',copied=0,duplicate=1,watching=0,failed=0,needs_choice=0,cancelled=False,items=[dict(source_path=path,status='duplicate',message='已跳过重复材料')],project_ids=[],created_project_ids=[])
                elif pid is not None and not entry.get('opaque'):
                    if choice and choice['action']=='replace':
                        with self.service.db.connect() as c:
                            kind=entry.get('material_kind') or self.service.production.match_kind(c,pid,Path(path).name)
                            previous=next((m for m in choice['row']['materials'] if m['kind']==kind),{})
                        if not kind:raise ValueError('此文件无法确定对应材料，请在收录预览中核对规则后重试。')
                        result=self.service.production.receive(pid,kind,path,cancel_event=cancel_event,progress=progress,replace_current=True,expected_version_id=previous.get('id') or 0)
                    elif entry.get('material_kind'):
                        result = self.service.production.receive(pid, entry['material_kind'], path, cancel_event=cancel_event, progress=progress)
                    elif entry.get('folder_item') and Path(path).suffix.casefold() != '.zip':
                        from folder_intake import FolderIntake
                        result = FolderIntake(self.service).receive_items([entry['folder_item']], pid, cancel_event=cancel_event, progress=progress)
                    else:
                        result = self.service.receive_path(path, pid, manual=True, cancel_event=cancel_event, progress=progress)
                elif entry.get('folder_item'):
                    item = dict(entry['folder_item'], retry_path=path, candidate_ids=entry['project_ids'], status='needs_choice', basis='请选择材料所属项目', message='归属未确定')
                    result = dict(kind='folder_result', copied=0, duplicate=0, watching=0, failed=0, needs_choice=1, items=[item], project_ids=[], created_project_ids=[])
                else:
                    result = self.service.receive_path(path, cancel_event=cancel_event, progress=progress)
            except CancelledError:
                raise
            except Exception as error:
                result = self.failure(path, error)
            results.append(result)
            paths.append(path)
        total = self.combine(results, paths)
        total.update(material_intake=True, batch_ids=imported.get('batch_ids', []))
        total['created_project_ids'].extend(imported.get('project_ids', []))
        return total
