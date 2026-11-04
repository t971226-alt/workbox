"""Local portable release discovery and transactional program-only replacement."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4
import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
import time

from native_files import plain_path, path_is_within, cancelled
from portable_release import APP_ID, VERSION, RELEASE_BUILD, RELEASE_KEY

MANIFEST='BOX-Release.json'
ROOT_ITEMS={'WorkBox.exe','_internal','licenses','LICENSE','THIRD_PARTY_NOTICES.md','PORTABLE.md','SOURCE.zip',MANIFEST}


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with temporary.open('w',encoding='utf-8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    temporary.replace(path)


def key(value):
    parts=value['version'].split('.')
    if len(parts)!=3 or any(not x.isdecimal() for x in parts):raise ValueError('版本号无效。')
    result=tuple(map(int,parts))+(int(value['build']),)
    if any(n<0 or n>65535 for n in result):raise ValueError('版本号超出范围。')
    return result


def relative_file(raw):
    if '\\' in raw or ':' in raw:raise ValueError('更新文件路径无效。')
    p=PurePosixPath(raw)
    if p.as_posix()!=raw or p.is_absolute() or any(v in ('..','.') for v in p.parts) or not p.parts or p.parts[0] not in ROOT_ITEMS:raise ValueError('更新清单不能包含数据或其他目录。')
    if len(p.parts)>1 and p.parts[0] not in ('_internal','licenses'):raise ValueError('更新文件路径无效。')
    return Path(*p.parts)


def pe_identity(executable):
    """Read the language-independent Windows fixed version and WorkBox resource name."""
    if os.name!='nt':return None
    from ctypes import wintypes as w
    api=ctypes.WinDLL('version',use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes=[w.LPCWSTR,ctypes.POINTER(w.DWORD)];api.GetFileVersionInfoSizeW.restype=w.DWORD
    api.GetFileVersionInfoW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID];api.GetFileVersionInfoW.restype=w.BOOL
    api.VerQueryValueW.argtypes=[w.LPCVOID,w.LPCWSTR,ctypes.POINTER(w.LPVOID),ctypes.POINTER(w.UINT)];api.VerQueryValueW.restype=w.BOOL
    ignored=w.DWORD();size=api.GetFileVersionInfoSizeW(str(executable),ctypes.byref(ignored))
    if not size:return None
    data=ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(executable),0,size,data):return None
    def query(name):
        pointer=w.LPVOID();length=w.UINT()
        if not api.VerQueryValueW(data,name,ctypes.byref(pointer),ctypes.byref(length)):return None,0
        return pointer,length.value
    pointer,length=query('\\')
    if not pointer or length<52:return None
    values=ctypes.cast(pointer,ctypes.POINTER(w.DWORD*13)).contents
    if values[0]!=0xFEEF04BD:return None
    translation,length=query('\\VarFileInfo\\Translation')
    if not translation or length<4:return None
    languages=ctypes.cast(translation,ctypes.POINTER(w.WORD*(length//2))).contents
    valid=False
    for i in range(0,len(languages)-1,2):
        pointer,_=query(f'\\StringFileInfo\\{languages[i]:04x}{languages[i+1]:04x}\\InternalName')
        if pointer and ctypes.wstring_at(pointer).casefold()=='workbox':valid=True
    if not valid:return None
    return (values[2]>>16,values[2]&65535,values[3]>>16,values[3]&65535)


@dataclass(frozen=True)
class Release:
    root: Path
    version: tuple
    sha256: str
    manifest: dict|None = None

    @property
    def label(self):return '.'.join(map(str,self.version[:3]))+' · 构建 '+str(self.version[3])


def _read_release(folder,*,full=False):
    folder=plain_path(folder).resolve();exe=plain_path(folder/'WorkBox.exe')
    if not exe.is_file():raise ValueError('此位置没有 WorkBox.exe。')
    manifest=folder/MANIFEST;identity=pe_identity(exe)
    if manifest.exists():
        value=json.loads(manifest.read_text('utf-8'))
        if value.get('app_id')!=APP_ID or value.get('schema')!=1:raise ValueError('不是可识别的 BOX 更新包。')
        version=key(value);files=value['files'];seen=set()
        for name,meta in files.items():
            relative_file(name)
            if name.casefold() in seen or name==MANIFEST:raise ValueError('更新清单存在重复或无效文件。')
            seen.add(name.casefold())
            if not isinstance(meta['size'],int) or meta['size']<0 or len(meta['sha256'])!=64:raise ValueError('更新校验信息无效。')
        if 'WorkBox.exe' not in files or not any(n.startswith('_internal/') for n in files):raise ValueError('请完整解压 BOX，缺少必要程序文件。')
        sha=digest(exe)
        if sha!=files['WorkBox.exe']['sha256'] or (os.name=='nt' and identity!=version):raise ValueError('程序与版本清单不一致，请重新解压完整免安装包。')
        if full:
            for name,meta in files.items():
                path=plain_path(folder/relative_file(name))
                if path.stat().st_size!=meta['size'] or digest(path)!=meta['sha256']:raise ValueError('更新包文件不完整：'+name)
        return Release(folder,version,sha,value)
    if identity is None:raise ValueError('无法识别该程序版本，不自动覆盖。可使用“导入旧 BOX”带入数据。')
    return Release(folder,identity,digest(exe))


def read_release(folder,*,full=False):
    try:return _read_release(folder,full=full)
    except (KeyError,TypeError,AttributeError) as error:raise ValueError('版本清单损坏，请重新解压完整免安装包。') from error


def write_release(folder):
    folder=plain_path(folder);files={}
    if pe_identity(folder/'WorkBox.exe')!=RELEASE_KEY:raise ValueError('请同步增加 portable_release 与 Windows 程序的构建版本号。')
    for name in sorted(ROOT_ITEMS-{MANIFEST}):
        base=folder/name
        sources=sorted(base.rglob('*')) if base.is_dir() else [base]
        for path in sources:
            path=plain_path(path)
            if path.is_file():files[path.relative_to(folder).as_posix()]=dict(size=path.stat().st_size,sha256=digest(path))
    write_json(folder/MANIFEST,dict(schema=1,app_id=APP_ID,version=VERSION,build=RELEASE_BUILD,files=files))
    return read_release(folder,full=True)


def index_path():return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'WorkBox'/'portable-locations.json'


def independent_instance(folder):
    marker=Path(folder)/'data/BOX-Instance.json'
    if not marker.exists():return False
    try:
        value=json.loads(marker.read_text('utf-8'))
        if value==dict(schema=1,mode='independent'):return True
    except (OSError,ValueError):pass
    raise ValueError('独立运行标记无法识别，请保留当前目录，暂不打开其他总表。')


def create_independent_instance(folder):
    folder=read_release(folder).root
    if independent_instance(folder):return
    data=folder/'data'
    if data.exists() and any(data.iterdir()):
        raise ValueError('当前目录已有数据。请将完整新版放到一个没有 data 的独立文件夹，再选择独立使用；原数据保留。')
    write_json(data/'BOX-Instance.json',dict(schema=1,mode='independent'))


def read_index(path=None):
    try:
        value=json.loads(Path(path or index_path()).read_text('utf-8'))
        if isinstance(value,dict) and value.get('schema')==1 and isinstance(value.get('preferred',''),str) and isinstance(value.get('pending',''),str) and isinstance(value.get('locations',[]),list) and all(isinstance(p,str) for p in value.get('locations',[])):return value
    except (OSError,ValueError):pass
    return dict(schema=1,preferred='',locations=[],pending='')


def register(folder,path=None):
    folder=read_release(folder).root
    if independent_instance(folder):return
    value=read_index(path);value['preferred']=str(folder)
    value['locations']=[str(folder)]+[p for p in value.get('locations',[]) if Path(p)!=folder][:19]
    write_json(path or index_path(),value)


def discover(current,*,index=None,roots=None):
    value=read_index(index);candidates=[value.get('preferred','')]+value.get('locations',[])
    if roots is None:
        home=Path.home();roots=[home/'Desktop',home/'Downloads',Path(current).parent]
        if os.name=='nt':
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for name in (r'Software\Microsoft\Windows\CurrentVersion\Uninstall',r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                    try:
                        with winreg.OpenKey(hive,name) as parent:
                            for i in range(winreg.QueryInfoKey(parent)[0]):
                                try:
                                    with winreg.OpenKey(parent,winreg.EnumKey(parent,i)) as child:
                                        label=winreg.QueryValueEx(child,'DisplayName')[0]
                                        if 'workbox' in str(label).casefold():candidates.append(winreg.QueryValueEx(child,'InstallLocation')[0])
                                except OSError:pass
                    except OSError:pass
    examined=0
    for root in roots:
        stack=[(Path(root),0)]
        while stack and examined<5000:
            parent,depth=stack.pop()
            try:
                for p in parent.iterdir():
                    examined+=1
                    if examined>5000:break
                    if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:continue
                    if p.name.casefold()=='workbox.exe' and (p.parent/'data/workbox.db').is_file():candidates.append(str(p.parent))
                    elif p.is_dir() and depth<3 and p.name not in {'.git','.venv','.local','_internal','data','node_modules'}:stack.append((p,depth+1))
            except OSError:pass
    found=[];seen={str(plain_path(current)).casefold()}
    for raw in candidates:
        if not raw:continue
        try:
            p=plain_path(raw)
            if str(p).casefold() in seen:continue
            release=read_release(p);seen.add(str(p).casefold())
            if not independent_instance(p):found.append(release)
        except (OSError,ValueError,KeyError,TypeError):continue
    return found


def data_version(folder):
    return data_directory_version(Path(folder)/'data')


def data_directory_version(folder):
    marker=Path(folder)/'BOX-Data-Version.json'
    if not marker.exists():return (0,0,0,0)
    try:return key(json.loads(marker.read_text('utf-8')))
    except (KeyError,TypeError,ValueError) as error:raise ValueError('数据版本标记无法识别，暂不打开原数据，请保留原件。') from error


def mark_data(folder):
    if data_version(folder)>RELEASE_KEY:raise ValueError('此数据已由更高版本 BOX 使用，请打开新版。')
    write_json(Path(folder)/'data/BOX-Data-Version.json',dict(version=VERSION,build=RELEASE_BUILD))


def make_job(source,target,*,index=None):
    source=read_release(source,full=True);target=read_release(target)
    if source.manifest is None or source.version<=target.version:raise ValueError('只允许用较新版本更新已有 BOX。')
    if source.version<data_version(target.root):raise ValueError('已有数据来自更高版本，不能降级。')
    if path_is_within(source.root,target.root) or path_is_within(target.root,source.root):raise ValueError('更新来源与原目录不能互相包含。')
    token=uuid4().hex;job=plain_path(target.root.parent/('.BOX-update-'+token));job.mkdir()
    plan=dict(schema=1,token=token,source=str(source.root),target=str(target.root),source_manifest=digest(source.root/MANIFEST),source_version=list(source.version),expected_old_sha256=target.sha256,index=str(index or index_path()))
    write_json(job/'plan.json',plan);write_json(job/'state.json',dict(state='pending',steps=[]))
    value=read_index(index);value['pending']=str(job/'plan.json');write_json(index or index_path(),value)
    return job/'plan.json',token


def load_job(path,token):
    path=plain_path(path);plan=json.loads(path.read_text('utf-8'));job=path.parent;target=plain_path(plan['target'])
    if plan.get('schema')!=1 or plan.get('token')!=token or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('更新请求无效。')
    if job.name!='.BOX-update-'+token or job.parent!=target.parent or path.name!='plan.json':raise ValueError('更新暂存位置无效。')
    if target==target.parent or target==job:raise ValueError('不能更新此目录。')
    candidates=[target/'WorkBox.exe',job/'rollback/WorkBox.exe']
    known=any(p.is_file() and digest(plain_path(p))==plan.get('expected_old_sha256') and pe_identity(p) is not None for p in candidates)
    if not known:
        state=json.loads((job/'state.json').read_text('utf-8'))
        if state.get('state')!='complete':raise ValueError('原 BOX 身份不一致，请保留数据并重新核对。')
        current=read_release(target)
        if current.version<tuple(plan.get('source_version',(65535,65535,65535,65535))):raise ValueError('原 BOX 身份不一致，请保留数据并重新核对。')
    return job,plan,target


def restore(job,target,state):
    discard=job/('未完成的新程序-'+uuid4().hex);discard.mkdir(exist_ok=True)
    for step in reversed(state['steps']):
        name=step['name']
        if name not in ROOT_ITEMS:raise ValueError('恢复清单路径无效。')
        live=plain_path(target/name);backup=plain_path(job/'rollback'/name)
        if backup.exists():
            if live.exists():live.replace(discard/name)
            backup.replace(live)
        elif not step['old_exists'] and live.exists():live.replace(discard/name)
    state.update(state='restored',steps=[]);write_json(job/'state.json',state)


def _remove_job(job,target):
    # Only a verified dedicated sibling directory may be removed recursively.
    job=plain_path(job);target=plain_path(target)
    if job.parent!=target.parent or not job.name.startswith('.BOX-update-') or len(job.name)!=44:raise ValueError('更新清理目录无效。')
    shutil.rmtree(job)


def cleanup_completed(index=None):
    value=read_index(index);remaining=[]
    for raw in value.get('cleanups',[]):
        try:
            path=Path(raw)
            if not path.exists():continue
            plan=json.loads(path.read_text('utf-8'));job,_,target=load_job(path,plan['token'])
            if json.loads((job/'state.json').read_text('utf-8'))['state']!='complete':remaining.append(raw);continue
            _remove_job(job,target)
        except (OSError,ValueError,KeyError,TypeError):remaining.append(raw)
    if remaining!=value.get('cleanups',[]):value['cleanups']=remaining;write_json(index or index_path(),value)


def apply_job(path,token,*,quiesce=None,cancel_event=None,progress=None,restore_only=False):
    """Run from the downloaded EXE, never from the directory being replaced."""
    from PySide6.QtCore import QLockFile
    job,plan,target=load_job(path,token);source=plain_path(plan['source'])
    update_lock=QLockFile(str(target/'.BOX-update.lock'));update_lock.setStaleLockTime(0)
    plain_path(target/'data').mkdir(exist_ok=True)
    session=QLockFile(str(target/'data/workbox.db.session.lock'));session.setStaleLockTime(0)
    if not update_lock.tryLock(0):raise ValueError('已有更新正在进行，请稍后再试。')
    state=json.loads((job/'state.json').read_text('utf-8'));backup_path=None
    try:
        if quiesce:quiesce(target)
        for _ in range(50):
            cancelled(cancel_event)
            if session.tryLock(0):break
            time.sleep(.1)
        else:raise ValueError('请先完成当前工作并退出原 BOX，再点击重试；程序不会强制结束收录。')
        if state['state']=='swapping':restore(job,target,state)
        if restore_only:
            value=read_index(plan['index']);value['pending']='';write_json(plan['index'],value)
            receipt=dict(target=str(target),restored=True,complete=True)
            _remove_job(job,target);return receipt
        if state['state']=='complete':raise ValueError('此更新已经完成，请直接打开原位置的 BOX。')
        current=read_release(target)
        release=read_release(source,full=True)
        if digest(source/MANIFEST)!=plan['source_manifest'] or current.sha256!=plan['expected_old_sha256']:raise ValueError('更新来源或原程序已变化，请重新打开新版本并核对。')
        if release.version<=current.version or release.version<data_version(target):raise ValueError('不能用相同或较旧版本覆盖现有程序。')
        # User-selected material roots must not overlap any program location.
        db=target/'data/workbox.db'
        if db.exists():
            with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as c:
                existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'workflow_settings' in existing:
                    for name,value in c.execute('SELECT key,value FROM workflow_settings'):
                        if name in ('work_root','archive_root'):
                            root=plain_path(json.loads(value))
                            if any(path_is_within(root,target/item) or path_is_within(target/item,root) for item in ('_internal','licenses')):raise ValueError('材料目录与程序目录重叠，请先在 BOX 中调整材料位置。')
        needed=sum(r['size'] for r in release.manifest['files'].values())+(db.stat().st_size*3 if db.exists() else 0)
        if shutil.disk_usage(target.parent).free < needed+16*1024**2:raise ValueError('原位置空间不足，请释放空间后重试。')
        payload=job/'payload';payload.mkdir(exist_ok=True)
        for i,(name,meta) in enumerate(release.manifest['files'].items()):
            cancelled(cancel_event);src=plain_path(source/relative_file(name));dst=plain_path(payload/relative_file(name));dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,dst)
            if digest(dst)!=meta['sha256']:raise ValueError('更新暂存校验失败：'+name)
            if progress:progress(i+1,len(release.manifest['files']))
        shutil.copy2(source/MANIFEST,payload/MANIFEST);read_release(payload,full=True)
        # Every byte of current data is left in place. Back up the database while holding its session lock.
        if db.exists():
            backup_path=target/'data/更新备份'/token/'更新前.db';backup_path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as c,closing(sqlite3.connect(backup_path)) as b:c.backup(b)
            marker=target/'data/BOX-Data-Version.json'
            if marker.exists():shutil.copy2(marker,backup_path.parent/marker.name)
        # Keep unlisted additions rather than discarding a user's file stored among runtime files.
        known=set(current.manifest['files']) if current.manifest else set(release.manifest['files'])
        for directory in ('_internal','licenses'):
            base=target/directory
            if not base.exists():continue
            for path in base.rglob('*'):
                path=plain_path(path)
                if path.is_file() and path.relative_to(target).as_posix() not in known:
                    saved=target/'data/更新备份'/token/'保留的附加文件'/path.relative_to(target)
                    saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,saved)
                    if digest(path)!=digest(saved):raise ValueError('附加文件备份校验失败。')
        state.update(state='swapping',steps=[]);write_json(job/'state.json',state)
        (job/'rollback').mkdir(exist_ok=True)
        # Swap complete runtime directories. The executable is installed last.
        names=sorted({relative_file(n).parts[0] for n in release.manifest['files']}|{MANIFEST},key=lambda n:(n=='WorkBox.exe',n))
        for name in names:
            cancelled(cancel_event);live=plain_path(target/name);staged=plain_path(payload/name)
            step=dict(name=name,old_exists=live.exists());state['steps'].append(step);write_json(job/'state.json',state)
            for attempt in range(50):
                try:
                    if live.exists():live.replace(job/'rollback'/name)
                    break
                except PermissionError:
                    if attempt==49:raise
                    time.sleep(.1)
            staged.replace(live)
        read_release(target,full=True)
        state['state']='complete';write_json(job/'state.json',state)
        receipt=dict(version=release.label,target=str(target),backup=str(backup_path or ''),complete=True)
        try:
            register(target,plan['index']);value=read_index(plan['index']);value['pending']='';value['cleanups']=list(dict.fromkeys(value.get('cleanups',[])+[str(job/'plan.json')]));write_json(plan['index'],value)
            write_json(target/'data/更新备份'/token/'更新结果.json',receipt)
        except OSError:receipt['registration_notice']='程序已更新，位置登记或报告未保存；原有数据保留。'
        try:
            _remove_job(job,target)
            value=read_index(plan['index']);value['cleanups']=[p for p in value.get('cleanups',[]) if p!=str(job/'plan.json')];write_json(plan['index'],value)
        except OSError:receipt['cleanup_notice']='更新完成，临时程序文件将在下次启动时清理。'
        return receipt
    except BaseException:
        if state['state']=='swapping':restore(job,target,state)
        raise
    finally:
        session.unlock();update_lock.unlock()
