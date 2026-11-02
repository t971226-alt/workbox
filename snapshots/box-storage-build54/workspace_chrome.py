"""Compact workspace controls with native button/menu/keyboard behavior."""
from PySide6.QtCore import Qt, QSize, QRectF, QPointF, QDate, QEvent, QFile, QIODevice
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QPainterPath, QIcon, QIconEngine, QPixmap
from PySide6.QtWidgets import QPushButton, QComboBox, QLineEdit, QMenu, QWidgetAction, QCalendarWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStyledItemDelegate, QStyle, QListView, QAbstractItemView
from theme import resolved_mode
from functools import lru_cache
from PySide6.QtSvg import QSvgRenderer
import lucide_icons_rc  # Registers the vendored official SVGs; works offline in the EXE.


def colors():
    dark = resolved_mode() == 'dark'
    return dict(ink='#ECECEC' if dark else '#292929', muted='#ADADAD' if dark else '#6B6B6B',
                hover='#333333' if dark else '#F1F1F1', border='#444444' if dark else '#E2E2E2',
                solid='#ECECEC' if dark else '#252525', on_solid='#202020' if dark else '#FFFFFF',
                field='#292929' if dark else '#F7F7F7', surface='#252525' if dark else '#FFFFFF')


ICON_NAMES = {
    'categories': 'layers-2', 'minus': 'minus', 'plus': 'plus', 'send': 'arrow-up', 'more': 'ellipsis', 'compose': 'square-pen',
    'copy': 'copy', 'upload': 'upload', 'arrow': 'arrow-right', 'back': 'arrow-left',
    'refresh': 'refresh-cw', 'check': 'check', 'close': 'x', 'chevron': 'chevron-down', 'expand': 'chevron-down',
    'search': 'search', 'filter': 'list-filter', 'settings': 'sliders-horizontal',
    'calendar': 'calendar', 'filter-clear': 'rotate-ccw',
    'box': 'package-open', 'show': 'eye', 'hide': 'eye-off', 'power': 'power',
}


@lru_cache(maxsize=128)
def icon_renderer(name, color):
    source = QFile(':/workbox/icons/' + ICON_NAMES[name] + '.svg')
    if not source.open(QIODevice.ReadOnly):
        raise RuntimeError('图标资源缺失：' + name)
    data = bytes(source.readAll()); source.close()
    renderer = QSvgRenderer(data.replace(b'currentColor', color.encode('ascii')))
    if not renderer.isValid():
        raise RuntimeError('图标资源无效：' + name)
    return renderer


def draw_icon(painter, rect, name, color):
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        if name=='expand':
            painter.translate(rect.center());painter.rotate(-90)
            rect=QRectF(-rect.width()/2,-rect.height()/2,rect.width(),rect.height())
        icon_renderer(name, color).render(painter, rect)
    finally:
        painter.restore()


class _OutlineIcon(QIconEngine):
    def __init__(self, name):
        super().__init__()
        self.name = name

    def clone(self):
        return _OutlineIcon(self.name)

    def paint(self, painter, rect, mode, state):
        draw_icon(painter, QRectF(rect), self.name, colors()['muted' if mode == QIcon.Disabled else 'ink'])

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        self.paint(painter, result.rect(), mode, state)
        painter.end()
        return result


def outline_icon(name):
    return QIcon(_OutlineIcon(name))


class WorkspaceMenu(QMenu):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.aboutToShow.connect(self.refresh_chrome)
        self.refresh_chrome()

    def refresh_chrome(self):
        c = colors()
        self.setStyleSheet('QMenu { background: '+c['surface']+'; color: '+c['ink']+'; border: 1px solid '+c['border']+'; border-radius: 9px; padding: 6px; font-size: 13px; } QMenu::item { background: transparent; color: '+c['ink']+'; padding: 8px 24px 8px 12px; margin: 1px 0; border: none; border-radius: 5px; } QMenu::item:selected { background: '+c['hover']+'; } QMenu::item:disabled { color: '+c['muted']+'; } QMenu::separator { height: 1px; background: '+c['border']+'; margin: 5px 8px; }')


class MaterialCheckDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        c = colors(); painter.save(); painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(c['hover']))
        checked = index.data(Qt.CheckStateRole) in (Qt.Checked, Qt.Checked.value)
        rect = QRectF(option.rect.center().x()-6.5, option.rect.center().y()-6.5, 14, 14)
        painter.setPen(QPen(QColor(c['solid'] if checked else c['muted']), 1))
        painter.setBrush(QColor(c['solid']) if checked else Qt.NoBrush)
        painter.drawRoundedRect(rect, 3, 3)
        if checked:
            draw_icon(painter, rect.adjusted(1,1,-1,-1), 'check', c['on_solid'])
        painter.restore()

    def editorEvent(self, event, model, option, index):
        mouse = event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
        key = event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Select)
        if mouse or key:
            checked = index.data(Qt.CheckStateRole) in (Qt.Checked, Qt.Checked.value)
            return model.setData(index, Qt.Unchecked.value if checked else Qt.Checked.value, Qt.CheckStateRole)
        return False


class WorkspaceOptionDelegate(QStyledItemDelegate):
    def __init__(self,combo):
        super().__init__(combo);self.combo=combo

    def sizeHint(self,option,index):
        f=QFont('Microsoft YaHei UI');f.setPixelSize(13)
        return QSize(min(420,max(120,QFontMetrics(f).horizontalAdvance(str(index.data() or ''))+48)),34)

    def paint(self,painter,option,index):
        c=colors();painter.save();painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(option.rect,QColor(c['surface']))
        active=bool(option.state&(QStyle.State_Selected|QStyle.State_MouseOver));enabled=bool(index.flags()&Qt.ItemIsEnabled)
        rect=QRectF(option.rect).adjusted(3,1,-3,-1)
        if active and enabled:
            painter.setPen(Qt.NoPen);painter.setBrush(QColor(c['hover']));painter.drawRoundedRect(rect,5,5)
        f=QFont('Microsoft YaHei UI');f.setPixelSize(13);painter.setFont(f)
        painter.setPen(QColor(c['ink'] if enabled else c['muted']))
        decoration=index.data(Qt.DecorationRole);has_icon=isinstance(decoration,QIcon) and not decoration.isNull()
        if has_icon:decoration.paint(painter,int(rect.left()+8),int(rect.center().y()-10),16,21)
        offset=30 if has_icon else 9
        text=QFontMetrics(f).elidedText(str(index.data() or ''),Qt.ElideRight,max(1,int(rect.width()-offset-26)))
        painter.drawText(rect.adjusted(offset,0,-26,0),Qt.AlignVCenter,text)
        if enabled and index.row()==self.combo.currentIndex():
            draw_icon(painter,QRectF(rect.right()-21,rect.center().y()-7,14,14),'check',c['ink'])
        painter.restore()


class WorkspaceComboBox(QComboBox):
    """Native Qt selection and popups, styled by the workspace theme."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        view=QListView();view.setMouseTracking(True);view.setFrameShape(QListView.NoFrame)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setView(view);self.setItemDelegate(WorkspaceOptionDelegate(self))
        self.setStyleSheet('WorkspaceComboBox { border: none; padding: 0 0 0 10px; min-height: 0; font-size: 13px; } WorkspaceComboBox::drop-down {width:30px;border:none;}')

    def sizeHint(self):
        return QSize(max(90, self.fontMetrics().horizontalAdvance(self.currentText())+42), 32)

    def minimumSizeHint(self):
        return QSize(80, 32)

    def setEditable(self,editable):
        super().setEditable(editable)
        if self.lineEdit():
            self.lineEdit().setFrame(False)
            self.lineEdit().setStyleSheet('QLineEdit {background:transparent;border:none;padding:0;margin:0;font-family:Microsoft YaHei UI;font-size:13px;}')

    def paintEvent(self, event):
        c = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plain = bool(self.property('plain'))
        painter.setPen(Qt.NoPen if plain else QPen(QColor(c['border']), 1))
        painter.setBrush(QColor(c['hover'] if self.underMouse() else c['field']) if not plain or self.underMouse() else Qt.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5), 8, 8)
        font = QFont('Microsoft YaHei UI'); font.setPixelSize(self.property('fontPixelSize') or 13)
        painter.setFont(font); painter.setPen(QColor(c['ink']))
        caption = self.currentData(Qt.ToolTipRole) if self.property('history') else self.currentText()
        caption = caption or self.currentText()
        if not self.isEditable():
            current_icon=self.itemIcon(self.currentIndex());offset=10
            if not current_icon.isNull():current_icon.paint(painter,10,(self.height()-22)//2,17,22);offset=34
            painter.drawText(QRectF(offset, 0, max(1,self.width()-offset-28), self.height()), Qt.AlignVCenter,
                             QFontMetrics(font).elidedText(caption, Qt.ElideRight, max(1,self.width()-offset-28)))
        draw_icon(painter, QRectF(self.width()-26, (self.height()-18)/2, 18, 18), 'chevron', c['muted'])

    def showPopup(self):
        if self.property('history') and not any(self.itemData(i) is not None and self.model().flags(self.model().index(i,0))&Qt.ItemIsEnabled for i in range(self.count())):
            self.setToolTip('还没有会话记录，发送一条名称或需求后会保存在这里。');return
        c = colors()
        self.view().setStyleSheet('QListView { background:'+c['surface']+'; color:'+c['ink']+'; border:1px solid '+c['border']+'; border-radius:7px; padding:4px; outline:0; } QListView::item { padding:0; border:none; }')
        super().showPopup()


class ChromeButton(QPushButton):
    """Draw compact surfaces directly; preserve QPushButton activation semantics."""
    def __init__(self, text='', *, icon=None, variant='quiet', icon_only=False, label=None):
        super().__init__(text)
        self.symbol, self.variant, self.icon_only = icon, variant, icon_only
        self.keyboard_focus = False
        self.setStyleSheet('ChromeButton { border: none; padding: 0; min-height: 0; min-width: 0; background: transparent; }')
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setAttribute(Qt.WA_Hover)
        if icon:
            self.setIcon(outline_icon(icon))
            self.setIconSize(QSize(16, 16))
        self.setAccessibleName(label or text)
        if label:
            self.setToolTip(label)
        if icon_only:
            self.setFixedWidth(30)

    def drawing_font(self):
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(int(self.property('fontPixelSize') or 13))
        font.setHintingPreference(QFont.PreferFullHinting)
        return font

    def sizeHint(self):
        if self.icon_only:
            return QSize(30, 30)
        width = QFontMetrics(self.drawing_font()).horizontalAdvance(self.text()) + 24
        if self.symbol:
            width += 22
        return QSize(width, 30)

    def minimumSizeHint(self):
        return self.sizeHint()

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.keyboard_focus = False
        super().focusOutEvent(event)

    def paintEvent(self, event):
        c = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(.75, .75, -.75, -.75)
        hovered = self.underMouse() or self.isDown() or self.isChecked()
        foreground = c['ink']
        background = c['hover'] if hovered else None
        border = None
        if self.variant == 'primary':
            background, foreground = c['solid'], c['on_solid']
        elif self.variant == 'secondary':
            border = c['border']
        elif self.variant == 'on-dark':
            foreground = '#DDDDDD'
            background = '#444444' if hovered else None
        if self.property('expandableGreen'):
            dark=QColor(c['surface']).lightness()<128
            background=('#365442' if hovered else '#2D4638') if dark else ('#DEEEE3' if hovered else '#EAF5ED')
            foreground='#D8EDDF' if dark else '#2D513A'
        if not self.isEnabled():
            painter.setOpacity(.42)
        painter.setPen(QPen(QColor(border), 1) if border else Qt.NoPen)
        painter.setBrush(QColor(background) if background else Qt.NoBrush)
        radius = 3 if self.property('checkbox') else 15 if self.icon_only else 7
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus() and self.keyboard_focus:
            painter.setPen(QPen(QColor(c['muted']), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius-1, radius-1)
        if self.icon_only:
            if self.symbol:
                extent = 14 if self.property('checkbox') else 18
                draw_icon(painter, QRectF((self.width()-extent)/2, (self.height()-extent)/2, extent, extent), self.symbol, foreground)
        else:
            font = self.drawing_font()
            painter.setFont(font)
            display = QFontMetrics(font).elidedText(self.text(), Qt.ElideRight, max(1,self.width()-20-(22 if self.symbol else 0))) if self.property('leftAligned') else self.text()
            text_width = QFontMetrics(font).horizontalAdvance(display)
            start = (self.width()-text_width-(22 if self.symbol else 0))/2
            if self.property('leftAligned'):
                start = 6
            if self.symbol:
                draw_icon(painter, QRectF(start, (self.height()-16)/2, 16, 16), self.symbol, foreground)
                start += 22
            painter.setPen(QColor(foreground))
            painter.drawText(QRectF(start, 0, text_width+1, self.height()), Qt.AlignVCenter | Qt.AlignLeft, display)


def chrome_button(text='', **kwargs):
    return ChromeButton(text, **kwargs)


class SearchField(QLineEdit):
    """Use the editor's native text layout and one shared, centered icon box."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrame(False)
        self.setFixedHeight(34)
        self.setTextMargins(34, 0, 30, 0)
        self.setStyleSheet('SearchField { background: transparent; border: none; padding: 0; min-height: 0; font-size: 13px; }')
        self.clear_control = chrome_button(icon='close', icon_only=True, label='清除搜索')
        self.clear_control.setParent(self)
        self.clear_control.setFocusPolicy(Qt.NoFocus)
        self.clear_control.clicked.connect(self.clear)
        self.textChanged.connect(lambda text: self.clear_control.setVisible(bool(text)))
        self.clear_control.hide()

    def setClearButtonEnabled(self, enabled):
        # The native QLineEdit tool button has a separate icon family and padding.
        if hasattr(self, 'clear_control'):
            self.clear_control.setVisible(bool(enabled and self.text()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.clear_control.move(self.width()-31, (self.height()-30)//2)

    def paintEvent(self, event):
        c = colors()
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(c['muted'] if self.hasFocus() else c['border']), 1))
        painter.setBrush(QColor(c['field']))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5), 9, 9)
        painter.end()
        super().paintEvent(event)
        painter = QPainter(self)
        draw_icon(painter, QRectF(11, (self.height()-16)/2, 16, 16), 'search', c['muted'])


class PeriodButton(ChromeButton):
    def __init__(self, date_control, mode_control):
        super().__init__('', icon='calendar')
        self.date_control, self.mode_control = date_control, mode_control
        self.setAccessibleName('选择统计日期')
        self.clicked.connect(self.choose)
        date_control.dateChanged.connect(self.sync)
        mode_control.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self, *_):
        date = self.date_control.date()
        self.setText(date.toString('yyyy年M月' if self.mode_control.currentIndex() else 'M月d日'))
        self.updateGeometry(); self.update()

    def choose(self):
        menu = WorkspaceMenu(self)
        calendar = QCalendarWidget(menu)
        calendar.setNavigationBarVisible(False)
        calendar.setSelectedDate(self.date_control.date())
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        calendar.setFixedSize(284, 235)
        body = QWidget(menu); layout = QVBoxLayout(body); layout.setContentsMargins(10, 10, 10, 10)
        body.setFixedSize(304, 293)
        heading = QHBoxLayout()
        previous = chrome_button(icon='back', icon_only=True, label='上个月')
        following = chrome_button(icon='arrow', icon_only=True, label='下个月')
        caption = QLabel(calendar.selectedDate().toString('yyyy年M月'))
        caption.setAlignment(Qt.AlignCenter)
        previous.clicked.connect(calendar.showPreviousMonth); following.clicked.connect(calendar.showNextMonth)
        calendar.currentPageChanged.connect(lambda year, month: caption.setText(f'{year}年{month}月'))
        heading.addWidget(previous); heading.addWidget(caption, 1); heading.addWidget(following)
        layout.addLayout(heading); layout.addWidget(calendar)
        c = colors()
        calendar.setStyleSheet('QCalendarWidget QWidget { background: '+c['surface']+'; color: '+c['ink']+'; font-size: 12px; } QCalendarWidget QAbstractItemView { border: none; selection-background-color: '+c['solid']+'; selection-color: '+c['on_solid']+'; } QCalendarWidget QAbstractItemView::item { padding: 0; border: none; } QCalendarWidget QAbstractItemView::item:selected { background: '+c['solid']+'; color: '+c['on_solid']+'; border-radius: 4px; }')
        action = QWidgetAction(menu); action.setDefaultWidget(body); menu.addAction(action)
        calendar.clicked.connect(lambda day: (self.date_control.setDate(day), menu.close()))
        calendar.activated.connect(lambda day: (self.date_control.setDate(day), menu.close()))
        try:
            menu.exec(self.mapToGlobal(self.rect().topLeft()))
        finally:
            menu.deleteLater()
