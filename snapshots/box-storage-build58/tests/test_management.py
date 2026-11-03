def test_independent_management_and_real_folder_operations(tmp_path):
    from scripts.verify_management import verify
    assert all(verify(tmp_path/'management').values())


def test_other_file_types_stay_available_without_the_long_suffix_menu(tmp_path):
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication
    from desktop_service import DesktopService
    from management_ui import RuleManagementDialog
    app=QApplication.instance() or QApplication([])
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    d=RuleManagementDialog(s,new=True);d.editor.name.setText('数据库资料')
    with patch('management_ui.QInputDialog.getText',return_value=('db',True)):d.form.other()
    d.save();assert not d.dirty,d.status.text()
    p=next(p for p in s.classifications.profiles(True) if p['name']=='数据库资料')
    assert any(k.endswith('.db') for k in p['kinds'])
    d.reject();d.deleteLater();app.processEvents()


def test_folder_rename_rolls_back_files_and_binding_on_database_failure(tmp_path,monkeypatch):
    from desktop_service import DesktopService
    from intake_folders import type_draft,records
    from folder_management import create,bind,relocate
    import unified_storage
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    p=type_draft('文档规则',{'word':1});pid=s.classifications.save(p['name'],p['kinds'],mode='collect',family_map=p['family_map'],name_patterns=p['name_patterns']);p['id']=pid
    target=create(s,tmp_path/'saved','材料目录');bind(s,target,p);(target/'保留.txt').write_bytes(b'original')
    def fail(*args):raise OSError('database rewrite failure')
    monkeypatch.setattr(unified_storage,'_rewrite_references',fail)
    import pytest
    with pytest.raises(OSError):relocate(s,target,target.with_name('修改后'),rename=True)
    assert (target/'保留.txt').read_bytes()==b'original' and not target.with_name('修改后').exists()
    assert records(s)[0]['name']==target.name
