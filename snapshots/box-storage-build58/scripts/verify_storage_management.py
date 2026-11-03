"""Runtime validation of selected-root staging and non-destructive cleanup."""
from pathlib import Path
from zipfile import ZipFile
import os,time


def verify(root):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from storage_management import staging_root,work_root,finish_plan,cleanup,INTERNAL
    from storage_management_ui import StorageManagementDialog
    from saved_locations import collection_location
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    s=DesktopService(root/'data/workbox.db');first=root/'第一材料库';second=root/'第二材料库';s.workspaces.configure_materials(first,complete_setup=True)
    old=s.production.create_batch('已有任务',['保留原工作区系统'],['软件说明书.docx'])['project_ids'][0];old_path=Path(s.workspaces.get_workspace(old)['path'])
    p=root/'缓存验证系统.zip'
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'storage check')
    plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True);source=Path(plan['entries'][0]['path']);assert source.is_relative_to(first/INTERNAL/'导入暂存区')
    s.workspaces.configure_materials(second,complete_setup=True);preview=DirectMaterialIntake(s).prepare([str(p)],include_known=True);temp=Path(preview['entries'][0]['path']);assert temp.is_relative_to(staging_root(s))
    finish_plan(s,preview,discard=True);assert not temp.exists() and p.is_file()
    result=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect');pid=result['project_ids'][0]
    assert source.is_file() and Path(s.workspaces.get_workspace(old)['path'])==old_path
    assert Path(s.workspaces.get_workspace(pid)['path']).is_relative_to(work_root(s))
    published=collection_location(s,pid,'软件说明书.docx');before=published.read_bytes()
    orphan=staging_root(s)/'unused/x.txt';orphan.parent.mkdir(parents=True,exist_ok=True);orphan.write_bytes(b'old unused')
    os.utime(orphan,(time.time()-172800,time.time()-172800));os.utime(source,(time.time()-172800,time.time()-172800))
    dialog=StorageManagementDialog(s);dialog.show()
    def wait():
        until=time.monotonic()+15
        while dialog.job.isRunning() and time.monotonic()<until:app.processEvents();time.sleep(.01)
        app.processEvents();assert not dialog.job.isRunning()
    wait();assert dialog.clean_button.isEnabled() and orphan.is_file() and dialog.table.rowCount()==4
    dialog.grab().save(str(root/'存储管理.png'));dialog.clean_button.click();wait()
    assert not orphan.exists() and source.is_file() and published.read_bytes()==before
    assert not cleanup(s)['errors'];dialog.close();dialog.deleteLater();app.processEvents()
    return dict(staging_uses_selected_material_root=True,new_workspaces_follow_root_existing_paths_preserved=True,
        pending_preview_survives_root_switch=True,cancelled_unused_preview_is_cleaned=True,
        storage_cleanup_preserves_referenced_files_and_archives=True,storage_manager_previews_and_cleans_real_files=True)
