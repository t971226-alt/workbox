import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QCalendarWidget, QMenu, QCheckBox, QSpinBox
from PySide6.QtTest import QTest
from compact_ui import CompactPanel, DirectorySettingsDialog
from desktop_service import DesktopService
from theme import install_theme, set_theme
from workspace_chrome import SearchField, colors


@pytest.fixture(scope='module')
def app():
    value = QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_search_icon_is_centered_and_clear_search_keeps_native_editing(app, mode):
    install_theme(); set_theme(mode, persist=False)
    edit = SearchField(); edit.resize(240, 34); edit.show(); app.processEvents()
    try:
        pixels = edit.grab().toImage()
        baseline = pixels.pixelColor(30, 17).lightness()
        rows = [y for y in range(5, 29) for x in range(11, 28)
                if abs(pixels.pixelColor(x, y).lightness()-baseline) > 35]
        assert rows and abs((min(rows)+max(rows))/2 - 16.5) <= 1.5
        edit.setFocus(); QTest.keyClicks(edit, 'inventory')
        assert edit.text() == 'inventory' and edit.clear_control.isVisible()
        QTest.mouseClick(edit.clear_control, Qt.LeftButton)
        assert edit.text() == '' and not edit.clear_control.isVisible()
    finally:
        edit.close(); edit.deleteLater(); install_theme()


def test_primary_actions_are_relevant_and_filter_menu_updates_real_table(app, tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    service.production.create_batch('第一批', ['甲系统','乙系统'], ['说明书'])
    install_theme(service)
    panel = CompactPanel(service); panel.show(); app.processEvents()
    try:
        assert (panel.width(), panel.height()) == (870, 640)
        page = panel.production_page
        page.show_legacy_workspace();app.processEvents()
        menu_text = ' '.join(action.text() for action in panel.more_button.menu().actions())
        assert all(term not in menu_text for term in ('ZIP','项目与','待核对'))
        assert not page.state_filter.isVisible() and not page.period_filter.isVisible()
        assert page.search.isVisible() and page.filter_button.isVisible()
        page.filter_menu.popup(page.filter_button.mapToGlobal(page.filter_button.rect().bottomLeft()))
        app.processEvents()
        assert page.state_filter.isVisible()
        page.state_filter.setCurrentText('已提交')
        assert not page.rows
        page.filter_menu.close(); page.show_all()
        app.processEvents()
        assert len(page.rows) == 2
        page.grouped.select_task(page.rows[0]['id'], True)
        assert page.table.item(0, 0).checkState() == Qt.Checked
        assert page.selection_bar.isVisible() and page.submit_button.isEnabled()
        settings = DirectorySettingsDialog(service, panel)
        assert not settings.findChildren(QSpinBox) and not settings.findChildren(QCheckBox)
        settings.reject(); settings.deleteLater()
    finally:
        panel.conversation_page.shutdown(); panel.document_pane.shutdown()
        panel.close(); panel.deleteLater(); install_theme()


def test_calendar_keyboard_selection_changes_statistics_date(app, tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    panel = CompactPanel(service); panel.show(); app.processEvents()
    page = panel.production_page
    before = page.statistics_date.date()
    errors = []
    def choose():
        popup = QApplication.activePopupWidget()
        try:
            assert isinstance(popup, QMenu)
            calendar = popup.findChild(QCalendarWidget)
            calendar.setFocus()
            QTest.keyClick(QApplication.focusWidget(), Qt.Key_Right)
            QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)
        except Exception as error:
            errors.append(str(error))
            if popup: popup.close()
    QTimer.singleShot(0, choose)
    deadline = QTimer(panel)
    deadline.setSingleShot(True)
    def expired():
        errors.append('日期选择未完成')
        popup = QApplication.activePopupWidget()
        if popup: popup.close()
    deadline.timeout.connect(expired)
    deadline.start(2000)
    try:
        page.period_button.click()
        assert not errors
        assert page.statistics_date.date() == before.addDays(1)
        assert page.period_button.text() == before.addDays(1).toString('M月d日')
    finally:
        deadline.stop()
        panel.conversation_page.shutdown(); panel.document_pane.shutdown()
        panel.close(); panel.deleteLater()


@pytest.mark.parametrize('scale', [1, 2])
def test_all_embedded_icons_render_offline_at_normal_and_high_density(app, scale):
    from PySide6.QtCore import QFile, QIODevice, QRectF
    from PySide6.QtGui import QImage, QPainter
    from workspace_chrome import ICON_NAMES, draw_icon
    import hashlib
    import json
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[1]/'assets/icons/lucide/SOURCE.json').read_text())
    for symbol, filename in ICON_NAMES.items():
        resource = QFile(':/workbox/icons/' + filename + '.svg')
        assert resource.open(QIODevice.ReadOnly), symbol
        data = bytes(resource.readAll()); resource.close()
        assert hashlib.sha256(data).hexdigest() == manifest['files'][filename+'.svg']
        for color in ('#292929', '#ECECEC'):
            image = QImage(18*scale, 18*scale, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            image.setDevicePixelRatio(scale)
            painter = QPainter(image)
            try:
                draw_icon(painter, QRectF(0, 0, 18, 18), symbol, color)
            finally:
                painter.end()
            assert any(image.pixelColor(x,y).alpha() for x in range(image.width()) for y in range(image.height())), symbol
