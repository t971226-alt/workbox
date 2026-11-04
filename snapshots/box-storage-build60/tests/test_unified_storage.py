from pathlib import Path
import pytest
from desktop_service import DesktopService
from unified_storage import unify_storage,needs_unification,_rewrite_references,_manifest
from material_collection import category_for
from storage_management import INTERNAL,work_root

def fixture_rename(source,target,root):
    # Windows scanners may briefly hold a freshly copied fixture directory.
    # Retry only that transient setup operation, never a product assertion.
    import time
    assert source.resolve().is_relative_to(root.resolve()) and target.resolve().is_relative_to(root.resolve())
    for attempt in range(6):
        try:return source.replace(target)
        except PermissionError:
            if attempt==5:raise
            time.sleep(.05*2**attempt)

def old_layout(tmp_path):
    s=DesktopService(tmp_path/'BOX/data/workbox.db');root=tmp_path/'材料';s.workspaces.configure_materials(root,complete_setup=True)
    batch=s.production.create_batch('批次甲',['软件甲系统','软件乙系统'],['软件说明书.docx'])
    for i,pid in enumerate(batch['project_ids']):
        p=tmp_path/f'原件{i}.docx';p.write_bytes(f'original {i}'.encode());s.production.receive(pid,'软件说明书.docx',p)
    s.production.set_batch_mode(batch['batch_id'],'review')
    first=s.production.rows()[0];s.production.review(first['materials'][0]['id'],True);s.production.submit([first['materials'][0]['id']])
    cache=Path(s.workspaces.get_workspace(batch['project_ids'][0])['group_path']);old=root/'旧工作副本';fixture_rename(cache,old,tmp_path)
    current=root/'单Word';previous=root/'分类收录/单Word';previous.parent.mkdir();fixture_rename(current,previous,tmp_path)
    mappings=[dict(source=str(cache),target=str(old)),dict(source=str(current),target=str(previous))]
    legacy=s.capture_message('1. 旧资料系统','旧批次');pid=legacy['project_ids'][0]
    note=tmp_path/'旧资料.txt';note.write_bytes(b'legacy content');s.workspaces.import_file(note,pid)
    legacy_dir=Path(s.workspaces.get_workspace(pid)['group_path']);legacy_old=root/'旧资料系统';fixture_rename(legacy_dir,legacy_old,tmp_path)
    (legacy_old/'个人备注.txt').write_bytes(b'preserve extra notes')
    mappings.append(dict(source=str(legacy_dir),target=str(legacy_old)))
    with s.db.connect() as c:_rewrite_references(c,mappings)
    return s,root

def test_new_intake_only_publishes_type_and_batch_in_visible_root(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');root=tmp_path/'材料';s.workspaces.configure_materials(root,complete_setup=True)
    bid=s.production.create_batch('第一批',['单件软件系统'],['软件说明书.docx']);source=tmp_path/'single.docx';source.write_bytes(b'manual')
    assert {p.name for p in root.iterdir()}=={INTERNAL}
    s.production.receive(bid['project_ids'][0],'软件说明书.docx',source)
    assert {p.name for p in root.iterdir()}=={'单Word',INTERNAL}
    row=s.production.rows()[0];assert Path(row['collection_folder']).name=='单件软件系统-1件'
    assert Path(s.workspaces.get_workspace(row['id'])['path']).is_relative_to(work_root(s))
    assert not needs_unification(s)
    assert category_for('桌面14件')=='14件材料' and category_for('14件＋双视频')=='14件＋视频'
    with pytest.raises(ValueError,match='内部工作缓存'):s.workspaces.configure_materials(s.db.path.parent/'工作缓存')

def test_existing_layout_is_backed_up_rebased_and_preserves_history_and_extras(tmp_path):
    s,root=old_layout(tmp_path);before=s.production.rows();versions=[(r['id'],[(m['id'],m['path'],m['sha256'],m.get('passed'),m.get('submission_id')) for m in r['materials']]) for r in before]
    note=str(root/'旧工作副本/人工备注')
    with s.db.connect() as c:c.execute('UPDATE material_reviews SET note=?',(note,))
    original_hashes=_manifest(root)
    assert needs_unification(s)
    result=unify_storage(s);assert result['storage_unified'] and not needs_unification(s)
    assert {p.name for p in root.iterdir()}=={'单Word','旧版材料',INTERNAL}
    rows=s.production.rows();assert versions==[(r['id'],[(m['id'],m['path'],m['sha256'],m.get('passed'),m.get('submission_id')) for m in r['materials']]) for r in rows]
    assert [(r['reviewed_count'],r['submitted_count'],r['collection_count']) for r in before]==[(r['reviewed_count'],r['submitted_count'],r['collection_count']) for r in rows]
    assert (root/'旧版材料/旧资料系统/个人备注.txt').read_bytes()==b'preserve extra notes'
    with s.db.connect() as c:
        for row in c.execute('SELECT path FROM work_files'):assert Path(row['path']).is_file()
        assert c.execute('SELECT note FROM material_reviews LIMIT 1').fetchone()[0]==note
    backup=Path(result['storage_backup']);assert (backup/'整理前.db').is_file()
    saved=[]
    for p in (backup/'原目录备份').iterdir():saved.extend(_manifest(p).values())
    assert all(meta in saved for meta in original_hashes.values())
    assert unify_storage(s)['moved_directories']==0

def test_reference_failure_restores_original_directories_and_retry_completes(tmp_path,monkeypatch):
    import unified_storage
    s,root=old_layout(tmp_path);before=_manifest(root);rows=s.production.rows();original=unified_storage._rewrite_references
    monkeypatch.setattr(unified_storage,'_rewrite_references',lambda *args:(_ for _ in ()).throw(RuntimeError('isolated failure')))
    with pytest.raises(RuntimeError,match='isolated'):unify_storage(s)
    assert _manifest(root)==before and s.production.rows()==rows
    monkeypatch.setattr(unified_storage,'_rewrite_references',original)
    assert unify_storage(s)['storage_unified'] and not needs_unification(s)

def test_user_category_named_like_old_wrapper_is_not_migrated(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');root=tmp_path/'材料';s.workspaces.configure_materials(root,complete_setup=True)
    s.classifications.save('分类收录',['软件说明书.docx'])
    batch=s.production.create_batch('自定义批',['自定义系统'],['软件说明书.docx'],package='分类收录')
    source=tmp_path/'source.docx';source.write_bytes(b'custom manual');s.production.receive(batch['project_ids'][0],'软件说明书.docx',source)
    before=_manifest(root)
    assert not needs_unification(s) and unify_storage(s)['moved_directories']==0
    assert _manifest(root)==before

def test_copy_across_volumes_verifies_before_removing_old_copies(tmp_path,monkeypatch):
    import unified_storage
    s,root=old_layout(tmp_path);before=s.production.rows()
    monkeypatch.setattr(unified_storage,'_same_volume',lambda *args:False)
    result=unify_storage(s)
    assert not result['retained_old_files'] and not needs_unification(s)
    assert {p.name for p in root.iterdir()}=={'单Word','旧版材料',INTERNAL}
    assert [(r['id'],r['reviewed_count'],r['submitted_count'],r['collection_count']) for r in s.production.rows()]==[(r['id'],r['reviewed_count'],r['submitted_count'],r['collection_count']) for r in before]
    assert (root/'旧版材料/旧资料系统/个人备注.txt').read_bytes()==b'preserve extra notes'

def test_zip_watch_reference_keeps_one_source_after_relocation(tmp_path):
    from zipfile import ZipFile
    s,root=old_layout(tmp_path);row=s.production.rows()[0]
    original=Path(s.workspaces.get_workspace(row['id'])['path'])/'工作副本.zip'
    with ZipFile(original,'w') as z:z.writestr('readme.txt','material')
    s.snapshots.watch_zip(original,row['id'])
    with s.db.connect() as c:source_id=c.execute('SELECT id FROM zip_sources WHERE source_path=?',(str(original),)).fetchone()[0]
    unify_storage(s)
    moved=Path(s.workspaces.get_workspace(row['id'])['path'])/'工作副本.zip'
    s.snapshots.watch_zip(moved,row['id'])
    with s.db.connect() as c:
        watched=c.execute('SELECT id,source_path,source_key FROM zip_sources').fetchall()
        assert len(watched)==1 and watched[0]['id']==source_id
        assert watched[0]['source_path']==str(moved) and watched[0]['source_key']==s.snapshots._key(moved)
