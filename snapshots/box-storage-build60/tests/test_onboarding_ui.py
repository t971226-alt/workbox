"""首次设置与随时可读的使用指南，不改写已有项目或历史。"""
import importlib
import sqlite3

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QRadioButton, QSpinBox, QStackedWidget

from compact_ui import CompactPanel, DirectorySettingsDialog
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def onboarding():
    assert importlib.util.find_spec('onboarding_ui') is not None, '首次设置界面尚未实现'
    return importlib.import_module('onboarding_ui')


def finish_steps(dialog):
    dialog.finish_button.click()


def test_first_use_cancel_preserves_defaults_without_writes(qapp, service, tmp_path):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service)
    assert dialog.work_root.text() == before['work_root']
    assert not hasattr(dialog, 'archive_root')
    dialog.work_root.setText(str(tmp_path / 'chosen-work'))
    dialog.cancel_button.click()
    assert dialog.result() == QDialog.Rejected
    assert service.workspaces.settings() == before
    assert not (tmp_path / 'chosen-work').exists()
    assert service.db.counts()['projects'] == 0


def test_first_use_finish_persists_manual_rule_and_completion(qapp, service):
    dialog = onboarding().FirstRunDialog(service)
    finish_steps(dialog)
    assert dialog.result() == QDialog.Accepted
    saved = service.workspaces.settings()
    assert saved['auto_deliver_zip'] is False
    assert saved['auto_create_projects'] is True
    assert saved['zip_delivery_min_files'] == 4
    assert saved['stable_seconds'] == 10
    assert service.workspaces.setup_required() is False
    from pathlib import Path
    assert not Path(saved['archive_root']).exists()
    assert service.db.counts()['projects'] == 0


def test_first_use_has_only_directories_and_saves_in_one_click(qapp, service):
    dialog = onboarding().FirstRunDialog(service)
    dialog.show()
    qapp.processEvents()
    assert not dialog.findChildren(QRadioButton)
    assert not dialog.findChildren(QSpinBox)
    assert not dialog.findChildren(QStackedWidget)
    assert not hasattr(dialog, 'next_button') and not hasattr(dialog, 'back_button')
    assert not hasattr(dialog, 'archive_root')
    assert dialog.finish_button.isVisible() and dialog.finish_button.isDefault()
    dialog.finish_button.click()
    assert dialog.result() == QDialog.Accepted
    assert not service.workspaces.settings()['auto_deliver_zip']
    assert not service.workspaces.setup_required()


def test_invalid_paths_keep_editable_form_without_marking_complete(qapp, service):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service)
    dialog.work_root.clear()
    finish_steps(dialog)
    assert dialog.result() != QDialog.Accepted
    assert dialog.error.text()
    assert dialog.work_root.text() == ''
    assert service.workspaces.settings() == before
    assert service.workspaces.setup_required()
    dialog.reject()


def test_single_material_directory_preserves_existing_archive_files(qapp, service, tmp_path):
    from pathlib import Path
    original = service.workspaces.settings()
    service.workspaces.configure(original['work_root'], original['archive_root'], complete_setup=True)
    history = Path(original['archive_root']) / '历史包.zip'
    history.write_bytes(b'archive history preserved')
    before = service.db.counts()
    dialog = DirectorySettingsDialog(service)
    assert not hasattr(dialog, 'archive_root')
    dialog.work_root.setText(str(tmp_path / 'new-materials'))
    dialog.save()
    assert dialog.result() == QDialog.Accepted
    assert service.workspaces.settings()['archive_root'] == original['archive_root']
    assert history.read_bytes() == b'archive history preserved'
    assert service.db.counts() == before


def test_save_failure_keeps_input_and_allows_retry(qapp, service, monkeypatch, tmp_path):
    dialog = onboarding().FirstRunDialog(service)
    chosen = str(tmp_path / 'chosen-materials')
    dialog.work_root.setText(chosen)
    configure = service.workspaces.configure
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('isolated database is locked')
    monkeypatch.setattr(service.workspaces, 'configure', fail)
    dialog.finish_button.click()
    assert dialog.result() != QDialog.Accepted and 'locked' in dialog.error.text()
    assert dialog.work_root.text() == chosen
    monkeypatch.setattr(service.workspaces, 'configure', configure)
    dialog.finish_button.click()
    assert dialog.result() == QDialog.Accepted
    assert service.workspaces.settings()['work_root'] == chosen
    assert not service.workspaces.settings()['auto_deliver_zip']


def test_settings_has_no_legacy_modes_or_thresholds(qapp, service):
    dialog = DirectorySettingsDialog(service)
    assert not dialog.findChildren(QRadioButton) and not dialog.findChildren(QSpinBox)
    assert not hasattr(dialog, 'archive_root') and not hasattr(dialog, 'automatic_mode_button')
    dialog.reject()


@pytest.mark.parametrize('first_run', [True, False])
def test_runtime_save_failure_preserves_form_and_pending_setup(qapp, service, monkeypatch, first_run):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service) if first_run else DirectorySettingsDialog(service)
    chosen = dialog.work_root.text() + '-chosen'
    dialog.work_root.setText(chosen)
    def fail(*args, **kwargs):
        raise RuntimeError('isolated invalid setup state')
    monkeypatch.setattr(service.workspaces, 'configure', fail)
    dialog.save()
    assert dialog.result() != QDialog.Accepted
    assert 'invalid setup state' in dialog.error.text()
    assert dialog.work_root.text() == chosen
    assert service.workspaces.settings() == before
    assert service.workspaces.setup_required()
    dialog.reject()


def test_quick_guide_reads_actual_preferences_and_never_writes(qapp, service):
    current = service.workspaces.settings()
    service.workspaces.configure(current['work_root'], current['archive_root'], 15,
        auto_create_projects=False, auto_deliver_zip=False, zip_delivery_min_files=8)
    before = service.workspaces.settings()
    guide = onboarding().QuickStartDialog(service)
    assert not hasattr(guide, 'mode_label')
    assert before['work_root'] in guide.work_root.text()
    assert not hasattr(guide, 'archive_root')
    assert not hasattr(guide, 'auto_create_label')
    guide.close_button.click()
    assert service.workspaces.settings() == before
    service.workspaces.configure(before['work_root'], before['archive_root'], 15,
        auto_deliver_zip=True, zip_delivery_min_files=8)
    guide = onboarding().QuickStartDialog(service)
    assert not hasattr(guide, 'mode_label')
    guide.reject()


def test_panel_help_opens_real_read_only_guide(qapp, service):
    panel = CompactPanel(service)
    before = service.workspaces.settings()
    seen = []
    def close_guide():
        guide = QApplication.activeModalWidget()
        assert isinstance(guide, onboarding().QuickStartDialog)
        seen.append(guide.windowTitle())
        guide.close_button.click()
    QTimer.singleShot(0, close_guide)
    panel.help_button.click()
    assert seen and service.workspaces.settings() == before
    panel.close()
