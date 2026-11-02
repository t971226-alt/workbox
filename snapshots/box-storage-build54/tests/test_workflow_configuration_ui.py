"""工作设置界面通过真实服务保存规则，取消和失败不改变既有设置。"""
import time
import sqlite3
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from compact_ui import DirectorySettingsDialog, CompactPanel
from desktop_service import DesktopService
from desktop_controller import FloatingController


@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path/'data/workbox.db')


def test_save_roundtrip_directory_preserves_unrelated_rules(qapp, service, tmp_path):
    current = service.workspaces.settings()
    service.workspaces.configure(current['work_root'], current['archive_root'], 25,
        auto_create_projects=False, auto_deliver_zip=False, zip_delivery_min_files=8)
    before = service.workspaces.settings()
    dialog = DirectorySettingsDialog(service)
    chosen = str(tmp_path / 'materials')
    dialog.work_root.setText(chosen)
    assert not hasattr(dialog, 'auto_deliver_zip') and not hasattr(dialog, 'zip_delivery_min_files')
    dialog.save()
    assert dialog.result() == QDialog.Accepted
    saved = service.workspaces.settings()
    assert saved == dict(before, work_root=chosen)
    restored = DirectorySettingsDialog(service)
    assert restored.work_root.text() == chosen
    restored.reject()


def test_cancel_directory_change_does_not_write(qapp, service, tmp_path):
    before = service.workspaces.settings()
    dialog = DirectorySettingsDialog(service)
    assert not hasattr(dialog, 'reset_rules_button')
    dialog.work_root.setText(str(tmp_path / 'not-saved'))
    dialog.reject()
    assert service.workspaces.settings() == before


def test_invalid_directory_does_not_save_any_rule(qapp,service):
    before=service.workspaces.settings()
    dialog=DirectorySettingsDialog(service)
    dialog.work_root.clear()
    dialog.save()
    assert dialog.result()!=QDialog.Accepted and dialog.error.text()
    assert service.workspaces.settings()==before
    dialog.reject()


def test_database_failure_keeps_form_and_reports_error(qapp,service,monkeypatch):
    before=service.workspaces.settings()
    dialog=DirectorySettingsDialog(service)
    chosen=dialog.work_root.text()+'-chosen'
    dialog.work_root.setText(chosen)
    def fail(*args,**kwargs):
        raise sqlite3.OperationalError('isolated database is locked')
    monkeypatch.setattr(service.workspaces,'configure',fail)
    dialog.save()
    assert dialog.result()!=QDialog.Accepted and 'locked' in dialog.error.text()
    assert dialog.work_root.text()==chosen
    assert service.workspaces.settings()==before
    dialog.reject()


def test_rule_feedback_reflects_current_configuration(qapp,service):
    settings=service.workspaces.settings()
    service.workspaces.configure(settings['work_root'],settings['archive_root'],18,
        auto_deliver_zip=True,zip_delivery_min_files=8)
    panel=CompactPanel(service)
    panel.refresh_archive_status()
    assert '8' in panel.status_buttons['已交付'].toolTip()
    service.workspaces.configure(settings['work_root'],settings['archive_root'],18,auto_deliver_zip=False)
    panel.refresh_archive_status()
    assert '关闭' in panel.status_buttons['已交付'].toolTip()
    assert '人工' in panel.status_buttons['已交付'].toolTip()
    panel.hide()


def test_disabled_creation_empty_project_list_has_actionable_feedback(qapp,service,tmp_path,monkeypatch):
    settings=service.workspaces.settings()
    service.workspaces.configure(settings['work_root'],settings['archive_root'],auto_create_projects=False)
    app=FloatingController(service,tray_enabled=False)
    assert app.start(); app.monitor_timer.stop()
    monkeypatch.setattr(app.panel,'choose_file_project',lambda *_:pytest.fail('空项目列表不应打开无法完成的选择框'))
    source=tmp_path/'未建立的项目.txt';source.write_text('isolated')
    try:
        app.receive({'kind':'files','paths':[str(source)]})
        end=time.monotonic()+8
        while app.busy and time.monotonic()<end:
            qapp.processEvents();time.sleep(.01)
        assert not app.busy and service.db.counts()['projects']==0 and source.exists()
        assert '创建' in app.panel.notice.text() and '项目' in app.panel.notice.text()
    finally:
        app.shutdown()
