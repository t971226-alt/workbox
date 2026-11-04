import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QInputDialog
from PySide6.QtTest import QTest

from desktop_service import DesktopService
from compact_ui import CompactPanel
from intake_ui import IntakeDialog
from task_requirements_ui import RequirementsDialog, TaskMaterialsDialog
from requirement_intake import proposal, write_template, parse_text
from material_templates import PRESETS
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.production.create_batch('旧批次', ['旧系统'], ['说明书'])
    install_theme(service)
    widget = CompactPanel(service)
    widget.tabs.setCurrentWidget(widget.production_page)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.document_pane.shutdown()
    widget.close()
    install_theme()


def test_text_preview_edit_individual_packages_and_cancel(panel, qapp):
    page = panel.production_page
    dialog = IntakeDialog(page)
    dialog.show()
    dialog.source.setPlainText('项目：甲委托\n第一批：五件材料\n1. 库存系统\n2. 客户系统')
    QTest.mouseClick(dialog.parse_button, Qt.LeftButton)
    assert dialog.table.rowCount() == 2
    dialog.table.cellWidget(1, 4).setCurrentText('14件＋双视频')
    assert len(dialog.values()[1]['kinds']) == 16
    assert len(dialog.values()[0]['kinds']) == 5
    before = page.service.db.counts()
    QTest.mouseClick(dialog.cancel_button, Qt.LeftButton)
    assert page.service.db.counts() == before
    dialog.deleteLater()


def test_source_edit_invalidates_preview_and_custom_requirements_edit(panel, monkeypatch):
    dialog = IntakeDialog(panel.production_page)
    dialog.source.setPlainText('1. 库存系统')
    dialog.parse()
    dialog.source.setPlainText('1. 另一个系统')
    assert not dialog.confirm_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Yes)
    dialog.parse()
    assert dialog.confirm_button.isEnabled()
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a, **kw: ('操作手册.docx\n截图.png', True))
    dialog.edit_required(0, 5)
    assert dialog.values()[0]['kinds'] == ['操作手册.docx', '截图.png']
    dialog.confirm()
    assert len(dialog.plan[0]['kinds']) == 2
    dialog.deleteLater()


def test_excel_and_folder_buttons_create_only_preview(panel, tmp_path, monkeypatch):
    dialog = IntakeDialog(panel.production_page)
    target = tmp_path / '模板.xlsx'
    write_template(target)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(target), ''))
    QTest.mouseClick(dialog.sheet_button, Qt.LeftButton)
    assert dialog.table.rowCount() == 3
    assert panel.service.db.counts()['projects'] == 1
    folder = tmp_path / '文件夹系统'
    folder.mkdir()
    (folder / '软件说明书.docx').write_bytes(b'test')
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(folder))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Yes)
    QTest.mouseClick(dialog.folder_button, Qt.LeftButton)
    assert dialog.table.rowCount() == 1 and not dialog.values()[0]['kinds']
    assert panel.service.db.counts()['projects'] == 1
    dialog.deleteLater()


def test_mixed_requirements_summary_and_grouped_details(panel, qapp):
    page = panel.production_page
    rows = [proposal('甲系统', batch='混合批次', requirement='单手册'),
            proposal('乙系统', batch='混合批次', requirement='14件＋双视频')]
    result = page.store.import_plan(rows, token='summary')
    page.pending_batch_id = result['batch_ids'][0]
    page.refresh()
    assert page.summary_mode
    assert page.table.columnCount() == 9
    assert page.table.item(0, 3).text() == '0/1'
    assert page.table.item(1, 3).text() == '0/16'
    details = TaskMaterialsDialog(page, page.rows[1])
    assert details.table.rowCount() == 16
    groups = {details.table.item(i, 0).text() for i in range(details.table.rowCount())}
    assert {'视频', '数据库与脚本', '文档材料'} <= groups
    set_theme('dark', page.service)
    qapp.processEvents()
    assert details.table.rowCount() == 16
    details.deleteLater()


def test_edit_task_preset_changes_only_one_task_and_requires_reason(panel):
    page = panel.production_page
    b = page.store.create_batch('本项调整', ['甲系统', '乙系统'], PRESETS['单手册 Word'])
    page.pending_batch_id = b['batch_id']
    page.refresh()
    dialog = RequirementsDialog(page, page.rows[0])
    dialog.preset.setCurrentText('14件＋双视频')
    QTest.mouseClick(dialog.save, Qt.LeftButton)
    assert '原因' in dialog.error.text()
    dialog.reason.setText('追加完整交付')
    dialog.validate()
    page.store.configure_task(page.rows[0]['id'], dialog.required, reason=dialog.reason.text())
    page.refresh()
    assert [r['required_count'] for r in page.rows] == [16, 1]
    dialog.deleteLater()


def test_commission_and_date_filters_keep_batches_separate(panel):
    page = panel.production_page
    result = page.store.import_plan([proposal('同名系统', commission=c, batch='第一批', requirement='单手册')
                                   for c in ['甲委托', '乙委托']], token='filter')
    page.refresh()
    page.commission_filter.setCurrentIndex(page.commission_filter.findData('甲委托'))
    assert page.batch.count() == 2
    assert page.rows[0]['commission'] == '甲委托'
    page.period_filter.setCurrentText('昨天')
    assert page.batch.count() == 2 and page.table.rowCount() == 0
    page.period_filter.setCurrentText('今天')
    assert page.batch.count() == 2


def test_repeat_keeps_mixed_requirements_without_copying_confirmation(panel, monkeypatch):
    page = panel.production_page
    result = page.store.import_plan([proposal('甲系统', batch='原批', requirement='单手册'),
                                    proposal('乙系统', batch='原批', requirement='五件材料')], token='original')
    page.pending_batch_id = result['batch_ids'][0]
    page.refresh()
    captured = []
    monkeypatch.setattr(page, 'import_requirements', lambda **kw: captured.extend(kw['rows']))
    QTest.mouseClick(page.repeat_button, Qt.LeftButton)
    assert [len(r['kinds']) for r in captured] == [1, 5]
    assert captured[0]['batch'] != '原批'


def test_real_import_button_uses_controller_and_selects_new_batch(qapp, tmp_path):
    from desktop_controller import FloatingController
    service = DesktopService(tmp_path / 'controller' / 'workbox.db')
    service.workspaces.configure_materials(tmp_path/'controller-materials',complete_setup=True)
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        page = controller.panel.production_page
        controller.panel.tabs.setCurrentWidget(page)
        QTest.mouseClick(page.import_button, Qt.LeftButton)
        chat = controller.panel.conversation_page
        assert controller.panel.tabs.currentWidget() is chat
        chat.receive_text('项目：控制器委托\n第一批：14件＋双视频\n1. 控制器系统')
        qapp.processEvents()
        tid = chat.store.turns(chat.current_id)[-1]['id']
        QTest.mouseClick(chat.cards[tid]['confirm'], Qt.LeftButton)
        deadline = time.monotonic() + 10
        while controller.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not controller.busy
        assert page.rows[0]['name'] == '控制器系统'
        assert page.rows[0]['required_count'] == 16
        assert page.summary_mode
        assert controller.panel.tabs.currentWidget() is chat
        QTest.mouseClick(chat.cards[tid]['view'], Qt.LeftButton)
        assert controller.panel.tabs.currentWidget() is controller.panel.workspace_page
        assert page.isVisibleTo(controller.panel)
    finally:
        controller.shutdown()
        controller.dispose_ui()


def test_main_paste_button_opens_requirement_preview(qapp, tmp_path):
    from desktop_controller import FloatingController
    service = DesktopService(tmp_path / 'paste' / 'workbox.db')
    service.workspaces.configure_materials(tmp_path/'paste-materials',complete_setup=True)
    controller = FloatingController(service, tray_enabled=False)
    before = QApplication.clipboard().text()
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        QApplication.clipboard().setText('项目：粘贴委托\n批次：单项\n套餐：单手册 Word\n1. 粘贴测试系统')
        controller.panel.tabs.setCurrentWidget(controller.panel.production_page)
        def choose_rule():
            from intake_link_ui import ClassificationRuleDialog
            dialog=QApplication.activeModalWidget();assert isinstance(dialog,ClassificationRuleDialog)
            dialog.rules.setCurrentIndex(dialog.rules.findData('单手册 Word'));dialog.confirm_button.click()
        QTimer.singleShot(50,choose_rule)
        QTest.mouseClick(controller.panel.paste_button, Qt.LeftButton)
        chat = controller.panel.conversation_page
        assert controller.panel.tabs.currentWidget() is chat
        qapp.processEvents()
        tid = chat.store.turns(chat.current_id)[-1]['id']
        QTest.mouseClick(chat.cards[tid]['confirm'], Qt.LeftButton)
        deadline = time.monotonic() + 10
        while controller.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert controller.panel.production_page.rows[0]['name'] == '粘贴测试系统'
        assert controller.panel.production_page.rows[0]['required_count'] == 1
    finally:
        QApplication.clipboard().setText(before)
        controller.shutdown()
        controller.dispose_ui()


def test_pending_only_install_reopens_intake_with_material_table_accessible(qapp, tmp_path):
    service = DesktopService(tmp_path / 'pending' / 'workbox.db')
    service.production.import_plan([proposal('待定系统', batch='单项')], token='pending-only')
    reopened = DesktopService(service.db.path)
    panel = CompactPanel(reopened)
    try:
        assert panel.tabs.currentWidget() is panel.conversation_page
        panel.tabs.setCurrentWidget(panel.production_page)
        assert panel.production_page.rows[0]['production_state'] == '要求待确认'
    finally:
        panel.document_pane.shutdown()
        panel.close()
