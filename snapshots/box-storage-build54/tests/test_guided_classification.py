import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox,QInputDialog
from classification_ui import ClassificationDialog
from desktop_service import DesktopService
from theme import install_theme,set_theme


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


@pytest.fixture
def editor(tmp_path,app):
    s=DesktopService(tmp_path/'data/workbox.db');install_theme(s)
    d=ClassificationDialog(s);d.show();d.new();app.processEvents()
    yield d
    d.dirty=False;d.close();d.deleteLater();install_theme()


def starter(d,name):
    record=next(r for r in d.records if r['name']==name)
    d.starter.setCurrentIndex(d.starter.findData(record['id']))


def test_template_name_save_creates_without_extra_confirmation_and_original_is_unchanged(editor,app,monkeypatch):
    d=editor;before=d.catalog.profiles()
    assert d.materials.rowCount()==0 and not d.save_button.isEnabled() and d.materials.isColumnHidden(1)
    assert d.starter_section.isVisible() and not d.advanced_panel.isVisible()
    starter(d,'五件材料');d.name.setText('我的交付资料')
    assert d.materials.rowCount()==5 and '5 份' in d.summary.text() and d.save_button.isEnabled()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:pytest.fail('New classification should save in one action'))
    QTest.mouseClick(d.save_button,Qt.LeftButton);app.processEvents()
    assert d.catalog.packages()['我的交付资料']==next(r for r in before if r['name']=='五件材料')['kinds']
    assert next(r for r in d.catalog.profiles() if r['name']=='五件材料')==next(r for r in before if r['name']=='五件材料')
    assert not d.save_button.isEnabled() and '已保存' in d.guide.text()


def test_quick_add_choose_format_paste_filename_remove_and_inline_validation(editor,app):
    d=editor;d.name.setText('验收材料')
    QTest.mouseClick(d.quick_buttons[0],Qt.LeftButton);app.processEvents()
    box=d.materials.cellWidget(0,0);assert box.name_edit.text()=='软件说明书'
    box.name_edit.setText('验收报告');box.format_combo.setCurrentIndex(box.format_combo.findData('.pdf'))
    assert d.materials.item(0,0).text()=='验收报告.pdf' and 'PDF 1份' in d.summary.text()
    box.name_edit.setText('签字页.docx')
    assert box.name_edit.text()=='签字页' and d.materials.item(0,0).text()=='签字页.docx'
    QTest.mouseClick(d.quick_buttons[0],Qt.LeftButton)
    d.materials.cellWidget(1,0).name_edit.setText('签字页')
    assert not d.save_button.isEnabled() and '名称和格式相同' in d.guide.text()
    QTest.mouseClick(d.materials.cellWidget(1,2),Qt.LeftButton)
    assert d.materials.rowCount()==1 and d.save_button.isEnabled()


def test_browsing_templates_does_not_prompt_but_replacing_hand_edits_protects_draft(editor,monkeypatch):
    d=editor;messages=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:messages.append(a[1]) or QMessageBox.No)
    starter(d,'单手册 Word');d.name.setText('自己的分类名');starter(d,'五件材料')
    assert not messages and d.name.text()=='自己的分类名' and d.materials.rowCount()==5
    d.materials.cellWidget(0,0).name_edit.setText('我改过的说明书');starter(d,'桌面14件')
    assert messages==['更换起始模板'] and d.materials.item(0,0).text()=='我改过的说明书.docx'
    assert d.starter.currentData()==next(r['id'] for r in d.records if r['name']=='五件材料')


def test_hidden_advanced_values_are_preserved_and_edit_explains_changes(editor,app,monkeypatch):
    d=editor;pid=d.catalog.save('审查报告',['报告.docx'],{'报告.docx':['审查单']},'collect',active=False)
    d.reload(pid);assert not d.advanced_panel.isVisible() and '停用' in d.guide.text()
    QTest.mouseClick(d.advanced_button,Qt.LeftButton);app.processEvents()
    assert d.advanced_panel.isVisible() and not d.materials.isColumnHidden(1)
    d.active.setChecked(True);d.name.setText('审查资料');messages=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:messages.append(a[2]) or QMessageBox.Cancel)
    d.save_button.click();assert d.catalog.profiles()[-1]['name']=='审查报告'
    assert messages and '停用 → 启用' in messages[0] and '新批次' in messages[0]
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Save);d.save_button.click()
    saved=next(r for r in d.catalog.profiles() if r['id']==pid)
    assert saved['aliases']=={'报告.docx':['审查单']} and saved['active'] and saved['mode']=='collect'


def test_other_format_is_available_and_cancel_keeps_previous_format(editor,monkeypatch):
    d=editor;d.quick_add('');box=d.materials.cellWidget(0,0);box.name_edit.setText('附加资料')
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**k:('json',True))
    box.format_combo.setCurrentIndex(box.format_combo.findData(None));assert d.materials.item(0,0).text()=='附加资料.json'
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**k:('',False))
    box.format_combo.setCurrentIndex(box.format_combo.findData(None));assert d.materials.item(0,0).text()=='附加资料.json'


def test_invalid_values_do_not_show_confirmation_and_failed_save_keeps_draft(editor,monkeypatch):
    d=editor;starter(d,'单手册 Word');d.name.setText('五件材料')
    assert not d.save_button.isEnabled() and '已经有了' in d.guide.text()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:pytest.fail('Invalid values must be fixed inline'))
    d.save();assert '已经有了' in d.error.text()
    d.name.setText('还没有保存的材料');monkeypatch.setattr(d.catalog,'save',lambda *a,**k:(_ for _ in ()).throw(OSError('disk unavailable')))
    d.save_button.click();assert d.dirty and d.name.text()=='还没有保存的材料' and '内容仍保留' in d.error.text()


def test_created_rule_recognizes_files_and_records_collection(editor,tmp_path):
    d=editor;starter(d,'单手册 Word');d.name.setText('验收材料')
    d.materials.cellWidget(0,0).name_edit.setText('验收报告');d.materials.item(0,1).setText('验收单')
    d.mode.setCurrentIndex(d.mode.findData('collect'));d.save_button.click()
    source=tmp_path/'设备系统_验收单.docx';source.write_bytes(b'isolated report')
    plan=d.service.receive_interactive([str(source)])['material_preview']
    assert plan['entries'][0]['package']=='验收材料' and plan['mode']=='collect'
    d.service.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
    task=d.service.production.rows()[0]
    assert task['production_state']=='已收纳' and task['collection_count']==1 and task['reviewed_count']==task['submitted_count']==0


def test_guidance_and_controls_fit_new_edit_advanced_light_and_dark(editor,app):
    d=editor
    for mode in ('light','dark'):
        set_theme(mode,d.service)
        for advanced in (False,True):
            d.loading=True;d.load(None);d.loading=False;starter(d,'14件＋双视频')
            d.advanced_button.setChecked(advanced);d.resize(760,560)
            for _ in range(5):app.processEvents()
            for control in (d.name,d.mode,d.materials,d.advanced_button,d.save_button,d.cancel_button,d.summary):
                assert d.rect().contains(control.mapTo(d,control.rect().bottomRight()))
            box=d.materials.cellWidget(0,0)
            assert box.rect().contains(box.name_edit.geometry()) and box.rect().contains(box.format_combo.geometry())
            assert d.materials.rowCount()==16 and '16 份' in d.summary.text()
