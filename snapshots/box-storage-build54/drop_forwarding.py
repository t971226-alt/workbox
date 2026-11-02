"""Route local file drops through one intake even over nested editor viewports."""
from PySide6.QtCore import QObject,QEvent,Qt,QTimer
from PySide6.QtWidgets import QApplication,QWidget
from floating import decode_mime
import weakref
import shiboken6

class FileDropForwarder(QObject):
    def __init__(self,panel):
        super().__init__(QApplication.instance());self._panel=weakref.ref(panel);self.active=True
        self._watched=weakref.WeakSet();self._refresh=QTimer(self);self._refresh.setSingleShot(True);self._refresh.timeout.connect(self.refresh)
        panel.destroyed.connect(self.detach);self.attach(panel)
    def attach(self,widget):
        if not self.active or not shiboken6.isValid(widget) or not isinstance(widget,QWidget):return
        for child in [widget,*widget.findChildren(QWidget)]:
            if child not in self._watched:
                self._watched.add(child);child.setAcceptDrops(True);child.installEventFilter(self)
    def refresh(self):
        panel=self._panel()
        if self.active and panel is not None and shiboken6.isValid(panel):self.attach(panel)
    def detach(self,*_):
        if not self.active:return
        self.active=False
        self._refresh.stop()
        for widget in list(self._watched):
            if shiboken6.isValid(widget):widget.removeEventFilter(self)
        self._watched.clear()
        self.deleteLater()
    def eventFilter(self,watched,event):
        kind=event.type();panel=self._panel()
        if kind==QEvent.ChildAdded:self._refresh.start(0);return False
        if kind==QEvent.DeferredDelete and watched is panel:self.detach();return False
        if kind not in (QEvent.DragEnter,QEvent.DragMove,QEvent.Drop):return False
        if not self.active or panel is None or not shiboken6.isValid(panel) or not isinstance(watched,QWidget) or watched.window()!=panel:return False
        mime=event.mimeData()
        if not mime.hasUrls():return False
        data=decode_mime(mime)
        if data['kind']!='files':event.ignore();return True
        event.setDropAction(Qt.CopyAction);event.accept()
        if kind==QEvent.Drop:panel.intake.emit(data)
        return True

def install(panel):panel._file_drop_forwarder=FileDropForwarder(panel)
