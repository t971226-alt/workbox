"""Conservative, shared material suffix recognition for new and existing tasks."""
import re
import unicodedata
from pathlib import Path

ALIASES = {
    '软件说明书': ('说明书', '说明手册', '操作手册', '用户手册'),
    '软件著作权代码段': ('源代码', '代码', '代码段', '源程序'),
    '登记申请信息': ('软件信息', '信息表'),
    '软件操作演示': ('软件演示',),
    '数据库演示': ('SQLite数据演示', '数据库视频'),
}

def norm(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()

def candidates(filename, kinds, aliases=None, project_name=None):
    source=Path(filename);suffix=source.suffix.casefold();stem=source.stem.strip()
    # Browser duplicate suffixes do not belong to the software name.
    stem=re.sub(r'\s+\([1-9]\d*\)$','',stem)
    aliases=aliases or {};found=[]
    for kind in kinds:
        if Path(kind).suffix and Path(kind).suffix.casefold()!=suffix:continue
        role=Path(kind).stem
        material_stem=re.sub(r'\s*[（(][A-Za-z0-9+#./ -]+[)）]$','',stem) if role=='软件著作权代码段' else stem
        for term in (role,*ALIASES.get(role,()),*aliases.get(kind,())):
            value=norm(material_stem);key=norm(term);name=None
            if not key:continue
            if value==key:name=''
            else:
                match=re.fullmatch(r'(.+?)[ _\-—·.]+?'+re.escape(term),material_stem,re.I)
                if match:name=match[1].strip()
                # Common Chinese file names omit a separator: 系统软件说明书.docx.
                elif value.endswith(key) and re.search(r'[\u3400-\u9fff]',term):
                    prefix=material_stem[:-len(term)].rstrip(' _-—·.')
                    if prefix:name=prefix
                if name is None and role in ('软件操作演示','数据库演示'):
                    match=re.fullmatch(re.escape(term)+r'[ _\-—·.]+(.+)',stem,re.I)
                    if match:name=match[1].strip()
            if name is not None:found.append((len(key),name,kind))
    if found:
        longest=max(item[0] for item in found)
        return list(dict.fromkeys((name,kind) for length,name,kind in found if length==longest))
    if project_name and norm(stem)==norm(project_name):
        return [(stem,k) for k in kinds if Path(k).suffix.casefold()==suffix and Path(k).stem in ('软件说明书','登记申请信息')]
    return []
