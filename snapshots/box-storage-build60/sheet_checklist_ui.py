"""Compact, read-only checklist comparison launched from the main workbench."""
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QStyledItemDelegate,
    QTableWidgetItem,QHeaderView,QAbstractItemView,QFileDialog,QComboBox,QWidget)
from workspace_chrome import chrome_button
from theme import apply_to,resolved_mode,theme_signal,tokens
from sheet_checklist import (read_tables,suggest_columns,extract_names,compare,
    save_checklist,load_checklist,normalized,date_text,date_keys,filter_dates)


def label(text=''):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setWordWrap(True);return w


class ChecklistDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        c=tokens();background=index.data(Qt.BackgroundRole)
        color=background.color() if hasattr(background,'color') else QColor(c['surface'])
        painter.save();painter.fillRect(option.rect,color);painter.setClipRect(option.rect);painter.setFont(option.font);painter.setPen(QColor(c['ink']))
        rect=option.rect.adjusted(7,0,-5,0);text=option.fontMetrics.elidedText(str(index.data() or ''),Qt.ElideRight,max(0,rect.width()))
        painter.drawText(rect,Qt.AlignVCenter|Qt.AlignLeft,text);painter.restore()


def table(headers):
    w=QTableWidget(0,len(headers));w.setHorizontalHeaderLabels(headers);w.verticalHeader().hide()
    w.setEditTriggers(QAbstractItemView.NoEditTriggers);w.setSelectionBehavior(QAbstractItemView.SelectRows)
    w.setSelectionMode(QAbstractItemView.SingleSelection);w.setWordWrap(False);w.setShowGrid(True)
    w.verticalHeader().setDefaultSectionSize(25);w.horizontalHeader().setMinimumSectionSize(38);w.setItemDelegate(ChecklistDelegate(w))
    return w


def mark(state):
    dark=resolved_mode()=='dark'
    return QColor(('#603c4a' if dark else '#f8dfe7') if state=='尚未收录' else ('#654d30' if dark else '#ffedcf'))


class SheetImportDialog(QDialog):
    def __init__(self,page,path):
        super().__init__(page);self.page=page;self.tables=read_tables(path);self.entries=[]
        self.setWindowTitle('导入表格');self.resize(560,350);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(14,12,14,12);layout.setSpacing(7)
        layout.addWidget(label(Path(path).name))
        line=QHBoxLayout();self.sheet=QComboBox();self.sheet.addItems([n for n,_ in self.tables]);self.sheet.setAccessibleName('工作表')
        self.name_column=QComboBox();self.name_column.setAccessibleName('软件名称列');self.date_column=QComboBox();self.date_column.setAccessibleName('清单日期列')
        for title,c in [('工作表',self.sheet),('软件名称',self.name_column),('清单日期',self.date_column)]:
            box=QVBoxLayout();box.setSpacing(3);box.addWidget(label(title));box.addWidget(c);line.addLayout(box,1)
        layout.addLayout(line)
        scope=QHBoxLayout();scope.addWidget(label('导入日期'));self.date_scope=QComboBox();self.date_scope.setAccessibleName('导入日期范围')
        for title,key in [('全部日期','all'),('某一天','day'),('时间段','range')]:self.date_scope.addItem(title,key)
        self.date_start=QComboBox();self.date_start.setAccessibleName('导入开始日期');self.date_end=QComboBox();self.date_end.setAccessibleName('导入结束日期');self.date_to=label('至')
        for w in (self.date_scope,self.date_start,self.date_to,self.date_end):scope.addWidget(w)
        scope.addStretch();layout.addLayout(scope)
        for c in (self.date_scope,self.date_start,self.date_end):c.currentIndexChanged.connect(self.update_preview)
        self.summary=label();layout.addWidget(self.summary)
        self.preview=table(['软件名称','核对结果']);self.preview.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.preview.setColumnWidth(1,105);layout.addWidget(self.preview,1)
        layout.addWidget(label('只保存核对清单；已有任务、规则和材料保持。重复名称逐条保留。'))
        actions=QHBoxLayout();actions.addStretch();cancel=chrome_button('取消');self.confirm=chrome_button('导入并核对',variant='primary');actions.addWidget(cancel);actions.addWidget(self.confirm);layout.addLayout(actions)
        cancel.clicked.connect(self.reject);self.confirm.clicked.connect(self.commit)
        self.sheet.currentIndexChanged.connect(self.choose_sheet);self.name_column.currentIndexChanged.connect(self.update_preview);self.date_column.currentIndexChanged.connect(self.update_preview)
        self.choose_sheet()
        theme_signal().connect(self.update_preview)

    def choose_sheet(self,*_):
        matrix=self.tables[self.sheet.currentIndex()][1];width=max((len(r) for r in matrix),default=1);nc,dc=suggest_columns(matrix)
        for c in (self.name_column,self.date_column):c.blockSignals(True);c.clear()
        self.date_column.addItem('不指定',-1)
        for i in range(width):
            title=(chr(65+i) if i<26 else str(i+1))+' 列'
            self.name_column.addItem(title,i);self.date_column.addItem(title,i)
        self.name_column.setCurrentIndex(nc);self.date_column.setCurrentIndex(dc+1)
        for c in (self.name_column,self.date_column):c.blockSignals(False)
        self.update_preview()

    def update_preview(self,*_):
        try:
            available=extract_names(self.tables[self.sheet.currentIndex()][1],self.name_column.currentData(),self.date_column.currentData())
            days=sorted(set(k for k in date_keys(available) if k));signature=tuple(days)
            if getattr(self,'_days',None)!=signature:
                self._days=signature
                for c in (self.date_start,self.date_end):
                    old=c.currentData();c.blockSignals(True);c.clear()
                    for day in days:c.addItem(day,day)
                    c.setCurrentIndex(c.findData(old) if old in days else (len(days)-1 if c is self.date_end else 0));c.blockSignals(False)
            mode=self.date_scope.currentData();self.date_start.setVisible(mode!='all');self.date_end.setVisible(mode=='range');self.date_to.setVisible(mode=='range')
            self.entries=filter_dates(available,mode,self.date_start.currentData(),self.date_end.currentData())
            results=compare(self.entries,self.page.store.rows());counts=Counter(r['state'] for r in results);duplicates=sum(e['duplicate'] for e in self.entries)
            undated=sum(not r['date'] for r in available)
            self.summary.setText(f'本次导入 {len(results)} / {len(available)} 个名称 · '+ ' · '.join(f'{k} {v}' for k,v in counts.items())+(f' · 重复名称 {duplicates} 条' if duplicates else '')+(f' · 未标日期 {undated} 条未选入' if undated and mode!='all' else ''))
            self.preview.setRowCount(len(results))
            for i,r in enumerate(results):
                for j,value in enumerate((r['name'],r['state'])):
                    item=QTableWidgetItem(value);item.setToolTip((f"第 {r['row']} 行 · 清单日期：{r['date'] or '未注明'}"+' · 重复名称' if r['duplicate'] else f"第 {r['row']} 行 · 清单日期：{r['date'] or '未注明'}"));self.preview.setItem(i,j,item)
                    if r['state']!='已收齐':item.setBackground(mark(r['state']))
            self.confirm.setEnabled(bool(self.entries))
        except (ValueError,OSError,sqlite3.Error) as error:
            self.entries=[];self.preview.setRowCount(0);self.summary.setText(str(error));self.confirm.setEnabled(False)

    def commit(self):
        try:
            # Keep earlier checklists, but make a repeated import idempotent.
            existing=load_checklist(self.page.service.db.path.parent);keys={(normalized(r['name']),r['date'],r['row']) for r in existing}
            combined=list(existing)
            for r in self.entries:
                key=(normalized(r['name']),r['date'],r['row'])
                if key not in keys:combined.append(r);keys.add(key)
            save_checklist(self.page.service.db.path.parent,combined);self.accept()
        except (ValueError,OSError) as error:self.summary.setText('清单未写入：'+str(error))


class ChecklistDetails(QDialog):
    def __init__(self,page,entry,parent=None):
        super().__init__(parent or page);self.page=page;self.entry=entry;self.row=None;self.target=None
        self.setWindowTitle('材料详情');self.resize(500,255);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(12,10,12,10);layout.setSpacing(6)
        layout.addWidget(label(entry['name']));self.meta=label();layout.addWidget(self.meta)
        self.choice=QComboBox();self.choice.setAccessibleName('选择同名收录记录');layout.addWidget(self.choice);self.choice.hide()
        candidates=entry['candidates']
        if len(candidates)>1:
            self.choice.addItem('请选择同名收录记录',None)
            for r in candidates:self.choice.addItem(f"{r.get('batch_name','')} · WB-{r['id']:06d}",r['id'])
            self.choice.show();self.choice.currentIndexChanged.connect(self.refresh)
        self.files=table(['材料 / 文件','当前状态']);self.files.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.files.setColumnWidth(1,75);layout.addWidget(self.files,1)
        location=QHBoxLayout();self.path=label();self.path.setTextInteractionFlags(Qt.TextSelectableByMouse);location.addWidget(self.path,1)
        self.folder=chrome_button(icon='folder',icon_only=True,label='打开保存位置');self.folder.setFixedSize(26,26);self.folder.clicked.connect(self.open_folder);location.addWidget(self.folder);layout.addLayout(location)
        self.error=label();self.error.hide();layout.addWidget(self.error);actions=QHBoxLayout();actions.addStretch();close=chrome_button('关闭');close.clicked.connect(self.accept);actions.addWidget(close);layout.addLayout(actions)
        self.refresh();theme_signal().connect(self.refresh)

    def refresh(self,*_):
        self.target=None;self.row=None;candidates=self.entry['candidates']
        pid=self.choice.currentData() if len(candidates)>1 else candidates[0]['id'] if candidates else None
        self.row=next((r for r in self.page.store.rows() if r['id']==pid),None) if pid else None
        self.files.setRowCount(0)
        if not self.row:
            self.meta.setText('存在多个同名记录，请先选择。' if candidates else '尚未收录 · 清单日期：'+(self.entry.get('date') or '未注明'))
            self.path.setText('暂无可确定的保存位置');self.folder.setEnabled(False);return
        r=self.row;self.meta.setText(f"{r['package_name']} · 已收 {r['collection_count']}/{r['required_count']} 份"+(' · 清单日期：'+self.entry['list_date'] if self.entry.get('list_date') else ''))
        data=[(m.get('filename') or m['kind'],'文件异常' if m.get('collection_issue') else '已收录' if m.get('collection_saved') else '缺少') for m in r['materials']]
        if not r['required_count']:
            from task_query import current_originals
            data=[(Path(z['path']).name,'已收录' if Path(z['path']).is_file() else '文件缺失') for z in current_originals(r)]
            self.meta.setText(f"{r['package_name']} · 原包 {len(data)} 份")
        self.files.setRowCount(len(data))
        for i,values in enumerate(data):
            for j,v in enumerate(values):
                item=QTableWidgetItem(v);item.setToolTip(v);self.files.setItem(i,j,item)
                if values[1]!='已收录':item.setBackground(mark('材料不齐'))
        try:
            from saved_locations import collection_location
            if r['materials']:self.target=collection_location(self.page.service,r['id'])
            else:
                from task_query import current_originals
                self.target=next((Path(z['path']).parent for z in current_originals(r) if Path(z['path']).is_file()),None)
        except (ValueError,OSError,sqlite3.Error):pass
        self.path.setText('保存至：'+str(self.target) if self.target else '暂无已保存的材料');self.path.setToolTip(self.path.text());self.folder.setEnabled(self.target is not None)

    def open_folder(self):
        if not self.target:return
        try:
            from saved_locations import open_location
            open_location(self.target)
        except (ValueError,OSError) as error:self.error.setText(str(error));self.error.show()


class ChecklistOverview(QDialog):
    def __init__(self,page):
        super().__init__(page);self.page=page;self.entries=[];self.fingerprint=None
        self.setWindowTitle('软件总览表');self.resize(950,250);self.setMinimumSize(650,145);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,8);layout.setSpacing(5);line=QHBoxLayout();self.tables=[]
        for side in ('左侧','右侧'):
            t=table(['日期','软件名称']);t.setAccessibleName(side+'软件总览表');t.setColumnWidth(0,54);t.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);t.setColumnWidth(0,54)
            t.cellDoubleClicked.connect(lambda i,c,t=t:self.open_details(t,i));line.addWidget(t,1);self.tables.append(t)
        layout.addLayout(line,1);bottom=QHBoxLayout();self.note=label('淡橙：材料不齐 / 待确认　淡玫瑰：尚未收录');bottom.addWidget(self.note,1);close=chrome_button('关闭');close.clicked.connect(self.accept);bottom.addWidget(close);layout.addLayout(bottom)
        self.timer=QTimer(self);self.timer.setInterval(3000);self.timer.timeout.connect(self.refresh);theme_signal().connect(self.retheme);self.retheme()

    def retheme(self,*_):
        c=tokens();self.setStyleSheet(f'QTableWidget {{font-size:11px;background:{c["surface"]};color:{c["ink"]};gridline-color:{c["border"]};}} QHeaderView::section {{font-size:11px;background:{c["raised"]};color:{c["ink"]};padding:5px;}} QLabel {{font-size:11px;color:{c["muted"]};}}')
        self.fingerprint=None;self.refresh()

    def refresh(self):
        try:
            records=self.page.store.rows();items=load_checklist(self.page.service.db.path.parent);fingerprint=repr((records,items,resolved_mode()))
            if fingerprint==self.fingerprint:return
            self.fingerprint=fingerprint;byname={}
            for item in items:byname.setdefault(normalized(item['name']),[]).append(item)
            from task_query import receipt_time
            entries=[]
            for r in records:
                matched=byname.get(normalized(r['name']),[]);day=self.page.store.local_date(receipt_time(r) or r.get('created_at'))
                e=compare([dict(name=r['name'],date=day)], [r])[0];e['list_date']='、'.join(dict.fromkeys(i['date'] for i in matched if i['date']));entries.append(e)
            known={normalized(r['name']) for r in records}
            entries.extend(compare([i for i in items if normalized(i['name']) not in known],records))
            entries.sort(key=lambda e:((str(date.today().year)+'-'+e['date'] if len(e['date'])==5 else e['date']),e['name']),reverse=True);self.entries=entries;middle=(len(entries)+1)//2
            for t,subset in zip(self.tables,(entries[:middle],entries[middle:])):
                t.clearSpans();t.setRowCount(len(subset));t.setUpdatesEnabled(False)
                for i,e in enumerate(subset):
                    day=e['date'][-5:] if e['date'] else '未注明'
                    for col,value in enumerate((day,e['name'])):
                        item=QTableWidgetItem(value);item.setData(Qt.UserRole,e);item.setToolTip(e['name']+' · '+e['state']+'\n双击查看详情'+('\n清单日期：'+e.get('list_date','') if e.get('list_date') else ''));t.setItem(i,col,item)
                        if col==1 and e['state']!='已收齐':item.setBackground(mark(e['state']))
                    if i==0 or subset[i-1]['date']!=e['date']:
                        end=i+1
                        while end<len(subset) and subset[end]['date']==e['date']:end+=1
                        if end-i>1:t.setSpan(i,0,end-i,1)
                    t.setRowHeight(i,25)
                t.setUpdatesEnabled(True)
            self.note.setText(f'共 {len(entries)} 条 · 淡橙：不齐 / 待确认　淡玫瑰：未收录')
            self.resize(self.width(),max(155,min(520,25*middle+90)))
        except (ValueError,OSError,sqlite3.Error) as error:self.note.setText(str(error))

    def open_details(self,t,index):
        item=t.item(index,1)
        if not item:return
        dialog=ChecklistDetails(self.page,item.data(Qt.UserRole),self)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def showEvent(self,event):super().showEvent(event);self.refresh();self.timer.start()
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)


def show_overview(page):
    if not hasattr(page,'checklist_overview'):
        page.checklist_overview=ChecklistOverview(page)
    page.checklist_overview.refresh();page.checklist_overview.show();page.checklist_overview.raise_();page.checklist_overview.activateWindow()


def import_sheet(page):
    path,_=QFileDialog.getOpenFileName(page,'导入表格','','名称清单 (*.xlsx *.csv *.tsv)')
    if not path:return
    try:
        dialog=SheetImportDialog(page,path)
        try:accepted=dialog.exec()==QDialog.Accepted
        finally:dialog.deleteLater()
        if accepted:show_overview(page)
    except (ValueError,OSError,sqlite3.Error,UnicodeError) as error:page.error.setText('导入未完成：'+str(error))
