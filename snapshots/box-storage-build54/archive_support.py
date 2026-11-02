"""Archive validation and bounded 7z material extraction, without executing files."""
from pathlib import Path
import os,struct,zlib,hashlib,tempfile,shutil
from concurrent.futures import CancelledError
from native_files import plain_path,ZipLease,cancelled

def validate_stream(stream,path, cancel_event=None):
    suffix=Path(path).suffix.casefold();stream.seek(0)
    try:
        if suffix=='.zip':
            from workspace_manager import _zip_structure
            return _zip_structure(stream,cancel_event)
        if suffix=='.7z':
            header=stream.read(32)
            if len(header)!=32 or header[:6]!=b'7z\xbc\xaf\x27\x1c':raise ValueError('7z 文件头不完整，请检查压缩包。')
            if zlib.crc32(header[12:])!=struct.unpack_from('<I',header,8)[0]:raise ValueError('7z 文件头校验失败。')
            offset,length,crc=struct.unpack_from('<QQI',header,12);stream.seek(0,2);size=stream.tell()
            if offset+32+length>size:raise ValueError('7z 压缩包不完整。')
            stream.seek(32+offset);value=0
            while length:
                cancelled(cancel_event);block=stream.read(min(length,1024*1024))
                if not block:raise ValueError('7z 目录数据不完整。')
                value=zlib.crc32(block,value);length-=len(block)
            if value!=crc:raise ValueError('7z 目录校验失败。')
            return
        if suffix=='.rar':
            if stream.read(8)[:7] not in (b'Rar!\x1a\x07\x00',b'Rar!\x1a\x07\x01'):raise ValueError('没有可识别的 RAR 文件头。')
            return
        raise ValueError('此压缩格式暂不支持，请使用 ZIP、7z 或 RAR 原包。')
    finally:stream.seek(0)

def member_name(name):
    parts=name.replace('\\','/').split('/')
    if not name or name.startswith(('/','\\')) or any(not p or p in ('.','..') or ':' in p or any(c in p for c in '<>"|?*\x00') or p.endswith((' ','.')) for p in parts):raise ValueError('压缩包内包含不能安全保存的文件名。')
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if any(p.split('.')[0].upper() in reserved for p in parts):raise ValueError('压缩包内包含系统保留名称。')
    return '/'.join(parts)

def unpack_7z(service,path,*,cancel_event=None):
    import py7zr
    from threading import RLock
    from storage_management import staging_root,hold
    from workspace_manager import _hash_stream
    path=plain_path(path,allow_network=True)
    class Product(py7zr.Py7zIO):
        def __init__(self,factory,name):self.factory=factory;self.name=name;self.file=open(factory.temp/name,'w+b');self.length=0
        def write(self,data):
            cancelled(cancel_event)
            with self.factory.lock:
                end=self.file.tell()+len(data);growth=max(0,end-self.length)
                if end>self.factory.sizes[self.name] or end>256*1024**2 or self.factory.total+growth>1024**3:raise ValueError('压缩包展开大小超过上限，请分批收录。')
                n=self.file.write(data);self.length=max(self.length,end);self.factory.total+=growth;return n
        def read(self,size=None):return self.file.read(-1 if size is None else size)
        def seek(self,offset,whence=0):
            result=self.file.seek(offset,whence)
            if result<0 or result>self.factory.sizes[self.name]:raise ValueError('压缩包文件大小不一致。')
            return result
        def flush(self):self.file.flush()
        def size(self):return self.length
        def close(self):
            if not self.file.closed:self.file.flush();self.file.close()
    class Factory(py7zr.WriterFactory):
        def __init__(self,temp,sizes):self.temp=temp;self.sizes=sizes;self.products={};self.total=0;self.lock=RLock()
        def create(self,filename):
            name=member_name(filename)
            if name not in self.sizes or name in self.products:raise ValueError('压缩包内出现未登记或重复文件。')
            p=plain_path(self.temp/name);p.parent.mkdir(parents=True,exist_ok=True);product=Product(self,name);self.products[name]=product;return product
    temporary=None;factory=None
    try:
        with ZipLease(path,movable=False) as lease:
            validate_stream(lease.stream,path,cancel_event);digest,_=_hash_stream(lease.stream,cancel_event=cancel_event);lease.stream.seek(0)
            with py7zr.SevenZipFile(lease.stream,'r',max_extract_size=1024**3) as archive:
                if archive.needs_password():raise ValueError('此 7z 已加密，可以直接保存原包；解压分类前请先解密。')
                items=archive.list();names=set();sizes={}
                if len(items)>5000:raise ValueError('材料包文件过多，请分批收录。')
                for item in items:
                    name=member_name(item.filename.rstrip('/') if item.is_directory else item.filename);key=name.casefold()
                    if key in names or item.is_symlink or not(item.is_file or item.is_directory):raise ValueError('压缩包含重复名称、链接或特殊文件。')
                    names.add(key)
                    if item.is_file:sizes[name]=item.uncompressed
                if sum(sizes.values())>1024**3 or any(n>256*1024**2 for n in sizes.values()):raise ValueError('材料包过大，请分批收录。')
                base=staging_root(service);base.mkdir(parents=True,exist_ok=True);temporary=Path(tempfile.mkdtemp(prefix='.7z-reading-',dir=base));factory=Factory(temporary,sizes)
                archive.extractall(factory=factory);cancelled(cancel_event)
            for p in factory.products.values():p.close()
            if set(factory.products)!=set(sizes) or any((temporary/n).stat().st_size!=size for n,size in sizes.items()):raise ValueError('7z 文件展开不完整。')
            root=plain_path(base/digest[:24]);files=[]
            for name in sizes:
                cancelled(cancel_event);source=plain_path(temporary/name);target=plain_path(root/name)
                if not target.is_relative_to(root):raise ValueError('材料超出暂存目录。')
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():raise ValueError('已有收录暂存已变化，请重新核对。')
                else:os.replace(source,target)
                files.append(target)
            hold(root);return files
    except (CancelledError,ValueError,OSError):raise
    except Exception as error:raise ValueError('7z 读取失败，可先保存原包，或解压后拖入文件夹。'+str(error)) from error
    finally:
        if factory:
            for p in factory.products.values():p.close()
        if temporary:
            # Exact, private mkdtemp directory; no archive-created filesystem links.
            checked=plain_path(temporary)
            if checked.parent!=staging_root(service) or not checked.name.startswith('.7z-reading-'):raise ValueError('暂存清理范围不正确。')
            shutil.rmtree(checked)
