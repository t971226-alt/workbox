import time

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from desktop_service import DesktopService
from desktop_controller import FloatingController
from requirement_intake import proposal
from theme import install_theme, set_theme


def test_compact_update_shrinks_once_and_then_remembers_user_window_size(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.set_setting('manager_geometry', [20, 20, 1280, 820])
    for expected in ((870, 640), (1160, 760)):
        controller = FloatingController(service, tray_enabled=False)
        try:
            assert controller.start()
            controller.monitor_timer.stop()
            assert (controller.panel.width(), controller.panel.height()) == expected
            assert service.get_setting('workspace_chrome_revision') == 2
            controller.panel.resize(1160, 760)
        finally:
            controller.shutdown()
            controller.dispose_ui()
    with pytest.raises(ValueError):
        service.set_setting('workspace_chrome_revision', True)


def test_conversation_query_filters_ledger_reruns_and_keeps_new_task_intake(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.production.import_plan([proposal(n, batch='工作批次') for n in ('统计分析系统', '客户系统')], token='first')
    source = tmp_path / '统计分析系统.docx'
    source.write_bytes(b'word fixture')
    service.receive_path(source)
    install_theme(service)
    controller = FloatingController(service, tray_enabled=False)
    def wait():
        deadline = time.monotonic() + 10
        while controller.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        app.processEvents()
        assert not controller.busy
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        panel = controller.panel
        chat, page = panel.conversation_page, panel.production_page
        ledger=page.task_workspace
        before = service.production.rows()
        chat.composer.setPlainText('哪些软件还没检查？')
        QTest.keyClick(chat.composer, Qt.Key_Return, Qt.ControlModifier)
        wait()
        turn = chat.store.turns(chat.current_id)[-1]
        assert turn['source_kind'] == 'query' and turn['result']['count'] == 1
        assert ledger.rows[0]['name'] == '统计分析系统' and len(ledger.rows) == 1
        assert chat.chat_body.isVisible() and ledger.isVisible()
        assert service.production.rows() == before
        ledger.reset_button.click()
        assert len(ledger.rows) == 2 and ledger.external_spec is None
        QTest.mouseClick(chat.cards[turn['id']]['view_query'], Qt.LeftButton)
        assert len(ledger.rows) == 1
        service.production.review(ledger.rows[0]['materials'][0]['id'], True)
        QTest.mouseClick(chat.cards[turn['id']]['query_again'], Qt.LeftButton)
        wait()
        assert len(ledger.rows) == 0 and chat.store.turn(turn['id'])['result']['count'] == 0
        assert chat.store.count(chat.current_id) == 1
        chat.receive_text('批次：后续批次\n套餐：单手册 Word\n1. 新增名称系统')
        tid = chat.store.turns(chat.current_id)[-1]['id']
        assert len(service.production.rows()) == 2
        QTest.mouseClick(chat.cards[tid]['confirm'], Qt.LeftButton)
        wait()
        assert len(service.production.rows()) == 3 and ledger.external_spec is None
        assert [r['name'] for r in ledger.rows]==['新增名称系统']
        set_theme('dark', service)
        panel.resize(780, 650)
        app.processEvents()
        assert panel.width() == 780
        assert chat.send_button.isVisible() and ledger.isVisible()
        assert not chat.scroll.horizontalScrollBar().isVisible()
        assert chat.composer.mapTo(panel, chat.composer.rect().bottomRight()).x() < panel.width()
        panel.tabs.setCurrentWidget(panel.zips_page)
        assert panel.return_button.isVisible()
        QTest.mouseClick(panel.return_button, Qt.LeftButton)
        assert panel.tabs.currentWidget() is panel.workspace_page and ledger.isVisible()
    finally:
        controller.shutdown()
        controller.dispose_ui()
        install_theme()


def test_window_resize_controls_and_maximized_reopen_keep_workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    controller = FloatingController(DesktopService(tmp_path/'data/workbox.db'), tray_enabled=False)
    try:
        assert controller.start()
        controller.show_panel()
        panel = controller.panel
        assert panel.windowFlags() & Qt.WindowMaximizeButtonHint
        assert panel.size_grip.isVisible()
        assert not hasattr(panel.conversation_page, 'new_button')
        assert panel.conversation_page.history.itemText(0) == '会话记录'
        panel.resize(1020, 720); app.processEvents()
        assert panel.size_grip.geometry().right() < panel.width()
        assert panel.size_grip.geometry().bottom() < panel.height()
        assert panel.conversation_page.chat_body.isVisible() and panel.production_page.isVisible()
        panel.showMaximized(); app.processEvents()
        assert panel.isMaximized()
        controller.show_panel(); app.processEvents()
        assert panel.isMaximized()
        panel.showNormal(); app.processEvents()
        assert (panel.width(),panel.height()) == (1020,720)
    finally:
        controller.shutdown(); controller.dispose_ui()
