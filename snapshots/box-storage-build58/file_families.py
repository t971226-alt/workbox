"""Logical file types are rule options; legacy requirements stay suffix-exact."""
from pathlib import Path
from file_formats import DOCUMENTS,SHEETS,SLIDES,TEXT,IMAGES,VIDEOS,AUDIO,ARCHIVES

TYPES={
 'word':('Word 文档','.docx',DOCUMENTS),'sheet':('Excel 表格','.xlsx',SHEETS),
 'slides':('PPT 演示','.pptx',SLIDES),'pdf':('PDF','.pdf',{'.pdf'}),
 'text':('文本','.txt',TEXT),'archive':('压缩包','.zip',ARCHIVES),
 'image':('图片','.png',IMAGES),'video':('视频','.mp4',VIDEOS),'audio':('音频','.mp3',AUDIO)}
SCHEMA='''CREATE TABLE IF NOT EXISTS profile_file_families(
 profile_id INTEGER NOT NULL REFERENCES material_profiles(id),kind TEXT NOT NULL,family TEXT NOT NULL,
 PRIMARY KEY(profile_id,kind));
CREATE TABLE IF NOT EXISTS task_file_families(
 project_id INTEGER NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,family TEXT NOT NULL,
 PRIMARY KEY(project_id,kind));'''


def family_for(ext):return next((key for key,(_,_,exts) in TYPES.items() if ext.casefold() in exts),None)
def type_title(ext):
    key=family_for(ext);return TYPES[key][0] if key else ext.upper()
def compatible(kind,path,families=None):
    ext=Path(path).suffix.casefold();family=(families or {}).get(kind)
    return ext in TYPES[family][2] if family in TYPES else ext==Path(kind).suffix.casefold()
def validate(kinds,families):
    if not isinstance(families,dict) or any(k not in kinds or f not in TYPES or Path(k).suffix.casefold() not in TYPES[f][2] for k,f in families.items()):
        raise ValueError('材料类型规则无效，请重新选择收录类型。')
    seen=set()
    for kind in kinds:
        key=(Path(kind).stem.strip().casefold(),families.get(kind,Path(kind).suffix.casefold()))
        if key in seen:raise ValueError('同一材料的同类格式只需一项，请移除重复项。')
        seen.add(key)
    return families
def task_families(c,pid):return {r[0]:r[1] for r in c.execute('SELECT kind,family FROM task_file_families WHERE project_id=?',(pid,))}


def infer(filename,kinds,families=None,aliases=None,project_name=None,patterns=None):
    from material_naming import candidates
    if patterns:
        from name_patterns import match
        found=match(filename,project_name,kinds,families,patterns)
        if found:return found
        kinds=[k for k in kinds if k not in patterns]
    families=families or {};virtual=[];mapping={};adapted={}
    for kind in kinds:
        if not compatible(kind,filename,families):continue
        name=Path(kind).stem+Path(filename).suffix
        virtual.append(name);mapping.setdefault(name,[]).append(kind);adapted[name]=(aliases or {}).get(kind,[])
    matches={kind for _,name in candidates(filename,virtual,adapted,project_name) for kind in mapping.get(name,[])}
    if len(matches)==1:return next(iter(matches))
    if not matches and project_name and Path(filename).stem.casefold()==project_name.casefold() and len(virtual)==1:return mapping[virtual[0]][0]
    return None


def matching_options(filename,kinds,families=None):return [kind for kind in kinds if compatible(kind,filename,families)]


def coalesce(kinds):
    result=[];families={};seen=set()
    for kind in kinds:
        ext=Path(kind).suffix;family=family_for(ext);stem=Path(kind).stem
        key=(stem.casefold(),family or ext)
        if key in seen:continue
        seen.add(key);canonical=stem+(TYPES[family][1] if family else ext);result.append(canonical)
        if family:families[canonical]=family
    return result,families
