"""按钮从选中/禁用状态恢复后，Qt 实际文字内容区域仍须完整容纳标题。"""
import pytest
from PySide6.QtCore import QRect, Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton

from compact_ui import CompactPanel, DirectorySettingsDialog
from delivery_ui import TodayDeliveryDialog
from desktop_service import DesktopService
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def assert_text_fits(button):
    """使用真实 style 的内容框，覆盖实际尺寸及布局能分配的最小建议宽度。"""
    from workspace_chrome import ChromeButton
    from ledger_view import FileLink
    if isinstance(button, FileLink):
        assert button.toolTip()
        return
    if isinstance(button, ChromeButton):
        if button.property('checkbox'):
            assert button.width()>=16 and button.height()>=16 and button.accessibleName()
            return
        if button.property('leftAligned'):
            assert button.width()>=80 and button.toolTip()
            return
        # These buttons paint their content directly; QStyle's padding is unused.
        if button.icon_only:
            # The compact top settings control intentionally uses a 24x22 target.
            assert button.width() >= 22 and button.height() >= 22
            assert button.accessibleName() and not button.icon().isNull()
        else:
            from PySide6.QtGui import QFontMetrics
            available = button.width() - 24 - (22 if button.symbol else 0)
            assert available >= QFontMetrics(button.drawing_font()).horizontalAdvance(button.text()), button.text()
        return
    option = QStyleOptionButton()
    button.initStyleOption(option)
    text_width = button.fontMetrics().size(Qt.TextShowMnemonic, button.text()).width()
    menu_width = (button.style().pixelMetric(QStyle.PM_MenuButtonIndicator, option, button)
                  if button.menu() is not None else 0)
    for size in (button.size(), button.sizeHint()):
        option.rect = QRect(0, 0, size.width(), size.height())
        content = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
        assert content.width() - menu_width >= text_width, (
            button.text(), button.isChecked(), button.isEnabled(),
            size.width(), content.width(), text_width, button.font().toString())


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
@pytest.mark.parametrize('dense', [False, True])
def test_project_filter_text_fits_after_check_and_uncheck(qapp, tmp_path, mode, dense):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    panel.tabs.setCurrentWidget(panel.projects_page)
    panel.resize(1080, 520 if dense else 720)
    panel.show()
    qapp.processEvents()
    # Windows 首次显示会将窗口压入高 DPI 的小逻辑屏幕；随后设置测试尺寸，
    # 分别验证常规与紧凑皮肤，不把系统首次定位当成按钮回归。
    panel.resize(1080, 520 if dense else 720)
    qapp.processEvents()
    try:
        assert bool(panel.property('dense')) is dense
        # “全部”初始为选中；实际点击另一筛选项使它恢复未选中。
        panel.status_buttons['待交付'].click()
        qapp.processEvents()
        assert not panel.status_buttons[None].isChecked()
        for button in panel.status_buttons.values():
            assert_text_fits(button)
        panel.status_buttons[None].click()
        qapp.processEvents()
        for button in panel.status_buttons.values():
            assert_text_fits(button)
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
@pytest.mark.parametrize('target', ['intake', 'materials', 'projects', 'zips', 'pending', 'settings', 'delivery'])
def test_all_buttons_keep_text_space_across_interaction_states(qapp, tmp_path, mode, target):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    dialog = (DirectorySettingsDialog(service, panel) if target == 'settings' else
              TodayDeliveryDialog(service, panel) if target == 'delivery' else panel)
    pages = dict(intake=panel.conversation_page, materials=panel.production_page,
                 projects=panel.projects_page, zips=panel.zips_page, pending=panel.pending_page)
    if target in pages:
        panel.tabs.setCurrentWidget(pages[target])
    if target == 'intake':
        panel.conversation_page.receive_text('批次：按钮验证\n套餐：五件材料\n1. 界面检查系统')
    dialog.show()
    qapp.processEvents()
    try:
        # Hidden tab pages are not laid out yet; check each page while it is actually shown.
        for _ in range(4):qapp.processEvents()
        buttons = [b for b in dialog.findChildren(QPushButton) if b.isVisibleTo(dialog)]
        assert buttons
        for button in buttons:
            # 保留各控件的真实标题、menu、checkable、字体和Qt样式，只遍历交互状态。
            checked = button.isChecked()
            enabled = button.isEnabled()
            button.setEnabled(False)
            button.sizeHint()
            button.setEnabled(True)
            for state in (False, True) if button.isCheckable() else (False,):
                button.setChecked(state)
                for focused, pressed in ((False, False), (True, False), (True, True)):
                    button.setFocus() if focused else button.clearFocus()
                    button.setDown(pressed)
                    qapp.processEvents()
                    assert_text_fits(button)
                QTest.mouseMove(button, button.rect().center())
                qapp.processEvents()
                assert_text_fits(button)
            if button.menu() is not None:
                button.menu().popup(button.mapToGlobal(button.rect().bottomLeft()))
                qapp.processEvents()
                assert_text_fits(button)
                button.menu().close()
            button.setDown(False)
            button.setChecked(checked)
            button.setEnabled(enabled)
    finally:
        if isinstance(dialog, TodayDeliveryDialog):
            dialog.shutdown()
        dialog.close()
        panel.conversation_page.shutdown()
        panel.document_pane.shutdown()
        panel.close()
        if dialog is not panel:
            dialog.deleteLater()
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        install_theme()


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
@pytest.mark.parametrize('target', ['archive', 'delivery'])
def test_archive_and_delivery_button_text_fits_after_enabling(qapp, tmp_path, mode, target):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    delivery = TodayDeliveryDialog(service)
    panel.tabs.setCurrentWidget(panel.zips_page)
    panel.show()
    delivery.show()
    qapp.processEvents()
    try:
        for button in ((panel.approve_button,) if target == 'archive' else (delivery.void_button,)):
            assert not button.isEnabled()
            # 先取得布局在禁用时缓存的真实建议宽度，再模拟选中可操作记录后的启用。
            button.sizeHint()
            button.setEnabled(True)
            qapp.processEvents()
            assert_text_fits(button)
            button.setFocus()
            qapp.processEvents()
            assert_text_fits(button)
            button.setEnabled(False)
            qapp.processEvents()
            assert_text_fits(button)
    finally:
        delivery.shutdown()
        delivery.close()
        panel.document_pane.shutdown()
        panel.close()
        install_theme()
