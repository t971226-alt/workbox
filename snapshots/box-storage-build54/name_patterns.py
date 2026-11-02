"""Explicit reusable filename patterns. Labels never become hidden wildcards."""
from pathlib import Path
import re,json,unicodedata
from file_families import TYPES,family_for,compatible

SCHEMA='''CREATE TABLE IF NOT EXISTS profile_name_patterns(
 profile_id INTEGER NOT NULL REFERENCES material_profiles(id),kind TEXT NOT NULL,pattern TEXT NOT NULL,
 PRIMARY KEY(profile_id,kind));
CREATE TABLE IF NOT EXISTS task_name_patterns(
 project_id INTEGER NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,pattern TEXT NOT NULL,
 PRIMARY KEY(project_id,kind));'''
TITLES={'word':'Word','sheet':'表格','slides':'演示文稿','pdf':'PDF','text':'文本','archive':'压缩包','image':'图片','video':'视频','audio':'音频'}
SOFTWARE_TOKEN='{software}'


def keyword_terms(value):
    words=re.split(r'[，,+\s]+',unicodedata.normalize('NFKC',value).strip())
    return list(dict.fromkeys(SOFTWARE_TOKEN if w in ('软件名称','当前软件名',SOFTWARE_TOKEN) else w for w in words if w))


def keyword_norm(value):return re.sub(r'[\s_\-—·]+','',unicodedata.normalize('NFKC',value)).casefold()


def keywords_match(filename,software,terms):
    stem=keyword_norm(stem_of(filename))
    return all(bool(software if t==SOFTWARE_TOKEN else t) and keyword_norm(software if t==SOFTWARE_TOKEN else t) in stem for t in terms)

def norm(value):
    return re.sub(r'[\s_\-—·]+',' ',unicodedata.normalize('NFKC',value)).strip().casefold()

def marker(value):
    value=unicodedata.normalize('NFKC',value).strip(' _-—·')
    # Language decorations are optional only on an explicit code marker.
    code=re.fullmatch(r'(?:代码|代码文档|代码文件|源代码|源程序|软件著作权代码段)\s*(?:\([A-Za-z0-9+#./ _-]+\))?',value,re.I)
    return '代码' if code else norm(value)

def stem_of(filename):
    return re.sub(r'\s+\([1-9]\d*\)$','',unicodedata.normalize('NFKC',Path(filename).stem)).strip()

def placeholder(label):
    value=label.strip()
    for token in ['{软件名称}','软件名称']:
        if value.startswith(token):
            rest=value[len(token):].strip(' _-—·')
            return dict(mode='marker',marker=marker(rest)) if rest else dict(mode='same')
    return None

def validate(kinds,patterns):
    if not isinstance(patterns,dict):raise ValueError('文件名识别规则无效。')
    seen=set()
    for k,p in patterns.items():
        if k not in kinds or not isinstance(p,dict) or p.get('mode') not in ('same','marker','manual','type','keywords'):raise ValueError('请选择有效的文件名识别方式。')
        if p['mode']=='keywords':
            terms=p.get('terms')
            if not isinstance(terms,list) or len(terms)>12 or any(not isinstance(t,str) or not t.strip() or len(t)>100 or t!=SOFTWARE_TOKEN and any(x in t for x in '/\\<>:"|?*{}') for t in terms):raise ValueError('请填写有效关键词，最多12个；用逗号、空格或 + 分隔。')
        if p['mode']=='marker' and (not isinstance(p.get('marker'),str) or not 1<=len(p['marker'].strip())<=60 or any(x in p['marker'] for x in '/\\<>:"|?*{}')):raise ValueError('请填写简短文件标记，例如代码、演示、统计表。')
        if p['mode'] not in ('manual','type'):
            key=(family_for(Path(k).suffix) or Path(k).suffix.casefold(),p['mode'],tuple(sorted(set(keyword_norm(t) for t in p['terms']))) if p['mode']=='keywords' else marker(p.get('marker','')))
            if key in seen:raise ValueError('同类型材料的识别条件重复，请合并或改为手动对应。')
            seen.add(key)
    return patterns

def read(c,table,column,key):
    return {r[0]:json.loads(r[1]) for r in c.execute('SELECT kind,pattern FROM '+table+' WHERE '+column+'=?',(key,))}

def match(filename,software,kinds,families,patterns):
    if not software:return None
    stem=norm(stem_of(filename));base=norm(software);found=[];typed=[]
    for k,p in patterns.items():
        if k not in kinds or not compatible(k,filename,families):continue
        if p['mode']=='type':typed.append(k)
        if p['mode']=='keywords' and keywords_match(filename,software,p['terms']):found.append(k)
        if p['mode']=='same' and stem==base:found.append(k)
        if p['mode']=='marker':
            # The software prefix is exact after Unicode/separator normalization.
            rest=stem[len(base):].strip() if stem.startswith(base) else None
            if rest and marker(rest)==marker(p['marker']):found.append(k)
            elif marker(stem)==marker(p['marker']):found.append(k)
    return found[0] if len(found)==1 else None if found else typed[0] if len(typed)==1 else None

def bind_type(c,pid,kind,filename):
    """Remember a received type slot's identity in this task, never its preset."""
    row=c.execute('SELECT pattern FROM task_name_patterns WHERE project_id=? AND kind=?',(pid,kind)).fetchone()
    if not row or json.loads(row[0]).get('mode')!='type':return
    software=c.execute('SELECT name FROM projects WHERE id=?',(pid,)).fetchone()[0]
    stem=norm(stem_of(filename));base=norm(software)
    if stem==base:p=dict(mode='same')
    else:
        tag=marker(stem[len(base):].strip()) if stem.startswith(base) else marker(stem_of(filename))
        if not 1<=len(tag)<=60 or any(x in tag for x in '/\\<>:"|?*{}'):return
        p=dict(mode='marker',marker=tag)
    c.execute('UPDATE task_name_patterns SET pattern=? WHERE project_id=? AND kind=?',(json.dumps(p,ensure_ascii=False),pid,kind))

def assign_types(profile,entries):
    """Distinct stems consume distinct type slots; suffix variants share one."""
    from file_families import infer
    kinds=profile['kinds'];families=profile.get('family_map',{});patterns=profile.get('name_patterns',{})
    typed=[k for k,p in patterns.items() if p.get('mode')=='type'];used=set();identities={};result={}
    for e in entries:
        k=infer(Path(e['path']).name,kinds,families,profile.get('aliases'),e['name'],patterns)
        if k and k not in typed:result[e['path']]=k;used.add(k)
    for e in entries:
        if e['path'] in result:continue
        options=[k for k in typed if compatible(k,e['path'],families)]
        family=next((families.get(k) for k in options),None)
        identity=(norm(stem_of(e['path'])),family or Path(e['path']).suffix.casefold())
        selected=identities.get(identity) or next((k for k in options if k not in used),None)
        result[e['path']]=selected
        if selected:identities[identity]=selected;used.add(selected)
    return result

def software_name(path,fallback,parents=(),role_known=False):
    stem=Path(path).stem
    for parent in parents:
        if norm(stem)==norm(parent) or norm(stem).startswith(norm(parent)+' '):return parent
    if fallback and (role_known and norm(stem)!=norm(fallback) or norm(stem)==norm(fallback) and '_' not in stem):return fallback
    if '_' in stem:
        prefix=stem.split('_',1)[0].strip()
        if re.search(r'系统|软件|平台|工具|助手|程序|应用',prefix):return prefix
    match=re.fullmatch(r'(.+(?:系统|软件|平台|工具|助手|程序|应用)(?:\s*[vV]?\d+(?:\.\d+)*)?)[ _\-—·]+.+',stem)
    if match:return match[1].strip()
    if '_' in stem:
        prefix=stem.rsplit('_',1)[0].strip()
        if re.search(r'系统|软件|平台|工具|助手|程序|应用',prefix):return prefix
    # A recognisable software title anchors the prefix; arbitrary filenames
    # remain visible for manual confirmation rather than merging by first word.
    match=re.fullmatch(r'(.+(?:系统|软件|平台|工具|助手|程序|应用)(?:\s*[vV]?\d+(?:\.\d+)*)?)[ _\-—·]+.+',stem)
    return match[1].strip() if match else fallback

def suggest(entries):
    kinds=[];families={};patterns={};mapping={}
    for e in entries:
        suffix=Path(e['path']).suffix.casefold();family=family_for(suffix);title=TITLES.get(family,suffix.lstrip('.').upper() or '文件')
        stem=norm(stem_of(e['path']));base=norm(e['name'])
        if stem==base:pattern=dict(mode='same');label='同名 '+title
        elif base and stem.startswith(base) and stem[len(base):].strip():
            tag=marker(stem[len(base):].strip());pattern=dict(mode='marker',marker=tag);label=tag+' '+title
        else:
            # Established canonical roles (e.g. a bare 软件说明书.docx) are useful
            # marker patterns too. Completely unknown files keep an exact marker.
            tag=marker(stem_of(e['path']));pattern=dict(mode='marker',marker=tag);label=tag+' '+title
        if pattern['mode']=='marker' and (len(pattern['marker'])>60 or any(x in pattern['marker'] for x in '/\\<>:"|?*{}')):
            pattern=dict(mode='manual');label=title+' 材料 '+str(len(kinds)+1)
        if len(label)>65:label=label[:65]
        ext=TYPES[family][1] if family else suffix or '.file'
        if not suffix:pattern=dict(mode='manual');label='无后缀文件'
        kind=label+ext
        if kind not in kinds:kinds.append(kind)
        if family:families[kind]=family
        patterns[kind]=pattern;mapping[e['path']]=kind
    validate(kinds,patterns)
    name='＋'.join(Path(k).stem for k in kinds)
    if len(name)>38:name='本次'+str(len(kinds))+'类材料'
    return dict(name=name,kinds=kinds,family_map=families,name_patterns=patterns),mapping
