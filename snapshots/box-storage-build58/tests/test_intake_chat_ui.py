import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QFrame, QTableWidget, QLabel,QDialog

from desktop_service import DesktopService
from compact_ui import CompactPanel
from intake_ui import IntakeDialog
from requirement_intake import write_template
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    panel = CompactPanel(service)
    panel.show()
    qapp.processEvents()
    yield panel
    panel.conversation_page.shutdown()
    panel.document_pane.shutdown()
    panel.close()
    install_theme()


RAW = '项目：建筑材料\n第一批：五件材料\n1. 建筑检测管理系统\n2. 工程质量分析软件'


def test_six_row_receipt_card_keeps_table_and_actions_inside_card(panel, qapp):
    from requirement_intake import proposal
    page = panel.conversation_page
    names = [f'表格验证{i}系统' for i in range(6)]
    tid = page.store.add(page.ensure_conversation(), '\n'.join(names), [proposal(n, batch='六项') for n in names])
    page.store.create_tasks(tid)
    page.refresh_turns()
    for _ in range(6):
        qapp.processEvents()
    card = next(w for w in page.transcript.findChildren(QFrame) if w.objectName() == 'intakeReply')
    table = card.findChild(QTableWidget)
    action = page.cards[tid]['view']
    assert table is None
    assert len(panel.service.production.rows()) == 6
    assert card.rect().contains(action.geometry())


def test_home_page_is_first_and_keeps_tools_around_composer(panel):
    page = panel.conversation_page
    assert panel.tabs.currentWidget() is page
    assert panel.tabs.indexOf(page) == 0 and panel.tabs.indexOf(panel.production_page) == -1
    assert page.workspace_splitter.widget(0) is page.chat_body
    assert page.workspace_splitter.widget(1) is panel.production_page
    assert page.tools_button.isVisible() and len(page.tools_menu.actions()) == 3
    assert panel.production_page.isVisible() and not panel.tabs.tabBar().isVisible()
    assert page.composer.isVisible() and not panel.search.isVisible()
    assert not panel.today_button.isVisible()


def test_keyboard_send_creates_inline_review_card_not_tasks(panel, qapp):
    page = panel.conversation_page
    page.composer.setPlainText(RAW)
    QTest.keyClick(page.composer, Qt.Key_Return)
    assert not page.store.turns(page.current_id)
    QTest.keyClick(page.composer, Qt.Key_Return, Qt.ControlModifier)
    qapp.processEvents()
    turns = page.store.turns(page.current_id)
    assert len(turns) == 1 and turns[0]['state'] == 'needs-review'
    assert page.cards[turns[0]['id']]['confirm'].isVisible()
    assert page.composer.toPlainText() == ''
    assert page.service.db.counts()['projects'] == 0


def test_inline_edit_saves_back_to_same_turn_without_creating_tasks(panel, qapp):
    page = panel.conversation_page
    page.receive_text(RAW)
    tid = page.store.turns(page.current_id)[0]['id']
    def change():
        editor = QApplication.activeModalWidget()
        assert isinstance(editor, IntakeDialog) and editor.review_only
        editor.table.cellWidget(0, 4).setCurrentText('单手册 Word')
        QTest.mouseClick(editor.confirm_button, Qt.LeftButton)
    QTimer.singleShot(0, change)
    QTest.mouseClick(page.cards[tid]['edit'], Qt.LeftButton)
    assert len(page.store.turn(tid)['rows'][0]['kinds']) == 1
    assert page.service.db.counts()['projects'] == 0


def test_cancel_inline_card_preserves_source_and_composer_draft(panel):
    page = panel.conversation_page
    page.receive_text(RAW)
    tid = page.store.turns(page.current_id)[0]['id']
    page.composer.setPlainText('下一批尚未整理')
    QTest.mouseClick(page.cards[tid]['discard'], Qt.LeftButton)
    assert page.store.turn(tid)['state'] == 'discarded'
    assert page.composer.toPlainText() == '下一批尚未整理'
    assert page.service.db.counts()['projects'] == 0


def test_history_switch_and_reopen_restore_unsent_draft(panel):
    page = panel.conversation_page
    page.composer.setPlainText('第一段未发送')
    page.flush_draft()
    first = page.current_id
    # 外部文字到达时仍保护旧草稿；手动新建入口已移除。
    assert not hasattr(page, 'new_button')
    page.receive_text('第二段未发送', organize=False)
    page.flush_draft()
    second = page.current_id
    page.history.setCurrentIndex(page.history.findData(first))
    assert page.composer.toPlainText() == '第一段未发送'
    reopened = DesktopService(page.service.db.path)
    assert reopened.intake_chat.active()['draft_text'] == '第一段未发送'
    assert first != second


def test_spreadsheet_folder_and_template_tools_stay_in_conversation(panel, tmp_path, monkeypatch):
    page = panel.conversation_page
    template = tmp_path / '需求.xlsx'
    write_template(template)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(template), ''))
    page.tools_menu.actions()[0].trigger()
    assert page.store.turns(page.current_id)[0]['source_kind'] == 'sheet'
    folder = tmp_path / '库存系统'
    folder.mkdir()
    (folder / '软件说明书.docx').write_bytes(b'sample')
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **kw: str(folder))
    page.tools_menu.actions()[1].trigger()
    assert page.store.turns(page.current_id)[-1]['source_kind'] == 'folder'
    assert all('模板' not in action.text() for action in page.tools_menu.actions())
    assert panel.tabs.currentWidget() is page and page.service.db.counts()['projects'] == 0


def test_parse_failure_keeps_original_in_chat_for_reediting(panel):
    page = panel.conversation_page
    page.receive_text('尽快做完，谢谢。')
    turn = page.store.turns(page.current_id)[0]
    assert turn['state'] == 'failed' and turn['source_text'] == '尽快做完，谢谢。'
    QTest.mouseClick(page.cards[turn['id']]['edit_source'], Qt.LeftButton)
    assert page.composer.toPlainText() == turn['source_text']


def test_theme_resize_and_workspace_navigation_keep_conversation(panel, qapp):
    page = panel.conversation_page
    page.receive_text(RAW)
    original = page.store.turns(page.current_id)[0]
    set_theme('dark', page.service)
    panel.resize(780, 650)
    qapp.processEvents()
    assert page.composer.isVisible() and page.send_button.isVisible()
    panel.tabs.setCurrentWidget(panel.projects_page)
    assert panel.search.isVisible()
    panel.tabs.setCurrentWidget(page)
    assert panel.production_page.isVisible() and not panel.search.isVisible()
    assert page.store.turns(page.current_id)[0]['id'] == original['id']


def test_replacing_empty_greeting_hides_old_card_immediately(panel):
    page = panel.conversation_page
    old = next(page.messages.itemAt(i).widget() for i in range(page.messages.count()) if page.messages.itemAt(i).widget())
    page.receive_text(RAW)
    assert old.isHidden()


def test_busy_paste_does_not_replace_unsent_draft(panel):
    page = panel.conversation_page
    page.composer.setPlainText('正在输入的需求')
    page.set_busy(True)
    page.receive_text('外部新文字')
    assert page.composer.toPlainText() == '正在输入的需求'
    page.set_busy(False)
    assert page.store.active()['draft_text'] == '正在输入的需求'


def test_new_message_gets_a_fresh_default_batch(panel):
    page = panel.conversation_page
    page.receive_text('1. 第一项系统')
    first = page.store.turns(page.current_id)[0]['rows'][0]['batch']
    page.receive_text('1. 第二项系统')
    second = page.store.turns(page.current_id)[-1]['rows'][0]['batch']
    assert first != second


def test_database_busy_does_not_clear_composer_or_block_for_seconds(panel):
    import sqlite3
    page = panel.conversation_page
    page.composer.setPlainText(RAW)
    with sqlite3.connect(page.service.db.path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        start = time.monotonic()
        page.send()
        assert time.monotonic() - start < 2
        assert page.composer.toPlainText() == RAW
        assert '保留' in page.error.text()
    page.send()
    assert page.store.turns(page.current_id)[0]['state'] == 'needs-review'


def test_failed_draft_save_blocks_conversation_switch_and_external_replacement(panel, monkeypatch):
    import sqlite3
    page = panel.conversation_page
    page.composer.setPlainText('原来的未发送需求')
    page.flush_draft()
    previous = page.current_id
    def fail(*a, **kw):
        raise sqlite3.OperationalError('模拟写入失败')
    monkeypatch.setattr(page.store, 'save_draft', fail)
    page.composer.setPlainText('刚补充的未保存内容')
    page.new_conversation()
    page.receive_text('外部粘贴的新内容')
    assert page.current_id == previous
    assert page.composer.toPlainText() == '刚补充的未保存内容'


def test_repeated_card_rebuild_theme_switch_and_window_disposal(qapp, tmp_path):
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    service = DesktopService(tmp_path / 'dispose' / 'workbox.db')
    install_theme(service)
    for i in range(4):
        panel = CompactPanel(service)
        panel.show()
        qapp.processEvents()
        page = panel.conversation_page
        page.new_conversation()
        page.receive_text(RAW)
        page.receive_text('批次：第二批\n套餐：单手册 Word\n1. 新系统')
        set_theme('dark' if i % 2 == 0 else 'light', service)
        qapp.processEvents()
        page.shutdown()
        panel.document_pane.shutdown()
        panel.close()
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        assert not shiboken6.isValid(panel)
    install_theme()


def test_real_controller_creates_once_and_view_button_opens_material_table(qapp, tmp_path):
    from desktop_controller import FloatingController
    service = DesktopService(tmp_path / 'controller' / 'workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        page = controller.panel.conversation_page
        page.receive_text(RAW)
        qapp.processEvents()
        tid = page.store.turns(page.current_id)[0]['id']
        QTest.mouseClick(page.cards[tid]['confirm'], Qt.LeftButton)
        deadline = time.monotonic() + 10
        while controller.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        qapp.processEvents()
        assert not controller.busy
        assert service.db.counts()['projects'] == 2
        assert page.store.turn(tid)['state'] == 'committed'
        assert controller.panel.tabs.currentWidget() is page
        QTest.mouseClick(page.cards[tid]['view'], Qt.LeftButton)
        assert controller.panel.tabs.currentWidget() is controller.panel.workspace_page
        assert controller.panel.production_page.isVisible()
        assert len(controller.panel.production_page.rows) == 2
        controller.panel.tabs.setCurrentWidget(page)
        assert page.store.turn(tid)['source_text'] == RAW
        service.intake_chat.create_tasks(tid)
        assert service.db.counts()['projects'] == 2
    finally:
        controller.shutdown()
        controller.dispose_ui()


def test_ball_paste_opens_home_and_previews_without_creating_tasks(qapp, tmp_path,monkeypatch):
    from desktop_controller import FloatingController
    from intake_link_ui import ClassificationRuleDialog
    def choose(d):d.rule='单手册 Word';return QDialog.Accepted
    monkeypatch.setattr(ClassificationRuleDialog,'exec',choose)
    service = DesktopService(tmp_path / 'paste-entry' / 'workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    before = QApplication.clipboard().text()
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        assert not controller.panel.isVisible()
        QApplication.clipboard().setText(RAW)
        controller.paste()
        qapp.processEvents()
        page = controller.panel.conversation_page
        assert controller.panel.isVisible()
        assert controller.panel.tabs.currentWidget() is page
        assert page.store.turns(page.current_id)[0]['state'] == 'needs-review'
        assert service.db.counts()['projects'] == 0
    finally:
        QApplication.clipboard().setText(before)
        controller.shutdown()
        controller.dispose_ui()
