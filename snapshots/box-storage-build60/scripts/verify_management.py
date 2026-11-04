"""Exercise real independent managers, picker cancellation and durable routing."""
from pathlib import Path


def verify(root):
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QMessageBox,QDialog,QPushButton
    from desktop_service import DesktopService
    from management_ui import RuleManagementDialog,FolderManagementDialog
    from folder_management import folders,create,bind,remove,relocate
    from folder_route_ui import FolderRoute
    from intake_folders import records
    from compact_ui import CompactPanel
    from theme import install_theme,set_theme
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);install_theme()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    def settle():
        for _ in range(6):app.processEvents()
        from PySide6.QtCore import QCoreApplication,QEvent
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
    f=FolderManagementDialog(s);f.show();settle();assert f.table.rowCount()==0
    f.create_button.click();assert f.draft and not folders(s)
    f.name_edit.setText('取消的新目录');f.cancel_create();assert not folders(s) and not (root/'saved/取消的新目录').exists()
    parent=root/'自选位置';parent.mkdir()
    f.create_button.click();f.name_edit.setText('文档归档')
    with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(parent)) as picker:f.choose_parent();assert picker.call_count==1
    f.confirm_create();target=parent/'文档归档';assert target.is_dir() and folders(s)[0]['rule'] is None
    d=RuleManagementDialog(s,new=True);d.show();settle();d.editor.name.setText('文档收纳');assert d.form.folder_button.text()=='请为规则选择文件夹'
    with patch('management_ui.QFileDialog.getExistingDirectory',return_value=''):d.choose_folder()
    assert not records(s)
    d.form.count([0],2);assert d.editor.table.rowCount()==2
    d.form.keyword(0,'');d.form.keyword(1,'代码+手册')
    with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(target)):d.choose_folder()
    assert not records(s);d.save();assert not d.dirty,d.status.text()
    p=next(p for p in s.classifications.profiles(True) if p['name']=='文档收纳');assert len(p['kinds'])==2
    from name_patterns import match
    name='全源人才聚合研判招聘系统'
    assert match(name+'_代码_手册.doc',name,p['kinds'],p['family_map'],p['name_patterns'])==p['kinds'][1]
    assert FolderRoute(s,p,'测试软件').storage('测试软件')['root']==str(target)
    assert len(records(s))==1
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    source=root/(name+'.docx');source.write_bytes(b'original word bytes')
    intake=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(source)],include_known=True,keep_archives=True))
    group=intake.form.grouped_view.groups[0];intake.form.grouped_view.assign(group,p);settle();intake.confirm()
    assert intake.operation,intake.error.text();intake.operation();intake.reject();intake.deleteLater();settle()
    assert (target/name/source.name).read_bytes()==source.read_bytes()
    # Binding changes never rewrite existing task data; explicit folder rename does.
    oldfile=target/'原有文件.txt';oldfile.write_bytes(b'untouched')
    new=target.with_name('文档存档');relocate(s,target,new,rename=True);assert (new/oldfile.name).read_bytes()==b'untouched' and Path(records(s)[0]['root'])/records(s)[0]['name']==new
    row=s.production.rows()[0];assert Path(row['collection_folder']).is_relative_to(new)
    assert Path(row['materials'][0]['path']).read_bytes()==source.read_bytes() and source.is_file()
    remove(s,new);assert new.is_dir() and (new/oldfile.name).read_bytes()==b'untouched' and not folders(s)
    # Reject reserved app storage and same-name collisions without deleting anything.
    try:create(s,parent,new.name);raise AssertionError('Overwrote an existing folder')
    except FileExistsError:pass
    try:create(s,s.db.path.parent,'不应创建');raise AssertionError('Allowed private storage')
    except ValueError:pass
    bind(s,new,p);f.refresh();settle();assert d.form.rows.count()==1 and d.form.rows.itemAt(0).widget().isVisible();f.grab().save(str(root/'folder-manager.png'));d.grab().save(str(root/'rule-manager.png'))
    set_theme('dark');settle();d.grab().save(str(root/'rule-manager-dark.png'));set_theme('light')
    panel=CompactPanel(s);panel.show();settle();menu=panel.more_button.menu();names=[a.text() for a in menu.actions()]
    assert names.count('规则管理')==1 and names.count('文件夹管理')==1
    page=panel.production_page;page.task_workspace.rules.rebuild();assert not hasattr(page.task_workspace.rules,'add_button')
    assert not any('管理全部分类' in a.text() for a in page.task_workspace.more_menu.actions())
    page.build_category_menu();assert not any('添加分类' in a.text() or '管理全部分类' in a.text() for a in page.category_menu.actions())
    panel.conversation_page.shutdown();panel.document_pane.shutdown();panel.close();panel.deleteLater()
    d.dirty=False;d.reject();d.deleteLater();f.reject();f.deleteLater();settle()
    return dict(two_independent_settings_management_entries=True,duplicate_workbench_management_entries_removed=True,
                native_folder_picker_and_cancel_leave_no_binding=True,custom_folder_creation_and_binding_route_intake=True,
                grouped_keywords_support_file_type_families=True,folder_rename_updates_binding_and_preserves_files=True,
                renamed_receipts_remain_readable_and_original_sources_are_untouched=True,
                removing_management_keeps_materials=True,reserved_and_existing_folders_are_not_overwritten=True)
