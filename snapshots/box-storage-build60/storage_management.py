"""Storage areas and reference-aware cleanup; never treat archives as garbage."""
from pathlib import Path
from contextlib import nullcontext
from threading import RLock
import json,os,time
from native_files import plain_path,ZipLease,cancelled
from file_formats import EXTRACTABLE

INTERNAL='BOX工作文件（自动管理）'
STAGING='导入暂存区'
WORK='材料处理区'
LEGACY_INTERNAL='BOX内部数据'
LEGACY_STAGING='收录暂存'
LEGACY_WORK='任务工作区'
_active=set()
_lock=RLock()


def internal_root(service):return plain_path(Path(service.workspaces.settings()['work_root'])/INTERNAL)
def staging_root(service):return internal_root(service)/STAGING
def work_root(service):return internal_root(service)/WORK


def remember_roots(c,*roots):
    row=c.execute("SELECT value FROM workflow_settings WHERE key='internal_storage_roots'").fetchone()
    values=json.loads(row['value']) if row else []
    for root in roots:
        path=str(plain_path(Path(root)/INTERNAL))
        if path not in values:values.append(path)
    c.execute("INSERT INTO workflow_settings(key,value) VALUES ('internal_storage_roots',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(values,ensure_ascii=False),))


def internal_roots(service):
    with service.db.connect() as c:row=c.execute("SELECT value FROM workflow_settings WHERE key='internal_storage_roots'").fetchone()
    values=[internal_root(service),Path(service.workspaces.settings()['work_root'])/LEGACY_INTERNAL]
    if row:values.extend(Path(p) for p in json.loads(row['value']))
    return list(dict.fromkeys(plain_path(p) for p in values))


def staging_roots(service):return list(dict.fromkeys([*(p/name for p in internal_roots(service) for name in (STAGING,LEGACY_STAGING)),service.db.path.parent/'材料收件缓存']))


def staging_base_for(service,path):
    path=plain_path(path)
    for root in staging_roots(service):
        if path.is_relative_to(root):return root
    raise ValueError('文件不在登记的收录暂存区。')


def hold(path):
    with _lock:_active.add(str(plain_path(path)))


def plan_areas(service,plan):
    areas=set()
    for e in plan.get('entries',[]):
        try:
            path=plain_path(e.get('path') or e.get('source_path'));base=staging_base_for(service,path)
            relative=path.relative_to(base)
            if relative.parts:areas.add(base/relative.parts[0])
        except (TypeError,ValueError,OSError):pass
    return areas


def release_plan(service,plan):
    with _lock:
        for area in plan_areas(service,plan):_active.discard(str(area))


def references(c):
    result=set()
    def add(value):
        if isinstance(value,str):
            if len(value)>2 and (value[1:3] in (':\\',':/') or value.startswith('\\\\')):
                try:result.add(plain_path(value,allow_network=True))
                except (ValueError,OSError):pass
            elif value[:1] in ('{','[','"'):
                try:add(json.loads(value))
                except (ValueError,RecursionError):pass
        elif isinstance(value,dict):
            for k,v in value.items():add(k);add(v)
        elif isinstance(value,list):
            for v in value:add(v)
    for table, in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        quoted='"'+table.replace('"','""')+'"'
        for row in c.execute('SELECT * FROM '+quoted):
            if table=='workflow_settings' and row['key'] in ('internal_storage_roots','work_root','archive_root'):continue
            for value in row:add(value)
    return result


def _files(root,cancel_event=None,exclude=None):
    if not root.exists():return
    root=plain_path(root)
    # Check each entry once; repeatedly checking every ancestor made a small
    # collection needlessly slow on Windows and prevented prompt cancellation.
    import stat
    from native_files import _windows_path
    excluded=set(exclude if isinstance(exclude,(list,tuple,set)) else [exclude])
    pending=[root]
    while pending:
        cancelled(cancel_event);directory=pending.pop()
        with os.scandir(_windows_path(directory) if os.name=='nt' else directory) as entries:
            for entry in entries:
                cancelled(cancel_event);path=directory/entry.name
                if path in excluded:continue
                info=entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('已跳过含目录联接或云占位文件的位置。')
                if stat.S_ISDIR(info.st_mode):pending.append(path)
                elif stat.S_ISREG(info.st_mode):yield path


def cleanup(service,*,roots=None,minimum_age=86400,cancel_event=None,progress=None,dry_run=False):
    """Only unreferenced staging files are eligible, under registered roots."""
    allowed=staging_roots(service);chosen=list(roots or allowed)
    if any(not any(p==a or p.is_relative_to(a) for a in allowed) for p in chosen):raise ValueError('清理位置不在登记的收录暂存区。')
    removed=freed=retained=0;errors=[];now=time.time()
    with getattr(service.db,'_operation_lock',nullcontext()),_lock,service.db.connect() as c:
        c.execute('BEGIN IMMEDIATE');used=references(c)
        for root in chosen:
            cancelled(cancel_event)
            if not root.exists():continue
            try:
                root=plain_path(root);relevant=[p for p in used if p==root or p.is_relative_to(root)]
                for path in _files(root,cancel_event):
                    cancelled(cancel_event)
                    if any(path==p or path.is_relative_to(p) for p in relevant) or any(path.is_relative_to(Path(a)) for a in _active):retained+=1;continue
                    stat=path.stat()
                    if now-stat.st_mtime<minimum_age:retained+=1;continue
                    if not dry_run:
                        with ZipLease(path,movable=True) as lease:
                            if os.fstat(lease.stream.fileno()).st_mtime_ns!=stat.st_mtime_ns:retained+=1;continue
                            lease.remove_after_verified_commit()
                    removed+=1;freed+=stat.st_size
                if not dry_run:
                    for directory,dirs,names in os.walk(root,topdown=False,followlinks=False):
                        p=plain_path(directory)
                        if p!=root:
                            try:p.rmdir()
                            except OSError:pass
            except (ValueError,OSError) as error:errors.append(str(error))
    return dict(storage_cleanup=True,files=removed,bytes=freed,retained=retained,errors=errors,dry_run=dry_run)


def finish_plan(service,plan,*,discard=False):
    areas=plan_areas(service,plan);release_plan(service,plan)
    if discard:
        # Keep the only remaining copy if the outer source was moved away.
        containers=[Path(p) for p in plan.get('sources',{}) if Path(p).suffix.lower() in EXTRACTABLE and not any(Path(p).is_relative_to(a) for a in areas)]
        if not containers or any(not p.is_file() for p in containers):return
    if areas:return cleanup(service,roots=areas,minimum_age=0)


def describe(service,*,cancel_event=None,progress=None):
    from collections import defaultdict
    current=Path(service.workspaces.settings()['work_root']);groups=defaultdict(list)
    groups[STAGING]=staging_roots(service)
    groups[WORK]=[*(p/name for p in internal_roots(service) for name in (WORK,LEGACY_WORK)),service.db.path.parent/'工作缓存']
    groups['归档与历史版本']=[Path(service.workspaces.settings()['archive_root']),service.db.path.parent/'材料历史']
    groups['正式材料']=[current]
    rows=[]
    for name,roots in groups.items():
        size=count=0
        for root in dict.fromkeys(roots):
            try:
                for p in _files(root,cancel_event,exclude=[current/INTERNAL,current/LEGACY_INTERNAL] if name=='正式材料' else None):
                    size+=p.stat().st_size;count+=1
            except (ValueError,OSError):pass
        row=dict(name=name,paths=[str(p) for p in dict.fromkeys(roots)],files=count,bytes=size);rows.append(row)
        if progress:progress(row)
    return rows
