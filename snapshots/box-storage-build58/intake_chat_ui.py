"""The always-visible first stage: local intake conversations and reviewable task cards."""
import json
import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QApplication, QSizePolicy, QDialog, QLayout, QSplitter, QMenu, QPushButton, QLineEdit)
from ui_components import button, ComboBox, LineEdit
from workspace_chrome import WorkspaceMenu, chrome_button, WorkspaceComboBox
from production_ui import text_label, local_time
from material_templates import PRESETS
from requirement_intake import parse_text, read_sheet, folder_proposals, write_template, validate_rows


class Composer(QPlainTextEdit):
    submit_requested = Signal()
    names_pasted=Signal()

    def insertFromMimeData(self,source):
        super().insertFromMimeData(source)
        if source.hasText() and source.text().strip():QTimer.singleShot(0,self.names_pasted.emit)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.submit_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class IntakeConversationPage(QWidget):
    job_requested = Signal(str, object)
    batch_requested = Signal(object)
    query_requested = Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service, self.store = service, service.intake_chat
        self.store.recover()
        self.current_id = None
        self.busy = False
        self.loading = False
        self.limit = 20
        self._fingerprint = None
        self.cards = {}
        self._follow_tail = False
        self._closing = False
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_body = QWidget()
        self.chat_body.setObjectName('chatWorkspace')
        self.outer_layout.addWidget(self.chat_body)
        layout = QVBoxLayout(self.chat_body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        from daily_receipts_ui import DailySummary
        self.daily_summary=DailySummary(service,self.query_requested.emit,self.chat_body)
        layout.addWidget(self.daily_summary)
        tools = QHBoxLayout()
        self.history = WorkspaceComboBox()
        self.history.setAccessibleName('需求会话历史')
        self.history.setMinimumWidth(80)
        self.history.setProperty('plain', True)
        self.history.setProperty('history', True)
        self.sheet_button = button('导入表格')
        self.sheet_button.setToolTip('导入 Excel / CSV 需求表')
        self.folder_button = button('文件夹')
        self.folder_button.setToolTip('选择已有软件材料文件夹')
        self.template_button = button('模板')
        self.template_button.setToolTip('保存 Excel / CSV 导入模板')
        heading = QHBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(self.history, 1)
        self.collapse_button = chrome_button(icon='back', icon_only=True, label='收起会话')
        self.collapse_button.clicked.connect(lambda:self.set_chat_collapsed(True))
        heading.addWidget(self.collapse_button)
        layout.addLayout(heading)
        for control in (self.sheet_button, self.folder_button, self.template_button):
            control.setParent(self.chat_body)
            control.hide()
        self.defaults_frame = QFrame()
        defaults = QVBoxLayout(self.defaults_frame)
        defaults.setContentsMargins(0, 0, 0, 0)
        self.commission = QLineEdit()
        self.commission.setPlaceholderText('默认委托项目（可选）')
        self.batch = QLineEdit(self.default_batch())
        self.batch.setPlaceholderText('未写批次时使用的名称')
        self.preset = WorkspaceComboBox()
        self.preset.addItems(list(service.classifications.packages()))
        defaults.addWidget(self.commission, 1)
        defaults.addWidget(self.batch, 1)
        defaults.addWidget(self.preset)
        layout.addWidget(self.defaults_frame)
        self.defaults_frame.hide()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setAccessibleName('需求会话记录')
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().installEventFilter(self)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.follow_scroll)
        self.scroll.verticalScrollBar().sliderPressed.connect(lambda: setattr(self, '_follow_tail', False))
        self.transcript = QWidget()
        self.transcript.setObjectName('intakeTranscript')
        self.messages = QVBoxLayout(self.transcript)
        self.messages.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self.messages.setContentsMargins(4, 4, 4, 4)
        self.messages.setSpacing(8)
        self.scroll.setWidget(self.transcript)
        layout.addWidget(self.scroll, 1)
        composer_frame = QFrame()
        composer_frame.setObjectName('chatComposer')
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(10, 8, 10, 8)
        composer_layout.setSpacing(2)
        self.composer = Composer()
        self.composer.setAccessibleName('输入本批需求')
        self.composer.setPlaceholderText('输入名称或查询进度…')
        self.composer.setFixedHeight(50)
        composer_layout.addWidget(self.composer)
        controls = QHBoxLayout()
        self.paste_button = button('粘贴')
        self.defaults_button = button('默认归属与套餐')
        self.send_button = chrome_button(icon='send', icon_only=True, variant='primary', label='发送需求或查询')
        self.send_button.setObjectName('chatSend')
        self.send_button.setAccessibleName('发送需求或查询')
        self.send_button.setToolTip('发送；Ctrl+Enter。Enter换行。')
        self.tools_button = chrome_button(icon='plus', icon_only=True, label='附件与导入')
        self.tools_button.setAccessibleName('会话附件与导入')
        self.tools_button.setObjectName('chatTools')
        self.tools_menu = WorkspaceMenu(self.tools_button)
        for caption, callback in [('导入 Excel / CSV', self.import_sheet),
                                  ('从文件夹导入名称', self.import_folder),
                                  ('默认材料要求', lambda: self.defaults_frame.setVisible(self.defaults_frame.isHidden()))]:
            self.tools_menu.addAction(caption, callback)
        self.tools_button.setMenu(self.tools_menu)
        self.paste_button.setParent(composer_frame)
        self.defaults_button.setParent(composer_frame)
        self.paste_button.hide()
        self.defaults_button.hide()
        controls.addWidget(self.tools_button)
        controls.addStretch()
        self.key_hint = text_label('Ctrl＋Enter 发送')
        self.key_hint.setObjectName('subtle')
        self.key_hint.setWordWrap(False)
        controls.addWidget(self.key_hint)
        controls.addWidget(self.send_button)
        composer_layout.addLayout(controls)
        layout.addWidget(composer_frame)
        self.error = text_label('')
        self.error.setObjectName('error')
        self.error.hide()
        layout.addWidget(self.error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.flush_draft)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self.finish_scroll)
        self.bubble_timer = QTimer(self)
        self.bubble_timer.setSingleShot(True)
        self.bubble_timer.timeout.connect(self.resize_bubbles)
        self.composer.textChanged.connect(self.draft_changed)
        for edit in (self.commission, self.batch):
            edit.textChanged.connect(self.draft_changed)
        self.preset.currentIndexChanged.connect(self.draft_changed)
        self.history.currentIndexChanged.connect(self.switch_history)
        self.paste_button.clicked.connect(self.paste)
        self.composer.names_pasted.connect(self.organize_pasted_names)
        self.send_button.clicked.connect(self.send)
        self.composer.submit_requested.connect(self.send)
        self.defaults_button.clicked.connect(lambda: self.defaults_frame.setVisible(self.defaults_frame.isHidden()))
        self.sheet_button.clicked.connect(self.import_sheet)
        self.folder_button.clicked.connect(self.import_folder)
        self.template_button.clicked.connect(self.template)
        active = self.store.active()
        if active:
            self.load(active)
        else:
            self.refresh_history()
            self.refresh_turns()
        self.daily_timer=QTimer(self);self.daily_timer.setInterval(60000)
        self.daily_timer.timeout.connect(lambda:self.daily_summary.refresh() if not self.busy and self.isVisible() else None)
        self.daily_timer.start()

    @staticmethod
    def default_batch():
        return datetime.now().strftime('%m-%d %H:%M:%S.%f')[:-3] + ' 收件'

    def defaults(self):
        return dict(default_batch=self.batch.text().strip() or self.default_batch(),
                    default_commission=self.commission.text().strip(), default_requirement=self.preset.currentText())

    def show_error(self, message):
        self.error.setText(str(message))
        self.error.setVisible(bool(message))

    def ensure_conversation(self, title='新需求会话'):
        if self.current_id is None:
            self.current_id = self.store.create(title[:60])
            self.refresh_history()
        return self.current_id

    def draft_changed(self, *_):
        if not self.loading and not self._closing:
            self.timer.start()

    def flush_draft(self):
        if self.loading:
            return True
        if self.busy:
            self.timer.start()
            return False
        text = self.composer.toPlainText()
        if self.current_id is None and not text.strip():
            return True
        try:
            self.ensure_conversation(text.strip().splitlines()[0] if text.strip() else '新需求会话')
            self.store.save_draft(self.current_id, text, self.defaults())
            return True
        except (ValueError, OSError, sqlite3.Error) as error:
            self.show_error('草稿尚未保存：' + str(error))
            if not self._closing:
                self.timer.start(1000)
            return False

    def refresh_history(self):
        self.history.blockSignals(True)
        self.history.clear()
        self.history.addItem('会话记录', None)
        for row in self.store.conversations():
            self.history.addItem(local_time(row['created_at']) + ' · ' + row['title'] + f" · #{row['id']}", row['id'])
            self.history.setItemData(self.history.count()-1, row['title'], Qt.ToolTipRole)
        self.history.setCurrentIndex(max(0, self.history.findData(self.current_id)))
        if self.current_id is not None:
            self.history.model().item(0).setEnabled(False)
        self.history.blockSignals(False)

    def load(self, row):
        self.loading = True
        self.current_id = row['id']
        defaults = json.loads(row['defaults_json'])
        self.composer.setPlainText(row['draft_text'])
        self.commission.setText(defaults.get('default_commission', ''))
        self.batch.setText(defaults.get('default_batch', self.default_batch()))
        self.preset.setCurrentText(defaults.get('default_requirement', '待确认'))
        self.loading = False
        self.limit = 20
        self._fingerprint = None
        self.refresh_history()
        self.refresh_turns()
        self.show_error('')

    def switch_history(self, *_):
        cid = self.history.currentData()
        if self.loading or cid is None or cid == self.current_id:
            return
        if not self.flush_draft():
            self.refresh_history()
            return
        try:
            self.load(self.store.select(cid))
        except (ValueError, OSError, sqlite3.Error) as error:
            self.refresh_history()
            self.show_error('会话尚未切换：' + str(error))

    def new_conversation(self):
        if self.busy:
            return False
        if not self.flush_draft():
            return False
        try:
            cid = self.store.create()
            self.load(self.store.select(cid))
            self.composer.setFocus()
            return True
        except (ValueError, OSError, sqlite3.Error) as error:
            self.show_error('尚未建立新会话，当前草稿保留：' + str(error))
            return False

    def receive_text(self, text, *, organize=True,prompt_rule=False):
        if self.busy:
            self.show_error('当前操作尚未结束，请完成后再粘贴新需求；输入中的草稿已保留。')
            return
        if self.composer.toPlainText().strip() and self.composer.toPlainText() != text:
            # Keep the previous unsent draft in its own conversation instead of overwriting it.
            if not self.new_conversation():
                return
        self.composer.setPlainText(text)
        if organize:
            if prompt_rule:self.organize_pasted_names()
            else:self.send()
        self.composer.setFocus()

    def paste(self):
        self.receive_text(QApplication.clipboard().text(),prompt_rule=True)

    def organize_pasted_names(self):
        if self.busy or self._closing or not self.composer.toPlainText().strip():return
        if len(self.composer.toPlainText())>200000:self.send();return
        from workspace_queries import parse_query
        if parse_query(self.composer.toPlainText()) is not None:self.send();return
        from intake_link_ui import ClassificationRuleDialog
        from dialog_lifecycle import transient_dialog
        with transient_dialog(ClassificationRuleDialog(self.service,self,current=self.preset.currentText())) as dialog:
            if dialog.exec()!=QDialog.Accepted:
                self.show_error('已取消分类选择，粘贴内容保留，尚未建立任务。');return
            rule=dialog.rule
        self.preset.clear();self.preset.addItems(list(self.service.classifications.packages()));self.preset.setCurrentText(rule)
        self.send()

    def record(self, source, rows, notes=(), *, kind='text', error=''):
        self.ensure_conversation(source.strip().splitlines()[0] if source.strip() else '新需求会话')
        rows = self.service.classifications.resolve(rows) if rows else rows
        turn = self.store.add(self.current_id, source, rows, notes, source_kind=kind, error=error)
        self.loading = True
        if kind == 'text':
            self.composer.clear()
        if self.batch.text().endswith(' 收件'):
            self.batch.setText(self.default_batch())
        self.loading = False
        self._fingerprint = None
        self.refresh_history()
        self.refresh_turns()
        self.show_error('')
        self.flush_draft()
        return turn

    def send(self):
        if self.busy:
            return
        raw = self.composer.toPlainText()
        if not raw.strip():
            self.show_error('请先输入或粘贴这一批的软件名称与材料要求。')
            return
        if len(raw) > 200000:
            self.show_error('单次需求最多200,000字符，请拆分。')
            return
        from workspace_queries import parse_query
        spec = parse_query(raw)
        if spec is not None:
            try:
                self.ensure_conversation(raw[:60])
                tid = self.store.begin_query(self.current_id, raw, spec)
                self.loading = True
                self.composer.clear()
                self.loading = False
                self.refresh_history()
                self.refresh_turns()
                self.show_error('')
                self.job_requested.emit('查询本地总表', lambda **kw: self.store.run_query(tid, **kw))
            except (ValueError, OSError, sqlite3.Error) as error:
                self.loading = False
                self.composer.setPlainText(raw)
                self.show_error('查询未能保存，输入已保留：' + str(error))
            return
        parse_error, rows, notes = '', [], []
        try:
            rows, notes = parse_text(raw, **self.defaults())
        except ValueError as error:
            parse_error = str(error)
        try:
            self.record(raw, rows, notes, error=parse_error)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.show_error('需求尚未写入，原文保留在输入框：' + str(error))

    def import_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入需求表', '', '需求表 (*.xlsx *.csv)')
        if path:
            try:
                rows, notes = read_sheet(path, **self.defaults())
                self.record('导入需求表：' + path, rows, notes, kind='sheet')
            except (ValueError, OSError, KeyError) as error:
                self.record('导入需求表：' + path, [], kind='sheet', error=str(error))

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择软件材料文件夹')
        if path:
            try:
                rows, notes = folder_proposals(path, **self.defaults())
                self.record('选择材料文件夹：' + path, rows, notes, kind='folder')
            except (ValueError, OSError) as error:
                self.record('选择材料文件夹：' + path, [], kind='folder', error=str(error))

    def template(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存需求表模板', 'BOX需求导入模板.xlsx', 'Excel (*.xlsx);;CSV (*.csv)')
        if path:
            try:
                write_template(path)
                self.show_error('模板已保存：' + path)
            except OSError as error:
                self.show_error(str(error))

    def open_plan(self, rows, source='从历史任务复用名称与材料要求'):
        if self.busy:
            self.show_error('请等待当前操作结束，再整理新的需求。')
            return
        self.record(source, rows, ['仅复用名称与材料要求，建立独立的收纳任务。'], kind='reuse')

    def refresh_turns(self, *, keep_position=False):
        if not self.busy:self.daily_summary.refresh()
        turns = self.store.turns(self.current_id, self.limit) if self.current_id else []
        with self.service.db.connect() as connection:
            for turn in turns:
                if turn['state'] == 'committed':
                    turn['receipts'] = [self.service.production._project(connection, pid) for pid in turn['result'].get('project_ids', [])]
        fingerprint = json.dumps(turns, ensure_ascii=False)
        if fingerprint == self._fingerprint:
            return
        scroll = self.scroll.verticalScrollBar().value()
        self._fingerprint = fingerprint
        self.cards = {}
        while self.messages.count():
            item = self.messages.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if self.current_id and self.store.count(self.current_id) > len(turns):
            earlier = button('查看更早的需求')
            earlier.clicked.connect(self.load_earlier)
            self.messages.addWidget(earlier)
        if not turns:
            self.messages.addStretch(1)
            greeting = QFrame(); greeting.setObjectName('chatWelcome')
            content = QVBoxLayout(greeting); content.setContentsMargins(4, 8, 4, 8); content.setSpacing(10)
            title = text_label('开始一批新材料'); title.setObjectName('welcomeTitle')
            content.addWidget(title)
            hint = text_label('发送软件名称与材料要求，\n我会先整理成清单供你核对。')
            hint.setObjectName('welcomeText'); content.addWidget(hint)
            self.messages.addWidget(greeting)
        for turn in turns:
            user = QFrame()
            user.setObjectName('intakeUser')
            user.setMaximumWidth(330)
            user_layout = QVBoxLayout(user)
            user_layout.setContentsMargins(9,7,9,7)
            user_layout.setSpacing(4)
            heading = QHBoxLayout()
            stamp = text_label('你 · ' + local_time(turn['created_at']))
            stamp.setObjectName('chatUserStamp')
            heading.addWidget(stamp)
            heading.addStretch()
            copy = chrome_button(icon='copy', icon_only=True, variant='on-dark', label='复制原文')
            copy.setObjectName('chatCopy')
            copy.clicked.connect(lambda _=False, raw=turn['source_text']: QApplication.clipboard().setText(raw))
            heading.addWidget(copy)
            user_layout.addLayout(heading)
            raw = QPlainTextEdit()
            raw.setObjectName('chatOriginal')
            raw.setReadOnly(True)
            raw.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            raw.setPlainText(turn['source_text'])
            raw.setFixedHeight(min(112, max(28, 18 * min(6, turn['source_text'].count('\n') + 1) + 8)))
            user_layout.addWidget(raw)
            self.messages.addWidget(user, 0, Qt.AlignRight)
            reply = self.query_card(turn) if turn['source_kind'] == 'query' else self.result_card(turn)
            self.messages.addWidget(reply)
        self.messages.addStretch(1 if not turns else 0)
        self._pending_scroll = scroll if keep_position else None
        self._follow_tail = not keep_position
        self.scroll_timer.start(0)

    def attach_ledger(self, ledger):
        self.outer_layout.removeWidget(self.chat_body)
        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.workspace_splitter.setObjectName('workspaceSplitter')
        self.workspace_splitter.setAccessibleName('左侧会话与右侧材料总表')
        self.workspace_splitter.setHandleWidth(5)
        self.chat_body.setMinimumWidth(250)
        ledger.setMinimumWidth(400)
        self.workspace_splitter.addWidget(self.chat_body)
        self.workspace_splitter.addWidget(ledger)
        self.workspace_splitter.setCollapsible(0, True)
        self.workspace_splitter.setCollapsible(1, False)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 2)
        self.workspace_splitter.setSizes([270, 580])
        self.collapsed_rail = QWidget()
        self.collapsed_rail.setFixedWidth(34)
        rail = QVBoxLayout(self.collapsed_rail);rail.setContentsMargins(2,10,2,0)
        self.expand_button = chrome_button(icon='arrow', icon_only=True, label='展开会话')
        self.expand_button.clicked.connect(lambda:self.set_chat_collapsed(False))
        rail.addWidget(self.expand_button);rail.addStretch()
        row = QHBoxLayout();row.setContentsMargins(0,0,0,0);row.setSpacing(0)
        row.addWidget(self.collapsed_rail);row.addWidget(self.workspace_splitter,1)
        self.outer_layout.addLayout(row)
        self._chat_width = self.service.get_setting('conversation_width',270)
        self.workspace_splitter.splitterMoved.connect(self.remember_chat_width)
        self.set_chat_collapsed(self.service.get_setting('conversation_collapsed',False),persist=False)
        self._initial_width_pending=True
        self.width_timer=QTimer(self);self.width_timer.setSingleShot(True)
        self.width_timer.timeout.connect(self.restore_initial_chat_width)

    def showEvent(self,event):
        super().showEvent(event)
        if not self.busy:self.daily_summary.refresh()
        if getattr(self,'_initial_width_pending',False):self.width_timer.start(0)

    def restore_initial_chat_width(self):
        if not self.isVisible():return
        self._initial_width_pending=False
        if not self.chat_body.isHidden():
            available=max(650,self.workspace_splitter.width())
            width=min(self._chat_width,max(250,available-400))
            self.workspace_splitter.setSizes([width,available-width])

    def remember_chat_width(self,*_):
        if self.chat_body.isHidden():return
        width=self.workspace_splitter.sizes()[0]
        if width==0:
            self.set_chat_collapsed(True)
        elif 250<=width<=2000:
            self._chat_width=width
            self.service.set_setting('conversation_width',width)

    def set_chat_collapsed(self,collapsed,*,persist=True):
        if not hasattr(self,'workspace_splitter'):return
        if collapsed and persist and not self.chat_body.isHidden():
            width=self.workspace_splitter.sizes()[0]
            if width>=250:self._chat_width=min(width,2000)
        self.chat_body.setVisible(not collapsed)
        self.collapsed_rail.setVisible(collapsed)
        if not collapsed:
            available=max(650,self.workspace_splitter.width())
            width=min(self._chat_width,max(250,available-400))
            self.workspace_splitter.setSizes([width,available-width])
            if hasattr(self,'width_timer'):self.width_timer.start(0)
        if persist:
            self.service.set_setting('conversation_width',self._chat_width)
            self.service.set_setting('conversation_collapsed',bool(collapsed))
            (self.expand_button if collapsed else self.composer).setFocus(Qt.OtherFocusReason)

    def query_card(self, turn):
        frame = QFrame()
        frame.setObjectName('intakeReply')
        frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        title = text_label('BOX · 查询结果' if turn['state'] == 'committed' else 'BOX · 正在查询' if turn['state'] == 'creating' else 'BOX · 查询未完成')
        title.setObjectName('chatReplyTitle')
        layout.addWidget(title)
        result = turn['result']
        layout.addWidget(text_label(result.get('response') or turn.get('error') or '正在读取本地总表…'))
        controls = {}
        if result.get('as_of'):
            stamp = text_label('查询时间 ' + local_time(result['as_of']))
            stamp.setObjectName('subtle')
            layout.addWidget(stamp)
        actions = QHBoxLayout()
        if turn['state'] == 'committed' and result.get('spec', {}).get('intent') in ('records', 'statistics', 'daily_receipts'):
            view = controls['view_query'] = chrome_button('在右侧查看', icon='arrow')
            view.clicked.connect(lambda _=False, spec=result['spec']: self.query_requested.emit(spec))
            actions.addWidget(view)
        if turn['state'] != 'creating':
            retry = controls['query_again'] = chrome_button('重新查询', icon='refresh')
            retry.clicked.connect(lambda _=False, tid=turn['id']: self.query_again(tid))
            actions.addWidget(retry)
        actions.addStretch()
        layout.addLayout(actions)
        for control in controls.values():
            control.setEnabled(not self.busy)
        self.cards[turn['id']] = controls
        return frame

    def query_again(self, turn_id):
        from workspace_queries import parse_query
        turn = self.store.turn(turn_id)
        spec = parse_query(turn['source_text'])
        if spec is not None and not self.busy:
            self.store.restart_query(turn_id, spec)
            self.refresh_turns(keep_position=True)
            self.job_requested.emit('重新查询本地总表', lambda **kw: self.store.run_query(turn_id, **kw))

    def result_card(self, turn):
        frame = QFrame()
        frame.setObjectName('intakeReply')
        frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        state = turn['state']
        captions = {'needs-review': '待核对', 'creating': '正在建立任务', 'committed': '已建立任务', 'discarded': '已取消建任务', 'failed': '需要处理'}
        title = text_label('BOX · ' + captions[state])
        title.setObjectName('chatReplyTitle')
        layout.addWidget(title)
        rows = turn['rows']
        batches = {(r['commission'], r['batch']) for r in rows}
        pending = sum(not r['kinds'] for r in rows)
        if rows:
            detail = (f'其中 {pending} 项材料要求待确认。' if pending else '请核对名称和本次材料要求。')
            if state == 'committed':
                complete = sum(p['collection_state'] == '已收齐' for p in turn.get('receipts', []))
                detail = f'已收齐 {complete}/{len(rows)} 项。拖入成品后会自动分类并更新此表。'
            elif state == 'discarded':
                detail = '本次未建立任务，原文和清单仍保留。'
            elif state == 'creating':
                detail = '正在保存任务和收录已选择的材料。'
            layout.addWidget(text_label(f'整理出 {len(batches)} 个批次、{len(rows)} 个软件任务。' + detail))
            if state == 'committed':
                layout.addWidget(text_label('这些名称已加入同一张软件材料总表，后续收录数量、缺失材料和保存位置均在总表更新。'))
            else:
                for i, row in enumerate(rows[:4]):
                    entry = text_label(f"{i+1}. {row['name']}\n{row['package']} · {row['batch']}")
                    entry.setToolTip((row['commission'] or '未分组') + ' / ' + row['batch'])
                    layout.addWidget(entry)
                if len(rows) > 4:
                    layout.addWidget(text_label(f'会话内显示前4项，修改清单可查看全部 {len(rows)} 项。'))
        if turn['notes']:
            note = text_label('\n'.join(turn['notes'][:6]))
            layout.addWidget(note)
        if turn['error']:
            error = text_label(turn['error'])
            error.setObjectName('error')
            layout.addWidget(error)
        actions = QHBoxLayout()
        controls = {}
        if state in ('needs-review', 'failed'):
            if rows:
                edit = controls['edit'] = chrome_button('核对清单', variant='secondary')
                confirm = controls['confirm'] = chrome_button('确认建表', icon='check', variant='primary')
                edit.clicked.connect(lambda _=False, tid=turn['id']: self.edit(tid))
                confirm.clicked.connect(lambda _=False, tid=turn['id']: self.confirm(tid))
                actions.addWidget(edit)
                actions.addWidget(confirm)
            else:
                edit_source = controls['edit_source'] = button('重新编辑原文')
                edit_source.clicked.connect(lambda _=False, text=turn['source_text']: self.receive_text(text, organize=False))
                actions.addWidget(edit_source)
            discard = controls['discard'] = chrome_button('取消')
            discard.clicked.connect(lambda _=False, tid=turn['id']: self.discard(tid))
            layout.addLayout(actions)
            actions = QHBoxLayout()
            actions.addWidget(discard)
        elif state == 'committed':
            result = turn['result']
            if result.get('intake_errors') or result.get('workspace_error'):
                layout.addWidget(text_label('任务已保存，部分目录或文件仍需处理。可继续导入，原件保留。'))
                retry = controls['retry'] = button('继续导入材料')
                retry.clicked.connect(lambda _=False, tid=turn['id']: self.request_creation(tid, retry=True))
                actions.addWidget(retry)
            view = controls['view'] = chrome_button('查看总表', icon='arrow')
            view.clicked.connect(lambda _=False, bids=result.get('batch_ids', []): self.batch_requested.emit(bids))
            actions.addWidget(view)
        actions.addStretch()
        layout.addLayout(actions)
        for control in controls.values():
            control.setEnabled(not self.busy)
        self.cards[turn['id']] = controls
        return frame

    def resize_bubbles(self):
        if self._closing:
            return
        for i in range(self.messages.count()):
            widget = self.messages.itemAt(i).widget()
            if widget and widget.objectName() == 'intakeUser':
                width = max(140, self.scroll.viewport().width() - 18)
                widget.setMinimumWidth(0)
                widget.setFixedWidth(width)

    def finish_scroll(self):
        self.resize_bubbles()
        self.scroll.verticalScrollBar().setValue(self._pending_scroll if self._pending_scroll is not None else self.scroll.verticalScrollBar().maximum())

    def follow_scroll(self, *_):
        if self._follow_tail:
            self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self.bubble_timer.start(0)
        if event.type() == QEvent.Wheel and watched in (self.scroll.viewport(), self.scroll.verticalScrollBar()):
            self._follow_tail = False
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_bubbles()
        self.key_hint.setVisible(self.chat_body.width() >= 330)

    def load_earlier(self):
        self.limit += 20
        self._fingerprint = None
        self.refresh_turns(keep_position=True)

    def edit(self, turn_id):
        from intake_ui import IntakeDialog
        turn = self.store.turn(turn_id)
        dialog = IntakeDialog(self, rows=turn['rows'], review_only=True)
        dialog.notice.setText('修改这条需求的清单，保存后回到会话；此处不会建立任务。')
        dialog.confirm_button.setText('保存清单，回到会话')
        try:
            if dialog.exec() == QDialog.Accepted:
                self.store.edit(turn_id, dialog.plan)
                self.refresh_turns(keep_position=True)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.show_error(str(error))
        finally:
            dialog.deleteLater()

    def confirm(self, turn_id):
        try:
            turn = self.store.turn(turn_id)
            rows = validate_rows(turn['rows'])
            self.service.production.check_import_conflicts(rows)
            pending = sum(not r['kinds'] for r in rows)
            if pending and QMessageBox.question(self, '保留待确认要求', f'{pending} 项材料要求尚未说明，先建立待确认任务？') != QMessageBox.Yes:
                return
            self.request_creation(turn_id)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.show_error(str(error))

    def request_creation(self, turn_id, retry=False):
        self.show_error('')
        if not self.flush_draft():
            return
        self.job_requested.emit('按需求清单建立任务' if not retry else '继续导入本批材料',
            lambda **kw: self.store.create_tasks(turn_id, retry_files=retry, **kw))

    def discard(self, turn_id):
        try:
            self.store.discard(turn_id)
            self.refresh_turns(keep_position=True)
        except ValueError as error:
            self.show_error(str(error))

    def set_busy(self, busy):
        self.busy = busy
        for control in (self.history, self.sheet_button, self.folder_button, self.send_button,
                        self.paste_button, self.defaults_button, self.defaults_frame, self.tools_button):
            control.setEnabled(not busy)
        for controls in self.cards.values():
            for control in controls.values():
                control.setEnabled(not busy)
        if not busy:
            self.flush_draft()
            self.refresh_turns(keep_position=True)

    def job_finished(self, result=None):
        self.refresh_turns(keep_position=True)
        if result and result.get('query_result') and result.get('conversation_id') == self.current_id:
            self._pending_scroll = None
            self._follow_tail = True
            self.scroll_timer.start(0)
            spec = result['query_result']['spec']
            if spec['intent'] in ('records', 'statistics', 'daily_receipts'):
                self.query_requested.emit(spec)

    def shutdown(self):
        self._closing = True
        self.busy = False
        self.timer.stop()
        self.scroll_timer.stop()
        self.bubble_timer.stop()
        self.daily_timer.stop()
        self.flush_draft()
