"""Archive rules describe alternatives; ordinary requirement lists remain exact."""
from pathlib import Path
import json
from file_formats import ARCHIVES


def suggested_name(service,kinds):
    from file_formats import DOCUMENTS,SHEETS,SLIDES,IMAGES
    kinds=list(dict.fromkeys(kinds));extensions={Path(k).suffix.casefold() for k in kinds}
    if len(kinds)==1:
        ext=next(iter(extensions));base='单份文档' if ext in DOCUMENTS else '单份表格' if ext in SHEETS else '单份演示文稿' if ext in SLIDES else '单份图片' if ext in IMAGES else '单份 PDF' if ext=='.pdf' else '单份材料'
    else:base='本次'+str(len(kinds))+'份材料'
    names={r['name'].casefold() for r in service.classifications.profiles(include_archives=True)}
    name=base;index=2
    while name.casefold() in names:name=base+'（'+str(index)+'）';index+=1
    return name


def match_unknown_formats(group,profiles):
    """Suggest a unique role for bare filenames, with confirmation still required."""
    unknown=[e for e in group if e['kind']==Path(e['path']).name]
    if not unknown:return None
    from collections import Counter
    counts=Counter(Path(e['path']).suffix.casefold() for e in unknown)
    if any(n>1 for n in counts.values()):return None
    found=[]
    for profile in profiles:
        mapped={};valid=True
        for e in group:
            if e['kind'] in profile['kinds']:mapped[e['path']]=e['kind'];continue
            if e not in unknown:valid=False;break
            roles=[kind for kind in profile['kinds'] if Path(kind).suffix.casefold()==Path(e['path']).suffix.casefold()]
            if len(roles)!=1:valid=False;break
            mapped[e['path']]=roles[0]
        if valid:found.append((profile,mapped))
    exact=[item for item in found if len(item[0]['kinds'])==len(set(item[1].values()))]
    choices=exact or found
    return choices[0] if len(choices)==1 else None


def archive_rules(service):
    return [r for r in service.classifications.profiles(True,include_archives=True) if r['intake_mode']=='original']


def validate_archive_rule(name,extensions):
    from classification_rules import Classifications
    name,_,_=Classifications.validate(name,[name.strip()+'.zip'],mode='collect')
    extensions=list(dict.fromkeys(extensions))
    if not extensions or any(ext not in ARCHIVES for ext in extensions):raise ValueError('请选择 ZIP、7z 或 RAR 格式。')
    return dict(name=name,extensions=extensions,intake_mode='original',kinds=[name+'.zip'])


def save_archive_rule(c,draft):
    d=validate_archive_rule(draft['name'],draft['extensions'])
    old=c.execute('SELECT p.*,o.intake_mode,o.extensions FROM material_profiles p LEFT JOIN profile_intake_options o ON o.profile_id=p.id WHERE p.name=? COLLATE NOCASE',(d['name'],)).fetchone()
    if old:
        if old['intake_mode']!='original' or set(json.loads(old['extensions']))!=set(d['extensions']) or not old['active']:
            raise ValueError('同名规则已有不同要求或已停用，请换一个规则名称。')
        return old['id']
    pos=c.execute('SELECT COALESCE(MAX(position),0)+1 FROM material_profiles').fetchone()[0]
    pid=c.execute("INSERT INTO material_profiles(name,kinds,mode,position) VALUES (?,?,'collect',?)",(d['name'],json.dumps(d['kinds'],ensure_ascii=False),pos)).lastrowid
    c.execute('INSERT INTO profile_intake_options VALUES (?,?,?)',(pid,'original',json.dumps(d['extensions'])))
    c.execute('INSERT INTO profile_changes(profile_id,before_json,after_json) VALUES (?,\'{}\',?)',(pid,json.dumps(d,ensure_ascii=False)))
    return pid


def raw_directory(service,pid,name,*,create=False):
    """Use a pinned destination across root switches and retries. Never migrate old copies."""
    from material_collection import segment
    from native_files import plain_path
    with service.db.connect() as c:
        from intake_storage import get
        saved=get(c,pid)
        rule=c.execute('SELECT * FROM task_raw_rules WHERE project_id=?',(pid,)).fetchone()
        if saved:directory=plain_path(saved['directory'])
        elif rule:
            directory=plain_path(rule['directory'])
        else:
            previous=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id LIMIT 1',(pid,)).fetchone()
            from intake_storage import available_directory
            directory=plain_path(previous[0]).parent if previous else available_directory(c,pid,Path(service.workspaces.settings()['work_root'])/'ZIP原包'/segment(name))
            if create and not previous:
                # Pin before copying: a saved copy may survive a receipt commit
                # failure and must remain discoverable after its source is gone.
                c.execute("INSERT OR IGNORE INTO task_storage_preferences VALUES (?,?,'{}')",(pid,str(directory)))
    if create:directory.mkdir(parents=True,exist_ok=True)
    return directory


def bind_raw_rule(service,c,pid,name,draft):
    from material_collection import segment
    from native_files import plain_path
    if c.execute('SELECT 1 FROM task_raw_rules WHERE project_id=?',(pid,)).fetchone():return
    # Existing tasks keep their original receipt directory even after an upgrade.
    previous=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id LIMIT 1',(pid,)).fetchone()
    from intake_storage import get
    saved=get(c,pid)
    if saved:directory=plain_path(saved['directory'])
    elif previous:directory=plain_path(previous['path']).parent
    else:
        base=plain_path(Path(service.workspaces.settings()['work_root'])/segment(draft['name'])/segment(name))
        from intake_storage import available_directory
        directory=available_directory(c,pid,base)
    c.execute('INSERT INTO task_raw_rules VALUES (?,?,?,?)',(pid,draft['name'],json.dumps(draft['extensions']),str(directory)))
