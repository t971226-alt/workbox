"""Read-only drop proposals; confirmation uses the normal versioned import queue."""
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from collections import OrderedDict
import re

from native_files import plain_path,cancelled,path_is_within
from material_templates import PRESETS
from file_formats import EXTRACTABLE,recognizable,naming_kinds
from production import infer_kind,normalized


GENERIC={'材料','成品','附件','文件','文档','收件','新建文件夹','说明书','软件说明书','操作手册','用户手册','手册','报告','代码','源代码','视频','演示','软件操作演示','数据库演示','单word','五件材料','14件材料','14件＋视频','14件＋双视频','readme','license','changelog','说明'}


def identity(path):
    s=plain_path(path,allow_network=True).stat()
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns]


def ranked_versions(entries,indices):
    """Newest modification first; identical times use a stable path order."""
    def key(i):
        e=entries[i];stamp=e.get('modified_ns')
        if stamp is None:stamp=Path(e['path']).stat().st_mtime_ns
        return (-stamp,normalized(e['path']),e['path'])
    return sorted(indices,key=key)


class DirectMaterialIntake:
    def __init__(self,service):self.service=service

    def profiles(self):
        from intake_folders import routing_profiles
        return routing_profiles(self.service,self.service.classifications.profiles(True))

    @staticmethod
    def role_and_name(path,profiles,parents):
        from material_naming import candidates,ALIASES
        from file_matcher import new_file_project_name
        # Recognize material suffixes even when that preset is disabled, without
        # reactivating it or treating a report as a manual.
        all_kinds=list(dict.fromkeys([k for p in profiles for k in p['kinds']]+[k for kinds in PRESETS.values() for k in kinds]))
        canonical_kinds=all_kinds[:]
        all_kinds=naming_kinds(all_kinds,path.suffix.casefold())
        aliases={}
        for p in profiles:
            for kind,terms in p['aliases'].items():aliases.setdefault(kind,[]).extend(terms)
        matches=candidates(path.name,all_kinds,aliases)
        names={name for name,_ in matches};roles={kind for _,kind in matches}
        name=next(iter(names)) if len(names)==1 else ''
        kind=next(iter(roles)) if len(roles)==1 else None
        generic={normalized(x) for x in GENERIC}|{normalized(Path(k).stem) for k in all_kinds}|{normalized(p['name']) for p in profiles}|{normalized(t) for terms in ALIASES.values() for t in terms}
        if not matches:name=new_file_project_name(path.name)
        if normalized(name) in generic:name=''
        if not name:
            name=next((p for p in parents if normalized(p) not in generic and p.strip() and not p.startswith('.') and not re.fullmatch(r'(?:第?[一二三四五六七八九十0-9]+批(?:次)?|批次|项目|20[0-9.\-年月日]+)',p)), '')
        # Bare 软件名称.docx remains the useful one-manual shortcut. A recognized
        # non-manual suffix never takes this fallback.
        if not matches and name:
            # A bare software title is a manual in Word/PDF and registration data
            # in TXT. Other report names still require an explicit material role.
            from file_formats import DOCUMENTS
            bare_kinds=canonical_kinds+(['软件说明书'+path.suffix.casefold()] if path.suffix.casefold() in DOCUMENTS else [])
            bare = candidates(path.name, bare_kinds, aliases, project_name=name)
            if path.suffix.casefold() == '.txt' and not re.search(r'(?:系统|平台|软件|工具|程序)(?:\s*[vV]?\d+(?:\.\d+)*)?$', name):
                bare = []
            roles = {role for _, role in bare}
            if len(roles) == 1:
                kind = next(iter(roles))
        return name,kind or path.name

    def prepare(self,paths,*,cancel_event=None,require_material_zip=False,include_known=False,allow_saved_zip=False,keep_archives=False):
        if not isinstance(paths,(list,tuple)) or not 1<=len(paths)<=100:raise ValueError('一次请选择1至100个文件或文件夹。')
        from folder_intake import FolderIntake
        from material_collection import unpack_material_zip
        profiles=self.profiles();entries=[];sources={};seen=set();total=0;skipped_archives=[]
        settings=self.service.workspaces.settings()
        protected=[Path(settings['work_root']),*map(Path,self.service.workspaces.archive_roots()),self.service.db.path.parent]
        for raw in paths:
            cancelled(cancel_event);root=plain_path(raw,allow_network=True)
            if any(path_is_within(root,p) or root.is_dir() and path_is_within(p,root) for p in protected):
                # Existing app-managed paths keep their established routing safeguards.
                allowed=False
                if allow_saved_zip and root.is_file() and root.suffix.casefold() in EXTRACTABLE:
                    with self.service.db.connect() as c:
                        receipt=c.execute('SELECT project_id FROM original_zip_receipts WHERE path=?',(str(root),)).fetchone()
                        if receipt:
                            self.service.db._assert_projects_editable(c,receipt['project_id']);allowed=True
                if not allowed:return None
            if not root.exists():raise ValueError('来源不存在，请重新选择材料。')
            if root.is_dir():
                files=FolderIntake(self.service)._scan(root,cancel_event)
                parents=lambda f:[*reversed(f.relative_to(root).parts[:-1]),root.name]
                split=not any(f.parent==root for f in files)
            elif not keep_archives and root.suffix.casefold() in EXTRACTABLE and not infer_kind(root.name,list(dict.fromkeys(k for p in profiles for k in p['kinds']))):
                sources[str(root)]=identity(root)
                files=unpack_material_zip(self.service,root,cancel_event=cancel_event)
                from storage_management import staging_root,staging_base_for
                cache=staging_base_for(self.service,files[0]) if files else staging_root(self.service)
                parents=lambda f:[*reversed(f.relative_to(cache).parts[1:-1]),root.stem]
                if not any(recognizable(f) or self.role_and_name(f,profiles,[])[1]!=f.name or infer_kind(f.name,list(dict.fromkeys(k for p in profiles for k in p['kinds']))) for f in files):
                    if len(paths)==1:
                        if require_material_zip:raise ValueError('ZIP 内未识别到可分类的材料。请重新拖入，选择「直接收录原包」，或手工解压核对。原件保留。')
                        return None
                    files=[root];parents=lambda f:[]
            else:
                files=[root];parents=lambda f:[]
            expanded = []
            archive_parents = {}
            containers = {}
            for candidate in files:
                cancelled(cancel_event)
                if not keep_archives and candidate.suffix.casefold() == '.rar':
                    skipped_archives.append(dict(path=str(candidate), reason='请先解压此压缩格式，再拖入材料文件夹'))
                    continue
                if not keep_archives and candidate.suffix.casefold() in EXTRACTABLE and not infer_kind(candidate.name, list(dict.fromkeys(k for p in profiles for k in p['kinds']))):
                    sources[str(candidate)] = identity(candidate)
                    members = unpack_material_zip(self.service, candidate, cancel_event=cancel_event)
                    if not any(recognizable(m) or self.role_and_name(m, profiles, [])[1] != m.name for m in members):
                        skipped_archives.append(dict(path=str(candidate), reason='未识别为材料包，请解压核对后收录'))
                        continue
                    from storage_management import staging_root,staging_base_for
                    cache = staging_base_for(self.service,members[0]) if members else staging_root(self.service)
                    for member in members:
                        # Inner archives are ordinary materials at this level;
                        # do not recursively extract or silently omit them.
                        expanded.append(member)
                        archive_parents[str(member)] = [*reversed(member.relative_to(cache).parts[1:-1]), candidate.stem]
                        containers[str(member)] = candidate
                else:
                    expanded.append(candidate)
            for f in expanded:
                cancelled(cancel_event);f=plain_path(f,allow_network=True)
                key=str(f).casefold()
                if key in seen:continue
                seen.add(key);total+=f.stat().st_size
                if len(seen)>5000 or total>20*1024**3:raise ValueError('材料数量或总大小超过上限，请分批拖入。')
                context=archive_parents[str(f)] if str(f) in archive_parents else parents(f)
                name,kind=self.role_and_name(f,profiles,context)
                from name_patterns import software_name
                name=software_name(f,name,context,kind!=f.name)
                route=self.service.file_routing(f)
                if route.get('requires_choice') and any(p.get('archived_at') or p.get('deleted_at') for p in route['candidates']):
                    raise ValueError('材料匹配到已归档或已删除项目，请先恢复项目后再收录。')
                known=[p for p in route['candidates'] if not p.get('archived_at') and not p.get('deleted_at') and self.service.production.managed(p['id'])]
                if len(known)==1:
                    name=known[0]['name']
                    with self.service.db.connect() as c:kind=self.service.production.match_kind(c,known[0]['id'],f.name) or kind
                origin = containers.get(str(f), f)
                batch_root=(root/origin.relative_to(root).parts[0] if split else root) if root.is_dir() else root if root.suffix.casefold() in EXTRACTABLE and root!=f else None
                entries.append(dict(path=str(f),name=name,kind=kind,batch_key=str(batch_root or ''),batch_label=batch_root.name if batch_root else '',
                                    relative_path=str(f.relative_to(batch_root)) if batch_root and f.is_relative_to(batch_root) else f.name,
                                    container=str(containers[str(f)]) if str(f) in containers else str(root) if root.is_file() and root.suffix.casefold() in EXTRACTABLE and root!=f else None,
                                    warning='' if name else '请补充软件名称',known_ids=[p['id'] for p in known]))
                sources[str(f)]=identity(f)
                entries[-1]['modified_ns']=sources[str(f)][3]
        if not entries:
            if skipped_archives:raise ValueError('此压缩包尚未收录。ZIP/7z 可选择解压分类；RAR 请先保存原包或手工解压。')
            raise ValueError('没有找到可收录的普通文件。')
        # Known tasks retain the original duplicate/new/overwrite workflow.
        managed=self.service.production.rows()
        identities=[(e['batch_key'],normalized(e['name']),e['kind']) for e in entries]
        if not include_known and not skipped_archives and not containers and len({e['batch_key'] for e in entries})==1 and len(set(identities))==len(identities) and all(any(normalized(r['name'])==normalized(e['name']) for r in managed) for e in entries):return None
        labels={};used={normalized(b['label']) for b in self.service.production.batches(period='今天') if not b['commission']}
        for e in entries:
            key=e['batch_key']
            if key not in labels:
                label=e['batch_label'] or '散装材料';base=label;i=2
                while normalized(label) in used:label=f'{base} ({i})';i+=1
                labels[key]=label;used.add(normalized(label))
            e['batch_label']=labels[key]
        groups=OrderedDict()
        for i,e in enumerate(entries):groups.setdefault((e['batch_key'],normalized(e['name']) if e['name'] else f'unknown:{i}'),[]).append(e)
        for group in groups.values():
            existing=[r for r in managed if group[0]['name'] and normalized(r['name'])==normalized(group[0]['name'])]
            from file_families import infer
            patterned=[]
            for p in profiles:
                if not p.get('name_patterns'):continue
                matched={e['path']:infer(Path(e['path']).name,p['kinds'],p.get('family_map'),p.get('aliases'),e['name'],p['name_patterns']) for e in group}
                if any(v.get('mode')=='type' for v in p['name_patterns'].values()):
                    from name_patterns import assign_types
                    matched=assign_types(p,group)
                if all(matched.values()):patterned.append((p,matched))
            complete=[p for p in patterned if len(set(p[1].values()))==len(p[0]['kinds'])]
            patterned=complete or patterned
            if len(patterned)==1:
                p,mapping=patterned[0]
                for e in group:e['kind']=mapping[e['path']]
            kinds={e['kind'] for e in group}
            candidates=[p for p,m in patterned] if patterned else [p for p in profiles if not p.get('name_patterns') and kinds.issubset(p['kinds'])]
            if not candidates:
                from intake_rule_options import match_unknown_formats
                suggestion=match_unknown_formats(group,profiles)
                if suggestion:
                    candidate,mapping=suggestion
                    for e in group:e['kind']=mapping[e['path']];e['warning']='按文件格式匹配到规则，请确认材料用途。'
                    kinds={e['kind'] for e in group};candidates=[candidate]
            if not candidates:
                from file_families import infer,matching_options
                matches=[]
                for profile in profiles:
                    families=profile.get('family_map',{})
                    if not families and not profile.get('name_patterns'):continue
                    mapping={}
                    for e in group:
                        role=infer(Path(e['path']).name,profile['kinds'],families,profile.get('aliases'),e['name'],profile.get('name_patterns'))
                        options=matching_options(e['path'],profile['kinds'],families)
                        if not role and not profile.get('name_patterns') and len(options)==1 and e['kind']==Path(e['path']).name:role=options[0]
                        if not role:break
                        mapping[e['path']]=role
                    if len(mapping)==len(group):matches.append((profile,mapping))
                exact=[m for m in matches if len(set(m[1].values()))==len(m[0]['kinds'])]
                matches=exact or matches
                if len(matches)==1:
                    selected,mapping=matches[0];candidates=[selected]
                    for e in group:e['kind']=mapping[e['path']]
                    kinds={e['kind'] for e in group}
            exact=[p for p in candidates if set(p['kinds'])==kinds]
            profile=exact[0] if len(exact)==1 else candidates[0] if len(candidates)==1 else None
            ambiguous=bool(candidates) and profile is None
            from name_patterns import suggest
            draft,mapping=suggest(group)
            for e in group:e['suggested_draft']=draft;e['suggested_kind']=mapping[e['path']]
            if not existing and (not profile or profile['name'] in PRESETS and len(kinds)<len(profile['kinds'])):
                profile=None;ambiguous=False
            for e in group:
                e['package']=profile['name'] if profile else '待确认' if ambiguous else '自定义'
                e['kinds']=list(profile['kinds']) if profile else list(dict.fromkeys(x['kind'] for x in group))
                if len(existing)==1:
                    task=existing[0]
                    if not task['materials'] and task.get('original_zips'):
                        e['warning']='匹配原包任务，确认后补充材料清单并更新原行'
                    else:
                        e['package']=task['package_name'];e['kinds']=[m['kind'] for m in task['materials']]
                        e['existing']=dict(id=task['id'],name=task['name']);e['warning']='匹配已有任务，沿用原材料清单'
                        e['existing_families']=task.get('family_map',{})
                        e['existing_patterns']=task.get('name_patterns',{})
                    with self.service.db.connect() as c:e['kind']=self.service.production.match_kind(c,task['id'],Path(e['path']).name) or e['kind']
                if not e['warning']:e['warning']='符合多个分类，请选择本次完整材料要求' if ambiguous else '按现有文件推测分类，请核对完整材料要求'
            if len(existing)==1 and existing[0]['materials']:
                task=existing[0];base=[m['kind'] for m in task['materials']];extras=[];extra_families={};extra_patterns={}
                with self.service.db.connect() as c:
                    for e in group:
                        k=self.service.production.match_kind(c,task['id'],Path(e['path']).name)
                        proposed=e['suggested_kind']
                        from file_families import compatible
                        unresolved_type=any(p.get('mode')=='type' and compatible(kind,e['path'],task.get('family_map',{})) for kind,p in task.get('name_patterns',{}).items())
                        if not k and proposed not in base and not unresolved_type:
                            k=proposed
                            if k not in extras:extras.append(k)
                            if k in draft['family_map']:extra_families[k]=draft['family_map'][k]
                            extra_patterns[k]=draft['name_patterns'][k]
                        if k:e['kind']=k
                if extras:
                    for e in group:e.update(kinds=base+extras,existing_base_kinds=base,extra_kinds=extras,extra_families=extra_families,extra_patterns=extra_patterns,warning='本次新增文件将补充到本任务清单；通用规则不变。')
        modes={p['mode'] for p in profiles if p['name'] in {e['package'] for e in entries}}
        default=next(iter(modes)) if len(modes)==1 else 'review'
        return dict(token=uuid4().hex,batch=datetime.now().strftime('%m-%d %H:%M:%S 拖入材料'),entries=entries,
                    profiles=profiles,sources=sources,skipped_archives=skipped_archives,split_batches=len(labels)>1,batch_labels=list(labels.values()),mode='collect')

    @staticmethod
    def can_auto_receive(plan):
        entries = plan.get('entries', [])
        profiles = {p['name']: p for p in plan.get('profiles', [])}
        seen = set()
        for entry in entries:
            key = (entry.get('batch_key', ''), normalized(entry.get('name', '')), entry.get('kind'))
            profile = profiles.get(entry.get('package'))
            existing=entry.get('existing')
            valid_existing=bool(existing and normalized(existing['name'])==normalized(entry.get('name','')) and entry.get('kind') in entry.get('kinds',[]))
            valid_profile=bool(profile and entry.get('kind') in profile['kinds'] and entry.get('kinds')==profile['kinds'])
            if not entry.get('name') or not (valid_existing or valid_profile) or key in seen:
                return False
            seen.add(key)
        return bool(entries)

    def validate(self,plan,rows,batch,mode):
        if not isinstance(rows,list) or not rows:raise ValueError('请至少保留一个待收录文件。')
        if mode not in ('collect','review'):raise ValueError('请选择处理方式。')
        originals={e['path']:e for e in plan['entries']};seen=set();groups=OrderedDict()
        for row in rows:
            path=row['path'];name=row['name'].strip();kind=row['kind'];kinds=row['kinds']
            if row['package']=='待确认':raise ValueError('存在多个可能的分类，请先选择材料分类。')
            if path not in originals or path in seen:raise ValueError('来源清单无效，请重新拖入。')
            seen.add(path)
            if not name or len(name)>100:raise ValueError('请为每个文件填写软件名称，最多100个字符。')
            if not isinstance(kind,str) or Path(kind).name!=kind or any(x in kind for x in '/\\<>:"|?*'):raise ValueError('材料类型请填写文件名与扩展名，不能包含路径。')
            from file_families import compatible
            origin=originals[path]
            if kind in origin.get('extra_kinds',[]) and not row.get('confirm_extra_materials'):raise ValueError('新增材料需要在确认窗口明确确认补充，不会替代已有的指定格式材料。')
            profile=next((p for p in plan['profiles'] if p['name']==row['package']),{})
            families=origin.get('existing_families',{}) if origin.get('existing') and normalized(name)==normalized(origin['existing']['name']) else profile.get('family_map',{})
            if origin.get('existing') and normalized(name)==normalized(origin['existing']['name']):families={**families,**origin.get('extra_families',{})}
            if kind not in kinds or not compatible(kind,path,families):raise ValueError('请选择与文件类型相符的材料项；已有规则仍保留原格式限制。')
            origin=originals[path];key=(origin.get('batch_key',''),normalized(name))
            label=row.get('batch',origin['batch_label']) if plan.get('split_batches') else batch.strip()
            group=groups.setdefault(key,dict(commission='',batch=label,name=name,category='',kinds=kinds,package=row['package'],source_files=[],source_kinds={},processing_mode=mode))
            patterns=origin.get('existing_patterns',{}) if origin.get('existing') and normalized(name)==normalized(origin['existing']['name']) else profile.get('name_patterns',{})
            group['family_map']=families;group['name_patterns']={**patterns,**origin.get('extra_patterns',{})}
            if origin.get('independent_new_rule'):group['independent_new_rule']=True
            if origin.get('extra_kinds') and origin.get('existing') and normalized(name)==normalized(origin['existing']['name']):group['extend_base']=origin['existing_base_kinds'];group['extra_kinds']=origin['extra_kinds']
            if group['kinds']!=kinds or group['package']!=row['package']:raise ValueError('同一软件的文件请选择相同分类。')
            if kind in group['source_kinds'].values():raise ValueError(f'「{name}」有多份同类型材料，请只保留一份，或分别填写不同软件名称。')
            group['source_files'].append(path);group['source_kinds'][path]=kind
            if row.get('storage'):
                from intake_storage import validate
                spec=validate(self.service,row['storage'])
                if group.get('storage') and group['storage']['directory']!=spec['directory']:raise ValueError('同一软件请选择相同的保存文件夹。')
                for previous_path,previous_kind in group['source_kinds'].items():
                    if previous_path!=path and group.get('storage_names',{}).get(previous_kind,'').casefold()==spec['base'].casefold() and Path(previous_path).suffix.casefold()==Path(path).suffix.casefold():
                        raise ValueError('同一文件夹内有重名文件，请修改保存文件名以区分材料。')
                group['storage']=spec;group.setdefault('storage_names',{})[kind]=spec['base']
        for group in groups.values():
            if group.get('extra_kinds'):
                selected=[k for k in group['extra_kinds'] if k in group['source_kinds'].values()]
                group['extra_kinds']=selected;group['kinds']=group['extend_base']+selected
                group['family_map']={k:v for k,v in group['family_map'].items() if k in group['kinds']};group['name_patterns']={k:v for k,v in group['name_patterns'].items() if k in group['kinds']}
        from requirement_intake import validate_rows
        return validate_rows(list(groups.values()))

    def confirm(self,plan,rows,batch,mode,*,decisions=None,expected=None,cancel_event=None,progress=None):
        token=plan['token']
        with self.service.db.connect() as c:
            if c.execute('SELECT 1 FROM production_imports WHERE token=?',(token,)).fetchone():
                result=self.service.production.retry_import_files(token,cancel_event=cancel_event,progress=progress)
                result['material_intake']=True;return result
        grouped=self.validate(plan,rows,batch,mode)
        chosen={r['path'] for r in rows};required_sources=set(chosen)
        required_sources.update(e['container'] for e in plan['entries'] if e['path'] in chosen and e.get('container'))
        for path,stamp in plan['sources'].items():
            if path not in required_sources:continue
            cancelled(cancel_event)
            if identity(path)!=stamp:raise ValueError('来源文件在核对期间发生变化，请重新拖入材料。')
        from duplicate_intake import DuplicateIntake
        duplicates=DuplicateIntake(self.service);current=self.service.production.rows();conflicts=[];snapshots={}
        targets=set()
        for row in grouped:
            candidates=[r for r in current if normalized(r['name'])==normalized(row['name'])]
            if not candidates or row.get('independent_new_rule'):
                if row['package']!='自定义':
                    profile=next((p for p in plan['profiles'] if p['name']==row['package']),None)
                    now=next((p for p in self.profiles() if p['name']==row['package']),None)
                    if not profile or now!=profile and not (profile.get('_transient') and now is None):raise ValueError('材料清单已修改或停用，请重新拖入核对。')
                    if row['kinds']!=profile['kinds']:raise ValueError('分类清单不一致，请重新核对。')
                continue
            key=(normalized(row['batch'])+'\x1f' if plan.get('split_batches') else '')+normalized(row['name']);snapshot=duplicates._snapshot(candidates)
            snapshots[key]=snapshot
            if expected and expected.get(key)!=snapshot:raise ValueError('已有任务在核对期间发生变化，请重新拖入核对。')
            conflict=len(candidates)>1 or any(duplicates._has_existing(dict(source_path=p,material_kind=k),r) for p,k in row['source_kinds'].items() for r in candidates)
            if conflict and not decisions:
                conflicts.append(dict(key=key,name=row['name'],candidates=candidates,files=row['source_files'],snapshot=snapshot));continue
            choice=(decisions or {}).get(key) if conflict else dict(action='overwrite',project_id=candidates[0]['id'])
            if not choice or choice.get('action') not in ('new','overwrite','replace','skip') or choice.get('project_id') not in [r['id'] for r in candidates]:raise ValueError('请选择保存新版本、直接覆盖、另存副本或跳过。')
            if choice['action']=='skip':
                row['skip_intake']=True
                continue
            target=next(r for r in candidates if r['id']==choice['project_id']);kinds=[m['kind'] for m in target['materials']]
            with self.service.db.connect() as c:
                snap=c.execute('SELECT aliases FROM task_profile_snapshots WHERE project_id=?',(target['id'],)).fetchone()
                if snap:
                    import json
                    row['snapshot_aliases']=json.loads(snap[0])
            if not kinds and target.get('original_zips'):
                profile=next((p for p in self.profiles() if p['name']==row['package'] and p['kinds']==row['kinds']),None)
                if not profile:
                    profile=next((p for p in plan['profiles'] if p['name']==row['package'] and p['kinds']==row['kinds'] and p.get('_transient') and not any(q['name']==p['name'] for q in self.profiles())),None)
                if not profile and row['package']!='自定义':raise ValueError('请为原包任务选择材料分类，或选择“按现有文件收录”。')
                if profile and profile.get('_transient'):row['confirmed_transient_profile']=dict(name=profile['name'],kinds=profile['kinds'])
                if choice['action'] in ('overwrite','replace'):row['initialize_empty_task']=True
            else:
                extras=row.get('extra_kinds',[])
                if extras and row.get('extend_base')!=kinds:raise ValueError('任务材料清单已变化，请重新拖入确认新增材料。')
                if any(k not in kinds+extras for k in row['source_kinds'].values()):raise ValueError('材料类型不在已有任务清单中，请先修改该任务的材料要求。')
                row['kinds']=kinds+extras;row['package']=target['package_name']
            row['commission']=target['commission'];row['category']=target['category']
            if choice['action'] in ('overwrite','replace'):
                if target['id'] in targets:raise ValueError('多个批次不能同时覆盖同一已有任务，请分别收录或选择新建。')
                targets.add(target['id']);row['target_project_id']=target['id'];row['replace_current']=choice['action']=='replace'
                row['replace_expected']={m['kind']:m.get('id') or 0 for m in target['materials']}
        if conflicts:
            resume=dict(direct_drop=True,direct_plan=plan,direct_rows=rows,batch=batch,mode=mode,
                        snapshots=snapshots)
            return dict(material_conflicts=conflicts,material_plan=resume)
        grouped=[r for r in grouped if not r.get('skip_intake')]
        if not grouped:
            return dict(material_intake=True,copied_count=0,skipped_count=len(rows),project_ids=[],created_project_ids=[],batch_ids=[],intake_errors=[])
        result=self.service.production.import_plan(grouped,token=token,source_text='直接拖入材料，核对名称和分类后收录',cancel_event=cancel_event,progress=progress)
        result['material_intake']=True
        result['skipped_archives']=plan.get('skipped_archives',[])
        return result
