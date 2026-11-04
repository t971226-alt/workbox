from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication,QPushButton
from desktop_service import DesktopService
from compact_ui import CompactPanel
from production_ui import MaterialDialog
from saved_locations import collection_location
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料 保存目录',complete_setup=True)
    ids=[s.production.create_batch('目录验证'+str(i),[name],['软件说明书.docx'])['project_ids'][0] for i,name in enumerate(['甲软件系统','乙软件系统'])]
    for i,pid in enumerate(ids):
        p=tmp_path/f'来源 {i}.docx';p.write_bytes(f'version {i}'.encode());s.production.receive(pid,'软件说明书.docx',p)
    return s

def test_resolves_published_copy_not_internal_archive_and_refuses_missing(data):
    s=data;row=s.production.rows()[0];before=s.production.rows()
    target=collection_location(s,row['id'],'软件说明书.docx')
    assert target.parent==Path(row['collection_folder']).resolve() and target!=Path(row['materials'][0]['path'])
    assert target.read_bytes()==b'version 0'
    assert collection_location(s,row['id'])==target.parent
    assert s.production.rows()==before
    target.unlink()
    with pytest.raises(ValueError,match='暂不可用'):collection_location(s,row['id'],'软件说明书.docx')

def test_no_material_does_not_create_empty_folder(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');pid=s.production.create_batch('空目录验证',['未收录系统'],['软件说明书.docx'])['project_ids'][0]
    before=list(tmp_path.rglob('*'))
    with pytest.raises(ValueError,match='尚未收录'):collection_location(s,pid)
    with pytest.raises(ValueError,match='尚未完成收录'):collection_location(s,pid,'软件说明书.docx')
    assert list(tmp_path.rglob('*'))==before

def test_row_material_menu_and_details_open_correct_saved_location(app,data,monkeypatch):
    import saved_locations
    s=data;install_theme(s);p=CompactPanel(s);p.production_page.show_legacy_workspace();p.show();app.processEvents();page=p.production_page;g=page.grouped;opened=[]
    monkeypatch.setattr(saved_locations,'open_location',lambda target:opened.append(Path(target)))
    try:
        before=s.production.rows();first,second=page.rows
        frame=g.task_buttons[second['id']].parentWidget();menu=next(b.menu() for b in frame.findChildren(QPushButton) if b.menu())
        next(a for a in menu.actions() if a.text()=='打开保存目录').trigger()
        assert opened[-1]==collection_location(s,second['id'])
        g.task_buttons[first['id']].click();app.processEvents()
        more=next(b for b in g.body.findChildren(QPushButton) if b.accessibleName()=='材料详情与版本：软件说明书.docx' and b.isVisible())
        more.menu().actions()[0].trigger();assert opened[-1]==collection_location(s,first['id'],'软件说明书.docx')
        dialog=MaterialDialog(page,first,first['materials'][0]);dialog.show();app.processEvents();dialog.folder_button.click()
        assert opened[-1]==collection_location(s,first['id'],'软件说明书.docx')
        dialog.close();dialog.deleteLater();assert s.production.rows()==before
        target=collection_location(s,first['id'],'软件说明书.docx');target.unlink();g.open_saved_location(first,first['materials'][0])
        assert '暂不可用' in page.error.text() and len(opened)==3
    finally:p.conversation_page.shutdown();p.document_pane.shutdown();p.close();p.deleteLater();install_theme()
