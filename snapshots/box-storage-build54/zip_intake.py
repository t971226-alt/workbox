"""One approved ZIP intake operation, retaining the existing durable copy queues."""
from pathlib import Path
from uuid import uuid4
from direct_material_intake import identity,DirectMaterialIntake
from production import normalized


def raw_plan(service,rows,archive_rule=None):
    from original_zip_intake import OriginalZipIntake
    paths=[r['path'] for r in rows if r.get('action')!='skip']
    if not paths:raise ValueError('请至少保留一个 ZIP。')
    intake=OriginalZipIntake(service);plan=intake.prepare(paths);groups={};decisions={}
    if archive_rule:
        from intake_rule_options import validate_archive_rule
        archive_rule=validate_archive_rule(archive_rule['name'],archive_rule['extensions'])
        if any(Path(p).suffix.casefold() not in archive_rule['extensions'] for p in paths):raise ValueError('文件格式不符合所选规则，请选择包含本次格式的规则。')
        plan['archive_rule']=archive_rule
    selected={r['path']:r for r in rows}
    for e in plan['entries']:
        r=selected[e['path']]
        if r.get('storage'):
            from intake_storage import validate
            e['storage']=validate(service,r['storage'])
        if r.get('identity') and identity(e['path'])!=list(r['identity']):raise ValueError('ZIP 在确认期间变化，请重新拖入。')
        name=r['name'].strip()
        if not name or len(name)>100 or any(c in name for c in '/\\<>:"|?*'):raise ValueError('请填写有效的软件名称，最多100字，不能包含路径字符。')
        pid=r.get('project_id');action=r.get('action','overwrite')
        if action not in ('new','overwrite'):raise ValueError('重复材料请选择替换、新建或跳过。')
        if pid is not None:
            row=intake.rows([pid])[0]
            with service.db.connect() as c:service.db._assert_projects_editable(c,pid)
            if r.get('expected') and intake.snapshot([row])!=r['expected']:raise ValueError('任务或原包在确认期间变化，请重新打开收录窗口。')
            if normalized(row['name'])!=normalized(name):raise ValueError('所属任务与软件名称不一致，请重新选择。')
            key='selected-task:'+str(pid)
            previous=decisions.get(key)
            if previous and previous['action']!=action:raise ValueError('同一任务的多个 ZIP 请选择相同处理方式。')
            g=dict(key=key,name=row['name'],candidates=[row],files=[],raw_original=True)
            g['snapshot']=intake.snapshot([row]);g=groups.setdefault(key,g);g['files'].append(e['path'])
            decisions[key]=dict(action=action,project_id=pid);e['groups']=[key]
        else:
            # This is an explicit new task selected in the confirmation window;
            # do not infer projects from ZIP members or bundle naming shortcuts.
            e['groups']=[];e['targets']=[dict(name=name,processing_mode='collect',package_name=archive_rule['name'] if archive_rule else '原包待核对')]
    plan['groups']=list(groups.values());plan['library_original']=True
    return plan,decisions


def confirm_raw(service,rows,*,archive_rule=None,**kwargs):
    from original_zip_intake import OriginalZipIntake
    plan,decisions=raw_plan(service,rows,archive_rule)
    return OriginalZipIntake(service).resolve(plan,decisions,**kwargs)


def confirm_materials(service,plan,rows,batch,*,drafts=None,decisions=None,expected=None,**kwargs):
    # Validate all proposed drafts and the complete intake before saving a rule.
    from copy import deepcopy
    prepared=deepcopy(plan);drafts=drafts or []
    names=set();profiles=service.classifications.profiles(True)
    for draft in drafts:
        name,kinds,aliases=service.classifications.validate(draft['name'],draft['kinds'],mode='collect')
        if normalized(name) in names:raise ValueError('本次新建分类名称不能重复。')
        names.add(normalized(name))
        old=next((p for p in profiles if normalized(p['name'])==normalized(name)),None)
        if old and (old['kinds']!=kinds or old.get('family_map',{})!=draft.get('family_map',{}) or old.get('name_patterns',{})!=draft.get('name_patterns',{})):raise ValueError('同名分类已有不同的材料要求，请换一个名称。')
        from name_patterns import validate
        validate(kinds,draft.get('name_patterns',{}))
        prepared['profiles'].append(old or dict(draft,name=name,kinds=kinds,aliases=aliases,mode='collect',active=1))
    DirectMaterialIntake(service).validate(prepared,rows,batch,'collect')
    for draft in drafts:
        name=draft['name'].strip()
        old=next((p for p in service.classifications.profiles(True) if normalized(p['name'])==normalized(name)),None)
        if not old:service.classifications.save(name,draft['kinds'],mode='collect',family_map=draft.get('family_map',{}),name_patterns=draft.get('name_patterns',{}))
    current=service.classifications.profiles(True)
    for draft in drafts:
        profile=next(p for p in current if normalized(p['name'])==normalized(draft['name']))
        prepared['profiles']=[p for p in prepared['profiles'] if p['name']!=profile['name']]+[profile]
    return service.confirm_direct_materials(prepared,rows,batch,'collect',decisions=decisions,expected=expected,**kwargs)


def extraction_conflicts(service,plan,rows,batch):
    """Read-only conflict preview in the same confirmation window."""
    from duplicate_intake import DuplicateIntake
    groups=DirectMaterialIntake(service).validate(plan,rows,batch,'collect');current=service.production.rows();result=[]
    duplicates=DuplicateIntake(service)
    for group in groups:
        candidates=[r for r in current if normalized(r['name'])==normalized(group['name'])]
        if not candidates:continue
        conflict=len(candidates)>1 or any(duplicates._has_existing(dict(source_path=p,material_kind=k),r) for p,k in group['source_kinds'].items() for r in candidates)
        if conflict:
            key=(normalized(group['batch'])+'\x1f' if plan.get('split_batches') else '')+normalized(group['name'])
            result.append(dict(key=key,name=group['name'],candidates=candidates,snapshot=duplicates._snapshot(candidates)))
    return result


def confirm_grouped_materials(service,plan,rows,batch,*,folders,**kwargs):
    from intake_folders import validate_records,commit
    checked=validate_records(service,folders['root'],folders['items'])
    result=confirm_materials(service,plan,rows,batch,**kwargs)
    if result.get('copied_count',0) or result.get('project_ids'):commit(service,checked)
    return result

def confirm_grouped_raw(service,rows,*,folders,**kwargs):
    from intake_folders import validate_records,commit
    checked=validate_records(service,folders['root'],folders['items'])
    result=confirm_raw(service,rows,**kwargs)
    if result.get('copied_count',0) or result.get('project_ids'):commit(service,checked)
    return result
