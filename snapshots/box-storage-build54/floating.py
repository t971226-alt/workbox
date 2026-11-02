"""Native circular intake surface; no clipboard monitoring or remote fetching."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal,QTimer,QPropertyAnimation,QEasingCurve
from PySide6.QtGui import QColor, QFont, QPainter, QPen,QRegion
from PySide6.QtWidgets import QApplication, QWidget

from orb_artwork import draw_orb
from theme import theme_signal
from workspace_chrome import WorkspaceMenu, outline_icon


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def decode_mime(mime):
    unsupported = {'kind': 'unsupported', 'message': '请拖入项目文字、文件或文件夹；支持可访问的共享路径，不接收网址和虚拟附件。'}
    if mime.hasUrls():
        urls = mime.urls()
        # 网络目录检查可能很慢；这里只读拖放数据，实际检查交给后台收件任务。
        if not urls or len(urls) > 100 or any(not u.isLocalFile() for u in urls):
            return unsupported
        return {'kind': 'files', 'paths': [str(Path(u.toLocalFile())) for u in urls]}
    if mime.hasText() and mime.text().strip():
        if len(mime.text()) > 200_000:
            return {'kind': 'unsupported', 'message': '文字太长，请分次拖入（单次最多 200,000 字符）。'}
        return {'kind': 'text', 'text': mime.text()}
    if mime.hasHtml() and len(mime.html()) <= 400_000:
        parser = _TextHTML()
        parser.feed(mime.html())
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
        if text:
            return {'kind': 'text', 'text': text}
    return unsupported


class DropSurface:
    def dragEnterEvent(self, event):
        data = decode_mime(event.mimeData())
        if data['kind'] != 'unsupported':
            event.setDropAction(Qt.CopyAction)
            event.accept()
            if hasattr(self, 'set_drop_hint'):
                self.set_drop_hint(data['kind'])
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        if hasattr(self, 'set_drop_hint'):
            self.set_drop_hint('')
        event.accept()

    def dropEvent(self, event):
        data = decode_mime(event.mimeData())
        if data['kind'] == 'unsupported':
            event.ignore()
        else:
            # 新收件保留原件，拖放源不能独立删除文件。
            event.setDropAction(Qt.CopyAction)
            event.accept()
        if hasattr(self, 'set_drop_hint'):
            self.set_drop_hint('')
        self.intake.emit(data)


def edge_targets(area,position,size):
    """Half-visible and fully-visible positions on this monitor's nearest side."""
    width,height=size.width(),size.height()
    side='left' if position.x()+width/2-area.left() <= area.right()+1-position.x()-width/2 else 'right'
    y=max(area.top(),min(position.y(),area.bottom()+1-height))
    full=QPoint(area.left() if side=='left' else area.right()+1-width,y)
    tucked=QPoint(area.left()-width//2 if side=='left' else area.right()+1-width//2,y)
    return side,tucked,full


class FloatingBall(DropSurface, QWidget):
    intake = Signal(object)
    open_requested = Signal()
    paste_requested = Signal()
    files_requested = Signal()
    quit_requested = Signal()
    background_requested = Signal()
    visibility_requested = Signal()
    saved_location_requested = Signal()
    position_changed = Signal(object)
    hint_changed = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('BOX · 悬浮球')
        self.setAccessibleName('BOX 悬浮球：点击打开总表，拖入名称或材料')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFixedSize(76, 76)
        self._press = None
        self._start = QPoint()
        self._dragged = False
        self.drop_kind = ''
        self.count = 0
        self.busy = False
        self.percent = 0
        self._hovered=False;self._menu_open=False;self._docked_side='';self._dock_screen=None;self._dock_area=None
        self.idle_timer=QTimer(self);self.idle_timer.setSingleShot(True);self.idle_timer.setInterval(15000);self.idle_timer.timeout.connect(self.dock_to_edge)
        self.edge_animation=QPropertyAnimation(self,b'pos',self);self.edge_animation.setDuration(180);self.edge_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.edge_animation.valueChanged.connect(self.clip_docked_ball)
        app=QApplication.instance()
        for screen in app.screens():self.watch_screen(screen)
        app.screenAdded.connect(self.watch_screen);app.screenRemoved.connect(self.screen_layout_changed)
        theme_signal().connect(self._appearance_changed)
        self.setToolTip('BOX · 单击或双击打开 · 按住拖动 · 右键更多\n15 秒未操作自动半隐藏，鼠标移入展开')

    def watch_screen(self,screen):
        screen.availableGeometryChanged.connect(self.screen_layout_changed)
        screen.geometryChanged.connect(self.screen_layout_changed)

    def screen_layout_changed(self,*_):
        self.edge_animation.stop();self.clearMask();self._docked_side='';self._dock_screen=None
        screen=QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen is None:return
        area=screen.availableGeometry();p=self.pos()
        self.move(max(area.left(),min(p.x(),area.right()+1-self.width())),max(area.top(),min(p.y(),area.bottom()+1-self.height())))
        self.restart_idle()

    def restart_idle(self):
        self.idle_timer.stop()
        if self.isVisible() and not (self._hovered or self._menu_open or self._docked_side or self._press is not None or self.drop_kind or self.busy):self.idle_timer.start()

    def dock_to_edge(self):
        if not self.isVisible() or self._docked_side or self._hovered or self._menu_open or self._press is not None or self.drop_kind or self.busy:return
        self.edge_animation.stop()
        self._dock_screen=QApplication.screenAt(self.geometry().center()) or self.screen()
        self._dock_area=QRect(self._dock_screen.availableGeometry())
        self._docked_side,target,_=edge_targets(self._dock_area,self.pos(),self.size())
        self.idle_timer.stop();self.animate_to(target)

    def animate_to(self,target):
        self.edge_animation.stop();self.edge_animation.setStartValue(self.pos());self.edge_animation.setEndValue(target);self.edge_animation.start()
        self.clip_docked_ball()

    def clip_docked_ball(self,*_):
        if self._docked_side and self._dock_area is not None:
            visible=self._dock_area.translated(-self.pos()).intersected(self.rect())
            self.setMask(QRegion(visible))

    def reveal(self):
        self.idle_timer.stop()
        if self._docked_side:
            side=self._docked_side;area=self._dock_area
            self.edge_animation.stop();self._docked_side='';self.clearMask()
            # Expand toward the screen interior; keep the visible half under the pointer.
            x=area.left() if side=='left' else area.right()+1-self.width()
            self.move(x,max(area.top(),min(self.y(),area.bottom()+1-self.height())))
        self.restart_idle()

    def enterEvent(self,event):
        self._hovered=True;self.reveal();super().enterEvent(event)

    def leaveEvent(self,event):
        self._hovered=False;self.restart_idle();super().leaveEvent(event)

    def showEvent(self,event):
        super().showEvent(event);self.restart_idle()

    def hideEvent(self,event):
        self.idle_timer.stop();self.edge_animation.stop();self._hovered=False
        self.reveal();super().hideEvent(event)

    def set_drop_hint(self, kind):
        self.drop_kind = kind
        if kind:self.reveal()
        self.restart_idle()
        self.hint_changed.emit('松开：分类收录材料，保留原件' if kind == 'files' else '松开：整理软件名称与材料要求' if kind else '')
        self.update()

    def set_summary(self, count):
        self.count = count
        self.update()

    def set_progress(self, busy, percent=0):
        changed=busy!=self.busy
        self.busy, self.percent = busy, percent
        if changed:
            if busy:self.reveal()
            self.restart_idle()
        self.update()

    def _appearance_changed(self, _mode):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        draw_orb(p, QRectF(self.rect()))
        if self.drop_kind:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor('#242424'), 1.5))
            p.drawEllipse(QRectF(8, 7, 60, 60))
        if self.busy:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor('#242424'), 2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(5, 4, 66, 66), 90 * 16, -int(max(12, self.percent * 3.6) * 16))
        if self.count and not self.busy:
            p.setPen(QPen(QColor('#FFFFFF'), 2))
            p.setBrush(QColor('#242424'))
            p.drawEllipse(QRectF(52, 3, 20, 20))
            p.setPen(QColor('#FFFFFF'))
            font = QFont('Segoe UI'); font.setPixelSize(10); font.setWeight(QFont.DemiBold)
            p.setFont(font)
            p.drawText(QRectF(52, 3, 20, 20), Qt.AlignCenter, str(self.count) if self.count < 100 else '99+')
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.reveal();self.idle_timer.stop()
            self._press = event.globalPosition().toPoint()
            self._start = self.pos()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None:
            delta = event.globalPosition().toPoint() - self._press
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragged = True
            if self._dragged:
                self.move(self._start + delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            if self._dragged:
                self.position_changed.emit([self.x(), self.y()])
            else:
                self.open_requested.emit()
            self._press = None
            self.restart_idle()

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton:
            # A native double-click is its own event, not a second release callback.
            self._press=None;self._dragged=False;self.reveal()
            self.open_requested.emit();event.accept()
        else:super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        self.reveal()
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.open_requested.emit()
        elif event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier:
            self.paste_requested.emit()
        else:
            super().keyPressEvent(event)

    def context_menu(self):
        menu = WorkspaceMenu(self)
        menu.setAccessibleName('BOX 快捷操作')
        menu.addAction(outline_icon('box'), '打开 BOX', self.open_requested.emit)
        menu.addAction(outline_icon('upload'), '收录材料', self.files_requested.emit)
        menu.addAction(outline_icon('box'), '打开文件保存位置', self.saved_location_requested.emit)
        menu.addSeparator()
        visibility = menu.addAction('', self.visibility_requested.emit)
        def sync_visibility():
            visibility.setText('隐藏悬浮球' if self.isVisible() else '显示悬浮球')
            visibility.setIcon(outline_icon('hide' if self.isVisible() else 'show'))
        menu.aboutToShow.connect(sync_visibility)
        sync_visibility()
        menu.addAction(outline_icon('power'), '退出 BOX', self.quit_requested.emit)
        return menu

    def contextMenuEvent(self, event):
        self._menu_open=True;self.reveal();menu=self.context_menu()
        try:menu.exec(event.globalPos())
        finally:menu.deleteLater();self._menu_open=False;self.restart_idle()
