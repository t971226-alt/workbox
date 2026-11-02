"""Explicit maintenance reset of a test library, preserving rules and source files."""
from pathlib import Path
from contextlib import contextmanager
import sqlite3,json,hashlib,shutil,os
from native_files import plain_path,ZipLease

KEEP={'desktop_settings','workflow_settings','workflow_first_run','workflow_archive_roots',
      'material_profiles','profile_intake_options','profile_file_families','profile_name_patterns','profile_changes','intake_folder_presets','zip_approval_migrations','delivery_event_migrations'}

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

@contextmanager
def connection(path):
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    try:yield c
    finally:c.close()

def plan(database):
    database=plain_path(database);candidates=set();sources=set();roots=set();errors=[]
    with connection(database) as c:
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        settings={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM workflow_settings')}
        for p in [settings['work_root'],settings['archive_root'],*settings.get('internal_storage_roots',[])]:roots.add(plain_path(p))
        for p, in c.execute('SELECT path FROM workflow_archive_roots'):roots.add(plain_path(p))
        for name in ['工作缓存','材料历史','材料目录整理','材料收件缓存','ZIP归档']:roots.add(plain_path(database.parent/name))
        for p,g in c.execute('SELECT path,group_path FROM project_workspaces'):roots.add(plain_path(p));roots.add(plain_path(g))
        for p, in c.execute('SELECT path FROM collection_batch_folders'):roots.add(plain_path(p))
        for p, in c.execute('SELECT directory FROM task_storage_preferences'):roots.add(plain_path(p))
        if any(r==Path(r.anchor) or r==database.parent for r in roots):raise ValueError('拒绝把磁盘根目录或数据库目录作为材料清理范围。')
        destination_columns={'collected_materials':['path'],'material_versions':['path'],'work_files':['path'],
            'original_zip_receipts':['path'],'zips':['path'],'production_import_files':['saved_path'],
            'managed_transfers':['destination'],'zip_snapshot_attempts':['target'],'collection_retirements':['path','history_path']}
        for table,cols in destination_columns.items():
            if table not in tables:continue
            for col in cols:
                for p, in c.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}"!=\'\''):
                    try:
                        path=plain_path(p)
                        if path.is_file() and any(path.is_relative_to(r) for r in roots):candidates.add(path)
                    except (ValueError,OSError) as e:errors.append(str(e))
        for table in tables:
            cols=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")')]
            if 'source_path' in cols:
                for p, in c.execute('SELECT source_path FROM "'+table+'" WHERE source_path IS NOT NULL'):
                    try:
                        path=plain_path(p,allow_network=True)
                        if path.is_file():sources.add(path)
                    except (ValueError,OSError):pass
        # Unfinished extraction previews are test data too, but no other folders
        # are scanned for deletion. Destination records remain the authority.
        from storage_management import _files,INTERNAL,STAGING,LEGACY_INTERNAL,LEGACY_STAGING
        internal_areas=[Path(settings['work_root'])/INTERNAL,Path(settings['work_root'])/LEGACY_INTERNAL,*(Path(p) for p in settings.get('internal_storage_roots',[]))]
        stages=[database.parent/'材料收件缓存',*(p/name for p in internal_areas for name in (STAGING,LEGACY_STAGING))]
        for root in dict.fromkeys(stages):
            try:candidates.update(_files(root))
            except (ValueError,OSError) as e:errors.append(str(e))
        counts={t:c.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in tables if t not in KEEP}
        config={t:[tuple(r) for r in c.execute('SELECT * FROM "'+t+'"')] for t in tables if t in KEEP}
    files=[dict(path=str(p),size=p.stat().st_size,sha256=digest(p)) for p in sorted(candidates)]
    originals={str(p):digest(p) for p in sources-candidates}
    return dict(database=str(database),files=files,originals=originals,roots=[str(p) for p in roots],counts=counts,config=config,errors=errors)

def reset(database,backup):
    """Caller must close BOX. Back up before clearing records or managed copies."""
    p=plan(database)
    if p['errors']:raise ValueError('有无法核对的材料路径，暂未清空：'+p['errors'][0])
    database=plain_path(database);backup=plain_path(backup)
    if backup.exists():raise ValueError('恢复备份目录已存在，请换一个新目录。')
    if any(backup==Path(r) or backup.is_relative_to(Path(r)) for r in p['roots']):raise ValueError('恢复备份必须放在材料清理范围之外。')
    backup.mkdir(parents=True)
    with connection(database) as c,sqlite3.connect(backup/'workbox.db') as dst:c.backup(dst)
    for i,item in enumerate(p['files']):
        dest=backup/'files'/str(i);dest.parent.mkdir(exist_ok=True);shutil.copy2(item['path'],dest)
        if digest(dest)!=item['sha256']:raise ValueError('材料正在变化，尚未清空。')
    (backup/'恢复清单.json').write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    removed=[]
    try:
        with connection(database) as c:
            c.execute('PRAGMA foreign_keys=OFF');c.execute('BEGIN IMMEDIATE')
            try:
                for t in p['counts']:c.execute('DELETE FROM "'+t+'"')
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
                for i,item in enumerate(p['files']):
                    with ZipLease(item['path'],movable=True) as lease:
                        if hashlib.file_digest(lease.stream,'sha256').hexdigest()!=item['sha256']:raise ValueError('文件在清空前发生变化，已中止。')
                        lease.remove_after_verified_commit()
                    removed.append(i)
                c.commit()
            except BaseException:c.rollback();raise
    except BaseException:
        for i in removed:
            target=Path(p['files'][i]['path']);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(backup/'files'/str(i),target)
        raise
    assert all(Path(n).is_file() and digest(n)==sha for n,sha in p['originals'].items())
    with connection(database) as c:
        assert all(c.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0]==0 for t in p['counts'])
        assert all([tuple(r) for r in c.execute('SELECT * FROM "'+t+'"')]==rows for t,rows in p['config'].items())
    # Prune only empty parents under a checked material root. Never recurse or
    # delete the library root, program directory, or unrelated files.
    for item in p['files']:
        parent=Path(item['path']).parent
        while any(parent!=Path(r) and parent.is_relative_to(Path(r)) for r in p['roots']):
            try:plain_path(parent).rmdir()
            except (OSError,ValueError):break
            parent=parent.parent
    return dict(cleared_projects=p['counts'].get('projects',0),deleted_files=len(removed),deleted_bytes=sum(f['size'] for f in p['files']),
        preserved_sources=len(p['originals']),rules_and_settings_preserved=True,backup=str(backup),all_business_tables_empty=True)
