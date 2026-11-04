from pathlib import Path
from time import monotonic
from concurrent.futures import CancelledError
import pytest
from PySide6.QtCore import Qt,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog
from desktop_service import DesktopService
from legacy_import_ui import LegacyImportDialog,LegacyTransferDialog
from desktop_controller import FloatingController
from requirement_intake import proposal
from theme import install_theme,set_theme

@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app

@pytest.fixture
def data(tmp_path,app):
    old=DesktopService(tmp_path/'旧BOX/data/workbox.db');new=DesktopService(tmp_path/'新版/data/workbox.db')
    old.production.import_plan([proposal('迁移软件',batch='原批',requirement='单手册 Word')],token='original')
    p=tmp_path/'迁移软件.docx';p.write_bytes(b'isolated original');old.receive_path(p)
    new.workspaces.configure_materials(tmp_path/'新材料',complete_setup=True);install_theme(new)
    yield old,new
    for window in QApplication.topLevelWidgets():
        if isinstance(window,LegacyImportDialog):window.shutdown();window.close()
    install_theme()

def wait(app,predicate):
    end=monotonic()+30
    while not predicate() and monotonic()<end:app.processEvents();QTest.qWait(10)
    assert predicate()

def test_preview_then_confirm_updates_master_and_report_without_extra_database_operations(data,app):
    old,new=data;d=LegacyImportDialog(new,auto_scan=False);d.show();d.prepare(old.db.path)
    wait(app,lambda:d.worker is None)
    assert d.pages.currentIndex()==1 and not new.production.rows() and d.confirm_button.isEnabled()
    assert '1 个软件' in d.preview_text.toPlainText()
    QTest.mouseClick(d.confirm_button,Qt.LeftButton);wait(app,lambda:d.worker is None)
    assert d.report['complete'] and d.pages.currentIndex()==2 and len(new.production.rows())==1
    assert '完整导入完成' in d.report_text.toPlainText() and Path(d.report['backup']).is_file()
    d.prepare(old.db.path);wait(app,lambda:d.worker is None)
    assert '已经导入过' in d.status.text() and len(new.production.rows())==1
    d.close();d.deleteLater()

def test_missing_files_require_explicit_partial_and_error_keeps_import_disabled(data,app):
    old,new=data
    with old.db.connect() as c:paths={r[0] for t in ('work_files','material_versions','collected_materials') for r in c.execute('SELECT path FROM '+t)}
    for p in paths:
        if Path(p).is_file():Path(p).unlink()
    d=LegacyImportDialog(new,auto_scan=False);d.show();d.prepare(old.db.path);wait(app,lambda:d.worker is None)
    assert d.partial.isVisible() and not d.confirm_button.isEnabled()
    d.partial.setChecked(True);assert d.confirm_button.isEnabled()
    QTest.mouseClick(d.confirm_button,Qt.LeftButton);wait(app,lambda:d.worker is None)
    assert not d.report['complete'] and '部分导入' in d.report_text.toPlainText()
    d.prepare(new.db.path);wait(app,lambda:d.worker is None)
    assert d.plan is None and not d.confirm_button.isEnabled() and '当前' in d.status.text()
    d.close();d.deleteLater()

def test_window_close_safely_cancels_background_operation(data,app):
    old,new=data;d=LegacyImportDialog(new,auto_scan=False);d.show()
    def operation(cancel_event,progress):
        cancel_event.wait(5);raise CancelledError()
    d.start('scan',operation,'正在查找');d.close();wait(app,lambda:d.worker is None)
    assert not d.isVisible() and not new.production.rows() and not d.importer.reports()
    d.deleteLater()

def test_menu_opens_import_assistant_and_monitor_resumes(data,app):
    old,new=data;c=FloatingController(new,tray_enabled=False);assert c.start();wait(app,lambda:not c.busy)
    c.monitor_timer.start();seen=[];timer=QTimer();timer.setInterval(10)
    def dismiss():
        dialog=QApplication.activeModalWidget()
        if isinstance(dialog,LegacyTransferDialog):dialog.copy_button.click()
        elif isinstance(dialog,LegacyImportDialog):
            if not seen:seen.append(not c.monitor_timer.isActive());dialog.reject()
    timer.timeout.connect(dismiss);timer.start()
    try:
        c.panel.legacy_import_action.trigger()
        assert seen==[True] and c.monitor_timer.isActive() and not c.busy
    finally:timer.stop();c.shutdown();c.dispose_ui()

def test_import_dialog_fits_light_dark_and_all_steps(data,app):
    old,new=data
    for mode in ('light','dark'):
        set_theme(mode,new);d=LegacyImportDialog(new,auto_scan=False);d.resize(660,480);d.show();app.processEvents()
        for index in range(3):
            d.pages.setCurrentIndex(index);app.processEvents()
            for control in (d.pages,d.close_button,d.scan_button,d.folder_button,d.file_button,d.history_button):
                assert d.rect().contains(control.mapTo(d,control.rect().bottomRight()))
        d.close();d.deleteLater()


def test_menu_choice_cancel_never_starts_search(data,app,monkeypatch):
    _,service=data;c=FloatingController(service,tray_enabled=False);assert c.start()
    from legacy_import import LegacyImporter
    monkeypatch.setattr(LegacyImporter,'discover',lambda *a,**kw:pytest.fail('取消后不应搜索'))
    seen=[];timer=QTimer();timer.setInterval(10)
    def cancel_choice():
        dialog=QApplication.activeModalWidget()
        if isinstance(dialog,LegacyTransferDialog):seen.append(True);dialog.cancel_button.click()
    timer.timeout.connect(cancel_choice);timer.start()
    try:c.panel.legacy_import_action.trigger();assert seen==[True] and not c.busy
    finally:timer.stop();c.shutdown();c.dispose_ui()


def test_move_choice_and_resumable_report(data,app):
    old,new=data;choice=LegacyTransferDialog();choice.show();app.processEvents()
    assert choice.copy_button.isDefault()
    QTest.mouseClick(choice.move_button,Qt.LeftButton);assert choice.transfer_mode=='move'
    d=LegacyImportDialog(new,auto_scan=False,transfer_mode=choice.transfer_mode);d.show();d.prepare(old.db.path)
    wait(app,lambda:d.worker is None);assert '迁移到 BOX' in d.mode_note.text()
    QTest.mouseClick(d.confirm_button,Qt.LeftButton);wait(app,lambda:d.worker is None)
    assert d.report and d.report['cleanup_complete'] and '原位置已移除材料' in d.report_text.toPlainText()
    d.close();d.deleteLater();choice.deleteLater()
