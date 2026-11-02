"""Confirmed output names are task data, independent from classification names."""
from pathlib import Path
import json,re
from native_files import plain_path
from datetime import datetime,timezone,timedelta

SCHEMA='''CREATE TABLE IF NOT EXISTS task_storage_preferences(
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),directory TEXT NOT NULL,
 filenames TEXT NOT NULL DEFAULT '{}');'''


def clean_name(value,label='名称'):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>120:raise ValueError(label+'需要 1 至 120 个字符。')
    value=value.strip()
    if value in ('.','..') or value.endswith(('.', ' ')) or any(c in value for c in '/\\<>:"|?*') or any(ord(c)<32 for c in value) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',value,re.I):raise ValueError(label+'不能包含路径符号或系统保留名称。')
    return value


def validate(service,spec):
    if not isinstance(spec,dict):raise ValueError('请确认保存位置。')
    root=plain_path(spec['root']);folder=clean_name(spec['folder'],'文件夹名称');base=clean_name(spec['base'],'保存文件名')
    # Application data, archives and caches are internal working areas.
    from storage_management import internal_roots
    forbidden=[service.db.path.parent,Path(service.workspaces.settings()['archive_root']),*internal_roots(service)]
    directory=plain_path(root/folder)
    if any(directory==p or directory.is_relative_to(p) or p.is_relative_to(directory) for p in map(plain_path,forbidden)):
        raise ValueError('请选择材料保存目录，不能使用 BOX 内部数据或版本归档目录。')
    return dict(root=str(root),folder=folder,base=base,directory=str(directory))


def bind(service,c,pid,spec,names):
    spec=validate(service,spec);directory=plain_path(spec['directory'])
    old=c.execute('SELECT * FROM task_storage_preferences WHERE project_id=?',(pid,)).fetchone()
    # Never share a task's folder with a different task. Also preserve unrelated
    # files already present at the requested destination.
    directory=available_directory(c,pid,directory)
    merged=json.loads(old['filenames']) if old else {}
    for kind,name in names.items():merged[kind]=clean_name(name,'保存文件名')
    c.execute('INSERT OR REPLACE INTO task_storage_preferences VALUES (?,?,?)',(pid,str(directory),json.dumps(merged,ensure_ascii=False)))
    return str(directory)


def task_time(c,pid):
    value=c.execute('SELECT created_at FROM projects WHERE id=?',(pid,)).fetchone()[0]
    moment=datetime.fromisoformat(value.replace('Z','+00:00'))
    if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')


def available_directory(c,pid,requested):
    """Plain software name first; timestamp only when another task owns that path."""
    requested=plain_path(requested);directory=requested;index=0;owners={}
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table,column,is_file in [('task_storage_preferences','directory',False),('task_raw_rules','directory',False),('collected_materials','path',True),('original_zip_receipts','path',True)]:
        if table not in tables:continue
        for row in c.execute('SELECT project_id,'+column+' FROM '+table):
            p=plain_path(row[1]);p=p.parent if is_file else p
            owners.setdefault(str(p).casefold(),set()).add(row[0])
    while True:
        ids=owners.get(str(directory).casefold(),set())
        occupied=directory.exists() and (not directory.is_dir() or pid not in ids and any(directory.iterdir()))
        if not ids-{pid} and not occupied:return directory
        index+=1
        directory=requested.with_name(requested.name+'_'+task_time(c,pid)+('' if index==1 else '_'+str(index)))


def get(c,pid):
    r=c.execute('SELECT * FROM task_storage_preferences WHERE project_id=?',(pid,)).fetchone()
    return dict(directory=r['directory'],filenames=json.loads(r['filenames'])) if r else None


def basename(software,kind,total):
    return software if total==1 else software+'_'+Path(kind).stem
