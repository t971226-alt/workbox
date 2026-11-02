"""Rename only BOX working areas with real backups and unchanged material bytes."""
from pathlib import Path
from unittest.mock import patch
import json


def verify(root):
    from desktop_service import DesktopService
    from storage_management import INTERNAL,WORK,STAGING,LEGACY_INTERNAL,LEGACY_WORK,LEGACY_STAGING,staging_roots
    from unified_storage import unify_storage,needs_unification,_rewrite_references,_manifest
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');saved=root/'saved';s.workspaces.configure_materials(saved,complete_setup=True)
    pid=s.production.create_batch('更名验证',['软件资料管理系统'],['说明.docx'])['project_ids'][0]
    source=root/'原文件.docx';source.write_bytes(b'v1');s.production.receive(pid,'说明.docx',source)
    source.write_bytes(b'v2');s.production.receive(pid,'说明.docx',source)
    new=saved/INTERNAL;old=saved/LEGACY_INTERNAL
    stage=new/STAGING;stage.mkdir(parents=True);(stage/'未完成导入.zip').write_bytes(b'pending bytes')
    with s.db.connect() as c:
        c.execute("INSERT INTO workflow_settings(key,value) VALUES ('pending_name_test',?)",(json.dumps({'path':str(stage/'未完成导入.zip')}),))
    maps=[dict(source=str(new/WORK),target=str(old/LEGACY_WORK)),dict(source=str(stage),target=str(old/LEGACY_STAGING)),dict(source=str(new),target=str(old))]
    # Simulate a previous release, entirely inside this isolated test directory.
    assert new.resolve().is_relative_to(root.resolve()) and old.resolve().is_relative_to(root.resolve())
    new.replace(old);(old/WORK).replace(old/LEGACY_WORK);(old/STAGING).replace(old/LEGACY_STAGING)
    with s.db.connect() as c:_rewrite_references(c,maps)
    before=_manifest(old);row=s.production.rows()[0]
    history=[(v['id'],v['path'],v['sha256']) for v in s.production.history(pid,'说明.docx')]
    published=Path(row['collection_folder'])
    assert needs_unification(s) and (old/LEGACY_STAGING) in staging_roots(s)
    # A failed reference transaction restores the exact old paths and files.
    with patch('unified_storage._rewrite_references',side_effect=RuntimeError('isolated reference failure')):
        try:unify_storage(s)
        except RuntimeError:pass
        else:raise AssertionError('Expected rollback')
    assert old.is_dir() and _manifest(old)==before
    result=unify_storage(s)
    assert not old.exists() and (new/WORK).is_dir() and (new/STAGING/'未完成导入.zip').read_bytes()==b'pending bytes'
    assert source.read_bytes()==b'v2' and published.is_dir()
    assert history==[(v['id'],v['path'],v['sha256']) for v in s.production.history(pid,'说明.docx')]
    assert s.production.rows()[0]['collection_count']==1 and Path(result['storage_backup'],'整理前.db').is_file()
    with s.db.connect() as c:
        assert all(Path(r[0]).is_file() for r in c.execute('SELECT path FROM work_files'))
        payload=json.loads(c.execute("SELECT value FROM workflow_settings WHERE key='pending_name_test'").fetchone()[0])
        assert Path(payload['path'])==new/STAGING/'未完成导入.zip'
    assert not needs_unification(s)
    assert unify_storage(s)['moved_directories']==0
    # Renaming working areas must not recreate a manually removed formal copy.
    s2=DesktopService(root/'missing/data/workbox.db');area=root/'missing/saved';s2.workspaces.configure_materials(area,complete_setup=True)
    pid=s2.production.create_batch('缺失副本保留',['手动移走材料系统'],['说明.docx'])['project_ids'][0]
    original=root/'missing/原件.docx';original.write_bytes(b'keep original');s2.production.receive(pid,'说明.docx',original)
    from saved_locations import collection_location
    copy=collection_location(s2,pid,'说明.docx');copy.unlink()
    new=area/INTERNAL;old=area/LEGACY_INTERNAL
    assert new.resolve().is_relative_to(root.resolve()) and old.resolve().is_relative_to(root.resolve())
    new.replace(old);(old/WORK).replace(old/LEGACY_WORK)
    with s2.db.connect() as c:_rewrite_references(c,[dict(source=str(new/WORK),target=str(old/LEGACY_WORK)),dict(source=str(new),target=str(old))])
    unify_storage(s2)
    assert not copy.exists() and original.read_bytes()==b'keep original' and len(s2.production.history(pid,'说明.docx'))==1
    return dict(working_directory_rename_preserves_bytes_history_preview_paths_and_rollback=True,
                working_name_change_does_not_republish_missing_formal_copies=True)
