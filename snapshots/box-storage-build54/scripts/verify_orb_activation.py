"""Exercise orb activation, including a Windows-native hidden window."""
from pathlib import Path


def verify(root):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QPoint
    from PySide6.QtTest import QTest,QSignalSpy
    from floating import FloatingBall
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    from theme import install_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    service=DesktopService(root/'data/workbox.db');service.workspaces.configure_materials(root/'材料库',complete_setup=True);install_theme(service)
    controller=FloatingController(service,tray_enabled=False);assert controller.start()
    controller.monitor_timer.stop()
    def settle():
        for _ in range(8):app.processEvents()
    orb=controller.orb;native_checked=False
    try:
        QTest.mouseClick(orb,Qt.LeftButton,pos=QPoint(38,38));settle();assert controller.panel.isVisible()
        controller.panel.close();settle();assert not controller.panel.isVisible()
        QTest.mouseDClick(orb,Qt.LeftButton,pos=QPoint(38,38));settle();assert controller.panel.isVisible()
        controller.panel.showMinimized();settle();QTest.mouseDClick(orb,Qt.LeftButton,pos=QPoint(38,38));settle()
        assert controller.panel.isVisible() and not controller.panel.isMinimized()
        # Hiding at the OS level can disagree with Qt's cached visibility after a hidden launch.
        if QApplication.platformName()=='windows':
            import ctypes
            from ctypes import wintypes
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];user.ShowWindow.restype=wintypes.BOOL
            user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindowVisible.restype=wintypes.BOOL
            hwnd=int(controller.panel.winId());user.ShowWindow(hwnd,0)
            assert not user.IsWindowVisible(hwnd)
            QTest.mouseDClick(orb,Qt.LeftButton,pos=QPoint(38,38));settle()
            assert user.IsWindowVisible(hwnd),'Qt activation did not restore the native window'
            native_checked=True
        controller.panel.close();settle()
        QTest.mousePress(orb,Qt.LeftButton,pos=QPoint(38,38));QTest.mouseMove(orb,QPoint(68,48),delay=30);QTest.mouseRelease(orb,Qt.LeftButton,pos=QPoint(68,48));settle()
        assert not controller.panel.isVisible(),'Dragging must not open the panel'
    finally:controller.shutdown();controller.dispose_ui();settle();install_theme()
    flags=dict(orb_single_double_click_and_minimized_activation_restore_panel=True,orb_drag_keeps_panel_closed=True)
    if native_checked:flags['native_windows_hidden_panel_restored']=True
    return flags
