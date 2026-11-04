"""Explicit folder registration; existing receipts keep their paths when rules change."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from native_files import plain_path
from intake_storage import clean_name,validate
from intake_folders import SCHEMA,records

MANAGED_SCHEMA='CREATE TABLE IF NOT EXISTS managed_collection_folders(path TEXT PRIMARY KEY COLLATE NOCASE);'


def folders(service):
    bound=records(service)
    with service.db.connect() as c:
        c.execute(MANAGED_SCHEMA)
        paths=[r[0] for r in c.execute('SELECT path FROM managed_collection_folders ORDER BY rowid')]
    result={str(plain_path(Path(r['root'])/r['name'])).casefold():dict(path=str(plain_path(Path(r['root'])/r['name'])),rule=r['rule']) for r in bound}
    for path in paths:result.setdefault(str(plain_path(path)).casefold(),dict(path=str(plain_path(path)),rule=None))
    return list(result.values())


def checked_path(service,path):
    path=plain_path(path)
    if path==Path(path.anchor):raise ValueError('请选择具体文件夹，不能使用整个磁盘。')
    validate(service,dict(root=str(path.parent),folder=path.name,base='软件名称'))
    # A software's receipt directory cannot become a collection root.
    with service.db.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table,column,is_file in [('task_storage_preferences','directory',False),('task_raw_rules','directory',False),('collected_materials','path',True),('original_zip_receipts','path',True)]:
            if table not in tables:continue
            for row in c.execute('SELECT '+column+' FROM '+table):
                if not row[0]:continue
                owned=plain_path(Path(row[0]).parent if is_file else row[0])
                if path==owned or path.is_relative_to(owned):raise ValueError('这是已收录软件的文件夹，请选择上级收纳文件夹。')
    return path


def create(service,parent,name):
    parent=plain_path(parent)
    if not parent.is_dir():raise ValueError('请先选择存在的保存位置。')
    path=checked_path(service,parent/clean_name(name,'新文件夹名称'))
    path.mkdir(exist_ok=False)
    try:
        with service.db.connect() as c:
            c.execute(MANAGED_SCHEMA);c.execute('INSERT INTO managed_collection_folders VALUES (?)',(str(path),))
    except Exception:
        path.rmdir()  # Newly created and still empty; never delete existing content.
        raise
    return path


def bind(service,path,profile):
    path=checked_path(service,path)
    if not path.is_dir():raise ValueError('文件夹不存在，请重新定位。')
    current=next((p for p in service.classifications.profiles(True,include_archives=True) if p['id']==profile['id']),None)
    if not current:raise ValueError('规则已停用或删除，请重新选择。')
    with service.db.connect() as c:
        c.executescript(SCHEMA);c.execute(MANAGED_SCHEMA)
        c.execute('INSERT OR IGNORE INTO managed_collection_folders VALUES (?)',(str(path),))
        c.execute('INSERT INTO intake_folder_presets(root,name,rule_json) VALUES (?,?,?) ON CONFLICT(root,name) DO UPDATE SET rule_json=excluded.rule_json',(str(path.parent),path.name,json.dumps(current,ensure_ascii=False)))


def remove(service,path):
    path=plain_path(path)
    with service.db.connect() as c:
        c.executescript(SCHEMA);c.execute(MANAGED_SCHEMA)
        c.execute('DELETE FROM intake_folder_presets WHERE root=? COLLATE NOCASE AND name=? COLLATE NOCASE',(str(path.parent),path.name))
        c.execute('DELETE FROM managed_collection_folders WHERE path=? COLLATE NOCASE',(str(path),))


def relocate(service,old,new,*,rename=False):
    """Back up the database before an explicit rename/relocation and rewrite receipts."""
    old=plain_path(old);new=checked_path(service,new)
    if old==new:return old
    if new.is_relative_to(old) or old.is_relative_to(new):raise ValueError('新位置不能是原文件夹的上级或子文件夹。')
    if any(plain_path(r['path'])==new for r in folders(service)):raise ValueError('这个位置已经在管理列表中。')
    if rename:
        if not old.is_dir():raise ValueError('原文件夹不存在，请重新定位。')
        if new.exists():raise ValueError('同位置已存在同名文件夹，请更换名称。')
    else:
        if old.exists():raise ValueError('原位置仍然存在，请使用修改名称，避免关联到其他材料。')
        if not new.is_dir():raise ValueError('请选择已移动后的原文件夹。')
        # All referenced saved materials must be present before updating paths.
        with service.db.connect() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('material_versions','original_zip_receipts'):
                if table not in tables:continue
                for row in c.execute('SELECT path,sha256 FROM '+table):
                    p=plain_path(row[0])
                    if p.is_relative_to(old):
                        target=new/p.relative_to(old)
                        if not target.is_file():raise ValueError('所选文件夹缺少已收录材料，未更改记录。')
                        import hashlib
                        with target.open('rb') as stream:
                            if hashlib.file_digest(stream,'sha256').hexdigest()!=row[1]:raise ValueError('所选文件夹中的材料与原收录内容不一致，未更改记录。')
    backup=service.db.path.parent/'管理操作备份'/('文件夹-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.db')
    backup.parent.mkdir(parents=True,exist_ok=True)
    with service.db.connect() as source,sqlite3.connect(backup) as destination:source.backup(destination)
    moved=False
    try:
        if rename:old.rename(new);moved=True
        from unified_storage import _rewrite_references
        with service.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            _rewrite_references(c,[dict(source=str(old),target=str(new))])
            for row in c.execute('SELECT id,root,name FROM intake_folder_presets').fetchall():
                path=plain_path(Path(row[1])/row[2])
                if path.is_relative_to(old):
                    target=new/path.relative_to(old)
                    c.execute('UPDATE intake_folder_presets SET root=?,name=? WHERE id=?',(str(target.parent),target.name,row[0]))
            c.execute(MANAGED_SCHEMA)
            c.execute('UPDATE managed_collection_folders SET path=? WHERE path=? COLLATE NOCASE',(str(new),str(old)))
    except Exception:
        if moved:new.rename(old)
        raise
    return new
