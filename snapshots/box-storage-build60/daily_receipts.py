"""Read-only daily material receipts, grouped by each task's saved rule."""
from pathlib import Path
from datetime import date, datetime, timedelta, timezone


def intact(service,v,path):
    """Cache by file identity and timestamps, invalidating external changes."""
    try:
        p=Path(path);s=p.stat();key=(str(p),v['sha256'],s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        cache=getattr(service,'_daily_receipt_integrity',{})
        if key not in cache:
            from production import source_digest
            if len(cache)>2000:cache={}
            cache[key]=source_digest(p)==v['sha256'];service._daily_receipt_integrity=cache
        return cache[key]
    except OSError:return False


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def category_title(name):
    return {'单手册 Word':'单 Word','原包待核对':'压缩包收纳',
            '桌面14件':'14件材料','14件＋双视频':'14件＋视频'}.get(name,name)


def snapshot(service, day=None):
    day=day or today()
    start=datetime.combine(date.fromisoformat(day),datetime.min.time(),tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    bounds=(start.strftime('%Y-%m-%dT%H:%M:%S'),(start+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S'))
    store=service.production
    rows={r['id']:r for r in store.rows()}
    result={};seen=set()
    # Version rows are committed only after the file was copied successfully.
    # Keep the latest receipt for each logical material on this local day.
    with service.db.connect() as c:
        versions=c.execute('SELECT * FROM material_versions WHERE created_at>=? AND created_at<? ORDER BY id DESC',bounds).fetchall()
        originals=c.execute('SELECT * FROM original_zip_receipts WHERE created_at>=? AND created_at<? ORDER BY id DESC',bounds).fetchall()
        saved={(r['project_id'],r['kind']):dict(r) for r in c.execute('SELECT * FROM collected_materials')}
    for source,raw in [(versions,False),(originals,True)]:
        for record in source:
            v=dict(record);pid=v['project_id'];row=rows.get(pid)
            if not row or store.local_date(v['created_at'])!=day:continue
            # Archives retained as provenance for extracted materials are not an extra item.
            if raw and row['required_count']:continue
            kind='压缩包原件' if raw else v['kind']
            key=(pid,kind)
            if key in seen:continue
            seen.add(key)
            path=v['path']
            if not raw:
                current=next((m for m in row['materials'] if m['kind']==kind),None)
                if current and current['id']==v['id']:
                    if current.get('collection_issue'):continue
                    path=saved.get((pid,kind),{}).get('path') or path
            if not intact(service,v,path):continue
            result[key]=dict(project_id=pid,name=row['name'],category=row['package_name'],
                kind=kind,filename=Path(path).name,path=path,
                created_at=v['created_at'],raw=raw,version_id=v['id'])
    entries=sorted(result.values(),key=lambda v:v['created_at'],reverse=True)
    categories=[]
    for name in dict.fromkeys(v['category'] for v in entries):
        subset=[v for v in entries if v['category']==name]
        categories.append(dict(name=name,title=category_title(name),materials=len(subset),
                               software=len({v['project_id'] for v in subset})))
    categories.sort(key=lambda c:(-c['materials'],c['title']))
    return dict(day=day,materials=len(entries),software=len({v['project_id'] for v in entries}),
                categories=categories,entries=entries)
