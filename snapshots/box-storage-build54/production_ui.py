"""Batch checklist using the existing native Qt/Fluent desktop components."""
from datetime import datetime
import sqlite3

from PySide6.QtCore import Qt, Signal, QUrl, QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QDialog, QSizePolicy, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QInputDialog, QDateEdit, QMenu, QWidgetAction, QListWidget, QListWidgetItem,QCheckBox,
)
from ui_components import button, ComboBox, LineEdit
from workspace_chrome import WorkspaceMenu, chrome_button, WorkspaceComboBox, SearchField, PeriodButton, MaterialCheckDelegate
from theme import apply_to


def text_label(text):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    item.setWordWrap(True)
    return item


def local_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M') if value else '—'


class NewProductionDialog(QDialog):
    def __init__(self, parent=None, *, names=None, kinds=None):
        super().__init__(parent)
        self.setWindowTitle('新建制作批次')
        self.resize(610, 560)
        apply_to(self)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label('同名新建会建立独立任务，原材料和历史版本保留。'))
        layout.addWidget(text_label('批次名称'))
        self.batch_name = LineEdit()
        self.batch_name.setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S 制作'))
        layout.addWidget(self.batch_name)
        scope = QHBoxLayout()
        self.commission = LineEdit()
        self.commission.setPlaceholderText('委托项目（可选）')
        self.category = LineEdit()
        self.category.setPlaceholderText('软件分类（可选）')
        scope.addWidget(self.commission)
        scope.addWidget(self.category)
        layout.addLayout(scope)
        from material_templates import PRESETS
        self.preset = ComboBox()
        self.preset.addItems([*PRESETS, '自定义'])
        self.preset.setCurrentText('待确认' if not kinds else '自定义')
        layout.addWidget(self.preset)
        layout.addWidget(text_label('项目名称（每行一个）'))
        self.names = QPlainTextEdit()
        self.names.setPlaceholderText('库存管理系统\n客户管理系统')
        self.names.setPlainText('\n'.join(names or []))
        layout.addWidget(self.names, 1)
        layout.addWidget(text_label('必需材料（每行一种，可自行调整）'))
        self.kinds = QPlainTextEdit()
        self.kinds.setPlainText('\n'.join(kinds or []))
        self.kinds.setMaximumHeight(110)
        layout.addWidget(self.kinds)
        self.preset.currentTextChanged.connect(lambda value: self.kinds.setPlainText('\n'.join(PRESETS[value])) if value in PRESETS else None)
        self.error = text_label('')
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = button('取消')
        self.save = button('创建批次', True)
        row.addWidget(cancel)
        row.addWidget(self.save)
        layout.addLayout(row)
        cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self.validate)

    def values(self):
        lines = lambda edit: [s.strip() for s in edit.toPlainText().splitlines() if s.strip()]
        return self.batch_name.text().strip(), lines(self.names), lines(self.kinds)

    def validate(self):
        from production import Production
        name, names, kinds = self.values()
        try:
            if not 1 <= len(name) <= 100:
                raise ValueError('请填写批次名称，最多 100 字符。')
            Production._clean(names, '项目名称', 500)
            if kinds:
                Production._clean(kinds, '材料类型', 40)
            self.accept()
        except ValueError as error:
            self.error.setText(str(error))


class InlineNotice(QLabel):
    changed=Signal(str)
    def __init__(self):
        super().__init__('');self.setWordWrap(True);self.setTextFormat(Qt.PlainText);self.hide()
    def setText(self,value):
        super().setText(value);self.setVisible(bool(value));self.changed.emit(value)


class ProductionPage(QWidget):
    job_requested = Signal(str, object)
    metadata_changed = Signal()
    details_changed = Signal()
    intake_requested = Signal(object)
    files_requested = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.store = service.production
        self.rows = []
        self.rendering = False
        self.filter_scope = None
        self.summary_mode = False
        self.pending_batch_id = None
        self.conversation_mode = False
        self.compact_mode = False
        self.query_spec = None
        self.package_filter = None
        self.active_batch = None
        self.stat_filter=None;self.overview_base_rows=[]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        organize = QHBoxLayout()
        self.commission_filter = WorkspaceComboBox()
        self.commission_filter.setAccessibleName('委托项目筛选')
        self.period_filter = WorkspaceComboBox()
        self.period_filter.addItems(['所有日期', '今天', '昨天', '本月', '上月', '最近7天'])
        self.import_button = button('导入需求', True)
        self.retry_import_button = button('继续文件导入')
        self.retry_import_button.hide()
        organize.addWidget(self.commission_filter, 1)
        organize.addWidget(self.period_filter)
        organize.addWidget(self.import_button)
        organize.addWidget(self.retry_import_button)
        layout.addLayout(organize)
        statistics = QHBoxLayout()
        self.statistics_mode = WorkspaceComboBox()
        self.statistics_mode.addItems(['按天统计', '按月统计'])
        self.statistics_date = QDateEdit(QDate.currentDate())
        self.statistics_date.setCalendarPopup(True)
        self.statistics_date.setDisplayFormat('yyyy-MM-dd')
        self.statistics_date.setAccessibleName('统计日期')
        self.statistics_label = text_label('')
        self.statistics_label.setToolTip('全表当前有效任务：新增按建表日期，收齐、检查通过、提交按各自完成日期。改版或撤销后同步重算；不受下方筛选影响。')
        statistics.addWidget(self.statistics_mode)
        statistics.addWidget(self.statistics_date)
        statistics.addWidget(self.statistics_label, 1)
        layout.addLayout(statistics)
        top = QHBoxLayout()
        self.batch = WorkspaceComboBox()
        self.batch.setMinimumWidth(200)
        self.batch.setAccessibleName('总表批次筛选')
        top.addWidget(self.batch, 1)
        self.new_button = button('添加名称', True)
        self.rename_button = button('修改名称')
        self.repeat_button = button('同名重做')
        self.configure_button = button('批次默认材料')
        for control in (self.new_button, self.rename_button, self.repeat_button, self.configure_button):
            top.addWidget(control)
        layout.addLayout(top)
        self.summary = text_label('')
        layout.addWidget(self.summary)
        self.hint = text_label('按批次检查材料，再勾选确认已提交。双击材料格可收录、打开和检查具体版本；提交记录不会向外发送文件。')
        layout.addWidget(self.hint)
        filters = QHBoxLayout()
        from PySide6.QtWidgets import QLineEdit
        self.search = SearchField()
        self.search.setPlaceholderText('搜索总表软件名称')
        self.search.setClearButtonEnabled(True)
        self.state_filter = WorkspaceComboBox()
        self.state_filter.addItems(['全部进度', '待收纳', '部分收纳', '已收纳', '要求待确认', '材料未齐', '材料副本异常', '待检查', '待更新', '需修改', '待提交', '部分提交', '已提交'])
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state_filter)
        self.refresh_button = button('刷新')
        filters.addWidget(self.refresh_button)
        self.collection_button = button('收录文件夹')
        filters.addWidget(self.collection_button)
        layout.addLayout(filters)
        self.table = QTableWidget()
        self.table.setAccessibleName('软件材料总表')
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        layout.addWidget(self.table, 1)
        self.selection_label = text_label('未勾选任务')
        layout.addWidget(self.selection_label)
        actions = QHBoxLayout()
        self.select_ready_button = button('勾选全部待提交项')
        self.clear_button = chrome_button('清空勾选')
        self.task_requirements_button = button('本项要求')
        self.export_button = button('导出表格')
        self.submit_button = chrome_button('确认已提交', icon='check', variant='primary')
        actions.addWidget(self.select_ready_button)
        actions.addWidget(self.clear_button)
        actions.addWidget(self.task_requirements_button)
        actions.addStretch()
        actions.addWidget(self.export_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)
        self.error = InlineNotice()
        layout.addWidget(self.error)
        self.batch.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.collection_button.clicked.connect(self.open_collection)
        self.new_button.clicked.connect(self.new_batch)
        self.rename_button.clicked.connect(self.rename_task)
        self.statistics_mode.currentIndexChanged.connect(self.refresh)
        self.statistics_date.dateChanged.connect(self.refresh)
        self.repeat_button.clicked.connect(lambda: self.new_batch(repeat=True))
        self.configure_button.clicked.connect(self.configure)
        self.table.cellDoubleClicked.connect(self.open_material)
        self.table.itemChanged.connect(self.update_selection)
        self.select_ready_button.clicked.connect(self.select_ready)
        self.clear_button.clicked.connect(self.clear_checks)
        self.submit_button.clicked.connect(self.submit_selected)
        self.export_button.clicked.connect(self.export)
        self.import_button.clicked.connect(self.import_requirements)
        self.retry_import_button.clicked.connect(self.retry_import)
        self.task_requirements_button.clicked.connect(lambda: self.edit_task())
        self.commission_filter.currentIndexChanged.connect(self.refresh)
        self.period_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def enable_workspace(self):
        if hasattr(self,'task_workspace'):return
        self._enable_legacy_workspace()
        self.legacy_container=QWidget(self);self.legacy_container.setLayout(self.layout())
        root=QVBoxLayout(self);root.setContentsMargins(0,3,0,0);root.setSpacing(0)
        self.legacy_back=chrome_button('返回任务查询',icon='back');self.legacy_back.clicked.connect(self.show_task_workspace);self.legacy_container.layout().insertWidget(0,self.legacy_back)
        from task_workspace_ui import TaskWorkspace
        self.task_workspace=TaskWorkspace(self);root.addWidget(self.task_workspace);root.addWidget(self.legacy_container);self.legacy_container.hide()
        self.details_changed.connect(self._refresh_task_workspace)

    def _refresh_task_workspace(self):
        if hasattr(self,'task_workspace'):self.task_workspace.refresh(getattr(self,'all_ledger_rows',None))
        if hasattr(self,'daily_receipts_page') and self.daily_receipts_page.isVisible():self.daily_receipts_page.refresh()

    def show_task_workspace(self):
        if hasattr(self,'daily_receipts_page'):self.daily_receipts_page.hide()
        self.legacy_container.hide();self.task_workspace.show();self._refresh_task_workspace()

    def show_legacy_workspace(self):
        if hasattr(self,'daily_receipts_page'):self.daily_receipts_page.hide()
        self.task_workspace.hide();self.legacy_container.show();self.refresh()

    def _enable_legacy_workspace(self):
        self.compact_mode = True
        self.setObjectName('ledgerWorkspace')
        layout = self.layout()
        def empty(container):
            while container.count():
                item = container.takeAt(0)
                if item.widget():
                    item.widget().hide()
                elif item.layout():
                    empty(item.layout())
                    item.layout().deleteLater()
        empty(layout)
        layout.setContentsMargins(20, 3, 12, 8)
        layout.setSpacing(4)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        title = text_label('材料总表'); title.setObjectName('ledgerTitle');title.setWordWrap(False);title.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Preferred)
        self.ledger_count = text_label('0'); self.ledger_count.setObjectName('ledgerCount');self.ledger_count.setWordWrap(False);self.ledger_count.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Preferred)
        heading.addWidget(title); heading.addWidget(self.ledger_count); heading.addStretch()
        self.receive_button = chrome_button('收录材料', icon='upload', variant='primary')
        self.receive_button.clicked.connect(self.files_requested)
        self.more_button = chrome_button(icon='more', icon_only=True, label='总表操作')
        menu = WorkspaceMenu(self.more_button); self.more_button.setMenu(menu)
        menu.addAction('查看总表',self.show_master_ledger)
        menu.addAction('管理分类',self.manage_classifications)
        menu.addAction('导出总表', self.export_button.click)
        menu.addAction('打开材料文件夹', self.collection_button.click)
        self.deleted_tasks_action=menu.addAction('已删除任务…',self.show_deleted_tasks)
        menu.addSeparator()
        self.workspace_actions = []
        for caption, control in [('修改软件名称', self.rename_button), ('材料要求', self.task_requirements_button)]:
            action = menu.addAction(caption, control.click)
            self.workspace_actions.append((action, control))
        self.retry_action = menu.addAction('继续未完成导入', self.retry_import_button.click)
        self.receipt_action=menu.addAction('查看本次收录结果',self.show_intake_receipt);self.receipt_action.setEnabled(False)
        from intake_history import latest
        last=latest(self.store)
        if last:self.last_intake_result=last;self.receipt_action.setEnabled(True)
        def sync_menu():
            for action, control in self.workspace_actions:
                action.setVisible(self.table.currentRow() >= 0)
                action.setEnabled(control.isEnabled())
            self.retry_action.setVisible(bool(self.pending_imports))
        menu.aboutToShow.connect(sync_menu)
        from ledger_view import ProcessingSwitch, GroupedLedger, MaterialTypeTabs
        self.processing_switch=ProcessingSwitch(self)
        self.processing_switch.clicked.connect(self.change_processing_mode)
        self.master_ledger_button=chrome_button('查看总表',icon='show');self.master_ledger_button.clicked.connect(self.show_master_ledger)
        heading.addWidget(self.master_ledger_button);heading.addWidget(self.receive_button);heading.addWidget(self.more_button)
        layout.addLayout(heading)
        mode_row=QHBoxLayout();mode_row.setSpacing(10);mode_caption=text_label('处理方式');mode_caption.setObjectName('muted');mode_caption.setWordWrap(False);mode_row.addWidget(mode_caption);mode_row.addWidget(self.processing_switch);mode_row.addStretch();layout.addLayout(mode_row)
        from PySide6.QtWidgets import QTabBar
        self.type_tabs=MaterialTypeTabs();self.type_tabs.setObjectName('materialTypeTabs');self.type_tabs.setAccessibleName('材料分类视图');self.type_tabs.setExpanding(False);self.type_tabs.setUsesScrollButtons(False);self.type_tabs.setElideMode(Qt.ElideNone)
        self.type_tabs.setStyleSheet('QTabBar#materialTypeTabs {font-family:Microsoft YaHei UI;font-size:11px;} QTabBar#materialTypeTabs::tab:top { background:transparent; color:#777; padding:7px 10px; border:none; border-bottom:2px solid transparent; font-size:12px; } QTabBar#materialTypeTabs::tab:top:selected { color:#292929; border-bottom:2px solid #292929; }')
        self.type_tabs.currentChanged.connect(self.change_type_filter)
        self.type_tabs.tabBarDoubleClicked.connect(self.edit_classification_tab)
        self.type_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.type_tabs.customContextMenuRequested.connect(self.classification_menu)
        category_row=QHBoxLayout();category_row.setSpacing(2)
        category_row.addWidget(self.type_tabs,1)
        self.category_button=chrome_button(icon='categories',icon_only=True,label='分类与管理');self.category_button.setFixedSize(26,26)
        self.add_type_button=self.category_button
        self.remove_type_button=chrome_button(icon='minus',icon_only=True,label='移除当前分类');self.remove_type_button.hide()
        self.remove_type_button.setParent(self)
        self.remove_type_button.clicked.connect(self.remove_current_classification)
        self.category_menu=WorkspaceMenu(self.category_button);self.category_button.setMenu(self.category_menu)
        self.category_menu.aboutToShow.connect(self.build_category_menu)
        category_row.addWidget(self.category_button)
        layout.addLayout(category_row)
        filters = QHBoxLayout(); filters.setSpacing(6)
        self.search.setPlaceholderText('搜索软件名称')
        self.search.setMinimumWidth(100)
        filters.addWidget(self.search, 1)
        self.filter_button = chrome_button(icon='filter', icon_only=True, label='筛选总表')
        self.filter_menu = WorkspaceMenu(self.filter_button)
        self.filter_button.setMenu(self.filter_menu)
        filter_body = QWidget()
        filter_layout = QVBoxLayout(filter_body); filter_layout.setContentsMargins(14, 12, 14, 12)
        for caption, control in [('进度', self.state_filter), ('建表日期', self.period_filter),
                                 ('委托项目', self.commission_filter), ('批次', self.batch)]:
            filter_layout.addWidget(text_label(caption)); filter_layout.addWidget(control)
            control.show()
        clear_filters = chrome_button('清除筛选', icon='filter-clear')
        clear_filters.clicked.connect(lambda: (self.show_all(), self.filter_menu.close()))
        filter_layout.addWidget(clear_filters)
        filter_body.setMinimumWidth(250)
        filter_action = QWidgetAction(self.filter_menu); filter_action.setDefaultWidget(filter_body)
        self.filter_menu.addAction(filter_action)
        filters.addWidget(self.filter_button)
        layout.addLayout(filters)
        self.query_bar = QWidget()
        query = QHBoxLayout(self.query_bar); query.setContentsMargins(0, 0, 0, 0)
        self.query_label = text_label(''); self.query_label.setObjectName('subtle')
        query.addWidget(self.query_label, 1)
        self.clear_query_button = chrome_button('显示全部', icon='close')
        self.clear_query_button.clicked.connect(lambda: self.show_all())
        query.addWidget(self.clear_query_button)
        layout.addWidget(self.query_bar); self.query_bar.hide()
        from ledger_statistics import StatisticsOverview
        scope_row=QHBoxLayout();scope_row.setSpacing(6)
        self.stats_scope=WorkspaceComboBox();self.stats_scope.addItem('全部批次','all');self.stats_scope.addItem('当前批次','batch');self.stats_scope.setFixedWidth(105)
        self.stats_scope.setToolTip('全部批次按当前分类、搜索及筛选统计；当前批次只统计所选批次。日期与状态统计共用这个范围。');self.stats_scope.setAccessibleName('统计范围');self.stats_scope.currentIndexChanged.connect(self.change_statistics_scope)
        self.stats_scope_label=QLabel();self.stats_scope_label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.stats_scope_label.setMinimumWidth(0)
        filters.insertWidget(1,self.stats_scope);self.stats_scope_label.hide()
        self.clear_stat_button=chrome_button(icon='filter-clear',icon_only=True,label='清除统计筛选');self.clear_stat_button.clicked.connect(self.clear_statistics_filter);self.clear_stat_button.hide();filters.addWidget(self.clear_stat_button)
        self.overview=StatisticsOverview(self);self.overview.metric_clicked.connect(self.select_statistic);layout.addWidget(self.overview)
        self.table.setObjectName('ledgerTable')
        self.table.viewport().setObjectName('ledgerViewport')
        self.table.setItemDelegateForColumn(0, MaterialCheckDelegate(self.table))
        self.table.setAlternatingRowColors(False); self.table.setShowGrid(False)
        self.table.setColumnHidden(4, True); self.table.setColumnHidden(7, True); self.table.setColumnHidden(8, True)
        self.table.setParent(self);self.table.hide()
        self.grouped=GroupedLedger(self);self.grouped.batch_selected.connect(self.set_active_batch);layout.addWidget(self.grouped,1)
        self.intake_bar=QWidget();receipt_line=QHBoxLayout(self.intake_bar);receipt_line.setContentsMargins(0,0,0,0)
        self.intake_notice=text_label('');self.intake_notice.setWordWrap(True);receipt_line.addWidget(self.intake_notice,1)
        self.receipt_button=chrome_button('查看本次收录');self.receipt_button.setProperty('fontPixelSize',11);self.receipt_button.clicked.connect(self.show_intake_receipt);receipt_line.addWidget(self.receipt_button)
        layout.addWidget(self.intake_bar);self.intake_bar.hide()
        self.empty_label = text_label('还没有材料记录\n在左侧添加名称，或拖入完成的材料')
        self.empty_label.setAlignment(Qt.AlignCenter); self.empty_label.setObjectName('ledgerEmpty')
        layout.addWidget(self.empty_label, 1)
        self.selection_bar = QWidget()
        selected = QHBoxLayout(self.selection_bar); selected.setContentsMargins(0, 0, 0, 0)
        self.selection_label.setStyleSheet('font-size:12px;')
        self.delete_tasks_button=chrome_button('删除所选',icon='close')
        self.delete_tasks_button.setProperty('fontPixelSize',12)
        self.delete_tasks_button.clicked.connect(lambda:self.delete_tasks())
        for control in (self.clear_button,self.submit_button):control.setProperty('fontPixelSize',12)
        selected.addWidget(self.selection_label, 1); selected.addWidget(self.clear_button);selected.addWidget(self.delete_tasks_button);selected.addWidget(self.submit_button)
        layout.addWidget(self.selection_bar)
        stats = QHBoxLayout(); stats.setSpacing(4)
        self.day_button = chrome_button('日'); self.month_button = chrome_button('月')
        for button in (self.day_button, self.month_button):
            button.setCheckable(True); button.setFixedWidth(40)
        self.day_button.clicked.connect(lambda: self.statistics_mode.setCurrentIndex(0))
        self.month_button.clicked.connect(lambda: self.statistics_mode.setCurrentIndex(1))
        self.period_button = PeriodButton(self.statistics_date, self.statistics_mode)
        self.statistics_label.setObjectName('subtle')
        stats.addWidget(self.day_button); stats.addWidget(self.month_button); stats.addWidget(self.period_button)
        stats.addSpacing(8); stats.addWidget(self.statistics_label, 1)
        layout.addLayout(stats)
        layout.addWidget(self.error)
        for control in (self.receive_button, self.more_button, self.search,
                        self.selection_label, self.clear_button, self.submit_button, self.statistics_label):
            control.show()
        from theme import theme_signal
        theme_signal().connect(self.refresh)
        self.summary.hide(); self.refresh()

    def set_active_batch(self,bid):
        self.active_batch=bid
        if not hasattr(self,'processing_switch'):return
        row=next((r for r in self.overview_base_rows if r['batch_id']==bid),None)
        mode=row['processing_mode'] if row else self.service.get_setting('default_processing_mode','review')
        self.processing_switch.blockSignals(True);self.processing_switch.setChecked(mode=='review');self.processing_switch.blockSignals(False)
        self.update_statistics_widgets()
        self.processing_switch.setEnabled(not (self.stat_filter and not self.rows))
        self.processing_switch.setToolTip(('当前批次：'+row['batch_name'] if row else '新批次默认处理方式')+'\n统计整理已完成材料：分类备份，收齐即完成。\n统计初始未提交材料：收录后人工核对，再登记实际提交。')

    def change_processing_mode(self,reviewing):
        if self.stat_filter and not self.rows:
            self.set_active_batch(self.active_batch)
            return
        mode='review' if reviewing else 'collect'
        try:
            if self.active_batch is None:self.service.set_setting('default_processing_mode',mode)
            else:self.store.set_batch_mode(self.active_batch,mode)
            self.refresh();self.metadata_changed.emit()
        except (ValueError,OSError,sqlite3.Error) as error:
            self.error.setText(str(error));self.set_active_batch(self.active_batch)

    def change_statistics_scope(self,*_):
        self.stat_filter=None;self.refresh()

    def show_batch_statistics(self):
        self.stats_scope.blockSignals(True);self.stats_scope.setCurrentIndex(1);self.stats_scope.blockSignals(False)
        if self.stat_filter:self.stat_filter=None;self.refresh()
        else:self.update_statistics_widgets()

    def clear_statistics_filter(self):
        self.stat_filter=None;self.refresh()

    def select_statistic(self,mode,metric):
        bid=self.active_batch if self.stats_scope.currentData()=='batch' else None
        chosen=(mode,metric,bid)
        self.stat_filter=None if self.stat_filter==chosen else chosen
        self.refresh()

    def update_statistics_widgets(self):
        if not hasattr(self,'overview'):return
        from ledger_statistics import activity
        from workspace_chrome import colors
        c=colors();rows=self.overview_base_rows
        current=self.stats_scope.currentData()=='batch'
        if current:rows=[r for r in rows if r['batch_id']==self.active_batch]
        scope='当前批次' if current or self.batch.currentData() is not None else '全部批次 · 当前筛选'
        if not rows and not getattr(self,'all_ledger_rows',[]):scope='新批次默认方式'
        self.stats_scope_label.setText(scope+' · '+str(len(rows))+' 个软件')
        self.stats_scope.setToolTip(self.stats_scope_label.text()+'；当前批次为所选软件所在批次。')
        self.stats_scope_label.setStyleSheet('font-size:12px;color:'+c['muted']+';background:transparent;')
        name=next((r['batch_name'] for r in rows),'') if current else ''
        self.stats_scope_label.setToolTip(scope+('：'+name if name else '')+'。数量受分类、搜索及筛选条件影响；以软件为单位。点击统计数字可筛选列表；统计卡片本身不会缩小统计基数。')
        selected=self.stat_filter[:2] if self.stat_filter else None
        default=self.service.get_setting('default_processing_mode','review')
        if current:
            task=next((r for r in self.overview_base_rows if r['batch_id']==self.active_batch),None)
            if task:default=task['processing_mode']
        self.overview.update_rows(rows,default,selected)
        self.clear_stat_button.setVisible(self.stat_filter is not None)
        self.clear_stat_button.setToolTip('清除统计卡片筛选，保留分类、搜索等条件')
        day=self.statistics_date.date().toString('yyyy-MM-dd');monthly=self.statistics_mode.currentIndex()==1
        events=activity(rows,day,monthly,self.store.local_date);parts=[]
        for mode,counts in events.items():
            if mode=='collect':value=f"新增 {counts['added']} · 整理完成 {counts['collected']}"
            else:value=f"新增 {counts['added']} · 收齐 {counts['collected']} · 检查 {counts['checked']} · 提交 {counts['submitted']}"
            if len(events)>1:value=('整理：' if mode=='collect' else '检查提交：')+value
            parts.append(value)
        self.statistics_label.setText('\n'.join(parts) if parts else '所选日期暂无完成记录')
        self.statistics_label.setStyleSheet('font-size:12px;color:'+c['muted']+';background:transparent;')
        self.statistics_label.setToolTip(scope+' · '+(day[:7] if monthly else day)+'。新增按建表日期，其他指标按对应完成日期；统计当前有效版本，撤销或更新后重算。日期统计与下方当前状态是两种口径。整理方式不计为已检查或已提交。')

    def update_type_tabs(self,rows,counts_rows=None):
        from workspace_chrome import colors
        c=colors()
        self.type_tabs.setStyleSheet('QTabBar#materialTypeTabs {font-family:Microsoft YaHei UI;font-size:11px;} QTabBar#materialTypeTabs::tab:top { margin:0; background:transparent; color:'+c['muted']+'; padding:7px 10px; border:none; border-bottom:2px solid transparent; font-size:12px; } QTabBar#materialTypeTabs::tab:top:selected { color:'+c['ink']+'; border-bottom:2px solid '+c['ink']+'; }')
        selected=self.package_filter;self.type_tabs.blockSignals(True)
        while self.type_tabs.count():self.type_tabs.removeTab(0)
        counted=rows if counts_rows is None else counts_rows
        self.type_tabs.addTab('全部 '+str(len(counted)));self.type_tabs.setTabData(0,None)
        names=self.service.classifications.packages()
        aliases={'单手册 Word':'单 Word','桌面14件':'14件材料','14件＋双视频':'14件＋视频'}
        for name in names:
            if name=='待确认':continue
            index=self.type_tabs.addTab(aliases.get(name,name)+' '+str(sum(r['package_name']==name for r in counted)));self.type_tabs.setTabData(index,name)
            self.type_tabs.setTabToolTip(index,'数字为当前批次、日期、搜索等条件下的软件数，不受当前分类和统计卡片影响。双击修改分类规则；右键查看更多操作')
        if any(r['package_name'] not in names for r in rows):
            index=self.type_tabs.addTab('其他 / 历史 '+str(sum(r['package_name'] not in names for r in counted)));self.type_tabs.setTabData(index,'__history__')
        index=next((i for i in range(self.type_tabs.count()) if self.type_tabs.tabData(i)==selected),0)
        self.type_tabs.setCurrentIndex(index);self.type_tabs.blockSignals(False)
        self.package_filter=self.type_tabs.tabData(index)
        self.type_tabs.refresh_scroll_controls()
        self.remove_type_button.setEnabled(self.current_classification() is not None)
        self.remove_type_button.setToolTip('移除当前分类，已有批次与文件保留' if self.remove_type_button.isEnabled() else '先选择一个具体分类，再移除')

    def build_category_menu(self):
        self.category_menu.clear()
        self.category_menu.addSection('显示分类')
        for i in range(self.type_tabs.count()):
            action=self.category_menu.addAction(self.type_tabs.tabText(i));action.setCheckable(True);action.setChecked(i==self.type_tabs.currentIndex())
            action.triggered.connect(lambda checked=False,index=i:self.select_category_tab(index))
        self.category_menu.addSeparator()
        self.add_class_action=self.category_menu.addAction('添加分类',lambda:self.manage_classifications(new=True))
        edit=self.category_menu.addAction('修改当前分类',lambda:self.manage_classifications(profile_id=self.current_classification()['id']) if self.current_classification() else None)
        edit.setEnabled(self.current_classification() is not None)
        remove=self.category_menu.addAction('移除当前分类',self.remove_current_classification);remove.setEnabled(self.current_classification() is not None)
        self.category_menu.addAction('管理全部分类',self.manage_classifications)

    def select_category_tab(self,index):
        self.type_tabs.setTabVisible(index,True);self.type_tabs.setCurrentIndex(index)

    def change_type_filter(self,index):
        self.package_filter=self.type_tabs.tabData(index);self.refresh()

    def current_classification(self):
        name=self.type_tabs.tabData(self.type_tabs.currentIndex())
        return next((p for p in self.service.classifications.profiles() if p['name']==name),None)

    def edit_classification_tab(self,index):
        if index<0:return
        self.type_tabs.setCurrentIndex(index)
        profile=self.current_classification()
        if profile:self.manage_classifications(profile_id=profile['id'])

    def classification_menu(self,point):
        index=self.type_tabs.tabAt(point)
        if index<0:return
        self.type_tabs.setCurrentIndex(index)
        menu=WorkspaceMenu(self.type_tabs)
        profile=self.current_classification()
        if profile:
            menu.addAction('修改分类规则',lambda:self.manage_classifications(profile_id=profile['id']))
            menu.addAction('移除分类',self.remove_current_classification)
            menu.addSeparator()
        menu.addAction('添加分类',lambda:self.manage_classifications(new=True))
        menu.addAction('管理全部分类',self.manage_classifications)
        menu.exec(self.type_tabs.mapToGlobal(point));menu.deleteLater()

    def remove_current_classification(self):
        profile=self.current_classification()
        if not profile:return
        message=f"移除分类「{profile['name']}」？\n\n已使用的分类会停用，未使用的分类会删除。已有批次、文件和完成记录保留，可在全部或历史分类中查看。"
        if QMessageBox.question(self,'移除分类',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:
            self.service.classifications.remove(profile['id']);self.classifications_changed()
        except (ValueError,OSError,sqlite3.Error) as error:self.error.setText(str(error))

    def manage_classifications(self,*,new=False,profile_id=None):
        if new:
            from quick_classification_ui import QuickClassificationDialog
            dialog=QuickClassificationDialog(self.service,self)
        else:
            from classification_ui import ClassificationDialog
            dialog=ClassificationDialog(self.service,self)
            if profile_id is not None:dialog.reload(profile_id)
        try:dialog.exec()
        finally:dialog.deleteLater()
        self.classifications_changed()

    def classifications_changed(self):
        self.package_filter=None;self.refresh();self.metadata_changed.emit()
        host=self.parentWidget()
        while host is not None:
            if hasattr(host,'conversation_page'):
                preset=host.conversation_page.preset;value=preset.currentText();preset.blockSignals(True);preset.clear();preset.addItems(list(self.service.classifications.packages()));preset.setCurrentText(value if value in self.service.classifications.packages() else '待确认');preset.blockSignals(False);break
            host=host.parentWidget()

    def apply_query(self, spec):
        if spec.get('intent')=='daily_receipts':
            if not hasattr(self,'task_workspace'):self.enable_workspace()
            from daily_receipts_ui import DailyReceiptsPage
            if not hasattr(self,'daily_receipts_page'):
                self.daily_receipts_page=DailyReceiptsPage(self);self.layout().addWidget(self.daily_receipts_page)
            self.task_workspace.hide();self.legacy_container.hide()
            self.daily_receipts_page.apply(spec);self.daily_receipts_page.show();return
        if hasattr(self,'task_workspace') and not self.legacy_container.isVisible():
            self.task_workspace.apply_query(spec);self.show_task_workspace();return
        self.show_all()
        self.query_spec = dict(spec)
        if spec.get('day'):
            self.statistics_date.setDate(QDate.fromString(spec['day'], 'yyyy-MM-dd'))
            self.statistics_mode.setCurrentIndex(int(bool(spec.get('monthly'))))
        self.refresh()

        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.scrollToTop()

    def open_collection(self):
        from pathlib import Path
        root = Path(self.service.workspaces.settings()['work_root'])
        root.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))):
            self.error.setText('无法打开收录文件夹：' + str(root))

    def rename_task(self):
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            self.error.setText('请先选中总表中的一个软件。')
            return
        row = self.rows[index]
        name, okay = QInputDialog.getText(self, '修改软件名称', '修改当前任务名称，材料和历史按任务编号保留，已有保存目录保持不变。', text=row['name'])
        if okay:
            try:
                self.service.rename_project(row['id'], name)
                self.refresh()
                self.metadata_changed.emit()
            except (ValueError, OSError, sqlite3.Error) as error:
                self.error.setText(str(error))

    def show_master_ledger(self):
        from master_ledger_ui import MasterLedgerDialog
        if not hasattr(self,'master_ledger_dialog'):
            self.master_ledger_dialog=MasterLedgerDialog(self.service)
            self.destroyed.connect(self.master_ledger_dialog.deleteLater)
        self.master_ledger_dialog.show();self.master_ledger_dialog.raise_();self.master_ledger_dialog.activateWindow()

    def focus_intake_projects(self,project_ids):
        ids=set(project_ids);self.show_all()
        affected=[r for r in self.rows if r['id'] in ids]
        if not affected:return
        for r in affected:self.grouped.closed_batches.discard(r['batch_id'])
        self.grouped.highlighted_tasks=ids;self.grouped.rebuild(True)
        first=affected[0];self.grouped.select_batch(first['batch_id']);self.grouped.scroll_to_task(first['id'])
        for i,r in enumerate(self.rows):
            if r['id']==first['id']:self.table.setCurrentCell(i,1);break
        from PySide6.QtCore import QTimer
        if not hasattr(self,'intake_highlight_timer'):
            self.intake_highlight_timer=QTimer(self);self.intake_highlight_timer.setSingleShot(True)
            self.intake_highlight_timer.timeout.connect(self.clear_intake_highlight)
        self.intake_highlight_timer.start(12000)
        if hasattr(self,'task_workspace'):self.task_workspace.focus_projects(project_ids)

    def clear_intake_highlight(self):
        self.grouped.highlighted_tasks=set();self.grouped.rebuild(True)

    def set_intake_result(self,result):
        self.last_intake_result=dict(result);self.receipt_action.setEnabled(True)
        updated=len(set(result.get('project_ids',[]))-set(result.get('created_project_ids',[])))
        self.intake_notice.setText(f"本次：收录 {result.get('copied_count',0)} 份，未完成 {result.get('pending_count',0)} 份，暂停 {result.get('paused_count',0)} 份；新增 {len(result.get('created_project_ids',[]))} 个，更新 {updated} 个软件。")
        if result.get('raw_zip_intake'):self.intake_notice.setText(f"ZIP 原包：保存 {result.get('copied_count',0)} 份，未完成 {result.get('pending_count',0)} 份；完整原包保留，不统计包内材料。")
        self.intake_bar.show();self.focus_intake_projects(result.get('project_ids',[]))

    def show_intake_receipt(self):
        if not getattr(self,'last_intake_result',None):return
        from intake_link_ui import IntakeReceiptDialog
        dialog=IntakeReceiptDialog(self,self.last_intake_result)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def reclassify_task(self,row):
        from intake_link import reclassification_preview,apply_reclassification
        from intake_link_ui import ReclassificationDialog
        try:
            preview=reclassification_preview(self.store,row['id']);dialog=ReclassificationDialog(preview,self)
            try:
                if dialog.exec()!=QDialog.Accepted:return
                rule=dialog.rules.currentData()
            finally:dialog.deleteLater()
            self.request('重新识别并分类材料',lambda **kw:apply_reclassification(self.store,preview,rule,**kw))
        except (ValueError,OSError) as error:self.error.setText(str(error))

    def show_all(self, batch_ids=()):
        self.query_spec = None
        self.package_filter = None
        self.stat_filter=None
        if hasattr(self,'stats_scope'):self.stats_scope.blockSignals(True);self.stats_scope.setCurrentIndex(0);self.stats_scope.blockSignals(False)
        self.pending_batch_id = None
        for control in (self.batch, self.commission_filter, self.period_filter, self.state_filter):
            control.blockSignals(True)
            control.setCurrentIndex(0)
            control.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()
        if hasattr(self,'task_workspace') and not self.legacy_container.isVisible():
            ids=[r['id'] for r in self.all_ledger_rows if r['batch_id'] in batch_ids]
            if ids:self.task_workspace.focus_projects(ids)
            else:self.task_workspace.reset()
        for i, row in enumerate(self.rows):
            if row['batch_id'] in batch_ids:
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 1))
                if self.compact_mode:
                    self.grouped.closed_batches.discard(row['batch_id']);self.grouped.select_batch(row['batch_id']);self.grouped.rebuild(True)
                    self.grouped.scroll_to_task(row['id'])
                break

    def refresh(self, *_):
        if self.rendering:
            return
        self.rendering = True
        try:
            scroll = self.table.verticalScrollBar().value()
            horizontal = self.table.horizontalScrollBar().value()
            checks = {r['id']: tuple(m['id'] for m in r['materials']) for i, r in enumerate(self.rows)
                      if self.table.item(i, 0).checkState() == Qt.Checked}
            current = self.pending_batch_id if self.pending_batch_id is not None else self.batch.currentData()
            self.pending_batch_id = None
            commission = self.commission_filter.currentData()
            self.commission_filter.blockSignals(True)
            self.commission_filter.clear()
            self.commission_filter.addItem('所有委托项目', None)
            for name in sorted({r['commission'] for r in self.store.batches()}):
                self.commission_filter.addItem(name or '未分组', name)
            self.commission_filter.setCurrentIndex(max(0, self.commission_filter.findData(commission)))
            self.commission_filter.blockSignals(False)
            batches = self.store.batches(self.commission_filter.currentData())
            self.batch.blockSignals(True)
            self.batch.clear()
            self.batch.addItem('所有批次 · 同一张总表', None)
            for batch in batches:
                self.batch.addItem(batch['name'], batch['id'])
            if current is not None:
                self.batch.setCurrentIndex(max(0, self.batch.findData(current)))
            self.batch.blockSignals(False)
            bid = self.batch.currentData()
            global_rows = self.store.rows()
            self.all_ledger_rows=global_rows
            all_rows = [r for r in global_rows if (bid is None or r['batch_id'] == bid)
                        and (self.commission_filter.currentData() is None or r['commission'] == self.commission_filter.currentData())
                        and self.in_period(r)]
            kinds = list(dict.fromkeys(m['kind'] for row in all_rows for m in row['materials']))
            scope = (bid, self.commission_filter.currentData(), self.period_filter.currentText(), self.search.text(), self.state_filter.currentText(), repr(self.query_spec),self.package_filter,repr(self.stat_filter))
            if scope != self.filter_scope:
                checks = {}
            self.filter_scope = scope
            from workspace_queries import matches
            self.type_count_rows=[r for r in all_rows if (self.search.text().casefold() in r['name'].casefold() or any(self.search.text().casefold() in m['kind'].casefold() or self.search.text().casefold() in (m.get('filename') or '').casefold() for m in r['materials']))
                         and (self.query_spec is None or matches(r,self.query_spec,self.store.local_date))
                         and (self.state_filter.currentIndex()==0 or self.matches_state(r,self.state_filter.currentText()))]
            self.overview_base_rows=[r for r in self.type_count_rows if self.package_filter is None or r['package_name']==self.package_filter or self.package_filter=='__history__' and r['package_name'] not in self.service.classifications.packages()]
            from ledger_statistics import matches_metric
            self.rows=[r for r in self.overview_base_rows if not self.stat_filter or (matches_metric(r,*self.stat_filter[:2]) and (self.stat_filter[2] is None or r['batch_id']==self.stat_filter[2]))]
            sets = {tuple(m['kind'] for m in row['materials']) for row in all_rows}
            self.summary_mode = True
            self.hint.setText('所有名称和回填进度保存在同一张总表。双击软件查看材料，检查后再勾选确认已提交；筛选只改变显示范围。')
            self.table.setColumnCount(9)
            self.table.setHorizontalHeaderLabels(['选择', '软件名称 / 分类', '材料类型', '已收录', '收录结果', '已检查 / 已提交', '后续进度', '建表日期', '所属批次']
                                                if self.summary_mode else ['选择', '名称 / 任务编号', *kinds, '整体进度'])
            self.table.setRowCount(len(self.rows))
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Interactive)
            self.table.setColumnWidth(0, 52)
            self.table.setColumnWidth(1, 205)
            for col in range(2, self.table.columnCount()-1):
                self.table.setColumnWidth(col, 95 if self.summary_mode else 142)
            self.table.setColumnWidth(3, 75)
            self.table.setColumnWidth(4, 80)
            self.table.setColumnWidth(7, 105)
            header.setSectionResizeMode(self.table.columnCount()-1, QHeaderView.Stretch)
            header.setMinimumSectionSize(50)
            if self.compact_mode:
                header.setMinimumSectionSize(32)
                for col, width in enumerate((30, 174, 88, 48, 64, 76, 82, 90, 160)):
                    self.table.setColumnWidth(col, width)
                header.setSectionResizeMode(1, QHeaderView.Interactive)
                header.setSectionResizeMode(8, QHeaderView.Interactive)
                header.setStretchLastSection(True)
                self.table.setHorizontalHeaderLabels(['选', '软件名称', '材料类型', '收录', '收录状态', '检查 / 提交', '进度', '建表日期', '所属批次'])
            for i, row in enumerate(self.rows):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                check.setData(Qt.UserRole, row['id'])
                same_versions = (checks.get(row['id']) == tuple(m['id'] for m in row['materials'])
                                 and row['production_state'] != '已提交')
                check.setCheckState(Qt.Checked if same_versions else Qt.Unchecked)
                self.table.setItem(i, 0, check)
                duplicate_name = sum(r['name'] == row['name'] for r in global_rows) > 1
                caption = row['name'] + (f"\nWB-{row['id']:06d}" if not self.compact_mode or duplicate_name else '')
                name_item = QTableWidgetItem(caption + (' · ' + row['category'] if row['category'] else ''))
                name_item.setToolTip(f"{row['name']}\n{row['batch_name']}\n任务编号 WB-{row['id']:06d}\n双击查看、检查材料")
                self.table.setItem(i, 1, name_item)
                if self.summary_mode:
                    package = row['package_name'] if row['materials'] else '待识别'
                    if self.compact_mode and package == '单手册 Word':
                        package = '单 Word'
                    for col, value in enumerate([package, f"{row['collection_count']}/{row['required_count'] or '?'}",
                            row['collection_state'], f"{row['reviewed_count']} / {row['submitted_count']}", row['production_state']], 2):
                        self.table.setItem(i, col, QTableWidgetItem(value))
                    self.table.setItem(i, 7, QTableWidgetItem(self.store.local_date(row['created_at'])))
                    batch_item = QTableWidgetItem(row['batch_name'])
                    batch_item.setToolTip(row['batch_name'])
                    self.table.setItem(i, 8, batch_item)
                for col, m in ([] if self.summary_mode else enumerate(row['materials'], 2)):
                    caption = m['state']
                    if m['id']:
                        caption += f" · v{m['version_number']}"
                    if m.get('submitted_at'):
                        caption += '\n' + local_time(m['submitted_at'])
                    item = QTableWidgetItem(caption)
                    item.setToolTip('\n'.join(filter(None, [m.get('filename'), m.get('review_note'), '双击处理此材料'])))
                    self.table.setItem(i, col, item)
                if not self.summary_mode:
                    self.table.setItem(i, len(kinds) + 2, QTableWidgetItem(row['production_state']))
            self.table.resizeRowsToContents()
            for i in range(self.table.rowCount()):
                self.table.setRowHeight(i, max(42 if self.compact_mode else 62, self.table.rowHeight(i)))
            self.table.verticalScrollBar().setValue(scroll)
            self.table.horizontalScrollBar().setValue(horizontal)
            counts = {state: sum(self.matches_state(r, state) for r in all_rows)
                      for state in ('要求待确认', '材料未齐', '材料副本异常', '待检查', '待更新', '需修改', '待提交', '部分提交', '已提交')}
            complete = sum(r['collection_state'] == '已收齐' for r in self.rows)
            self.summary.setText(f"总表累计 {len(global_rows)} 项 · 当前显示 {len(self.rows)} 项 · 显示范围已收齐 {complete}/{len(self.rows)}")
            day = self.statistics_date.date().toString('yyyy-MM-dd')
            monthly = self.statistics_mode.currentIndex() == 1
            stats = self.store.statistics(day, monthly) if not self.compact_mode else None
            self.statistics_date.setDisplayFormat('yyyy-MM' if monthly else 'yyyy-MM-dd')
            if stats is not None:self.statistics_label.setText(('全表 ' + (day[:7] if monthly else day)) + f" · 新增 {stats['added']} · 收齐 {stats['collected']} · 检查通过 {stats['checked']} · 提交 {stats['submitted']}")
            self.error.setText('可导入整段需求，也可新建批次；材料要求不清楚的任务请在「本项要求」补齐。' if not kinds and not self.compact_mode else '')
            self.repeat_button.setEnabled(bool(self.rows))
            self.configure_button.setEnabled(bid is not None)
            self.export_button.setEnabled(bool(global_rows))
            self.pending_imports = self.store.pending_imports()
            self.retry_import_button.setVisible(bool(self.pending_imports))
            if self.compact_mode:
                self.retry_import_button.hide()
                self.ledger_count.setText(str(len(global_rows)) if len(self.rows) == len(global_rows) else f'{len(self.rows)}/{len(global_rows)}')
                self.day_button.setChecked(not monthly); self.month_button.setChecked(monthly)
                self.filter_button.setToolTip('筛选总表' if self.state_filter.currentIndex() == self.period_filter.currentIndex() == self.commission_filter.currentIndex() == self.batch.currentIndex() == 0 else '筛选总表（已筛选）')
                self.query_bar.setVisible(self.query_spec is not None)
                self.query_label.setText('会话筛选：' + (self.query_spec or {}).get('description', ''))
                self.table.hide()
                self.update_type_tabs(global_rows,self.type_count_rows)
                self.grouped.setVisible(bool(self.rows));self.grouped.rebuild()
                self.update_statistics_widgets()
                self.empty_label.setVisible(not global_rows or not self.rows)
                self.empty_label.setText('没有匹配的记录\n调整筛选，或在左侧输入“显示全部”' if global_rows else '还没有材料记录\n在左侧添加名称，或拖入完成的材料')
                self.error.setVisible(bool(self.error.text()))
        finally:
            self.rendering = False
        self.update_selection()
        self.details_changed.emit()

    def in_period(self, row):
        from datetime import date, timedelta
        value = self.store.local_date(row['created_at'])
        today = date.today()
        period = self.period_filter.currentText()
        return (period == '所有日期' or period == '今天' and value == today.isoformat()
                or period == '昨天' and value == (today - timedelta(days=1)).isoformat()
                or period == '本月' and value[:7] == today.isoformat()[:7]
                or period == '上月' and value[:7] == (today.replace(day=1) - timedelta(days=1)).isoformat()[:7]
                or period == '最近7天' and (today - timedelta(days=6)).isoformat() <= value <= today.isoformat())

    @staticmethod
    def matches_state(row, state):
        if row.get('processing_mode')=='collect' and state in ('待检查','待更新','需修改','待提交','部分提交','未提交'):
            return False
        if state in ('材料未齐', '待检查', '需修改'):
            material_state = '待生成' if state == '材料未齐' else state
            return any(m['state'] == material_state for m in row['materials'])
        return row['production_state'] == state

    def checked(self):
        return [row for i, row in enumerate(self.rows) if self.table.item(i, 0).checkState() == Qt.Checked]

    def update_selection(self, *_):
        if self.rendering:
            return
        rows = self.checked()
        self.selection_label.setText(f'已勾选 {len(rows)} 项；勾选只用于本次操作，提交结果单独保存。')
        self.submit_button.hide()
        if self.compact_mode:
            self.selection_label.setText(f'已勾选 {len(rows)} 项')
            self.selection_bar.setVisible(bool(rows))
            self.grouped.sync_selection()

    def set_checks(self,ids,checked):
        previous=self.table.blockSignals(True)
        try:
            for i,row in enumerate(self.rows):
                if row['id'] in ids:self.table.item(i,0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:self.table.blockSignals(previous)
        self.update_selection()

    def select_ready(self):
        for i, row in enumerate(self.rows):
            self.table.item(i, 0).setCheckState(Qt.Checked if row['production_state'] == '待提交' else Qt.Unchecked)

    def clear_checks(self):
        self.set_checks({r['id'] for r in self.rows},False)

    def delete_tasks(self,rows=None):
        rows=self.checked() if rows is None else rows
        if not rows:return
        names='\n'.join(r['name'] for r in rows[:12])
        if len(rows)>12:names+=f'\n…等 {len(rows)} 项'
        box=QMessageBox(QMessageBox.Warning,'删除任务',f'从总表删除以下 {len(rows)} 个软件？\n\n{names}\n\n默认保留已收录文件，可在“已删除任务”恢复。',parent=self)
        remove_files=QCheckBox('同时删除已收录的材料');remove_files.setObjectName('deleteCollectedMaterials');remove_files.setChecked(False);box.setCheckBox(remove_files)
        remove_files.toggled.connect(lambda checked:box.setInformativeText('将删除 BOX 保存的副本及历史版本。拖入前的原文件和其他任务共用文件保留。恢复任务不会找回已删除文件。' if checked else ''))
        confirm=box.addButton('删除任务',QMessageBox.DestructiveRole);cancel=box.addButton('取消',QMessageBox.RejectRole)
        box.setDefaultButton(cancel);box.setEscapeButton(cancel);apply_to(box)
        box.exec();accepted=box.clickedButton()==confirm;delete_materials=remove_files.isChecked();box.deleteLater()
        if accepted:
            ids=[r['id'] for r in rows]
            self.request('删除软件任务',lambda **kw:self.store.delete_tasks(ids,confirmed=True,delete_materials=delete_materials,**kw))

    def show_deleted_tasks(self):
        rows=self.store.deleted_rows()
        dialog=QDialog(self);dialog.setWindowTitle('已删除任务');dialog.resize(520,390);apply_to(dialog)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(18,18,18,18);layout.setSpacing(12)
        layout.addWidget(text_label('勾选后恢复任务。保留的材料会重新显示；已同时删除的文件不会恢复。'))
        items=QListWidget(dialog);items.setObjectName('deletedTasksList');layout.addWidget(items,1)
        for row in rows:
            item=QListWidgetItem(row['name']+(' · 曾同时删除材料' if row.get('materials_deleted') else ' · 材料保留')+'\n'+row['batch_name']);item.setData(Qt.UserRole,row['id'])
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Unchecked);items.addItem(item)
        if not rows:layout.addWidget(text_label('暂无已删除任务。'))
        actions=QHBoxLayout();actions.addStretch();close=chrome_button('关闭');restore=chrome_button('恢复所选',variant='primary')
        restore.setEnabled(False);restore.setObjectName('restoreDeletedTasks')
        items.itemChanged.connect(lambda _:restore.setEnabled(any(items.item(i).checkState()==Qt.Checked for i in range(items.count()))))
        actions.addWidget(close);actions.addWidget(restore);layout.addLayout(actions)
        close.clicked.connect(dialog.reject);restore.clicked.connect(dialog.accept)
        try:
            if dialog.exec()==QDialog.Accepted:
                ids=[items.item(i).data(Qt.UserRole) for i in range(items.count()) if items.item(i).checkState()==Qt.Checked]
                if ids:self.request('恢复软件任务',lambda **kw:self.store.restore_tasks(ids,**kw))
        finally:dialog.deleteLater()

    def request(self, caption, operation):
        self.error.setText('')
        self.job_requested.emit(caption, operation)

    def new_batch(self, checked=False, *, repeat=False):
        if self.conversation_mode and not repeat:
            self.intake_requested.emit({})
            return
        names = None
        if repeat:
            from requirement_intake import proposal
            source = self.checked() or self.rows
            label = datetime.now().strftime('%m-%d %H:%M:%S 重做')
            rows = [dict(proposal(r['name'], commission=r['commission'], batch=label, category=r['category']),
                         package=r['package_name'], kinds=[m['kind'] for m in r['materials']]) for r in source]
            self.import_requirements(rows=rows)
            return
        dialog = NewProductionDialog(self, names=names, kinds=self.store.kinds(self.batch.currentData()) or None)
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            name, names, kinds = dialog.values()
            # Small metadata operation; ensure the newly created batch is selected immediately.
            result = self.store.create_batch(name, names, kinds, commission=dialog.commission.text().strip(), category=dialog.category.text().strip())
            self.show_all([result['batch_id']])
            self.metadata_changed.emit()
            if result.get('workspace_error'):
                self.error.setText('批次已保存，工作目录需重试：' + result['workspace_error'])
        except (ValueError, OSError, sqlite3.Error) as error:
            self.error.setText(str(error))
        finally:
            dialog.deleteLater()

    def configure(self):
        bid = self.batch.currentData()
        value, okay = QInputDialog.getMultiLineText(self, '批次默认材料', '每行一种。仅用于尚未启用清单的任务，不覆盖已有独立要求；修改具体软件请用「本项要求」。',
                                                   '\n'.join(self.store.kinds(bid) or ['申请书', '说明书', '源代码']))
        if okay:
            try:
                self.store.configure(bid, [s.strip() for s in value.splitlines() if s.strip()])
                self.refresh()
                self.metadata_changed.emit()
            except ValueError as error:
                self.error.setText(str(error))

    def submit_selected(self):
        rows = self.checked()
        if not rows:
            return
        blocked=[r for r in rows if r['processing_mode']!='review' or not r['materials'] or any(not m['id'] or m.get('passed')!=1 or m.get('dependency_invalid') for m in r['materials'])]
        if blocked:
            self.explain_submission_blocked(blocked)
            return
        if all(all(m.get('submission_id') for m in r['materials']) for r in rows):
            self.error.setText('所选材料已记录提交，无需重复操作。')
            QMessageBox.information(self,'已经记录提交',self.error.text());return
        names = '\n'.join(r['name'] for r in rows)
        if QMessageBox.question(self, '确认已提交', f'确认你已在外部完成以下 {len(rows)} 项的提交？\n\n{names}\n\n将记录当前材料版本与时间。') != QMessageBox.Yes:
            return
        ids = [r['id'] for r in rows]
        versions = [m['id'] for r in rows for m in r['materials']]
        self.request('记录材料提交', lambda **kw: self.store.submit_projects(ids, versions, **kw))

    def explain_submission_blocked(self,rows):
        missing=sum(not m['id'] for r in rows for m in r['materials'])
        unchecked=sum(bool(m['id']) and (m.get('passed')!=1 or bool(m.get('dependency_invalid'))) for r in rows for m in r['materials'])
        message=f'暂未记录提交：{len(rows)} 项尚未满足条件。'
        if missing:message+=f' 缺少 {missing} 份材料。'
        if unchecked:message+=f' {unchecked} 份材料尚未检查通过或需要更新。'
        if any(r['processing_mode']!='review' for r in rows):message+=' 请先切换到统计初始未提交材料。'
        if any(not r['materials'] for r in rows):message+=' 请先设置材料要求。'
        self.error.setText(message)
        dialog=QMessageBox(self);dialog.setWindowTitle('先完成材料检查');dialog.setIcon(QMessageBox.Information);dialog.setTextFormat(Qt.PlainText)
        dialog.setText(message);dialog.setInformativeText('勾选软件只表示选中，不能代替检查。展开材料、确认检查通过后，再登记实际提交。')
        dialog.setDetailedText('\n'.join(r['name'] for r in rows))
        go=dialog.addButton('去检查',QMessageBox.AcceptRole);dialog.addButton('返回',QMessageBox.RejectRole);dialog.exec()
        if dialog.clickedButton()==go:
            row=rows[0]
            if hasattr(self,'grouped'):
                self.grouped.closed_batches.discard(row['batch_id']);self.grouped.open_tasks.add(row['id']);self.grouped.select_batch(row['batch_id'])
                self.grouped.rebuild(True);self.grouped.scroll_to_task(row['id'])
            else:
                index=next((i for i,r in enumerate(self.rows) if r['id']==row['id']),0);self.table.setCurrentCell(index,1)
        dialog.deleteLater()

    def export(self):
        target, _ = QFileDialog.getSaveFileName(self, '导出软件材料总表', '软件材料总表.csv', 'CSV 表格 (*.csv)')
        if target:
            try:
                self.store.export_master(target)
                self.error.setText('已导出：' + target)
            except OSError as error:
                self.error.setText(str(error))

    def open_material(self, row, column):
        if self.summary_mode and column > 0:
            from task_requirements_ui import TaskMaterialsDialog
            dialog = TaskMaterialsDialog(self, self.rows[row])
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
            return
        if not 2 <= column < self.table.columnCount() - 1:
            return
        project = self.rows[row]
        material = project['materials'][column - 2]
        dialog = MaterialDialog(self, project, material)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def import_requirements(self, checked=False, *, rows=None, text=None):
        if self.conversation_mode:
            self.intake_requested.emit(dict(rows=rows, text=text))
            return
        from intake_ui import IntakeDialog
        dialog = IntakeDialog(self, rows=rows)
        try:
            if text:
                dialog.source.setPlainText(text)
                dialog.parse()
            if dialog.exec() != QDialog.Accepted:
                return
            plan, token, raw = dialog.plan, dialog.token, dialog.source_text
            self.request('建立导入的制作任务', lambda **kw: self.store.import_plan(plan, token=token, source_text=raw, **kw))
        finally:
            dialog.deleteLater()

    def retry_import(self):
        if self.pending_imports:
            token = self.pending_imports[0]['token']
            self.request('继续未完成的文件导入', lambda **kw: self.store.retry_import_files(token, **kw))

    def edit_task(self, project=None):
        if project is None:
            index = self.table.currentRow()
            if not 0 <= index < len(self.rows):
                self.error.setText('请先点击选择一个软件任务。')
                return
            project = self.rows[index]
        from task_requirements_ui import RequirementsDialog
        dialog = RequirementsDialog(self, project)
        try:
            if dialog.exec() == QDialog.Accepted:
                self.store.configure_task(project['id'], dialog.required, package=dialog.preset.currentText() if dialog.required==dialog.packages.get(dialog.preset.currentText()) else '自定义', category=dialog.category.text(), reason=dialog.reason.text())
                if dialog.rematch.isChecked():
                    self.request('按收纳规则整理已收文件',lambda **kw:self.store.classify_files(project['id'],**kw))
                self.refresh()
                self.metadata_changed.emit()
        except (ValueError, OSError, sqlite3.Error) as error:
            self.error.setText(str(error))
        finally:
            dialog.deleteLater()


class MaterialDialog(QDialog):
    """Current storage receipt with explicit, collapsible historical-file actions."""
    def __init__(self,page,project,material):
        super().__init__(page)
        self.page,self.project,self.material=page,project,material
        self.setWindowTitle('材料详情 · '+material['kind']);self.setObjectName('storageMaterial')
        self.resize(560,220);self.setMinimumSize(440,0);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,8,10,8);layout.setSpacing(5)
        self.title=text_label(material['kind']);self.title.setStyleSheet('font-size:12px;font-weight:500;');layout.addWidget(self.title)
        self.identity=text_label(f"{project['name']} · WB-{project['id']:06d}");layout.addWidget(self.identity)
        self.current=text_label('');layout.addWidget(self.current)
        self.location=LineEdit();self.location.setReadOnly(True);layout.addWidget(self.location)
        primary=QHBoxLayout();self.open_button=chrome_button('打开文件');self.folder_button=chrome_button('打开保存位置');self.import_button=chrome_button('更新材料',variant='primary');self.more_button=chrome_button('更多');self.more_menu=WorkspaceMenu(self.more_button);self.more_button.setMenu(self.more_menu)
        self.bind_button=button('归类已有文件');self.reuse_button=button('从同名任务复用材料')
        for hidden in (self.bind_button,self.reuse_button):hidden.setParent(self);hidden.hide()
        self.bind_action=self.more_menu.addAction('归类已有文件',self.bind_button.click);self.reuse_action=self.more_menu.addAction('从同名任务复用材料',self.reuse_button.click)
        self.more_menu.aboutToShow.connect(lambda:(self.bind_action.setEnabled(self.bind_button.isEnabled()),self.reuse_action.setEnabled(self.reuse_button.isEnabled())))
        for w in (self.open_button,self.folder_button,self.import_button,self.more_button):w.setProperty('fontPixelSize',11);w.setParent(self);w.hide()
        self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(self.context_menu)
        self.history_toggle=chrome_button('历史版本');self.history_toggle.setCheckable(True);self.history_toggle.clicked.connect(self.toggle_history);layout.addWidget(self.history_toggle)
        self.history_table=QTableWidget(0,4);self.history_table.setHorizontalHeaderLabels(['版本','文件名','收录时间','操作']);self.history_table.verticalHeader().hide();self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.history_table.setSelectionMode(QAbstractItemView.SingleSelection);self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.history_table.setWordWrap(False)
        self.history_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.history_table.setColumnWidth(0,36);self.history_table.setColumnWidth(2,94);self.history_table.setColumnWidth(3,36);self.history_table.cellDoubleClicked.connect(self.open_version);self.history_table.setContextMenuPolicy(Qt.CustomContextMenu);self.history_table.customContextMenuRequested.connect(self.history_context_menu);layout.addWidget(self.history_table);self.history_table.hide()
        self.error=text_label('');self.error.hide();layout.addWidget(self.error)
        bottom=QHBoxLayout();bottom.addWidget(text_label('双击历史文件打开 · 右键查看操作'));bottom.addStretch();close=chrome_button('关闭');bottom.addWidget(close);layout.addLayout(bottom);close.clicked.connect(self.reject)
        # Kept as hidden compatibility attributes for older detail callers only.
        self.note=QPlainTextEdit(self);self.note.hide()
        for name in ('pass_button','reject_button','submit_button','void_button'):
            control=button('');control.setParent(self);control.setEnabled(False);control.hide();setattr(self,name,control)
        self.open_button.clicked.connect(self.open_current);self.folder_button.clicked.connect(self.open_saved_location);self.import_button.clicked.connect(self.import_file);self.bind_button.clicked.connect(self.bind);self.reuse_button.clicked.connect(self.reuse)
        page.details_changed.connect(self.refresh_storage)
        page.error.changed.connect(self.show_storage_error)
        self.refresh_storage()
        from theme import theme_signal
        theme_signal().connect(self.apply_compact_theme);self.apply_compact_theme()

    def apply_compact_theme(self,*_):
        self.setStyleSheet('QDialog#storageMaterial QLabel,QDialog#storageMaterial QLineEdit,QDialog#storageMaterial QTableWidget,QDialog#storageMaterial QHeaderView::section {font-size:11px;}')

    def context_menu(self,point):
        from material_context import MaterialMenu
        menu=MaterialMenu(self)
        for caption,callback,enabled in [('打开文件',self.open_current,self.open_button.isEnabled()),('打开原始位置',self.open_original_location,bool(self.material.get('id'))),('打开保存位置',self.open_saved_location,self.folder_button.isEnabled()),('更新材料',self.import_file,self.import_button.isEnabled()),('归类已有文件',self.bind,self.bind_button.isEnabled()),('从同名任务复用材料',self.reuse,self.reuse_button.isEnabled())]:
            action=menu.addAction(caption,callback);action.setEnabled(enabled)
        try:menu.exec(self.mapToGlobal(point))
        finally:menu.deleteLater()

    def open_original_location(self):
        from saved_locations import original_file_location,open_location
        try:open_location(original_file_location(self.page.service,self.project['id'],version_id=self.material.get('id'),kind=self.material['kind']));self.show_storage_error('')
        except (ValueError,OSError,sqlite3.Error) as error:self.show_storage_error(error)

    def history_menu(self,index):
        from material_context import MaterialMenu
        record=self.history[index];menu=MaterialMenu(self)
        menu.addAction('打开文件',lambda:self.open_history(record['path']))
        menu.addAction('打开原始位置',lambda:self.open_version_source(record['id']))
        menu.addAction('打开版本位置',lambda:self.open_history(record['path'],True))
        return menu

    def history_context_menu(self,point):
        index=self.history_table.indexAt(point)
        if not index.isValid():return
        self.history_table.selectRow(index.row());menu=self.history_menu(index.row())
        try:menu.exec(self.history_table.viewport().mapToGlobal(point))
        finally:menu.deleteLater()

    def open_version_source(self,version_id):
        from saved_locations import original_file_location,open_location
        try:open_location(original_file_location(self.page.service,self.project['id'],version_id=version_id));self.show_storage_error('')
        except (ValueError,OSError,sqlite3.Error) as error:self.show_storage_error(error)

    def show_storage_error(self,message):
        self.error.setText(str(message));self.error.setVisible(bool(message))

    def refresh_storage(self):
        from pathlib import Path
        try:
            with self.page.service.db.connect() as c:
                self.page.service.db._assert_projects_editable(c,self.project['id'])
                self.project=self.page.store._project(c,self.project['id'])
                m=next((m for m in self.project['materials'] if m['kind']==self.material['kind']),None)
                active=m is not None
                self.material=m or self.page.store._current(c,self.project['id'],self.material['kind'])
            self.history=self.page.store.history(self.project['id'],self.material['kind'])
            path=self.current_path();exists=bool(path and path.is_file())
            state='文件异常' if self.material.get('collection_issue') or (self.material.get('id') and not exists) else '已收录' if exists else '未收录'
            stamp=local_time(self.material.get('created_at')) if self.material.get('id') else ''
            self.current.setText(f"{state} · {self.material.get('filename') or '尚未收录文件'}"+(' · '+stamp if stamp else ''))
            self.current.setToolTip(self.material.get('collection_issue') or self.current.text());self.location.setText(str(path) if path else '收录后显示实际保存位置');self.location.setCursorPosition(0);self.location.setToolTip(self.location.text())
            self.open_button.setEnabled(exists and not self.material.get('collection_issue'));self.folder_button.setEnabled(bool(path));self.import_button.setEnabled(active);self.import_button.setText('更新材料' if self.material.get('id') else '收录材料');self.bind_button.setEnabled(active);self.reuse_button.setEnabled(active)
            self.history_toggle.setText(f'历史版本 · {len(self.history)} 个'+('  ▴' if self.history_toggle.isChecked() else '  ▾'));self.history_toggle.setEnabled(bool(self.history));self.history_table.setRowCount(len(self.history))
            for i,v in enumerate(self.history):
                for j,value in enumerate([f'v{len(self.history)-i}',v['filename'],local_time(v['created_at'])]):
                    item=QTableWidgetItem(value);item.setToolTip(value);self.history_table.setItem(i,j,item)
                more=chrome_button('···');more.setFixedSize(28,25);more.setProperty('fontPixelSize',11);menu=self.history_menu(i);menu.setParent(more);more.setMenu(menu)
                self.history_table.setCellWidget(i,3,more);self.history_table.setRowHeight(i,30)
        except (ValueError,OSError,sqlite3.Error) as error:self.open_button.setEnabled(False);self.import_button.setEnabled(False);self.show_storage_error(error)

    def current_path(self):
        from pathlib import Path
        if not self.material.get('id'):return None
        if any(m['kind']==self.material['kind'] for m in self.project['materials']):
            with self.page.service.db.connect() as c:
                receipt=c.execute('SELECT path FROM collected_materials WHERE project_id=? AND kind=? AND version_id=?',(self.project['id'],self.material['kind'],self.material['id'])).fetchone()
            return Path(receipt['path']) if receipt else None
        return Path(self.material['path'])

    def toggle_history(self):
        expanded=self.history_toggle.isChecked();self.history_table.setVisible(expanded)
        if expanded:self.history_table.setFixedHeight(self.history_table.horizontalHeader().height()+38*max(1,min(5,len(self.history)))+4)
        self.layout().activate();self.resize(self.width(),min(self.sizeHint().height(),self.screen().availableGeometry().height()-50))
        self.history_toggle.setText(f'历史版本 · {len(self.history)} 个'+('  ▴' if expanded else '  ▾'))

    def open_history(self,path,locate=False):
        from pathlib import Path
        from saved_locations import open_location
        try:
            path=Path(path)
            if not path.is_file():raise ValueError('此文件已移动或缺失，请重新收录。')
            if locate:open_location(path)
            elif path.suffix.lower() in {'.docx','.doc','.pdf','.txt','.xlsx','.xls','.csv','.pptx','.ppt','.mp4','.png','.jpg','.jpeg','.zip'}:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):raise OSError('无法打开文件，请检查默认打开软件。')
            else:open_location(path)
            self.show_storage_error('')
        except (ValueError,OSError) as error:self.show_storage_error(error)

    def open_current(self):
        self.refresh_storage();path=self.current_path()
        if path and self.open_button.isEnabled():self.open_history(path)

    def open_saved_location(self):
        from saved_locations import collection_location,open_location
        try:
            open_location(collection_location(self.page.service,self.project['id'],self.material['kind']))
            self.error.setText('')
        except (ValueError,OSError) as error:self.show_storage_error(error)

    def dispatch(self, caption, operation):
        self.page.request(caption, operation)
        self.accept()

    def open_version(self, row, column):
        self.open_history(self.history[row]['path'])

    def import_file(self):
        source, _ = QFileDialog.getOpenFileName(self, '选择此项目的' + self.material['kind'])
        if source:
            self.dispatch('收录材料版本', lambda **kw: self.page.service.receive_interactive([source], project_id=self.project['id'], material_kind=self.material['kind'], **kw))

    def bind(self):
        with self.page.service.db.connect() as connection:
            files = [dict(r) for r in connection.execute('SELECT * FROM work_files WHERE project_id=? ORDER BY id DESC', (self.project['id'],))]
        if not files:
            self.error.setText('此任务没有已收录工作文件，请使用「收录新材料」。')
            return
        choices = [f"#{f['id']} · {f['filename']} · {f['path']}" for f in files]
        value, okay = QInputDialog.getItem(self, '归类已有工作文件', '请选择属于本项材料的文件', choices, 0, False)
        if okay:
            fid = files[choices.index(value)]['id']
            self.dispatch('归类已有材料', lambda **kw: self.page.store.bind(fid, self.material['kind'], **kw))

    def reuse(self):
        with self.page.service.db.connect() as connection:
            versions = [dict(r) for r in connection.execute('''SELECT v.*,b.name AS batch_name FROM material_versions v
                JOIN projects p ON p.id=v.project_id JOIN batches b ON b.id=p.batch_id
                WHERE p.name=? AND p.id!=? AND v.kind=? ORDER BY v.id DESC''',
                (self.project['name'], self.project['id'], self.material['kind']))]
        if not versions:
            self.error.setText('没有同名任务的此类历史材料。')
            return
        choices = [f"#{v['id']} · {v['batch_name']} · {v['filename']}" for v in versions]
        value, okay = QInputDialog.getItem(self, '复用历史材料', '选择来源批次和文件；复制到当前任务，来源文件和历史保留。', choices, 0, False)
        if okay:
            source = versions[choices.index(value)]['path']
            self.dispatch('复用材料到本轮', lambda **kw: self.page.store.receive(self.project['id'], self.material['kind'], source, **kw))

    def review(self, passed):
        note = self.note.toPlainText().strip()
        if not passed and not note:
            self.error.setText('请填写需要修改的原因。')
            return
        self.dispatch('保存材料检查结果', lambda **kw: self.page.store.review(self.material['id'], passed, note, **kw))

    def submit(self):
        if QMessageBox.question(self, '确认材料已提交', '确认你已在外部提交此材料的当前版本？') == QMessageBox.Yes:
            note = self.note.toPlainText()
            self.dispatch('记录单份材料提交', lambda **kw: self.page.store.submit([self.material['id']], note, **kw))

    def void(self):
        note = self.note.toPlainText().strip()
        if not note:
            self.error.setText('请填写撤销原因。')
            return
        sid = self.material['submission_id']
        if QMessageBox.question(self, '撤销提交记录', '仅撤销此材料当前版本的提交记录，文件与历史保留。是否继续？') == QMessageBox.Yes:
            self.dispatch('撤销材料提交记录', lambda **kw: self.page.store.void_submission(sid, note, version_ids=[self.material['id']], **kw))
