"""Create a separate intake rule and folder binding in one transaction."""
from pathlib import Path
import json

MESSAGE='现有收纳规则无法覆盖，请新建规则并选择保存文件夹。'
ACTION='新建规则并选择文件夹'


def mapping(profile, entries, software):
    from file_families import infer
    from name_patterns import assign_types
    rows=[dict(e,name=software) for e in entries]
    result={e['path']:infer(Path(e['path']).name,profile['kinds'],profile.get('family_map'),
        profile.get('aliases'),software,profile.get('name_patterns')) for e in rows}
    if any(p.get('mode')=='type' for p in profile.get('name_patterns',{}).values()):
        assigned=assign_types(profile,rows)
        for path,kind in assigned.items():
            if kind:result[path]=kind
    return result


def draft(service, entries):
    from name_patterns import suggest,stem_of,keyword_terms,SOFTWARE_TOKEN
    import unicodedata
    result,assigned=suggest(entries)
    # Show the literal suffix the user dragged in (e.g. 源程序), rather
    # than displaying a canonical alias such as 代码 in the new keyword field.
    for entry in entries:
        kind=assigned[entry['path']];stem=stem_of(entry['path']);software=unicodedata.normalize('NFKC',entry['name']).strip()
        if software and stem.casefold().startswith(software.casefold()):
            tail=stem[len(software):].strip(' _-—·')
            if tail:result['name_patterns'][kind]=dict(mode='keywords',terms=[SOFTWARE_TOKEN,*keyword_terms(tail)])
    names={p['name'].casefold() for p in service.classifications.profiles(False,include_archives=True)}
    base=result['name'];name=base;index=2
    while name.casefold() in names:name=base+'（新规则'+str(index)+'）';index+=1
    result['name']=name
    return result


def save(service, rule, folder):
    if not folder:raise ValueError('请先为新规则选择保存文件夹。')
    from folder_management import checked_path,folders,MANAGED_SCHEMA
    path=checked_path(service,folder)
    if not path.is_dir():raise ValueError('保存文件夹不存在，请重新选择。')
    folders(service)  # Initialize folder tables before the transaction.
    from native_files import plain_path
    with service.db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute('SELECT root,name FROM intake_folder_presets'):
            if plain_path(Path(row['root'])/row['name'])==path:
                raise ValueError('此文件夹已绑定规则，请选择其他文件夹，原绑定保持不变。')
        ident=service.classifications.save(rule['name'],rule['kinds'],mode='collect',
            intake_mode=rule.get('intake_mode','materials'),extensions=rule.get('extensions'),
            family_map=rule.get('family_map',{}),name_patterns=rule.get('name_patterns',{}),connection=c)
        profile=dict(c.execute('SELECT * FROM material_profiles WHERE id=?',(ident,)).fetchone())
        for field in ('kinds','aliases'):profile[field]=json.loads(profile[field])
        profile.update(id=ident,intake_mode=rule.get('intake_mode','materials'),extensions=rule.get('extensions',[]),
                       family_map=rule.get('family_map',{}),name_patterns=rule.get('name_patterns',{}))
        c.execute(MANAGED_SCHEMA)
        c.execute('INSERT OR IGNORE INTO managed_collection_folders VALUES (?)',(str(path),))
        c.execute('INSERT INTO intake_folder_presets(root,name,rule_json) VALUES (?,?,?)',
                  (str(path.parent),path.name,json.dumps(profile,ensure_ascii=False)))
    return next(p for p in service.classifications.profiles(True,include_archives=True) if p['id']==ident)
