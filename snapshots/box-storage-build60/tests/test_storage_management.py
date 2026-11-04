from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import os,time
import pytest
from desktop_service import DesktopService
from storage_management import staging_root,work_root,staging_roots,cleanup,hold,release_plan,finish_plan,INTERNAL,describe
from material_collection import unpack_material_zip
from direct_material_intake import DirectMaterialIntake


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'保存目录',complete_setup=True)
    p=tmp_path/'暂存归档系统.zip'
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'isolated content')
    return s,p,tmp_path


def old(p):os.utime(p,(time.time()-172800,time.time()-172800))


def test_zip_staging_follows_selected_material_root(data):
    s,p,root=data;plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True,require_material_zip=True)
    assert all(Path(e['path']).is_relative_to(root/'保存目录'/INTERNAL/'导入暂存区') for e in plan['entries'])
    assert not (s.db.path.parent/'材料收件缓存').exists()
    assert plan['entries'][0]['name']=='暂存归档系统'


def test_new_workspaces_follow_root_and_registered_old_workspaces_stay_valid(data):
    s,p,root=data;pid=s.production.create_batch('旧批次',['旧任务系统'],['软件说明书.docx'])['project_ids'][0];s.workspaces.ensure_projects([pid]);before=Path(s.workspaces.get_workspace(pid)['path'])
    assert before.is_relative_to(work_root(s))
    s.workspaces.configure_materials(root/'第二保存目录',complete_setup=True);s.workspaces.ensure_projects([pid]);assert Path(s.workspaces.get_workspace(pid)['path'])==before
    new=s.production.create_batch('新批次',['新任务系统'],['软件说明书.docx'])['project_ids'][0];s.workspaces.ensure_projects([new]);assert Path(s.workspaces.get_workspace(new)['path']).is_relative_to(root/'第二保存目录'/INTERNAL/'材料处理区')


def test_changing_root_preserves_pending_plan_and_new_staging_uses_new_root(data):
    s,p,root=data;first=DirectMaterialIntake(s).prepare([str(p)],include_known=True);path=Path(first['entries'][0]['path'])
    s.workspaces.configure_materials(root/'第二保存目录',complete_setup=True);second=DirectMaterialIntake(s).prepare([str(p)],include_known=True)
    assert path.is_file() and Path(second['entries'][0]['path']).is_relative_to(staging_root(s))
    assert path!=Path(second['entries'][0]['path'])
    result=s.confirm_direct_materials(first,first['entries'],first['batch'],'collect');assert result['copied_count']==1
    assert Path(s.production.rows()[0]['collection_folder']).is_relative_to(root/'第二保存目录')


def test_cancelled_unreferenced_preview_is_cleaned_but_source_kept(data):
    s,p,_=data;plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True);paths=[Path(e['path']) for e in plan['entries']]
    result=finish_plan(s,plan,discard=True);assert result['files']==1 and not any(p.exists() for p in paths) and p.is_file()


def test_cancel_keeps_only_remaining_copy_if_outer_zip_missing(data):
    s,p,_=data;plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True);path=Path(plan['entries'][0]['path']);p.unlink();finish_plan(s,plan,discard=True);assert path.is_file()


@pytest.mark.parametrize('location',['current','old'])
def test_cleanup_removes_only_unreferenced_staging_files(data,location):
    s,p,root=data;base=staging_root(s) if location=='current' else s.db.path.parent/'材料收件缓存';folder=base/'unused';folder.mkdir(parents=True);orphan=folder/'unused.txt';orphan.write_bytes(b'unused');old(orphan)
    source=root/'正式资料.docx';source.write_bytes(b'important');pid=s.production.create_batch('引用测试',['引用测试系统'],['软件说明书.docx'])['project_ids'][0]
    referenced=folder/'referenced.docx';referenced.write_bytes(b'work');s.production.receive(pid,'软件说明书.docx',referenced);old(referenced)
    before=s.production.rows()[0];result=cleanup(s);assert result['files']==1 and not orphan.exists() and referenced.is_file()
    assert s.production.rows()[0]['materials'][0]['id']==before['materials'][0]['id'] and source.is_file()


def test_cleanup_keeps_recent_and_active_files(data):
    s,p,_=data;folder=staging_root(s)/'active';folder.mkdir(parents=True);f=folder/'x.txt';f.write_bytes(b'active');old(f);hold(folder)
    assert cleanup(s)['files']==0 and f.is_file()
    release_plan(s,dict(entries=[dict(path=str(f))]));os.utime(f,None);assert cleanup(s)['files']==0
    old(f);assert cleanup(s,dry_run=True)['files']==1 and f.is_file();assert cleanup(s)['files']==1 and not f.exists()


def test_cleanup_refuses_unregistered_or_reparse_locations(data):
    s,p,root=data;outside=root/'不要删除';outside.mkdir();f=outside/'keep.txt';f.write_bytes(b'keep')
    with pytest.raises(ValueError):cleanup(s,roots=[outside],minimum_age=0)
    assert f.is_file()


def test_imported_member_source_is_retained_for_receipts_and_retry(data):
    s,p,_=data;plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True);f=Path(plan['entries'][0]['path']);s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect');old(f)
    assert cleanup(s)['files']==0 and f.is_file()


def test_folder_scan_does_not_reimport_internal_areas(data):
    from folder_intake import FolderIntake
    s,p,root=data;hidden=staging_root(s)/'cache';hidden.mkdir(parents=True);(hidden/'内部.txt').write_bytes(b'internal');visible=root/'保存目录/正式文件.txt';visible.write_bytes(b'visible')
    assert FolderIntake(s)._scan(root/'保存目录',None)==[visible]
    with pytest.raises(ValueError,match='内部'):FolderIntake(s)._scan(hidden,None)


def test_storage_overview_labels_and_paths_are_clear(data):
    s,p,_=data;rows=describe(s);assert {r['name'] for r in rows}=={'正式材料','导入暂存区','材料处理区','归档与历史版本'}
    assert str(staging_root(s)) in next(r for r in rows if r['name']=='导入暂存区')['paths']


def test_cannot_select_internal_area_as_material_root(data):
    s,p,_=data
    with pytest.raises(ValueError,match='内部'):s.workspaces.configure_materials(staging_root(s),complete_setup=True)


def test_storage_management_panel_checks_and_cleans_real_files(tmp_path):
    from PySide6.QtWidgets import QApplication
    from scripts.verify_storage_management import verify
    app=QApplication.instance() or QApplication([])
    assert all(verify(tmp_path/'storage-management-panel').values())
