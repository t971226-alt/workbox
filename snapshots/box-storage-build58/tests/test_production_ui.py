from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QInputDialog
from PySide6.QtTest import QTest

from compact_ui import CompactPanel
from desktop_service import DesktopService
from production_ui import MaterialDialog, NewProductionDialog
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    result = service.production.create_batch('十月制作', ['库存管理系统', '客户管理系统'], ['申请书', '说明书'])
    install_theme(service)
    value = CompactPanel(service)
    value.tabs.setCurrentWidget(value.production_page)
    value.show()
    qapp.processEvents()
    page = value.production_page
    page.job_requested.connect(lambda caption, operation: (operation(), value.refresh()))
    yield value
    value.document_pane.shutdown()
    value.close()
    install_theme()


def prepare(panel, tmp_path, row=0):
    page = panel.production_page
    pid = page.rows[row]['id']
    for kind in ('申请书', '说明书'):
        source = tmp_path / f'{kind}.txt'
        source.write_text('验证材料', encoding='utf-8')
        page.store.receive(pid, kind, source)
    page.refresh()
    for m in page.rows[row]['materials']:
        page.store.review(m['id'], True)
    page.refresh()


def test_click_checks_and_batch_submission_persist(panel, qapp, tmp_path, monkeypatch):
    page = panel.production_page
    prepare(panel, tmp_path)
    assert panel.tabs.currentWidget() is panel.workspace_page and page.isVisible()
    page.table.item(0, 0).setCheckState(Qt.Checked)
    assert len(page.checked()) == 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Yes)
    QTest.mouseClick(page.submit_button, Qt.LeftButton)
    qapp.processEvents()
    assert page.rows[0]['production_state'] == '已提交'
    page.refresh()
    assert '已提交' in page.table.item(0, 6).text()
    assert not page.checked()
    assert page.rows[1]['production_state'] == '材料未齐'


def test_selected_checks_clear_when_new_version_arrives(panel, tmp_path):
    page = panel.production_page
    prepare(panel, tmp_path)
    page.select_ready()
    assert page.checked()
    source = tmp_path / '新说明书.txt'
    source.write_text('新一版', encoding='utf-8')
    page.store.receive(page.rows[0]['id'], '说明书', source)
    page.refresh()
    assert not page.checked()
    assert page.rows[0]['materials'][0]['state'] == '检查通过'
    assert page.rows[0]['materials'][1]['state'] == '待检查'


def test_review_dialog_and_cancel_do_not_leak_between_rows(panel, qapp, tmp_path):
    page = panel.production_page
    pid = page.rows[0]['id']
    source = tmp_path / '申请书.txt'
    source.write_text('检查', encoding='utf-8')
    page.store.receive(pid, '申请书', source)
    page.refresh()
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][0])
    dialog.show()
    qapp.processEvents()
    QTest.mouseClick(dialog.reject_button, Qt.LeftButton)
    assert '原因' in dialog.error.text()
    dialog.note.setPlainText('格式需调整')
    QTest.mouseClick(dialog.reject_button, Qt.LeftButton)
    assert page.rows[0]['materials'][0]['state'] == '需修改'
    assert page.rows[1]['materials'][0]['state'] == '待生成'
    dialog.deleteLater()
    current = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][0])
    current.note.setPlainText('取消时不保存')
    current.reject()
    assert len(page.store.history(pid, '申请书')[0]['reviews']) == 1
    current.deleteLater()


def test_new_batch_cancel_validation_and_repeat_independent(panel, qapp):
    page = panel.production_page
    before = page.service.db.counts()
    dialog = NewProductionDialog(page)
    dialog.validate()
    assert '名称' in dialog.error.text()
    dialog.reject()
    dialog.deleteLater()
    assert page.service.db.counts() == before
    new = page.store.create_batch('下轮重做', [page.rows[0]['name']], ['申请书', '说明书'])
    page.refresh()
    page.batch.setCurrentIndex(page.batch.findData(new['batch_id']))
    assert len(page.rows) == 1
    assert all(m['state'] == '待生成' for m in page.rows[0]['materials'])


def test_filter_and_theme_keep_identity_and_material_state(panel, qapp, tmp_path):
    page = panel.production_page
    prepare(panel, tmp_path)
    pid = page.rows[0]['id']
    page.search.setText('库存')
    assert len(page.rows) == 1 and page.rows[0]['id'] == pid
    set_theme('dark', page.service)
    qapp.processEvents()
    assert page.rows[0]['production_state'] == '待提交'
    set_theme('light', page.service)
    page.state_filter.setCurrentText('材料未齐')
    assert len(page.rows) == 0
    page.search.clear()
    assert page.rows[0]['name'] == '客户管理系统'


def test_new_batch_button_saves_and_selects_new_round(panel, qapp):
    page = panel.production_page
    chat = panel.conversation_page
    panel.open_intake()
    assert chat.composer.hasFocus() and chat.isVisible() and page.isVisible()
    chat.receive_text('批次：再次制作\n套餐：单手册 Word\n1. 库存管理系统')
    tid = chat.store.turns(chat.current_id)[-1]['id']
    assert len(page.store.rows()) == 2
    chat.store.create_tasks(tid)
    panel.refresh()
    assert page.batch.currentData() is None
    assert len(page.rows) == 3 and page.rows[-1]['name'] == '库存管理系统'
    assert page.rows[-1]['materials'][0]['state'] == '待生成'


def test_import_submit_void_and_export_controls(panel, qapp, tmp_path, monkeypatch):
    page = panel.production_page
    source = tmp_path / '说明书.txt'
    source.write_text('逐份提交材料', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(source), ''))
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][1])
    QTest.mouseClick(dialog.import_button, Qt.LeftButton)
    dialog.deleteLater()
    current = page.rows[0]['materials'][1]
    assert current['state'] == '待检查'
    dialog = MaterialDialog(page, page.rows[0], current)
    QTest.mouseClick(dialog.pass_button, Qt.LeftButton)
    dialog.deleteLater()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Yes)
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][1])
    QTest.mouseClick(dialog.submit_button, Qt.LeftButton)
    dialog.deleteLater()
    assert page.rows[0]['production_state'] == '部分提交'
    target = tmp_path / '材料表.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(target), ''))
    QTest.mouseClick(page.export_button, Qt.LeftButton)
    assert '已提交' in target.read_text(encoding='utf-8-sig')
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][1])
    dialog.note.setPlainText('此材料误记提交')
    QTest.mouseClick(dialog.void_button, Qt.LeftButton)
    dialog.deleteLater()
    assert page.rows[0]['materials'][1]['state'] == '检查通过'


def test_bind_and_reuse_controls_create_unchecked_versions(panel, tmp_path, monkeypatch):
    page = panel.production_page
    pid = page.rows[0]['id']
    source = tmp_path / '通用文件.txt'
    source.write_text('复用的正文', encoding='utf-8')
    page.service.workspaces.import_file(source, pid)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda parent, title, prompt, choices, *args: (choices[0], True))
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][0])
    QTest.mouseClick(dialog.bind_button, Qt.LeftButton)
    dialog.deleteLater()
    assert page.rows[0]['materials'][0]['state'] == '待检查'
    old_id = page.rows[0]['materials'][0]['id']
    page.store.review(old_id, True)
    page.store.submit([old_id])
    new = page.store.create_batch('复用新轮次', ['库存管理系统'], ['申请书'])
    page.refresh()
    page.batch.setCurrentIndex(page.batch.findData(new['batch_id']))
    dialog = MaterialDialog(page, page.rows[0], page.rows[0]['materials'][0])
    QTest.mouseClick(dialog.reuse_button, Qt.LeftButton)
    dialog.deleteLater()
    assert page.rows[0]['materials'][0]['state'] == '待检查'
    assert page.rows[0]['materials'][0]['id'] != old_id


def test_real_controller_refreshes_regenerated_material_and_runs_review_job(qapp, tmp_path):
    import time
    from desktop_controller import FloatingController
    service = DesktopService(tmp_path / 'controller' / 'workbox.db')
    batch = service.production.create_batch('后台材料检查', ['后台测试系统'], ['说明书'])
    pid = batch['project_ids'][0]
    source = tmp_path / '说明书.txt'
    source.write_text('第一版', encoding='utf-8')
    saved = service.production.receive(pid, '说明书', source)
    Path(saved['file']['path']).write_text('重新生成', encoding='utf-8')
    service.production.scan()
    service.production._observed = {k: v - 20 for k, v in service.production._observed.items()}
    controller = FloatingController(service, tray_enabled=False)
    def settle():
        deadline = time.monotonic() + 10
        while controller.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not controller.busy
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.check_archives(silent=True)
        settle()
        page = controller.panel.production_page
        current = page.rows[0]['materials'][0]
        assert current['version_number'] == 2
        assert current['state'] == '待检查'
        page.job_requested.emit('检查当前材料', lambda **kw: service.production.review(current['id'], True, **kw))
        settle()
        assert page.rows[0]['materials'][0]['state'] == '检查通过'
        assert page.isEnabled()
    finally:
        controller.shutdown()
        controller.dispose_ui()
