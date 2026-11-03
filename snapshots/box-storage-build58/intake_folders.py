"""Independent folder defaults. Existing task destinations are never migrated."""
from pathlib import Path
from copy import deepcopy
import json
from intake_storage import clean_name,validate as validate_storage

SCHEMA='''CREATE TABLE IF NOT EXISTS intake_folder_presets(
 id INTEGER PRIMARY KEY,root TEXT NOT NULL,name TEXT NOT NULL,rule_json TEXT NOT NULL,
 UNIQUE(root,name COLLATE NOCASE));'''

LABELS={'单手册 Word':'Word 文档（1份）','五件材料':'说明书＋代码＋登记信息（5份）',
        '桌面14件':'软件交付材料（14份）','14件＋双视频':'软件交付材料＋视频（16份）',
        '压缩包收纳':'压缩包（1份）'}
FOLDERS={'单手册 Word':'Word文档','五件材料':'说明书与代码资料','桌面14件':'软件交付材料',
         '14件＋双视频':'软件交付与视频','压缩包收纳':'软件运行包','软件运行包':'软件运行包'}

def title(name):return LABELS.get(name,name)

def type_info(key):
    from file_families import TYPES
    import re
    if key in TYPES:return TYPES[key][0],TYPES[key][1]
    if isinstance(key,str) and re.fullmatch(r'\.[a-z0-9]{1,12}',key):return key[1:].upper()+' 文件',key
    raise ValueError('请选择有效的文件类型。')

def folder_name(profile):
    if profile.get('intake_mode')=='original':return '软件运行包'
    return FOLDERS.get(profile['name'],clean_name(profile['name'],'文件夹名称'))

def records(service,root=None):
    from native_files import plain_path
    with service.db.connect() as c:
        c.executescript(SCHEMA)
        query='SELECT * FROM intake_folder_presets';args=()
        if root is not None:query+=' WHERE root=? COLLATE NOCASE';args=(str(plain_path(root)),)
        return [dict(id='saved:'+str(r['id']),root=r['root'],name=r['name'],rule=json.loads(r['rule_json']))
                for r in c.execute(query+' ORDER BY id',args)]


def same_rule(a,b):
    if a.get('id') and b.get('id'):return a['id']==b['id']
    return a['name'].strip().casefold()==b['name'].strip().casefold()


def binding_matches(service,profile):
    return [r for r in records(service) if same_rule(r['rule'],profile)]


def routing_profiles(service,profiles):
    result=list(profiles);names={p['name'].casefold() for p in result}
    registered=service.classifications.profiles(False,include_archives=True)
    registered_names={p['name'].casefold() for p in registered}
    for record in records(service):
        p=deepcopy(record['rule'])
        if p['name'].casefold() in names or p.get('intake_mode')=='original':continue
        if p.get('id') or p['name'].casefold() in registered_names:continue
        service.classifications.validate(p['name'],p['kinds'],mode='collect')
        p.update(_transient=True,_folder_rule=True,aliases=p.get('aliases',{}),mode='collect',active=1)
        result.append(p);names.add(p['name'].casefold())
    return result

def validate_records(service,root,items):
    from native_files import plain_path
    root=plain_path(root);seen=set();result=[]
    for item in items:
        item_root=plain_path(item.get('root',root))
        name=clean_name(item['name'],'文件夹名称');key=(str(item_root).casefold(),name.casefold())
        if key in seen:raise ValueError('保存文件夹名称重复，请修改名称。')
        seen.add(key);rule=deepcopy(item['rule'])
        with service.db.connect() as c:
            c.executescript(SCHEMA)
            previous=c.execute('SELECT id,rule_json FROM intake_folder_presets WHERE root=? COLLATE NOCASE AND name=? COLLATE NOCASE',(str(item_root),name)).fetchone()
        if previous and item.get('id')!='saved:'+str(previous['id']) and not same_rule(json.loads(previous['rule_json']),rule):raise ValueError('该目录已绑定其他规则，请选择别的目录或明确更换绑定。')
        service.classifications.validate(rule['name'],rule['kinds'],mode='collect')
        from file_families import validate
        from name_patterns import validate as patterns
        validate(rule['kinds'],rule.get('family_map',{}));patterns(rule['kinds'],rule.get('name_patterns',{}))
        validate_storage(service,dict(root=str(item_root),folder=name,base='软件名称'))
        result.append(dict(id=item.get('id'),root=str(item_root),name=name,rule=rule))
    return dict(root=str(root),items=result)

def commit(service,snapshot):
    """Called after successful intake only; cancelled previews leave no settings."""
    value=validate_records(service,snapshot['root'],snapshot['items'])
    profiles={p['name'].casefold():p for p in service.classifications.profiles(False,include_archives=True)}
    for item in value['items']:
        current=profiles.get(item['rule']['name'].casefold())
        if current:item['rule']['id']=current['id']
    with service.db.connect() as c:
        c.executescript(SCHEMA)
        for item in value['items']:
            item_root=item.get('root',value['root'])
            if isinstance(item.get('id'),str) and item['id'].startswith('saved:'):
                ident=int(item['id'].split(':')[1]);old=c.execute('SELECT root FROM intake_folder_presets WHERE id=?',(ident,)).fetchone()
                if old and old[0].casefold()==item_root.casefold():
                    c.execute('UPDATE intake_folder_presets SET name=?,rule_json=? WHERE id=?',(item['name'],json.dumps(item['rule'],ensure_ascii=False),ident));continue
            c.execute('''INSERT INTO intake_folder_presets(root,name,rule_json) VALUES (?,?,?)
                         ON CONFLICT(root,name) DO UPDATE SET rule_json=excluded.rule_json''',
                      (item_root,item['name'],json.dumps(item['rule'],ensure_ascii=False)))

def type_draft(name,counts,base=None):
    """A type can require several logical files, with original suffixes retained."""
    from file_families import TYPES
    draft=deepcopy(base) if base else dict(kinds=[],family_map={},name_patterns={})
    draft={k:draft.get(k,{ } if k!='kinds' else []) for k in ('kinds','family_map','name_patterns')}
    draft['name']=name.strip()
    for family,count in counts.items():
        if type(count) is not int or not 1<=count<=40:raise ValueError('请选择文件类型和有效份数。')
        label,ext=type_info(family)
        for n in range(1,count+1):
            number=n;kind=label+' '+str(number)+ext
            while kind in draft['kinds']:number+=1;kind=label+' '+str(number)+ext
            draft['kinds'].append(kind)
            if family in TYPES:draft['family_map'][kind]=family
            draft['name_patterns'][kind]=dict(mode='type')
    if not draft['kinds'] or len(draft['kinds'])>40:raise ValueError('请添加 1 至 40 份所需文件。')
    return draft
