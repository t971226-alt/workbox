"""Task workbench: unresolved carry-over and filterable current completion records."""
import csv,sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QTimer
from PySide6.QtGui import QFontMetrics,QShortcut,QKeySequence
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,
    QHeaderView,QAbstractItemView,QTabBar,QDialog,QDateEdit,QMessageBox,QFileDialog,QPushButton,QFrame,QSizePolicy,QComboBox,QCheckBox,QWidgetAction)
from workspace_chrome import chrome_button,SearchField,WorkspaceComboBox,WorkspaceMenu,colors
from master_ledger_chrome import font
from task_query_chrome import TaskDelegate
from task_query import query,status,unfinished,completion_time,receipt_time,missing
from theme import theme_signal,tokens


def label(value='',name=''):
    w=QLabel(value);w.setTextFormat(Qt.PlainText);w.setObjectName(name);w.setWordWrap(True);return w


class RuleStrip(QWidget):
    def __init__(self,workspace):
        super().__init__();self.workspace=workspace;self.counts=Counter();self.total=0;self._key=None;self.setFixedHeight(30);self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.timeout.connect(self.rebuild)
        self.line=QHBoxLayout(self);self.line.setContentsMargins(0,0,0,0);self.line.setSpacing(6)
    def update_counts(self,counts,total=None):self.counts=counts;self.total=sum(counts.values()) if total is None else total;self._timer.start(0)
    def activate_category(self,name):
        self.workspace.choose_category(None if name is not None and name==self.workspace.category.currentData() else name);self._key=None;self._timer.start(0)
    def resizeEvent(self,event):super().resizeEvent(event);self._timer.start(0)
    def rebuild(self):
        key=(tuple(self.counts.items()),self.total,self.width(),self.workspace.category.currentData(),colors()['surface'])
        if key==self._key:return
        self._key=key
        while self.line.count():
            item=self.line.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        used=0;extra=[];metrics=QFontMetrics(font(11))
        for name,count in [(None,self.total),*self.counts.items()]:
            caption=f'{self.workspace.category_title(name) if name else "全部"}  {count} 项';width=metrics.horizontalAdvance(caption)+24
            if used+width>max(0,self.width()-98):extra.append((name,caption));continue
            b=chrome_button(caption);b.setProperty('fontPixelSize',11);b.setFixedHeight(28);b.setFixedWidth(width);b.setToolTip(name or '显示所有分类');b.setCheckable(True);b.setChecked(name==self.workspace.category.currentData());b.setAccessibleName((name or '全部分类')+'，'+str(count)+'项');b.setProperty('categoryName',name)
            b.clicked.connect(lambda checked=False,n=name:self.activate_category(n));self.line.addWidget(b);b.show();used+=width+6
        if extra:
            more=chrome_button('更多');menu=WorkspaceMenu(more);more.setMenu(menu)
            for name,caption in extra:
                action=menu.addAction(caption,lambda n=name:self.activate_category(n));action.setCheckable(True);action.setChecked(name==self.workspace.category.currentData())
            self.line.addWidget(more);more.show()
        self.line.addStretch();self.line.activate();self.updateGeometry()


class TaskWorkspace(QWidget):
    def __init__(self,page):
        super().__init__(page);self.setObjectName('taskWorkspace');self.page=page;self.service=page.service;self.store=page.store
        self.rows=[];self.all_rows=[];self.selected_versions={};self._loading=False;self._cache=None;self.view='unfinished';self.periods={'unfinished':('all',None,None),'completed':('latest',None,None)};self.external_spec=None;self.focus_ids=set()
        layout=QVBoxLayout(self);layout.setContentsMargins(12,0,10,4);layout.setSpacing(4)
        header=QHBoxLayout();header.setSpacing(6);header.addStretch()
        from sheet_checklist_ui import import_sheet,show_overview
        self.sheet_import_button=chrome_button('导入表格',icon='upload');self.sheet_import_button.setAccessibleName('导入表格并核对名称清单');self.sheet_import_button.clicked.connect(lambda:import_sheet(page));header.addWidget(self.sheet_import_button)
        self.master_button=chrome_button('查看总表',icon='show');self.master_button.clicked.connect(lambda:show_overview(page));header.addWidget(self.master_button)
        self.receive_button=chrome_button('收录材料',icon='upload',variant='primary');self.receive_button.clicked.connect(page.files_requested);header.addWidget(self.receive_button)
        self.more_button=chrome_button(icon='more',icon_only=True,label='总表操作');self.more_menu=WorkspaceMenu(self.more_button);self.more_button.setMenu(self.more_menu)
        self.more_button.setParent(self);self.more_button.hide();layout.addLayout(header)
        from storage_management_ui import show_storage
        self.more_menu.aboutToShow.connect(self.sync_intake_actions)
        for control in (self.sheet_import_button,self.master_button,self.receive_button,self.more_button):control.setProperty('fontPixelSize',12);control.setFixedHeight(28)
        self.tabs=QTabBar();self.tabs.setExpanding(False);self.tabs.addTab('待整理');self.tabs.addTab('收录查询');self.tabs.currentChanged.connect(self.change_view);layout.addWidget(self.tabs)
        search_row=QHBoxLayout();self.search=SearchField();self.search.setPlaceholderText('搜索软件名称或任务编号');self.search.setAccessibleName('筛选软件名称或任务编号');self.search.setFixedHeight(30);search_row.addWidget(self.search,1);self.reset_button=chrome_button('重置');self.reset_button.clicked.connect(self.reset);search_row.addWidget(self.reset_button);layout.addLayout(search_row)
        self.category=WorkspaceComboBox();self.category.setAccessibleName('材料分类筛选');self.state=WorkspaceComboBox();self.state.setAccessibleName('任务状态筛选');self.period=WorkspaceComboBox();self.period.setAccessibleName('日期范围筛选')
        self.filter_button=chrome_button('筛选',icon='filter');self.filter_button.setFixedHeight(28);self.filter_button.setProperty('fontPixelSize',11);self.filter_menu=WorkspaceMenu(self.filter_button);self.filter_button.setMenu(self.filter_menu)
        filter_body=QWidget();filters=QVBoxLayout(filter_body);filters.setContentsMargins(12,8,12,8);filters.setSpacing(6)
        for title,c in [('材料分类',self.category),('当前状态',self.state),('日期范围',self.period)]:
            filters.addWidget(label(title));c.setMinimumWidth(210);c.setFixedHeight(30);filters.addWidget(c)
        filter_action=QWidgetAction(self.filter_menu);filter_action.setDefaultWidget(filter_body);self.filter_menu.addAction(filter_action)
        self.rules=RuleStrip(self);rule_row=QHBoxLayout();rule_row.setSpacing(4);rule_row.addWidget(self.rules,1);rule_row.addWidget(self.filter_button);layout.addLayout(rule_row)
        self.scope_label=label('','muted');self.scope_label.hide()
        self.summary=QFrame(self);self.summary.hide();self.metrics=[label() for _ in range(4)]
        for metric in self.metrics:metric.setParent(self.summary)
        caption_row=QHBoxLayout();caption_row.setSpacing(7);self.select_all_check=QCheckBox('全选');self.select_all_check.setTristate(True);self.select_all_check.setAccessibleName('全选当前筛选结果');self.select_all_check.setToolTip('只勾选当前显示的任务；切换筛选后清空选择');self.select_all_check.clicked.connect(lambda:self.set_all_checked(len(self.selected_versions)<len(self.rows)));caption_row.addWidget(self.select_all_check)
        self.caption=label('','sectionTitle');self.caption.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);caption_row.addWidget(self.caption,1);self.summary_text=label('','muted');self.summary_text.setWordWrap(False);caption_row.addWidget(self.summary_text);layout.addLayout(caption_row)
        self.table=QTableWidget(0,6);self.table.setAccessibleName('材料整理与收录查询表');self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.verticalHeader().hide();self.table.setShowGrid(False);self.table.setItemDelegate(TaskDelegate(self.table));self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu);self.table.customContextMenuRequested.connect(self.context_menu);self.table.cellDoubleClicked.connect(lambda i,c:self.open_task(i));self.table.itemSelectionChanged.connect(self.selection_changed);self.table.itemChanged.connect(self.check_changed);self.table.horizontalHeader().setMinimumSectionSize(46);layout.addWidget(self.table,1)
        self.select_shortcut=QShortcut(QKeySequence.StandardKey.SelectAll,self.table);self.select_shortcut.setContext(Qt.WidgetWithChildrenShortcut);self.select_shortcut.activated.connect(lambda:self.set_all_checked(True))
        self.empty_label=label('');self.empty_label.setAlignment(Qt.AlignCenter);layout.addWidget(self.empty_label);self.empty_label.hide()
        self.actions=QWidget();actions=QHBoxLayout(self.actions);actions.setContentsMargins(0,0,0,0);self.checked_label=label();actions.addWidget(self.checked_label,1);self.clear_button=chrome_button('取消选择');self.clear_button.clicked.connect(self.clear_checks);self.delete_button=chrome_button('删除所选');self.delete_button.clicked.connect(self.delete_selected);self.submit_button=chrome_button('确认已提交',icon='check',variant='primary');self.submit_button.clicked.connect(self.submit_selected)
        self.review_button=chrome_button('核对通过');self.review_button.clicked.connect(self.review_selected)
        self.review_button.hide();self.submit_button.hide()
        for b in (self.clear_button,self.delete_button):b.setProperty('fontPixelSize',11);actions.addWidget(b)
        layout.addWidget(self.actions);self.actions.hide();self.footer=label('双击软件查看材料 · 悬停类型图标查看缺失文件名','muted');layout.addWidget(self.footer);self.error=label('','taskError');layout.addWidget(self.error);self.error.hide()
        self.search.textChanged.connect(self.filters_changed);self.category.currentIndexChanged.connect(self.filters_changed);self.state.currentIndexChanged.connect(self.filters_changed);self.period.currentIndexChanged.connect(self.change_period)
        page.error.changed.connect(self.show_error)
        theme_signal().connect(self.apply_theme);self.configure_filters();self.apply_theme();self.refresh()

    def apply_theme(self,*args):
        c=tokens();self.setStyleSheet(f'''QWidget#taskWorkspace {{background:{c['surface']};}} QWidget#taskWorkspace QLabel {{font-size:11px;color:{c['ink']};}} QWidget#taskWorkspace QLabel#muted {{font-size:10px;color:{c['muted']};}} QWidget#taskWorkspace QLabel#sectionTitle {{font-size:12px;font-weight:600;}} QWidget#taskWorkspace QLabel#taskError {{font-size:11px;color:{c['error']};}} QWidget#taskWorkspace QFrame#taskSummary {{border:none;border-radius:6px;background:{c['raised']};}} QWidget#taskWorkspace QTabBar::tab {{font-size:14px;background:transparent;color:{c['muted']};padding:7px 10px;border:none;border-bottom:2px solid transparent;}} QWidget#taskWorkspace QTabBar::tab:selected {{color:{c['ink']};font-weight:600;border-bottom:2px solid {c['ink']};}} QWidget#taskWorkspace QTableWidget {{background:{c['surface']};border:1px solid {c['border']};border-radius:4px;font-size:11px;}} QWidget#taskWorkspace QHeaderView::section {{background:{c['raised']};color:{c['ink']};font-size:11px;border:none;border-bottom:1px solid {c['border']};padding:7px 4px;}}''')
        self._cache=None;self.table.viewport().update();self.rules._key=None;self.rules._timer.start(0)

    @staticmethod
    def category_title(name):return {'原包待核对':'压缩包原件','单手册 Word':'单 Word','桌面14件':'14件材料','14件＋双视频':'14件＋视频'}.get(name,name)

    def configure_filters(self):
        self._loading=True
        try:
            state=self.state.currentData();self.state.clear();self.state.addItem('全部状态',None)
            names=['已收齐','原包已收录','待补材料','有异常','待确认','要求待确认']
            for n in names:self.state.addItem(n,n)
            self.state.setCurrentIndex(max(0,self.state.findData(state)))
            self.period.clear()
            for name,key in [('不限日期（含往日）','all'),('最近收录日','latest'),('今天','today'),('昨天','yesterday'),('最近7天','week'),('本月','month'),('自定义日期…','custom')]:
                if key!='latest' or self.view=='completed':self.period.addItem(name,key)
            self.period.setCurrentIndex(max(0,self.period.findData(self.periods[self.view][0])))
        finally:self._loading=False

    def change_view(self,index):
        if self._loading:return
        self.view='completed' if index else 'unfinished';self.focus_ids=set();self.external_spec=None;self.selected_versions.clear();self.configure_filters();self.refresh()

    def change_period(self,*args):
        if self._loading:return
        key=self.period.currentData();old=self.periods[self.view]
        if key=='custom':
            d=QDialog(self);d.setWindowTitle('选择日期范围');root=QVBoxLayout(d);root.addWidget(label('开始日期至结束日期（含当天）'))
            start=QDateEdit(QDate.fromString(old[1],'yyyy-MM-dd') if old[1] else QDate.currentDate());end=QDateEdit(QDate.fromString(old[2],'yyyy-MM-dd') if old[2] else QDate.currentDate())
            for edit in (start,end):edit.setCalendarPopup(True);edit.setDisplayFormat('yyyy-MM-dd');root.addWidget(edit)
            close=chrome_button('取消');okay=chrome_button('确定',variant='primary');buttons=QHBoxLayout();buttons.addWidget(close);buttons.addWidget(okay);root.addLayout(buttons);close.clicked.connect(d.reject)
            def accept():
                if start.date()>end.date():QMessageBox.information(d,'日期范围','开始日期不能晚于结束日期。');return
                d.accept()
            okay.clicked.connect(accept)
            try:
                if d.exec()!=QDialog.Accepted:self.configure_filters();return
                self.periods[self.view]=(key,start.date().toString('yyyy-MM-dd'),end.date().toString('yyyy-MM-dd'))
            finally:d.deleteLater()
        else:self.periods[self.view]=(key,None,None)
        self.filters_changed()

    def filters_changed(self,*args):
        if not self._loading:self.focus_ids=set();self.selected_versions.clear();self.refresh()

    def reset(self):
        self._loading=True
        self.search.clear();self.category.setCurrentIndex(0);self.state.setCurrentIndex(0);self.periods[self.view]=('latest' if self.view=='completed' else 'all',None,None);self.external_spec=None;self.focus_ids=set();self.selected_versions.clear();self._loading=False;self.configure_filters();self.refresh()

    def choose_category(self,name):self.category.setCurrentIndex(max(0,self.category.findData(name)))

    def refresh(self,rows=None):
        if self._loading:return
        try:self.all_rows=list(rows if rows is not None else self.store.rows());self.render_rows()
        except (OSError,ValueError,sqlite3.Error) as error:self.show_error(error)

    def render_rows(self):
        from production_ui import local_time
        current=self.selected_row();pid=current['id'] if current else None
        self._loading=True
        try:
            selected=self.category.currentData();names=list(dict.fromkeys([p['name'] for p in self.service.classifications.profiles(True)]+[r['package_name'] for r in self.all_rows]))
            self.category.clear();self.category.addItem('全部材料分类',None)
            for n in names:self.category.addItem(self.category_title(n),n)
            self.category.setCurrentIndex(max(0,self.category.findData(selected)))
        finally:self._loading=False
        key,start,end=self.periods[self.view]
        if self.focus_ids:
            shown=[r for r in self.all_rows if r['id'] in self.focus_ids];recent=''
        else:shown,recent=query(self.all_rows,self.store.local_date,view=self.view,text=self.search.text(),category=self.category.currentData(),state=self.state.currentData(),period=key,start=start,end=end,spec=self.external_spec)
        shown.sort(key=lambda r:(receipt_time(r) if self.view=='completed' else {'有异常':'0','待确认':'1','待补材料':'2','原包待整理':'3','要求待确认':'3'}.get(status(r),'9'),r['id']),reverse=self.view=='completed')
        self.tabs.setTabText(0,'待整理 '+str(sum(unfinished(r) for r in self.all_rows)))
        latest_index=self.period.findData('latest')
        if latest_index>=0:self.period.setItemText(latest_index,recent or '最近收录日（暂无）')
        if key=='custom':self.period.setItemText(self.period.findData('custom'),start+' 至 '+end)
        self.scope_label.setText('本次收录的任务 · 已临时显示完整结果，重置可返回默认范围' if self.focus_ids else '会话查询：'+self.external_spec.get('description','') if self.external_spec else '默认显示最近一天有收录的任务，含未收齐的材料；可筛选历史。' if self.view=='completed' else '缺失、归属待确认或保存异常的任务跨天保留。')
        stats=[('查询结果',len(shown),'#13836b')] if self.view=='completed' or self.external_spec is not None else [('待整理',len(shown),'#33465b'),('待补材料',sum(status(r)=='待补材料' for r in shown),'#c58b14'),('有异常',sum(status(r)=='有异常' for r in shown),'#c58b14')]
        for i,w in enumerate(self.metrics):
            w.setVisible(i<len(stats))
            if i<len(stats):name,n,color=stats[i];w.setText(f'<span style="font-size:20px;font-weight:600;color:{color}">{n}</span> {name}')
        if self.focus_ids:category_base=shown
        else:category_base,_=query(self.all_rows,self.store.local_date,view=self.view,text=self.search.text(),category=None,state=self.state.currentData(),period=key,start=start,end=end,spec=self.external_spec)
        counts=Counter(r['package_name'] for r in category_base)
        if key=='latest' and not self.focus_ids:
            available,_=query(self.all_rows,self.store.local_date,view=self.view,text=self.search.text(),state=self.state.currentData(),period='all',spec=self.external_spec)
            for name in dict.fromkeys(r['package_name'] for r in available):counts[name]=len(query(self.all_rows,self.store.local_date,view=self.view,text=self.search.text(),category=name,state=self.state.currentData(),period=key,spec=self.external_spec)[0])
        if self.category.currentData() is not None:counts.setdefault(self.category.currentData(),0)
        self.rules.update_counts(counts,len(category_base));self.caption.setText(('查询结果' if self.external_spec is not None else '收录记录' if self.view=='completed' else '材料清单')+f' · {len(shown)} 项');self.caption.setToolTip(self.scope_label.text())
        self.summary_text.setText(' · '.join(name.replace('材料','')+' '+str(n) for name,n,color in stats[1:]));self.filter_button.setText('筛选 · 已选' if self.state.currentData() is not None or key!=('latest' if self.view=='completed' else 'all') else '筛选');self.filter_button.setToolTip(self.scope_label.text()+'\n状态：'+self.state.currentText()+'\n日期：'+self.period.currentText())
        cache=(shown,self.view,self.search.text(),self.category.currentData(),self.state.currentData(),key,start,end,tuple(sorted(self.focus_ids)))
        if cache==self._cache:
            self.table.blockSignals(True)
            for i,r in enumerate(self.rows):self.table.item(i,0).setCheckState(Qt.Checked if r['id'] in self.selected_versions else Qt.Unchecked)
            self.table.blockSignals(False);self.update_actions();return
        self._cache=cache;scroll=self.table.verticalScrollBar().value();old_scope=getattr(self,'_scope',None);scope=(self.view,self.search.text(),self.category.currentData(),self.state.currentData(),key,start,end,tuple(sorted(self.focus_ids)),repr(self.external_spec));self._scope=scope
        if scope!=old_scope:self.selected_versions.clear();self.table._check_anchor=0
        self.rows=shown;self.table.blockSignals(True);self.table.setProperty('completedView',self.view=='completed');self.table.setHorizontalHeaderLabels(['软件名称','分类规则','收录进度','最近收录' if self.view=='completed' else '缺失材料','当前状态','收齐时间' if self.view=='completed' else '登记日期']);self.table.setRowCount(len(shown))
        try:
            for i,r in enumerate(shown):
                age=max(0,(QDate.currentDate().toPython()-date.fromisoformat(self.store.local_date(r['created_at']))).days);meta=f"WB-{r['id']:06d}"+(' · 往日遗留 '+str(age)+' 天' if self.view=='unfinished' and age and unfinished(r) else '')
                vals=[r['name']+'\n'+meta,self.category_title(r['package_name']),f"{r['collection_count']} / {r['required_count']}" if r['required_count'] else '原包' if r.get('original_zips') else '待确认',local_time(receipt_time(r)) if self.view=='completed' else '\n'.join(m['kind']+('（文件异常）' if m.get('collection_issue') else '') for m in missing(r)) or ('包内未分类' if not r['required_count'] else '—'),status(r),(local_time(completion_time(r)) if r['required_count'] else '—') if self.view=='completed' else self.store.local_date(r['created_at'])[5:]]
                signature=self.signature(r)
                if self.selected_versions.get(r['id'])!=signature:self.selected_versions.pop(r['id'],None)
                for j,value in enumerate(vals):
                    item=QTableWidgetItem(value);item.setData(Qt.UserRole,r['id']);item.setData(Qt.UserRole+1,r);item.setToolTip(r['name']+'\n'+r['batch_name']+'\n'+meta if j==0 else r['package_name'] if j==1 else '最近收录时间：'+value if j==3 and self.view=='completed' else value)
                    if j==0:item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked if r['id'] in self.selected_versions else Qt.Unchecked)
                    self.table.setItem(i,j,item)
                self.table.setRowHeight(i,48)
            selected_index=next((i for i,r in enumerate(shown) if r['id']==pid),0)
            if shown:self.table.selectRow(selected_index)
            self.selected_versions={r['id']:self.signature(r) for r in shown if r['id'] in self.selected_versions};self.table.verticalScrollBar().setValue(scroll)
        finally:self.table.blockSignals(False)
        self.empty_label.setVisible(not shown);self.empty_label.setText('没有匹配的查询结果，请调整查询或筛选条件。' if self.external_spec is not None else '没有匹配的收录记录，请调整日期或筛选条件。' if self.view=='completed' else '当前没有待整理任务，可在“收录查询”中查找已收录材料。');self.arrange_columns();self.selection_changed();self.update_actions()

    @staticmethod
    def signature(r):return (r['processing_mode'],tuple((m.get('id'),m.get('passed'),m.get('submission_id'),m.get('dependency_invalid'),m.get('collection_issue'),m.get('collection_saved')) for m in r['materials']))

    def selected_row(self):
        i=self.table.currentRow();item=self.table.item(i,0) if i>=0 else None
        return next((r for r in self.rows if item and r['id']==item.data(Qt.UserRole)),None)

    def selection_changed(self):
        row=self.selected_row()
        if row and self.isVisible():self.page.set_active_batch(row['batch_id'])

    def check_changed(self,item):
        if self._loading or item.column()!=0:return
        r=item.data(Qt.UserRole+1)
        if item.checkState()==Qt.Checked:self.selected_versions[r['id']]=self.signature(r)
        else:self.selected_versions.pop(r['id'],None)
        self.update_actions()

    def clear_checks(self):
        self.selected_versions.clear();self.table.blockSignals(True)
        for i in range(self.table.rowCount()):self.table.item(i,0).setCheckState(Qt.Unchecked)
        self.table.blockSignals(False);self.update_actions()

    def set_all_checked(self,checked):
        self.selected_versions={r['id']:self.signature(r) for r in self.rows} if checked else {}
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):self.table.item(i,0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.table.blockSignals(False);self.update_actions()

    def update_actions(self):
        n=len(self.selected_versions);self.actions.setVisible(bool(n));self.checked_label.setText(f'已选 {n} 项');self.select_all_check.blockSignals(True);self.select_all_check.setEnabled(bool(self.rows));self.select_all_check.setCheckState(Qt.Unchecked if not n else Qt.Checked if n==len(self.rows) else Qt.PartiallyChecked);self.select_all_check.blockSignals(False)
        self.review_button.hide();self.submit_button.hide()

    def selected_tasks(self):return [r for r in self.rows if r['id'] in self.selected_versions]

    def delete_selected(self):self.page.delete_tasks(self.selected_tasks())

    def review_selected(self):
        displayed=self.selected_tasks();expected=dict(self.selected_versions);fresh={r['id']:r for r in self.store.rows()}
        if not displayed:return
        if any(r['id'] not in fresh or self.signature(fresh[r['id']])!=expected[r['id']] for r in displayed):self.show_error('所选材料版本或状态已变化，请重新勾选。');self.refresh();return
        if any(r['processing_mode']!='review' or status(r) not in ('待核对','需修改','待提交') for r in displayed):self.show_error('请先补齐材料，并取消勾选已提交或仅整理的任务。');return
        versions=[m['id'] for r in displayed for m in r['materials']]
        if QMessageBox.question(self,'批量核对通过',f'确认所选 {len(displayed)} 个软件、共 {len(versions)} 份材料均已人工检查？这里只登记核对结果，不代表已经提交。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.page.request('保存所选软件的核对结果',lambda **kw:self.store.review_many(versions,**kw))

    def submit_selected(self):
        displayed=self.selected_tasks();expected=dict(self.selected_versions);fresh={r['id']:r for r in self.store.rows()}
        if not displayed:return
        if any(r['id'] not in fresh or self.signature(fresh[r['id']])!=expected[r['id']] for r in displayed):self.show_error('所选材料版本或状态已变化，请重新勾选。');self.refresh();return
        blocked=[r for r in displayed if status(r) not in ('待提交','已提交') or r['processing_mode']!='review']
        if blocked:self.show_error('请先补齐并核对材料，再登记实际提交。双击软件可查看待处理材料。');self.open_task_by_id(blocked[0]['id']);return
        if all(status(r)=='已提交' for r in displayed):self.show_error('所选任务已记录提交，无需重复操作。');return
        if QMessageBox.question(self,'确认已提交',f'确认你已在外部完成所选 {len(displayed)} 项的提交？将记录当前材料版本和时间。')!=QMessageBox.Yes:return
        ids=[r['id'] for r in displayed];versions=[m['id'] for r in displayed for m in r['materials']];self.page.request('记录材料提交',lambda **kw:self.store.submit_projects(ids,versions,**kw))

    def open_task(self,index):
        item=self.table.item(index,0)
        if item:self.open_task_by_id(item.data(Qt.UserRole))

    def open_task_by_id(self,pid):
        from task_requirements_ui import TaskMaterialsDialog
        row=next((r for r in self.store.rows() if r['id']==pid),None)
        if not row:self.show_error('任务已删除或归档，请刷新后重新选择。');self.refresh();return
        if not row['required_count']:
            if row.get('original_zips'):
                from zip_intake_ui import OriginalZipDetailsDialog
                dialog=OriginalZipDetailsDialog(self.page,row)
                try:dialog.exec()
                finally:dialog.deleteLater();self.page.refresh()
            else:self.show_error('该软件尚未收录材料。请拖入文件开始收录。')
            return
        dialog=TaskMaterialsDialog(self.page,row)
        try:dialog.exec()
        finally:dialog.deleteLater();self.page.refresh()

    def context_menu(self,point):
        index=self.table.indexAt(point)
        if not index.isValid():return
        self.table.selectRow(index.row());pid=self.table.item(index.row(),0).data(Qt.UserRole);row=next((r for r in self.store.rows() if r['id']==pid),None)
        if not row:self.refresh();return
        from material_context import task_menu
        menu=task_menu(self,row)
        try:menu.exec(self.table.viewport().mapToGlobal(point))
        finally:menu.deleteLater()

    def rename(self,pid):
        from PySide6.QtWidgets import QInputDialog
        row=next((r for r in self.store.rows() if r['id']==pid),None)
        if not row:return
        name,okay=QInputDialog.getText(self,'修改软件名称','材料、历史版本与保存位置按任务编号保留。',text=row['name'])
        if okay:
            try:self.service.rename_project(pid,name);self.page.refresh();self.page.metadata_changed.emit()
            except (ValueError,OSError,sqlite3.Error) as error:self.show_error(error)

    def open_original(self,pid):
        from saved_locations import original_file_location,open_location
        try:
            row=next((r for r in self.store.rows() if r['id']==pid),None)
            if not row:raise ValueError('任务已删除或归档，请刷新后重新选择。')
            if row.get('original_zips') and not row['materials']:
                target=original_file_location(self.service,pid,zip_id=row['original_zips'][0]['id'])
            else:
                material=next((m for m in row['materials'] if m.get('id')),None)
                if not material:raise ValueError('尚未收录材料，没有原始位置。')
                target=original_file_location(self.service,pid,kind=material['kind'])
            open_location(target);self.show_error('')
        except (ValueError,OSError,sqlite3.Error) as error:self.show_error(error)

    def extract_archive(self,pid):
        from zip_intake_ui import OriginalZipDetailsDialog
        row=next((r for r in self.store.rows() if r['id']==pid),None)
        if not row or not row.get('original_zips'):self.show_error('原包记录不存在，请刷新后重新选择。');return
        dialog=OriginalZipDetailsDialog(self.page,row)
        try:dialog.extract()
        finally:dialog.deleteLater()

    def open_saved(self,pid):
        from saved_locations import saved_file_location,collection_location,open_location
        try:
            row=next((r for r in self.store.rows() if r['id']==pid),None)
            if not row:raise ValueError('任务已删除或归档，请刷新后重新选择。')
            if row.get('original_zips') and not row['materials']:target=saved_file_location(self.service,pid,zip_id=row['original_zips'][0]['id'])
            else:target=collection_location(self.service,pid)
            open_location(target)
        except (ValueError,OSError,sqlite3.Error) as error:self.show_error(error)

    def show_error(self,error):self.error.setText(str(error));self.error.setVisible(bool(str(error)))

    def sync_intake_actions(self):
        self.page.retry_action.setVisible(bool(self.page.pending_imports))
        self.page.retry_action.setEnabled(self.page.retry_import_button.isEnabled())

    def focus_projects(self,ids):
        selected=[r for r in self.store.rows() if r['id'] in ids]
        self.view='completed' if selected and all(not unfinished(r) for r in selected) else 'unfinished'
        self._loading=True;self.tabs.setCurrentIndex(1 if self.view=='completed' else 0);self._loading=False;self.external_spec=None;self.focus_ids=set(ids);self.configure_filters();self.refresh()

    def apply_query(self,spec):
        self.external_spec=dict(spec);self.focus_ids=set();self.selected_versions.clear();self._loading=True
        try:
            self.search.clear();self.category.setCurrentIndex(0);self.state.setCurrentIndex(0)
            self.view='completed' if spec.get('metric') in ('checked','submitted','collected') or spec.get('state') in ('已提交','已收纳') else 'unfinished';self.tabs.setCurrentIndex(1 if self.view=='completed' else 0)
        finally:self._loading=False
        self.periods[self.view]=('all',None,None);self.configure_filters();self.refresh();self.table.scrollToTop()

    def arrange_columns(self):
        width=self.table.viewport().width();self.table.setColumnHidden(1,width<620)
        for i,w in enumerate((230,86,65,92,78,78)):self.table.setColumnWidth(i,w)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(46)

    def resizeEvent(self,event):super().resizeEvent(event);self.arrange_columns()

    def export(self):
        filename,_=QFileDialog.getSaveFileName(self,'导出当前筛选','材料查询.csv','CSV 表格 (*.csv)')
        if filename:
            try:self.export_to(filename);self.show_error('已导出：'+filename)
            except (OSError,ValueError,sqlite3.Error) as error:self.show_error(error)

    def export_to(self,target):
        from production_ui import local_time
        self.refresh();safe=lambda value:"'"+str(value) if str(value).lstrip().startswith(('=','+','-','@')) else str(value)
        with open(target,'w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['任务编号','软件名称','分类规则','所属批次','收录进度','缺失材料','当前状态','最近收录时间','收齐时间'])
            for r in self.rows:writer.writerow([safe(v) for v in [f"WB-{r['id']:06d}",r['name'],r['package_name'],r['batch_name'],f"{r['collection_count']}/{r['required_count']}",'、'.join(m['kind'] for m in missing(r)),status(r),local_time(receipt_time(r)),local_time(completion_time(r)) if r['required_count'] else '']])
