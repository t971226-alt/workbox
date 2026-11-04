"""Single-instance native lifecycle and sequential, cancellable intake jobs."""

from collections import deque
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QLockFile, QPoint, QRect, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QColor, QPainter, QPen, QLinearGradient
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QSystemTrayIcon, QVBoxLayout
import shiboken6

from compact_ui import CompactPanel, button, label, zip_delivery_hint
from floating import FloatingBall, decode_mime
from file_formats import ARCHIVES
from ui import FileJob, app_icon
from native_files import plain_path
from theme import apply_to
from dialog_lifecycle import transient_dialog


def clamp_position(position, size):
    screens = QApplication.screens()
    point = QPoint(*position)
    screen = next((s for s in screens if s.availableGeometry().contains(point)), screens[0])
    area = screen.availableGeometry().adjusted(6, 6, -6, -6)
    return QPoint(max(area.left(), min(point.x(), area.right()-size.width()+1)),
                  max(area.top(), min(point.y(), area.bottom()-size.height()+1)))


class FeedbackBubble(QFrame):
    open_requested = Signal()
    cancel_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('WorkBox 收件反馈')
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.navigation_target = None
        apply_to(self, 'feedback')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(342)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 22, 25, 24)
        layout.setSpacing(12)
        heading = QHBoxLayout();heading.setSpacing(10)
        self.symbol = QLabel();self.symbol.setFixedSize(32,32);self.symbol.setAlignment(Qt.AlignCenter)
        heading.addWidget(self.symbol,0,Qt.AlignTop)
        copy = QVBoxLayout();copy.setSpacing(4)
        self.heading = QLabel('BOX');copy.addWidget(self.heading)
        self.text = QLabel();self.text.setWordWrap(True);self.text.setTextFormat(Qt.PlainText)
        copy.addWidget(self.text);heading.addLayout(copy,1)
        from workspace_chrome import chrome_button
        self.close_button = chrome_button(icon='close',icon_only=True,label='收起提示')
        self.close_button.setFixedSize(24,24);self.close_button.clicked.connect(self.hide)
        heading.addWidget(self.close_button,0,Qt.AlignTop);layout.addLayout(heading)
        row = QHBoxLayout();row.setSpacing(6)
        self.cancel = chrome_button('取消处理',variant='secondary')
        self.view = chrome_button('查看',variant='primary')
        self.view.setFixedHeight(28);self.cancel.setFixedHeight(28)
        self.view.clicked.connect(self.open_requested)
        self.cancel.clicked.connect(self.cancel_requested)
        row.addStretch();row.addWidget(self.cancel);row.addWidget(self.view)
        layout.addLayout(row)
        from theme import manager
        manager().changed.connect(self.refresh_chrome)
        self.refresh_chrome()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def refresh_chrome(self,*_):
        from workspace_chrome import colors,outline_icon
        c=colors()
        self.setStyleSheet('QFrame#feedbackBubble { background: transparent; border: none; }')
        self.heading.setStyleSheet('background:transparent;border:none;color:'+c['muted']+';font-family:"Microsoft YaHei UI";font-size:11px;font-weight:400;')
        self.text.setStyleSheet('background:transparent;border:none;color:'+c['ink']+';font-family:"Microsoft YaHei UI";font-size:13px;font-weight:400;')
        self.symbol.setStyleSheet('border:none;border-radius:9px;background:'+c['field']+';')
        self.symbol.setPixmap(outline_icon('box').pixmap(18,18))
        self.update()

    def paintEvent(self,event):
        from workspace_chrome import colors
        c=colors();p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        card=QRectF(self.rect()).adjusted(10,8,-10,-13)
        p.setPen(Qt.NoPen)
        for spread in range(8,0,-1):
            p.setBrush(QColor(0,0,0,2 if spread>3 else 3))
            p.drawRoundedRect(card.adjusted(-spread,2-spread,spread,2+spread),14+spread,14+spread)
        gradient=QLinearGradient(card.topLeft(),card.bottomLeft())
        gradient.setColorAt(0,QColor(c['surface']));gradient.setColorAt(1,QColor('#FCFCFC' if c['surface']=='#FFFFFF' else '#222222'))
        p.setBrush(gradient);p.setPen(QPen(QColor(c['border']),.8));p.drawRoundedRect(card,14,14)
        p.end()

    def show_message(self, text, orb, busy=False, *, target=None):
        self.navigation_target = target
        self.heading.setText('准备收录' if text.startswith('松开：') else '正在处理' if busy else 'BOX')
        self.refresh_chrome()
        self.text.setText(text[:260])
        self.text.setToolTip(text)
        self.cancel.setVisible(busy)
        self.adjustSize()
        area = orb.screen().availableGeometry()
        x = orb.x()-self.width()-10 if orb.x() > area.center().x() else orb.x()+orb.width()+10
        self.move(clamp_position([x, orb.y()], self.size()))
        self.show()
        if busy:
            self.timer.stop()
        else:
            self.timer.start(6000)


class FloatingController(QObject):
    exit_requested = Signal()

    def __init__(self, service, *, tray_enabled=True):
        super().__init__()
        self.service = service
        self.tray_enabled = tray_enabled
        self.orb = self.panel = self.bubble = self.tray = self.server = None
        self.worker = None
        self.queue = deque()
        self.errors = []
        self.closed = False
        self.quitting = False
        self.owns_lock = False
        self.silent_current = False
        self.background_mode = False
        self.choosing_intake = False
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(lambda: self.check_archives(silent=True))
        self.lock = QLockFile(str(service.db.path) + '.session.lock')
        self.lock.setStaleLockTime(0)
        self.server_name = 'WorkBox-' + hashlib.sha256(str(service.db.path).casefold().encode()).hexdigest()[:24]

    @property
    def busy(self):
        return self.choosing_intake or self.worker is not None or bool(self.queue)

    @property
    def foreground_busy(self):
        # 监视仍占用同一个串行任务槽，但不能每两秒禁用界面。
        return (self.choosing_intake or (self.worker is not None and not self.silent_current)
                or any(not item[2] for item in self.queue))

    def _sync_panel_busy(self):
        busy = self.foreground_busy
        if self.panel.busy != busy:
            self.panel.set_busy(busy)

    def start(self, *, setup_callback=None):
        if not self.lock.tryLock(0):
            socket = QLocalSocket(self)
            self._activation_socket=socket
            socket.connectToServer(self.server_name)
            if socket.waitForConnected(1000):
                sources=getattr(self,'pending_legacy_sources',[])
                socket.write((json.dumps(dict(command='activate',legacy_sources=sources))+'\n').encode('utf-8'))
                socket.waitForBytesWritten(1000)
                socket.readyRead.connect(lambda:socket.disconnectFromServer())
                QTimer.singleShot(2000,socket.disconnectFromServer)
            return False
        self.owns_lock = True
        self.server = QLocalServer(self)
        QLocalServer.removeServer(self.server_name)
        if not self.server.listen(self.server_name):
            self.lock.unlock()
            self.owns_lock = False
            raise OSError('无法建立本地单实例通信。')
        self.server.newConnection.connect(self._activate)
        # 持有单实例锁后才打开首次向导；此时没有收件入口或监视任务。
        if setup_callback is not None and self.service.workspaces.setup_required():
            if not setup_callback():
                self.shutdown()
                return False
            if self.service.workspaces.setup_required():
                raise ValueError('首次设置尚未保存，未启动收件与归档。')
        self.orb = FloatingBall()
        self.panel = CompactPanel(self.service)
        self.bubble = FeedbackBubble()
        self.orb.intake.connect(self.receive_entry)
        self.panel.intake.connect(self.receive_entry)
        self.orb.open_requested.connect(self.show_panel)
        self.bubble.open_requested.connect(self.show_feedback)
        for signal in (self.orb.paste_requested, self.panel.paste_requested):
            signal.connect(self.paste)
        for signal in (self.orb.files_requested, self.panel.files_requested):
            signal.connect(self.choose_files)
        for signal in (self.orb.quit_requested, self.panel.quit_requested):
            signal.connect(self.request_quit)
        for signal in (self.orb.background_requested, self.panel.background_requested):
            signal.connect(self.run_in_background)
        self.orb.visibility_requested.connect(self.toggle_orb)
        self.orb.saved_location_requested.connect(lambda:self.open_folder(self.service.workspaces.settings()['work_root']))
        self.panel.folder_requested.connect(self.open_folder)
        self.panel.cancel_requested.connect(self.cancel_jobs)
        self.panel.legacy_import_requested.connect(self.open_legacy_import)
        self.panel.data_changed.connect(self.refresh)
        self.panel.production_requested.connect(lambda caption, operation: self.enqueue(caption, operation))
        self.bubble.cancel_requested.connect(self.cancel_jobs)
        self.orb.position_changed.connect(self.save_position)
        self.orb.hint_changed.connect(self.drop_hint)
        self.panel.state_requested.connect(lambda pid, state: self.enqueue('状态已更新', lambda **_: self.service.set_status(pid, state)))
        self.panel.urgent_requested.connect(lambda pid, urgent: self.enqueue('加急标记已更新', lambda **_: self.service.set_urgent(pid, urgent)))
        self.panel.rename_requested.connect(lambda pid, name: self.enqueue('项目名称已更新', lambda **_: self.service.rename_project(pid, name)))
        self.panel.bulk_requested.connect(self.update_projects)
        self.panel.delete_requested.connect(lambda pid: self.set_project_deleted(pid, True))
        self.panel.restore_deleted_requested.connect(lambda pid: self.set_project_deleted(pid, False))
        self.panel.workspace_requested.connect(self.open_workspace)
        self.panel.scan_requested.connect(lambda: self.check_archives(silent=False))
        self.panel.approve_requested.connect(lambda zid: self.enqueue('已标记为可用版本', lambda **_: self.service.snapshots.approve_version(zid)))
        self.panel.bundle_requested.connect(lambda zid, batch: self.enqueue('核对包内项目', lambda **kw: self.service.snapshots.recognize_bundle(zid, batch_id=batch, **kw)))
        self.panel.delivery_requested.connect(lambda pid, zid: self.enqueue('已手动设为已交付', lambda **kw: self.service.confirm_delivery(pid, zid, confirmed=True, **kw)))
        self.panel.link_requested.connect(lambda zid, pid: self.enqueue('关联交付包', lambda **_: self._link_zip(zid, pid)))
        available = QApplication.primaryScreen().availableGeometry()
        saved = self.service.get_setting('orb_position', [available.right()-100, available.center().y()])
        self.orb.move(clamp_position(saved, self.orb.size()))
        geometry = self.service.get_setting('manager_geometry')
        if geometry and len(geometry) == 4:
            self.panel.setGeometry(QRect(*geometry))
        if self.service.get_setting('workspace_chrome_revision') != 2:
            self.panel.resize(min(self.panel.width(), 870), min(self.panel.height(), 640))
            self.service.set_setting('workspace_chrome_revision', 2)
        self.orb.show()
        self.refresh()
        # 即使外壳暂未就绪也注册图标；Qt 会在托盘恢复可用时自动添加。
        if self.tray_enabled:
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip('BOX · 软件材料总表与分类收录')
            self.tray.setContextMenu(self.orb.context_menu())
            self.tray.activated.connect(lambda reason: self.show_panel() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self.tray.show()
        with self.service.db.connect() as connection:
            pending = connection.execute("SELECT COUNT(*) FROM managed_transfers WHERE state!='complete'").fetchone()[0]
        if pending:
            self.enqueue('检查上次归档结果', lambda **_: self.service.archive.recover())
        from unified_storage import needs_unification,unify_storage
        if needs_unification(self.service):
            self.enqueue('统一材料保存目录',lambda **kw:unify_storage(self.service,**kw))
        elif self.service.production.collection_migration_needed():
            self.enqueue('更新分类收录目录名称', lambda **kw: self.service.production.refresh_collections(**kw))
        self.monitor_timer.start()
        return True

    def check_archives(self, *, silent=True):
        if self.busy or self.closed or self.quitting or QApplication.activeModalWidget() is not None:
            return
        self.enqueue('检查 ZIP 归档', lambda **kw: self.service.scan_archives(**kw), silent=silent)

    def open_workspace(self, project_id):
        def operation(**_):
            rows = self.service.ensure_workspaces(project_id)
            return {'open_folder': next(row['path'] for row in rows if row['project_id'] == project_id)}
        project = self.service.get_project(project_id)
        if project.get('workspace') and Path(project['workspace']['path']).is_dir():
            self.open_folder(project['workspace']['path'])
        elif project.get('archived_at'):
            self.notify('此项目已归档且工作目录不存在，请先恢复项目或检查目录位置。')
        elif not self.foreground_busy:
            self.enqueue('创建项目工作目录', operation)
        else:
            self.notify('正在处理文件，工作目录尚未就绪，请稍后再试。')

    def _activate(self):
        if not hasattr(self,'_ipc_clients'):self._ipc_clients=set()
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._ipc_clients.add(socket)
            socket._box_message=b'';socket._box_handled=False
            socket.readyRead.connect(lambda s=socket:self._ipc_message(s))
            socket.disconnected.connect(lambda s=socket:self._ipc_disconnected(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():self._ipc_message(socket)

    def _ipc_disconnected(self,socket):
        if not socket._box_handled:self._ipc_message(socket)
        self._ipc_clients.discard(socket)

    def _ipc_message(self,socket):
        if socket._box_handled:return
        socket._box_message+=bytes(socket.readAll())
        raw=socket._box_message
        if len(raw)>65536:socket.disconnectFromServer();return
        if raw.startswith(b'{'):
            if b'\n' not in raw:return
            try:message=json.loads(raw.split(b'\n',1)[0])
            except ValueError:socket.disconnectFromServer();return
        elif raw==b'activate':message=dict(command='activate')
        else:return
        socket._box_handled=True
        if message.get('command')=='prepare-update':
            blocked=self.busy or self.closed or self.quitting or QApplication.activeModalWidget() is not None or not self._valid_update_request(message)
            socket.write(b'busy' if blocked else b'ready');socket.flush()
            if not blocked:QTimer.singleShot(0,self.request_quit)
            return
        if message.get('command')!='activate':return
        modal=QApplication.activeModalWidget()
        if self.panel is None and modal is not None:modal.showNormal();modal.raise_();modal.activateWindow()
        elif not message.get('silent'):self.show_panel()
        socket.write(b'activated\n');socket.flush()
        for source in message.get('legacy_sources',[])[:20]:self.offer_legacy_import(source)

    def _valid_update_request(self,message):
        try:
            from portable_update import load_job,read_release,digest,MANIFEST
            _,plan,target=load_job(message['path'],message['token'])
            if (target/'data/workbox.db').resolve()!=self.service.db.path:return False
            source=read_release(plan['source']);current=read_release(target)
            return source.version>current.version and digest(source.root/MANIFEST)==plan['source_manifest']
        except (OSError,ValueError,KeyError,TypeError):return False

    def offer_legacy_import(self,source):
        source=Path(source)
        if source==self.service.db.path or not source.is_file():return
        offered=getattr(self,'_offered_imports',set())
        if str(source) in offered:return
        offered.add(str(source));self._offered_imports=offered
        timer=QTimer(self);timer.setInterval(500)
        def prompt():
            if self.closed or self.quitting:timer.stop();timer.deleteLater();return
            if self.busy or QApplication.activeModalWidget() is not None:return
            timer.stop();timer.deleteLater()
            from portable_update_ui import ChoiceDialog
            with transient_dialog(ChoiceDialog('另一个目录里还有 BOX 数据',str(source)+'\n\n是否进入导入核对？原有总表和材料会保留，确认前不合并。',[('import','核对并导入'),('later','稍后处理')],self.panel)) as dialog:
                if dialog.exec()==QDialog.Accepted and dialog.choice=='import':self.open_legacy_import(source)
        timer.timeout.connect(prompt);timer.start()

    def refresh(self):
        if self.panel is None or self.closed:
            return
        self.panel.refresh()
        from task_query import unfinished
        pending = sum(unfinished(r) for r in self.service.production.rows())
        self.orb.set_summary(pending)
        self._sync_panel_busy()

    def update_projects(self, project_ids, options):
        if self.foreground_busy:
            return
        ids, changes = list(project_ids), dict(options)
        operation = '已归档' if changes.get('archived') else ('已恢复' if changes.get('archived') is False else '已更新')
        self.enqueue(f'{operation} {len(ids)} 个项目', lambda **_: self.service.bulk_update_projects(ids, **changes))

    def set_project_deleted(self, project_id, deleted):
        if self.foreground_busy:
            return
        def change(**_):
            result = self.service.delete_project(project_id, confirmed=True) if deleted else self.service.restore_deleted_project(project_id)
            return {'project_lifecycle': result}
        self.enqueue('正在删除项目' if deleted else '正在恢复项目', change)

    def show_panel(self):
        if self.panel is None or self.closed:
            return
        self.background_mode = False
        self.orb.show()
        self.refresh()
        area = self.orb.screen().availableGeometry()
        maximized = self.panel.isMaximized()
        if not maximized:
            self.panel.resize(min(self.panel.width(), area.width()-24), min(self.panel.height(), area.height()-48))
        was_visible = self.panel.isVisible()
        self.bubble.hide()
        if maximized:
            self.panel.showMaximized()
        else:
            self.panel.showNormal()
        if maximized:
            pass
        elif not was_visible:
            x = self.orb.x()-self.panel.width()-10
            if x < area.left():
                x = self.orb.x()+self.orb.width()+10
            self.panel.move(clamp_position([x, self.orb.y()-100], self.panel.frameGeometry().size()))
        else:
            self.panel.move(clamp_position([self.panel.x(), self.panel.y()], self.panel.frameGeometry().size()))
        self.panel.raise_()
        self.panel.activateWindow()
        # Restore once more after Windows has processed the initial native show.
        # A hidden/minimized first launch must not consume a later activation.
        QTimer.singleShot(0,self._ensure_panel_visible)

    def _ensure_panel_visible(self):
        if self.closed or self.background_mode or self.panel is None:return
        if not self.panel.isVisible() or self.panel.isMinimized():self.panel.showNormal()
        if QApplication.platformName()=='windows':
            # SW_HIDE at process launch can leave Qt's visibility cache out of sync.
            # showNormal() then does nothing, so restore the native window explicitly.
            import ctypes
            from ctypes import wintypes
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindowVisible.restype=wintypes.BOOL
            user.IsIconic.argtypes=[wintypes.HWND];user.IsIconic.restype=wintypes.BOOL
            user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];user.ShowWindow.restype=wintypes.BOOL
            handle=int(self.panel.winId())
            if not user.IsWindowVisible(handle) or user.IsIconic(handle):
                user.ShowWindow(handle,3 if self.panel.isMaximized() else 9)
        self.panel.raise_();self.panel.activateWindow()

    def show_feedback(self):
        target = self.bubble.navigation_target
        self.show_panel()
        if self.panel is None or self.closed or target is None:
            return
        kind, value = target
        if kind == 'projects':
            self.panel.focus_projects(value)
        else:
            self.panel.tabs.setCurrentIndex(value)

    def run_in_background(self):
        if self.closed or self.quitting or self.panel is None:
            return False
        self.panel.hide()
        self.bubble.hide()
        self.bubble.timer.stop()
        if self.tray is None or not self.tray.isVisible() or not QSystemTrayIcon.isSystemTrayAvailable():
            self.background_mode = False
            self.orb.show()
            self.notify('系统托盘当前不可用，已保留悬浮球入口；材料收录继续运行。')
            return False
        self.background_mode = True
        self.orb.hide()
        return True

    def toggle_orb(self):
        if self.closed or self.quitting or self.orb is None:
            return False
        if not self.orb.isVisible():
            self.background_mode = False
            self.orb.show()
            return True
        if self.tray is None or not self.tray.isVisible() or not QSystemTrayIcon.isSystemTrayAvailable():
            self.notify('系统托盘当前不可用，暂时保留悬浮球，避免无法重新打开 BOX。')
            return False
        self.orb.hide()
        self.bubble.hide()
        self.bubble.timer.stop()
        return True

    def save_position(self, position):
        if self.orb._docked_side:
            area=self.orb._dock_area
            x=area.left() if self.orb._docked_side=='left' else area.right()+1-self.orb.width()
            position=[x,self.orb.y()]
            point=clamp_position(position,self.orb.size())
            self.service.set_setting('orb_position',[point.x(),point.y()]);return
        self.orb.move(clamp_position(position, self.orb.size()))
        self.service.set_setting('orb_position', [self.orb.x(), self.orb.y()])

    def drop_hint(self, hint):
        if hint:
            self.bubble.show_message(hint, self.orb)
        elif not self.busy:
            self.bubble.hide()

    def notify(self, text, *, busy=False, target=None):
        # 目标属于当前这条反馈；忙碌、错误和普通提示不得沿用上一条成功结果。
        self.bubble.navigation_target = target
        self.panel.show_notice(text)
        if not self.panel.isVisible() and not self.background_mode:
            self.bubble.show_message(text, self.orb, busy, target=target)

    def paste(self):
        # User requested this read; never connect clipboard.dataChanged or poll it.
        mime = QApplication.clipboard().mimeData()
        data = decode_mime(mime)
        if data['kind'] == 'files':
            self.notify('剪贴板里是文件，尚未收进。请主动拖入，或点击「收进文件」。')
            return
        if self.panel.tabs.currentWidget() in (self.panel.conversation_page, self.panel.production_page):
            self.show_panel()
            self.panel.open_intake(dict(text=data.get('text', '')))
            if data.get('kind') != 'text':
                self.panel.conversation_page.show_error(data.get('message', '剪贴板没有可整理文字，请直接输入需求。'))
            return
        if data['kind'] != 'unsupported':
            self.receive(data)
        else:
            dialog = QDialog(self.panel)
            dialog.setWindowTitle('粘贴项目消息')
            apply_to(dialog)
            dialog.resize(560, 340)
            layout = QVBoxLayout(dialog)
            edit = QPlainTextEdit()
            edit.setPlaceholderText('在这里粘贴或输入项目消息')
            layout.addWidget(edit)
            confirm = button('收录项目', True)
            layout.addWidget(confirm)
            confirm.clicked.connect(dialog.accept)
            with transient_dialog(dialog):
                if dialog.exec() == QDialog.Accepted:
                    self.receive({'kind': 'text', 'text': edit.toPlainText()})

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self.panel if self.panel.isVisible() else self.orb,
            '选择要收进项目的文件；原件保留', '', '所有文件 (*)')
        if paths:
            self.receive({'kind': 'files', 'paths': paths})

    def open_folder(self, path):
        folder = Path(path)
        roots = self.service.workspaces.settings()
        if str(folder) in (roots['work_root'], roots['archive_root']):
            try:
                plain_path(folder).mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as error:
                self.notify('无法创建目录：' + str(error))
                return
        if not folder.is_dir():
            self.notify('文件夹不存在，未打开任何 ZIP。')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self.notify('无法打开文件夹：' + str(folder))

    def receive_entry(self, payload):
        if self.closed or self.quitting:
            return
        if payload.get('kind') == 'text' and self.panel.tabs.currentWidget() in (self.panel.conversation_page, self.panel.production_page):
            self.show_panel()
            self.panel.open_intake(dict(text=payload.get('text', '')))
        else:
            self.receive(payload)

    def receive(self, payload):
        if self.quitting or self.closed:
            return
        if not self.busy:
            self.errors.clear()
        if payload.get('kind') == 'text':
            text = payload.get('text', '')
            self.enqueue('收录项目消息', lambda **_: self.service.receive_text(text))
        elif payload.get('kind') == 'files':
            paths = payload.get('paths', [])
            if not paths or len(paths) > 100 or len(self.queue)+len(paths) > 100:
                self.notify('一次最多接收 100 个文件或文件夹。')
                return
            selected_paths = list(dict.fromkeys(paths))
            def cancelled_intake():
                for path in selected_paths:
                    self.service.record_file_decision(path, 'cancelled', {}, message='已取消尚未开始的收录；原件保留')
            zip_paths=[p for p in selected_paths if Path(p).suffix.casefold() in ARCHIVES]
            other_paths=[p for p in selected_paths if p not in zip_paths]
            if zip_paths and other_paths:
                self.enqueue('核对本批全部材料',lambda **kw:self.service.receive_interactive(selected_paths,linked=True,keep_archives=True,**kw),on_cancel=cancelled_intake)
                return
            if zip_paths:
                self.enqueue('选择压缩包收录方式',
                    lambda **kw: self.service.receive_interactive(zip_paths,zip_mode='ask',**kw),on_cancel=cancelled_intake)
            if other_paths:
                self.enqueue(f'收进 {len(other_paths)} 个文件或文件夹',
                    lambda **kw: self.service.receive_interactive(other_paths, linked=True,keep_archives=True,**kw), on_cancel=cancelled_intake)
        else:
            self.notify(payload.get('message', '没有收到项目文字或本地文件。'))

    def enqueue(self, caption, operation, *, silent=False, on_cancel=None):
        if self.quitting or self.closed:
            return
        if not self.busy:
            self.errors.clear()
        self.queue.append((caption, operation, silent, on_cancel))
        self._sync_panel_busy()
        if self.worker is None and not self.choosing_intake:
            self._next()

    def _next(self):
        if self.choosing_intake:
            return
        if self.quitting or not self.queue:
            if self.orb.busy:
                self.orb.set_progress(False)
            self._sync_panel_busy()
            if self.quitting:
                self.shutdown()
                self.exit_requested.emit()
            return
        self.caption, operation, self.silent_current, _ = self.queue.popleft()
        self.worker = FileJob(operation, self)
        if not self.silent_current:
            # wait() 结束后仍可能有已排队进度；停机后不再访问已释放的窗口。
            self.worker.progress_changed.connect(
                lambda percent: self.orb.set_progress(True, percent) if not self.closed else None)
        self.worker.finished.connect(self._completed)
        if not self.silent_current or self.orb.busy:
            self.orb.set_progress(not self.silent_current)
        self._sync_panel_busy()
        if not self.silent_current:
            self.notify(self.caption + '…', busy=True)
        self.worker.start()

    def _completed(self):
        if self.closed or self.worker is None:
            return
        worker = self.worker
        self.worker = None
        target = None if worker.cancelled or worker.error else self._result_target(worker.result)
        if worker.cancelled:
            message = '已取消未完成的处理；已成功入库的文件保留。'
        elif worker.error:
            message = '处理失败：' + worker.error
            self.errors.append(self.caption + '：' + worker.error)
        elif isinstance(worker.result, dict) and worker.result.get('storage_unified'):
            message='材料保存目录已统一。按材料类型和批次查看，原工作资料与版本历史保留。'
            if worker.result.get('storage_backup'):message+='\n整理备份：'+worker.result['storage_backup']
            if worker.result.get('retained_old_files'):message+='\n部分原文件在整理期间变化，已保留原位置，请核对。'
        elif isinstance(worker.result, dict) and 'deleted_count' in worker.result:
            message=f"已删除 {worker.result['deleted_count']} 个软件，总表与统计已更新。已收录文件保留。"
        elif isinstance(worker.result, dict) and 'restored_count' in worker.result:
            message=f"已恢复 {worker.result['restored_count']} 个软件，总表与统计已更新。"
        elif isinstance(worker.result, dict) and 'submitted_count' in worker.result:
            count=worker.result['submitted_count']
            message=f'已记录 {count} 份材料的提交，完成情况已更新。' if count else '所选材料已记录提交，无需重复操作。'
        elif isinstance(worker.result, dict) and 'material_preview' in worker.result:
            message = '请核对材料的软件名称和分类，确认后建立总表记录。'
        elif isinstance(worker.result, dict) and 'material_conflicts' in worker.result:
            message = '检测到同名材料，请选择新建或覆盖。'
        elif isinstance(worker.result, dict) and 'intake_choice' in worker.result:
            message = '请为文件选择对应的项目；原件保留。'
        elif isinstance(worker.result, dict) and worker.result.get('kind') == 'folder_result':
            result = worker.result
            message = (f"文件夹处理{'已取消' if result.get('cancelled') else '完成'}："
                       f"收进 {result['copied']} 个文件，重复 {result['duplicate']} 个，"
                       f"ZIP 待归档 {result['watching']} 个，"
                       f"失败 {result['failed']} 个，待选择 {result['needs_choice']} 个。原件保留。")
            if result['watching']:
                message += '\nZIP 稳定后复制归档。' + zip_delivery_hint(self.service.workspaces.settings())
            details = [item.get('source_path', '') + '：' + item.get('message', '')
                       for item in result.get('items', []) if item.get('status') in ('failed', 'needs_choice')]
            if details:
                message += '\n' + '\n'.join(details[:20])
            if result['failed']:
                self.errors.append(message)
        elif isinstance(worker.result, dict) and 'state' in worker.result:
            result = worker.result
            message = '已保留消息，请点击「待核对」检查项目名。' if result['state'] == 'needs-review' else f"{'已收录过，未重复创建' if result.get('duplicate') else '已收录'} {len(result['project_ids'])} 个项目。"
            if result.get('workspace_error'):
                message += '\n工作目录创建失败：' + result['workspace_error'] + '。选择项目后点击「工作目录」可重试。'
            elif result.get('workspaces'):
                message += ' 工作目录已就绪。'
        elif isinstance(worker.result, dict) and worker.result.get('watching'):
            message = '已开始观察 ZIP，原件保留。文件稳定后自动复制归档，保留原名。'
            if worker.result.get('working_zip_path'):
                message = 'ZIP 已按原名收进工作目录，原件保留。文件稳定后自动复制归档，保留原名。'
            message += ' ' + zip_delivery_hint(self.service.workspaces.settings())
            if self.service.is_bundle_candidate(Path(worker.result.get('source_path', '')).name):
                message += ' 合包先核对全部项目，待核对时不修改项目状态。'
            elif worker.result.get('project_id') is None:
                message += ' 归档后可在 ZIP 库选择对应项目。'
        elif isinstance(worker.result, dict) and 'file' in worker.result:
            project = self.service.db.get_project(worker.result['file']['project_id'])
            status = '已设为待交付' if project['status'] == '待交付' else '项目当前' + project['status']
            message = '文件已收进项目，' + status + '，原件保留。\n' + worker.result['file']['path']
        elif isinstance(worker.result, dict) and 'query_result' in worker.result:
            message = ''
            self.panel.show_notice('')
        elif isinstance(worker.result, dict) and 'batch_ids' in worker.result:
            result = worker.result
            message = f"已建立 {len(result['batch_ids'])} 个批次、{len(result['project_ids'])} 个软件任务。"
            if result.get('material_intake'):
                message=f"已收录 {result.get('copied_count',0)} 个文件，新增 {len(result.get('created_project_ids',[]))} 个软件任务；总表已更新。原件保留。"
            if result.get('skipped_archives'):
                message += f" 已跳过 {len(result['skipped_archives'])} 个压缩包，请解压后拖入。"
                message += '\n' + '\n'.join(e['path'] + '：' + e['reason'] for e in result['skipped_archives'])
            if result.get('workspace_error'):
                message += ' 工作目录需重试：' + result['workspace_error']
            if result.get('intake_errors'):
                pending = result.get('pending_count', len(result['intake_errors']))
                message = f"已收录 {result.get('copied_count', 0)} 份，未完成 {pending} 份；总表右上角「… → 继续未完成导入」可重试。原件保留。"
                message += '\n' + '\n'.join(e['path'] + '：' + e['error'] for e in result['intake_errors'][:10])
                self.errors.append(message)
            if result['batch_ids']:
                page = self.panel.production_page
                page.commission_filter.blockSignals(True)
                page.period_filter.blockSignals(True)
                page.commission_filter.setCurrentIndex(0)
                page.period_filter.setCurrentIndex(0)
                page.commission_filter.blockSignals(False)
                page.period_filter.blockSignals(False)
                page.show_all(result['batch_ids'])
                if not result.get('conversation_turn_id'):
                    self.panel.tabs.setCurrentWidget(self.panel.workspace_page)
        elif isinstance(worker.result, dict) and 'open_folder' in worker.result:
            self.open_folder(worker.result['open_folder'])
            message = '工作目录已打开。'
        elif isinstance(worker.result, dict) and 'archive' in worker.result and 'project' in worker.result:
            event = next((row for row in self.service.db.delivery_events_for_project(worker.result['project']['id'])
                if row['id'] == worker.result.get('event_id')), None)
            counting = ('本次首次交付已计入今日统计。' if event and event['counted'] else
                        '本次为补交，不增加今日首次交付数。' if event and event['counting_role'] == 'repeat' else
                        '交付记录已保存。')
            message = '项目工作文件已按原名归档，已设为已交付。' + counting + '\n' + worker.result['archive']['path']
        elif isinstance(worker.result, dict) and 'project_lifecycle' in worker.result:
            result = worker.result['project_lifecycle']
            message = ('项目已移入已删除，原文件、归档和历史交付记录保留。' if result['deleted'] else
                       '项目已恢复到已归档列表，原文件保留。' if result['archived'] else
                       '项目已恢复到日常列表，原文件保留。')
        elif isinstance(worker.result, dict) and 'archived' in worker.result:
            result = worker.result
            message = '' if self.silent_current else f"本次归档 {len(result['archived'])} 个版本，{result['pending']} 个文件等待稳定。"
            if result['archived']:
                message = f"已保存 {len(result['archived'])} 个 ZIP 归档版本，原件和历史版本保留。"
                bundles = [self.service.db.get_zip_bundle(z['id']) for z in result['archived']]
                matched = [b for b in bundles if b and b['state'] == 'matched']
                pending = [b for b in bundles if b and b['state'] == 'needs-review']
                if matched:
                    message += f" 已匹配 {len(set(pid for b in matched for pid in b['project_ids']))} 个项目。"
                details = self._archive_state_summary(result['archived'])
                if details:
                    message += ' ' + details
                if pending:
                    message += '\n合包需要核对：' + '；'.join(b['reason'] for b in pending) + ' 可在 ZIP 库点击「识别包内项目」。'
            if result['errors']:
                message = '部分文件暂未归档或更新，原件及已有版本保留。\n' + '\n'.join(item['source_path'] + '：' + item['error'] for item in result['errors'])
            if result.get('materials_changed'):
                message += f" 已发现 {result['materials_changed']} 份新材料版本，请在材料表重新检查。"
        elif isinstance(worker.result, dict) and 'zip' in worker.result:
            result = worker.result
            bundle = result.get('bundle') or self.service.db.get_zip_bundle(result['zip']['id'])
            if bundle:
                message = (f"已核对 {len(bundle['project_ids'])} 个项目。"
                           if bundle['state'] == 'matched' else '项目状态未修改：' + bundle['reason'])
            else:
                message = 'ZIP 已归档，原件保留。' if result.get('source_preserved') else 'ZIP 关联已更新。'
            if not bundle and result['zip']['project_id'] is None:
                message += ' 请在 ZIP 库选择对应项目。'
            details = self._archive_state_summary([result['zip']])
            if details:
                message += ' ' + details
        elif isinstance(worker.result, list) and worker.result:
            message = '有归档未完成，请查看以下位置；不会自动删除不明确的文件。\n' + '\n'.join(
                item.get('error', '') + '\n来源：' + item['source'] + '\n目标：' + item['destination'] for item in worker.result)
        else:
            message = self.caption
        if isinstance(worker.result, dict) and worker.result.get('created_project_ids') and not worker.result.get('intake_errors') and not worker.error and not worker.cancelled:
            message = f"已新建 {len(worker.result['created_project_ids'])} 个项目及工作目录。" + message
        worker.deleteLater()
        self.panel.conversation_page.job_finished(worker.result if not worker.error and not worker.cancelled else None)
        if (self.silent_current and isinstance(worker.result, dict) and 'archived' in worker.result
                and not worker.result['archived']
                and not worker.result.get('materials_changed')
                and not worker.error and not worker.cancelled):
            # 无业务变化时只更新归档等待信息，避免打断筛选弹窗、列表拖动与项目详情。
            self.panel.refresh_archive_status()
        else:
            self.refresh()
        if not self.queue and self.errors:
            if len(self.errors) == 1 and isinstance(worker.result, dict) and worker.result.get('intake_errors'):
                message = self.errors[0]
            else:
                message = f'本次有 {len(self.errors)} 项操作失败，其余文件独立处理。\n' + '\n'.join(self.errors)
            target = None
        if self.caption=='记录材料提交':
            self.panel.production_page.error.setText('未能记录提交：'+worker.error if worker.error else message)
        elif self.caption in ('删除软件任务','恢复软件任务'):
            self.panel.production_page.error.setText(message);self.panel.production_page.error.setVisible(True)
        if message:
            self.notify(message, target=target)
        if not worker.cancelled and not worker.error and isinstance(worker.result, dict):
            if worker.result.get('import_token'):
                self.panel.production_page.set_intake_result(worker.result)
                self.panel.tabs.setCurrentWidget(self.panel.workspace_page)
            elif worker.result.get('raw_zip_intake'):
                self.panel.production_page.set_intake_result(worker.result)
                self.panel.tabs.setCurrentWidget(self.panel.workspace_page)
            if worker.result.get('zip_intake_choice'):
                self._resolve_zip_intake(worker.result['zip_intake_choice'])
            elif worker.result.get('zip_original_results'):
                failures=[];saved=0;raw_results=[]
                for result in worker.result['zip_original_results']:
                    if result.get('zip_original_error'):
                        failures.append(result['source_path']+'：'+result['zip_original_error'])
                    else:
                        saved+=bool(result.get('working_zip_path'))
                        if result.get('raw_zip_intake'):raw_results.append(result)
                        self._resolve_intake_choices(result)
                if raw_results and not worker.result.get('import_token'):
                    combined=dict(raw_zip_intake=True,files=[f for r in raw_results for f in r['files']],project_ids=list(dict.fromkeys(p for r in raw_results for p in r['project_ids'])),created_project_ids=list(dict.fromkeys(p for r in raw_results for p in r.get('created_project_ids',[]))))
                    self.panel.production_page.set_intake_result(combined);self.panel.tabs.setCurrentWidget(self.panel.workspace_page)
                self.notify(f'已直接收录 {saved} 个压缩包原件。'+('未完成：\n'+'\n'.join(failures) if failures else ''))
            elif worker.result.get('material_preview'):
                self._resolve_material_preview(worker.result['material_preview'])
            elif worker.result.get('material_conflicts'):
                self._resolve_material_conflicts(worker.result)
            else:
                if worker.result.get('material_intake') and worker.result.get('batch_ids') and not worker.result.get('import_token'):
                    self.panel.open_production_batches(worker.result['batch_ids'])
                self._resolve_intake_choices(worker.result)
        self._next()

    def _resolve_zip_intake(self,paths):
        from zip_intake_ui import ZipIntakeDialog
        self.choosing_intake=True;self._sync_panel_busy();self.show_panel()
        try:
            with transient_dialog(ZipIntakeDialog(self.service,paths,self.panel)) as dialog:
                if dialog.exec()!=QDialog.Accepted or self.quitting or self.closed:
                    self.notify('已取消压缩包收录，原件保留。');return
                operation=dialog.operation
            self.enqueue('保存 ZIP 收录结果',operation)
        except (ValueError,OSError) as error:self.notify('压缩包暂时无法收录：'+str(error))
        finally:self.choosing_intake=False;self._sync_panel_busy()

    def _resolve_material_preview(self,plan):
        from zip_intake_ui import MaterialIntakeDialog
        self.choosing_intake=True;self._sync_panel_busy();self.show_panel()
        try:
            with transient_dialog(MaterialIntakeDialog(self.service,plan,self.panel)) as dialog:
                if dialog.exec()!=QDialog.Accepted or self.quitting or self.closed:
                    self.notify('已取消本次材料收录，未建立任务，原件保留。');return
                operation=dialog.operation
            self.panel.production_page.show_all()
            self.enqueue('确认后收录材料',operation)
        finally:
            self.choosing_intake=False;self._sync_panel_busy()

    def _resolve_material_conflicts(self, result):
        from duplicate_intake_ui import DuplicateMaterialDialog
        decisions = {};overwrite_all=False
        plan=result['material_plan']
        incoming=plan.get('direct_rows') or plan.get('entries') or plan.get('paths') or []
        allow_all=len(incoming)>1 or len({p for g in result['material_conflicts'] for p in g['files']})>1
        self.choosing_intake = True
        self._sync_panel_busy()
        self.show_panel()
        try:
            for group in result['material_conflicts']:
                if overwrite_all and len(group['candidates'])==1:
                    decisions[group['key']]=dict(action='overwrite',project_id=group['candidates'][0]['id']);continue
                with transient_dialog(DuplicateMaterialDialog(group, self.panel,allow_overwrite_all=allow_all)) as dialog:
                    if dialog.exec() != QDialog.Accepted or self.quitting or self.closed:
                        self.notify('已取消本次同名材料收录，原件和原任务保留。')
                        return
                    decisions[group['key']] = dialog.decision
                    overwrite_all=overwrite_all or dialog.apply_to_all
            self.enqueue('按选择收录同名材料', lambda **kw: self.service.resolve_duplicate_intake(result['material_plan'], decisions, **kw))
        finally:
            self.choosing_intake = False
            self._sync_panel_busy()

    def _resolve_intake_choices(self, result):
        if self.quitting or self.closed:
            return
        if (result.get('intake_choice') or result.get('needs_choice')) and not self.service.list_projects():
            self.notify('还没有可选项目。请先用「粘贴消息」创建项目，或恢复已有项目，再收进文件；原件保留。')
            return
        pending = []
        if result.get('intake_choice'):
            pending.append((result['intake_choice'], result['routing'], '', None))
        elif (result.get('kind') == 'folder_result' and not result.get('cancelled')
                and not result.get('manual_choice_attempted')):
            groups = {}
            for item in result.get('items', []):
                if item.get('status') == 'needs_choice' and item.get('retry_path'):
                    key = (item.get('root_path', item['retry_path']),
                           str(Path(item.get('relative_path', '.')).parent),
                           tuple(sorted(item.get('candidate_ids', []))))
                    groups.setdefault(key, []).append(item)
            for items in groups.values():
                item = items[0]
                pending.append((item['retry_path'], {'basis': item.get('basis', ''), 'candidates': []},
                                item.get('message', ''), items))
        self.choosing_intake = bool(pending)
        self._sync_panel_busy()
        try:
            seen = set()
            for path, routing, explanation, folder_items in pending:
                if self.quitting or self.closed or path in seen:
                    continue
                seen.add(path)
                title = Path(path).name + ('\n' + explanation if explanation else '')
                if folder_items and len(folder_items) > 1:
                    title = f"同目录的 {len(folder_items)} 个待选择文件\n" + '\n'.join(
                        Path(item['retry_path']).name for item in folder_items[:5]) + '\n' + explanation
                project_id = self.panel.choose_file_project(title)
                if project_id is None:
                    for cancelled_path in ([item['retry_path'] for item in folder_items] if folder_items else [path]):
                        self.service.record_file_decision(cancelled_path, 'cancelled', routing,
                            message='已取消项目选择；原件保留')
                    self.notify('已取消该项，原件保留：' + Path(path).name)
                else:
                    self.enqueue('收进文件或文件夹：' + Path(path).name,
                        lambda p=path, pid=project_id, items=folder_items, zm=result.get('zip_mode','extract'), **kw:
                            self.service.receive_interactive([p], project_id=pid, folder_items=items,
                                zip_mode=zm, **kw))
        finally:
            self.choosing_intake = False
            self._sync_panel_busy()

    def _link_zip(self, zip_id, project_id):
        self.service.link_zip(zip_id, project_id)
        return {'zip': self.service.db.get_zip(zip_id)}

    def _archive_state_summary(self, records):
        """旧版本只提供归档事实；反馈状态始终读取本次全部提交后的项目。"""
        latest = {}
        for record in sorted(records, key=lambda item: item['id'], reverse=True):
            bundle = self.service.db.get_zip_bundle(record['id'])
            if bundle and bundle['state'] == 'needs-review':
                continue
            for fact in self.service.db.get_zip_file_counts(record['id']):
                latest.setdefault(fact['project_id'], fact)
        lines = []
        for pid, fact in sorted(latest.items()):
            project = self.service.db.get_project(pid)
            status = '当前' + project['status']
            if project['status'] != fact['decided_status']:
                status = '归档时' + fact['decided_status'] + '；' + status
            lines.append(f"{project['name']}：{fact['file_count']} 个文件，{status}")
        return '；'.join(lines)

    def _result_target(self, result):
        if not isinstance(result, dict):
            return None
        if result.get('material_intake'):
            return ('tab', self.panel.tabs.indexOf(self.panel.workspace_page))
        if result.get('created_project_ids'):
            return ('projects', tuple(result['created_project_ids']))
        if isinstance(result.get('project'), dict) and result['project'].get('id'):
            return ('projects', (result['project']['id'],))
        if result.get('kind') == 'folder_result':
            ids = result.get('project_ids') or []
        elif 'state' in result:
            if result['state'] == 'needs-review':
                return ('tab', self.panel.tabs.indexOf(self.panel.pending_page))
            ids = result.get('project_ids') or []
        elif result.get('watching'):
            ids = [result['project_id']] if result.get('project_id') is not None else []
            if not ids:
                return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
        elif 'file' in result:
            pid = result['file'].get('project_id')
            ids = [pid] if pid is not None else []
        elif 'zip' in result:
            bundle = result.get('bundle') or self.service.db.get_zip_bundle(result['zip']['id'])
            if bundle:
                if bundle['state'] != 'matched':
                    return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
                ids = bundle.get('project_ids') or []
            else:
                pid = result['zip'].get('project_id')
                ids = [pid] if pid is not None else []
            if not ids:
                return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
        elif 'archived' in result:
            if result.get('errors'):
                return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
            ids = []
            for archive in result['archived']:
                bundle = self.service.db.get_zip_bundle(archive['id'])
                if bundle:
                    if bundle['state'] != 'matched':
                        return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
                    ids.extend(bundle.get('project_ids') or [])
                elif archive.get('project_id') is not None:
                    ids.append(archive['project_id'])
                else:
                    return ('tab', self.panel.tabs.indexOf(self.panel.zips_page))
        else:
            return None
        return ('projects', tuple(dict.fromkeys(ids))) if ids else None

    def cancel_jobs(self):
        if self.worker:
            self.worker.cancel_event.set()
        pending = list(self.queue)
        self.queue.clear()
        for _, _, _, on_cancel in pending:
            if on_cancel:
                try:
                    on_cancel()
                except Exception as error:
                    self.notify('取消收录的记录写入失败：' + str(error))

    def open_legacy_import(self,source=None):
        if self.busy or self.closed or self.quitting:
            self.notify('请等当前收录完成后再导入旧数据。');return
        from legacy_import_ui import LegacyImportDialog,LegacyTransferDialog
        watching=self.monitor_timer.isActive();self.monitor_timer.stop();self.choosing_intake=True;self._sync_panel_busy()
        try:
            with transient_dialog(LegacyTransferDialog(self.panel)) as choice:
                if choice.exec()!=QDialog.Accepted:return
                transfer_mode=choice.transfer_mode
            with transient_dialog(LegacyImportDialog(self.service,self.panel,transfer_mode=transfer_mode,auto_scan=source is None)) as dialog:
                self.legacy_dialog=dialog
                if source is not None:QTimer.singleShot(0,lambda:dialog.prepare(str(source)))
                dialog.exec();dialog.shutdown()
                if dialog.report:self.panel.production_page.show_all(dialog.report.get('batch_ids',[]))
        finally:
            self.legacy_dialog=None;self.choosing_intake=False;self._sync_panel_busy()
            if watching and not self.closed and not self.quitting:self.monitor_timer.start()
            if not self.closed:self.refresh();self._next()

    def request_quit(self):
        legacy=getattr(self,'legacy_dialog',None)
        if legacy is not None and legacy.worker is not None:
            legacy.reject()
            if not getattr(self,'_legacy_quit_waiting',False):
                self._legacy_quit_waiting=True
                timer=QTimer(self);timer.setSingleShot(True);timer.timeout.connect(self.request_quit)
                legacy.worker.finished.connect(lambda:timer.start(0))
            self.notify('正在安全停止旧版导入，完成后退出。');return
        if self.closed:return
        self.quitting = True
        self.monitor_timer.stop()
        self.cancel_jobs()
        if not self.worker:
            self.shutdown()
            self.exit_requested.emit()
        else:
            self.notify('正在结束当前文件操作，完成后退出。', busy=True)

    def shutdown(self):
        if self.closed:
            return
        self.monitor_timer.stop()
        legacy=getattr(self,'legacy_dialog',None)
        if legacy is not None:legacy.shutdown()
        self.cancel_jobs()
        if self.worker and not self.worker.wait(15000):
            raise RuntimeError('文件操作仍未结束，窗口与文件锁保持，不强制退出。')
        self.worker = None
        if self.panel is not None and getattr(self.panel, 'today_dialog', None) is not None:
            self.panel.today_dialog.shutdown()
        if self.panel is not None:
            self.panel.document_pane.shutdown()
            self.panel.conversation_page.shutdown()
        if self.orb:
            self.save_position([self.orb.x(), self.orb.y()])
            geometry = self.panel.normalGeometry() if self.panel.isMaximized() else self.panel.geometry()
            self.service.set_setting('manager_geometry', [geometry.x(), geometry.y(), geometry.width(), geometry.height()])
            for widget in (self.orb, self.panel, self.bubble):
                widget.hide()
            self.bubble.timer.stop()
        if self.tray:
            self.tray.hide()
        if self.server:
            self.server.close()
        if self.owns_lock:
            self.lock.unlock()
            self.owns_lock = False
        self.closed = True

    def dispose_ui(self):
        """真实入口在安全停机之后、QApplication 仍存活时释放本控制器的根窗口。"""
        if not self.closed:
            raise RuntimeError('请先完成业务停机，再释放窗口。')
        application = QApplication.instance()
        if application is None or QThread.currentThread() != application.thread():
            raise RuntimeError('窗口必须在 QApplication 存活的界面线程中释放。')
        roots = [widget for widget in (self.orb, self.panel, self.bubble)
                 if widget is not None and shiboken6.isValid(widget)]
        threads = self.findChildren(QThread)
        for widget in roots:
            threads.extend(widget.findChildren(QThread))
        if self.worker is not None or any(thread.isRunning() for thread in threads):
            raise RuntimeError('后台任务尚未结束，窗口保持，不强制释放。')
        if self.tray is not None and shiboken6.isValid(self.tray):
            # 托盘菜单归悬浮球所有；先解除平台托盘对该菜单的引用。
            self.tray.setContextMenu(None)
        for widget in roots:
            forwarder=getattr(widget,'_file_drop_forwarder',None)
            if forwarder is not None and shiboken6.isValid(forwarder):forwarder.detach()
        for widget in roots:
            widget.close()
            widget.deleteLater()
        # 主事件循环已返回，必须在 QApplication 析构前消费 Qt 延迟销毁事件。
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

