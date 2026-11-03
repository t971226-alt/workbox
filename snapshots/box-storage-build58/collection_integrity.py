"""Validate the delivered copy against its pinned archive version."""
from pathlib import Path
from native_files import ZipLease
from workspace_manager import _hash_stream


def issue(store,c,pid,version,*,force=False):
    if not version.get('id'):return ''
    receipt=c.execute('SELECT version_id,path FROM collected_materials WHERE project_id=? AND kind=?',(pid,version['kind'])).fetchone()
    if not receipt or receipt['version_id']!=version['id']:return '当前版本尚未保存到分类目录，请重新收录。'
    try:
        paths=[Path(version['path']),Path(receipt['path'])]
        stamps=tuple((str(p),p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in paths)
        key=(pid,version['kind']);cache=getattr(store,'_collection_integrity_cache',{})
        old=cache.get(key)
        if not force and old and old[0]==(version['id'],stamps):return old[1]
        message=''
        for p in paths:
            with ZipLease(p,movable=False) as lease:
                if _hash_stream(lease.stream)!=(version['sha256'],version['size']):
                    message=('版本归档已变化，请重新收录并检查，未保存确认。' if p==paths[0] else '分类副本内容已变化，请保留文件并重新收录、核对。');break
        cache[key]=((version['id'],stamps),message);store._collection_integrity_cache=cache
        return message
    except (OSError,ValueError):return '分类副本或版本归档缺失、无法读取，请核对保存位置。'
