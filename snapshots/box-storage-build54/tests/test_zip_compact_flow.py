from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
from threading import Event
import time
import pytest
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from zip_intake_ui import ZipIntakeDialog,CompactMaterialForm,OriginalZipDetailsDialog
from quick_classification_ui import QuickClassificationDialog
from material_templates import PRESETS
from task_query import status,unfinished


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    p=tmp_path/'原包测试系统.zip'
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'original word')
    return s,p,tmp_path


def settle(app,condition=lambda:True):
    end=time.monotonic()+12
    while time.monotonic()<end:
        app.processEvents()
        if condition():return
        time.sleep(.01)
    raise AssertionError('ZIP scan or dialog did not finish')


def extracted(app,s,p):
    d=ZipIntakeDialog(s,[str(p)],start_mode='extract');d.show();settle(app,lambda:d.form is not None or bool(d.error.text()))
    assert d.form is not None,d.error.text()
    if d.form.single:assert d.form.grouped_view.isVisible()
    return d


def close(app,d):
    d.reject();settle(app,lambda:not d.scan or not d.scan.isRunning());d.deleteLater();app.processEvents()


def test_raw_default_does_not_extract_or_require_rule(app,data):
    s,p,root=data
    with patch('material_collection.unpack_material_zip',side_effect=AssertionError('must not extract')):
        d=ZipIntakeDialog(s,[str(p)]);d.show();settle(app)
        assert d.raw_radio.isChecked() and d.scan is None and d.form is None
        assert not s.production.rows();d.confirm();assert d.result()==QDialog.Accepted
        result=d.operation();assert result['copied_count']==1
    row=s.production.rows()[0];assert status(row)=='原包已收录' and not unfinished(row) and row['collection_count']==0
    saved=Path(row['original_zips'][0]['path']);assert saved.is_relative_to(root/'材料库') and saved.read_bytes()==p.read_bytes()
    assert not (s.db.path.parent/'材料收件缓存').exists();close(app,d)


def test_raw_custom_name_is_explicit_and_original_filename_is_preserved(app,data):
    s,p,_=data;d=ZipIntakeDialog(s,[str(p)]);d.raw_controls[0]['name'].setText('自定义归属软件');d.confirm();d.operation()
    row=s.production.rows()[0];assert row['name']=='自定义归属软件' and Path(row['original_zips'][0]['path']).name=='自定义归属软件.zip'
    close(app,d)


def test_changed_source_after_confirmation_creates_no_task(app,data):
    s,p,_=data;d=ZipIntakeDialog(s,[str(p)]);d.confirm()
    with ZipFile(p,'w') as z:z.writestr('changed.txt',b'new')
    with pytest.raises(ValueError,match='变化'):d.operation()
    assert not s.production.rows();close(app,d)


def test_raw_duplicate_replace_and_new_keep_saved_history(app,data):
    s,p,_=data;d=ZipIntakeDialog(s,[str(p)]);d.confirm();d.operation();close(app,d)
    first=s.production.rows()[0];old=Path(first['original_zips'][0]['path']);original=old.read_bytes()
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'v2')
    d=ZipIntakeDialog(s,[str(p)]);assert d.raw_controls[0]['target'].currentData()==first['id'];d.confirm();d.operation();close(app,d)
    assert len(s.production.rows())==1 and len(s.production.rows()[0]['original_zips'])==2 and old.read_bytes()==original
    d=ZipIntakeDialog(s,[str(p)]);control=d.raw_controls[0]['action'];control.setCurrentIndex(control.findData('new'));d.confirm();d.operation();close(app,d)
    assert len(s.production.rows())==2


def test_invalid_raw_zip_does_not_create_placeholder_task(app,data):
    s,p,_=data;p.write_bytes(b'bad zip');d=ZipIntakeDialog(s,[str(p)]);d.confirm()
    with pytest.raises(ValueError):d.operation()
    assert not s.production.rows();close(app,d)


def test_extract_has_inline_rule_and_cancel_does_not_save_it(app,data):
    s,p,_=data;before=len(s.classifications.profiles());d=extracted(app,s,p);combo=d.form.class_controls[0];combo.setCurrentIndex(combo.findData('__new__'));settle(app)
    assert d.form.grouped_view.isVisible();d.form.quick_editor.name.setText('取消的新分类');assert not s.production.rows()
    close(app,d);assert len(s.classifications.profiles())==before


def test_extract_inline_rule_is_created_and_used_in_one_confirmation(app,data):
    s,p,_=data;d=extracted(app,s,p);combo=d.form.class_controls[0];combo.setCurrentIndex(combo.findData('__new__'));d.form.quick_editor.name.setText('一份Word定制规则');d.confirm()
    assert d.result()==QDialog.Accepted,d.error.text();result=d.operation();assert not result.get('material_conflicts')
    row=s.production.rows()[0];assert status(row)=='已收齐' and row['package_name']=='一份Word定制规则' and row['required_count']==1
    assert any(r['name']=='一份Word定制规则' for r in s.classifications.profiles());close(app,d)


def test_existing_five_rule_does_not_shrink_to_three_found_files(app,data):
    s,p,_=data
    with ZipFile(p,'w') as z:
        for kind in PRESETS['五件材料'][:3]:z.writestr(kind,kind.encode())
    d=extracted(app,s,p);combo=d.form.class_controls[0];combo.setCurrentIndex(combo.findData('五件材料'));settle(app)
    assert '3/5' in d.form.file_summary.text(),d.form.file_summary.text()
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();row=s.production.rows()[0]
    assert row['required_count']==5 and row['collection_count']==3 and status(row)=='待补材料';close(app,d)


def test_saved_raw_zip_can_be_classified_later_without_redrop(app,data):
    s,p,_=data;d=ZipIntakeDialog(s,[str(p)]);d.confirm();d.operation();close(app,d)
    row=s.production.rows()[0];saved=Path(row['original_zips'][0]['path']);d=extracted(app,s,saved)
    combo=d.form.class_controls[0];combo.setCurrentIndex(combo.findData('单手册 Word'));d.confirm()
    assert d.result()==QDialog.Accepted,d.error.text();d.operation();after=s.production.rows()[0]
    assert after['id']==row['id'] and after['collection_count']==1 and status(after)=='已收齐' and saved.is_file();close(app,d)


def test_duplicate_materials_are_resolved_in_same_zip_window(app,data):
    s,p,root=data;seed=s.production.create_batch('已有批次',['原包测试系统'],['软件说明书.docx']);word=root/'原包测试系统.docx';word.write_bytes(b'old');pid=seed['project_ids'][0];s.production.receive(pid,'软件说明书.docx',word)
    d=extracted(app,s,p);d.confirm();assert d.conflicts.isVisible() and d.result()!=QDialog.Accepted
    assert len(d.conflict_controls)==1;group,tasks,action=d.conflict_controls[0];action.setCurrentIndex(action.findData('overwrite'));d.confirm()
    assert d.result()==QDialog.Accepted,d.error.text();r=d.operation();assert not r.get('material_conflicts') and len(s.production.history(pid,'软件说明书.docx'))==2
    close(app,d)


def test_switch_to_raw_while_scan_runs_waits_before_destroying_thread(app,data):
    s,p,_=data;released=Event();original=__import__('direct_material_intake').DirectMaterialIntake.prepare
    def slow(self,*args,**kwargs):
        released.wait(2);return original(self,*args,**kwargs)
    with patch('direct_material_intake.DirectMaterialIntake.prepare',slow):
        d=ZipIntakeDialog(s,[str(p)],start_mode='extract');d.raw_radio.setChecked(True);d.confirm();assert d.scan.isRunning()
        released.set();settle(app,lambda:not d.scan.isRunning());settle(app);assert d.result()==QDialog.Accepted
    assert not s.production.rows();close(app,d)


def test_quick_classification_is_small_and_saves_without_manager(app,data):
    s,p,_=data;d=QuickClassificationDialog(s);d.show();settle(app);assert d.width()<=520 and d.height()<=360
    d.editor.name.setText('快捷分类');d.save();assert d.result()==QDialog.Accepted and d.profile_id
    assert next(r for r in s.classifications.profiles() if r['id']==d.profile_id)['kinds']==['快捷分类.docx'];d.deleteLater();app.processEvents()


def test_controller_uses_single_zip_confirmation_then_one_save_operation(app,data):
    from desktop_controller import FloatingController
    from PySide6.QtWidgets import QWidget
    s,p,_=data
    class Host:
        def __init__(self):self.service=s;self.panel=QWidget();self.quitting=False;self.closed=False;self.queued=[]
        def _sync_panel_busy(self):pass
        def show_panel(self):self.panel.show()
        def notify(self,*args):pass
        def enqueue(self,title,operation):self.queued.append(operation)
    host=Host();seen=[]
    def save():
        dialog=QApplication.activeModalWidget()
        if isinstance(dialog,ZipIntakeDialog):seen.append(dialog.windowTitle());dialog.save_button.click()
    timer=QTimer();timer.timeout.connect(save);timer.start(10)
    try:FloatingController._resolve_zip_intake(host,[str(p)])
    finally:timer.stop();host.panel.close()
    assert seen==['确认收录'] and len(host.queued)==1;host.queued[0]();assert status(s.production.rows()[0])=='原包已收录'
