"""Read-only, live software overview and per-material saved file locations."""
import csv
import sqlite3
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QSplitter,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QListWidget,QListWidgetItem,
    QPlainTextEdit,QFileDialog,QPushButton,QFrame,QApplication)
from workspace_chrome import chrome_button,WorkspaceComboBox,SearchField,WorkspaceMenu
from theme import apply_to,manager,tokens,resolved_mode
from master_ledger_chrome import LedgerDelegate,MaterialDelegate
from production_ui import text_label,local_time
from ui_components import button


HEADERS=['软件名称','所属批次','材料分类','收录进度','缺失材料','收纳状态','最近收录']


class SortItem(QTableWidgetItem):
    def __init__(self,text,key):super().__init__(text);self.key=key
    def __lt__(self,other):return self.key<other.key if isinstance(other,SortItem) else self.text()<other.text()


def abnormal(row):
    from task_query import current_originals
    return any(m.get('collection_issue') for m in row['materials']) or any(not Path(z['path']).is_file() for z in current_originals(row))


def values(row):
    from task_query import missing as missing_items,status,receipt_time
    missing=[m['kind'] for m in missing_items(row)]
    progress=f"{row['collection_count']} / {row['required_count']}" if row['required_count'] else '原包已收录' if row.get('original_zips') else '要求待确认'
    archive_label=' / '.join(sorted({Path(z['path']).suffix.lstrip('.').upper() for z in row.get('original_zips',[])}))+' 原包'
    return [row['name'],row.get('batch_label',row['batch_name']),archive_label if row.get('original_zips') and not row['required_count'] else row['package_name'],progress,'、'.join(missing) or ('文件异常' if abnormal(row) else '—'),status(row),local_time(receipt_time(row)) or '—']


class VersionHistoryDialog(QDialog):
    def __init__(self,service,pid,kind,parent=None):
        super().__init__(parent);self.setWindowTitle('材料版本记录 · '+kind);self.resize(950,460);apply_to(self)
        layout=QVBoxLayout(self);layout.addWidget(text_label('历史版本只供查看；打开保存文件位置会定位所选版本的归档文件。'))
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['版本','文件名','收录时间','保存位置']);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setWordWrap(False)
        self.rows=service.production.history(pid,kind);self.table.setRowCount(len(self.rows))
        for i,r in enumerate(self.rows):
            for j,value in enumerate((f"v{len(self.rows)-i}",r['filename'],local_time(r['created_at']),r['path'])):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,j,item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table)
        self.error=text_label('');self.error.setObjectName('error');layout.addWidget(self.error)
        actions=QHBoxLayout();open_button=chrome_button('打开保存文件位置');close=chrome_button('关闭');actions.addWidget(open_button);actions.addStretch();actions.addWidget(close);layout.addLayout(actions)
        open_button.clicked.connect(self.open_location);close.clicked.connect(self.accept)
        if self.rows:self.table.selectRow(0)
        open_button.setEnabled(bool(self.rows))

    def open_location(self):
        from saved_locations import open_location
        i=self.table.currentRow()
        if not 0<=i<len(self.rows):return
        try:
            path=Path(self.rows[i]['path'])
            if not path.is_file():raise ValueError('此历史版本已移动或缺失，请核对原保存位置。')
            open_location(path);self.error.clear()
        except (OSError,ValueError,sqlite3.Error) as error:self.error.setText(str(error))


class MasterLedgerDialog(QDialog):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;self.store=service.production;self.rows=[];self.visible_rows=[];self._loading=False
        self.setWindowTitle('BOX · 软件材料总表');self.resize(1180,720);self.setMinimumSize(940,580);self.setModal(False);apply_to(self)
        screen=QApplication.primaryScreen()
        if screen:
            available=screen.availableGeometry();self.setMinimumSize(min(940,available.width()),min(580,available.height()))
            self.resize(min(1180,available.width()-40),min(720,available.height()-50))
        self.setObjectName('masterLedger');layout=QVBoxLayout(self);layout.setContentsMargins(14,12,14,10);layout.setSpacing(10)
        header=QHBoxLayout();title=text_label('软件材料总表');title.setObjectName('pageTitle');title.setWordWrap(False);header.addWidget(title);sub=text_label('按软件汇总 · 与材料工作台同步');sub.setObjectName('muted');sub.setWordWrap(False);header.addWidget(sub);header.addStretch()
        close=QPushButton('返回工作台');close.clicked.connect(self.close);header.addWidget(close)
        self.export_button=QPushButton('导出总表');self.export_button.setObjectName('ledgerPrimary');menu=WorkspaceMenu(self.export_button);menu.addAction('导出当前筛选',lambda:self.export(False));menu.addAction('导出全部软件',lambda:self.export(True));self.export_button.setMenu(menu);header.addWidget(self.export_button);layout.addLayout(header)
        self.statistics=text_label('');self.statistics.hide()
        stats=QWidget();stats.setObjectName('ledgerStats');stats_layout=QHBoxLayout(stats);stats_layout.setContentsMargins(6,9,6,9);self.stat_counts=[]
        for i,name in enumerate(('软件总数','已收纳','待整理','异常')):
            if i:
                divider=QFrame();divider.setFrameShape(QFrame.VLine);divider.setFixedHeight(14);stats_layout.addWidget(divider)
            label=QLabel(name+'  0');label.setAlignment(Qt.AlignCenter);stats_layout.addWidget(label,1);self.stat_counts.append(label)
        layout.addWidget(stats)
        split=QSplitter(Qt.Horizontal);left=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,0,0)
        filters=QHBoxLayout();self.search=SearchField();self.search.setPlaceholderText('搜索软件名称或任务编号');self.search.setClearButtonEnabled(True);filters.addWidget(self.search,2)
        self.batch=WorkspaceComboBox();self.category=WorkspaceComboBox();self.state=WorkspaceComboBox()
        for c in (self.batch,self.category,self.state):c.setMinimumWidth(85);c.setMaximumWidth(125);c.setFixedHeight(32);filters.addWidget(c)
        reset=chrome_button('重置');reset.clicked.connect(self.reset);filters.addWidget(reset);ll.addLayout(filters)
        self.table=QTableWidget(0,len(HEADERS));self.table.setHorizontalHeaderLabels(HEADERS);self.table.setAccessibleName('软件材料总表预览');self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setWordWrap(False);self.table.setAlternatingRowColors(True);self.table.verticalHeader().hide();self.table.setSortingEnabled(True)
        self.table.setItemDelegate(LedgerDelegate(self.table));self.table.setShowGrid(False)
        for i,w in enumerate((260,72,96,82,80,96,94)):self.table.setColumnWidth(i,w)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setMinimumSectionSize(65);self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft|Qt.AlignVCenter);ll.addWidget(self.table,1);split.addWidget(left)
        right=QWidget();right.setObjectName('ledgerDetails');right.setMinimumWidth(270);rl=QVBoxLayout(right);rl.setContentsMargins(14,0,0,0);rl.setSpacing(8)
        detail_header=QHBoxLayout();heading=text_label('材料详情');heading.setObjectName('title');detail_header.addWidget(heading);detail_header.addStretch();self.details_toggle=chrome_button('收起',icon='back');detail_header.addWidget(self.details_toggle);rl.addLayout(detail_header)
        self.software=text_label('请选择一项软件');self.software.setObjectName('sectionTitle');self.software.setTextFormat(Qt.PlainText);rl.addWidget(self.software)
        self.identity=text_label('');self.identity.setTextFormat(Qt.PlainText);self.identity.setObjectName('muted');rl.addWidget(self.identity)
        self.detail_summary=text_label('');self.detail_summary.setTextFormat(Qt.PlainText);rl.addWidget(self.detail_summary)
        self.notice=text_label('');self.notice.setTextFormat(Qt.PlainText);self.notice.setObjectName('ledgerNotice');rl.addWidget(self.notice)
        self.materials=QListWidget();self.materials.setWordWrap(False);self.materials.setSpacing(0);self.materials.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.materials.setItemDelegate(MaterialDelegate(self.materials));rl.addWidget(self.materials)
        rl.addWidget(text_label('保存位置'));self.path=QPlainTextEdit();self.path.setReadOnly(True);self.path.setLineWrapMode(QPlainTextEdit.WidgetWidth);self.path.setFixedHeight(52);rl.addWidget(self.path)
        self.locate_button=QPushButton('打开保存文件位置');self.locate_button.setObjectName('ledgerPrimary');self.directory_button=QPushButton('打开收录目录');self.history_button=QPushButton('查看版本记录')
        rl.addWidget(self.locate_button);other=QHBoxLayout();other.addWidget(self.directory_button);other.addWidget(self.history_button);rl.addLayout(other)
        self.error=text_label('');self.error.setObjectName('error');rl.addWidget(self.error);rl.addStretch();split.addWidget(right);split.setSizes([850,290]);split.setStretchFactor(0,3);split.setStretchFactor(1,1);layout.addWidget(split,1)
        self.details_toggle.clicked.connect(lambda:self.toggle_details(False));self.details_panel=right;self.splitter=split
        self.reopen_details=QPushButton('显示材料详情');self.reopen_details.hide();header.insertWidget(header.count()-2,self.reopen_details);self.reopen_details.clicked.connect(lambda:self.toggle_details(True))
        self.footer=text_label('');self.footer.setObjectName('muted');layout.addWidget(self.footer)
        self.search.textChanged.connect(self.filter_rows)
        for c in (self.batch,self.category,self.state):c.currentIndexChanged.connect(self.filter_rows)
        self.table.itemSelectionChanged.connect(self.show_details);self.materials.currentRowChanged.connect(self.show_path)
        self.locate_button.clicked.connect(lambda:self.open_saved(False));self.directory_button.clicked.connect(lambda:self.open_saved(True));self.history_button.clicked.connect(self.show_history)
        self.timer=QTimer(self);self.timer.setInterval(3000);self.timer.timeout.connect(self.refresh)
        self.search.setFixedHeight(32);self.apply_compact_theme();manager().changed.connect(self.apply_compact_theme)
        self.refresh()

    def toggle_details(self,visible):
        self.details_panel.setVisible(visible);self.reopen_details.setVisible(not visible)
        if visible:self.splitter.setSizes([max(600,self.width()-310),290])

    def apply_compact_theme(self,*args):
        c=tokens();dark=resolved_mode()=='dark';surface=c['surface'];line=c['border'] if dark else '#e5eaf0';field=c['raised'] if dark else '#f7f9fb'
        self.setStyleSheet(f'''
        QWidget#masterLedger {{background:{surface};color:{c['ink']};}}
        QWidget#masterLedger QLabel {{font-size:12px; color:{c['ink']};}}
        QWidget#masterLedger QLabel#pageTitle {{font-size:21px;font-weight:700;}}
        QWidget#masterLedger QLabel#title {{font-size:15px;font-weight:700;}}
        QWidget#masterLedger QLabel#sectionTitle {{font-size:15px;font-weight:600;}}
        QWidget#masterLedger QLabel#muted {{font-size:11px;color:{c['muted']};}}
        QWidget#masterLedger QLabel#error {{font-size:11px;color:{c['error']};}}
        QWidget#ledgerStats {{border-top:1px solid {line};border-bottom:1px solid {line};}}
        QWidget#masterLedger QLabel#ledgerNotice {{background:{'#483c28' if dark else '#fff5e2'};color:{'#f4bf78' if dark else '#b97007'};border:1px solid {'#68573c' if dark else '#ffe0a4'};border-radius:4px;padding:7px;font-size:12px;}}
        QWidget#ledgerDetails {{border-left:1px solid {line};}}
        QWidget#masterLedger QTableWidget {{font-size:12px;background:{surface};border:1px solid {line};border-radius:3px;}}
        QWidget#masterLedger QHeaderView::section {{font-size:12px;font-weight:500;background:{field};color:{c['ink']};padding:9px 10px;border:none;border-bottom:1px solid {line};}}
        QWidget#masterLedger QListWidget {{font-size:12px;border:none;background:{surface};}}
        QWidget#masterLedger QPlainTextEdit {{font-size:11px;background:{field};color:{c['muted']};border:1px solid {line};border-radius:4px;padding:5px;}}
        QWidget#masterLedger QPushButton {{font-size:12px;min-height:30px;max-height:32px;padding:0 12px;border:1px solid {c['border']};border-radius:4px;background:{surface};color:{c['ink']};}}
        QWidget#masterLedger QPushButton:hover {{background:{c['hover']};}}
        QWidget#masterLedger QPushButton#ledgerPrimary {{background:{c['accent']};border:1px solid {c['accent']};color:{c['accent_text']};}}
        QWidget#masterLedger QPushButton::menu-indicator {{image:none;width:0px;height:0px;}}
        QWidget#masterLedger QPushButton:disabled {{background:{field};color:{c['disabled']};}}
        ''')
        self.table.viewport().update();self.materials.viewport().update()

    def showEvent(self,event):
        super().showEvent(event);self.refresh();self.timer.start()

    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)

    def refresh(self):
        if self._loading:return
        try:
            labels={b['id']:b['label'] for b in self.store.batches()}
            rows=[dict(r,batch_label=labels.get(r['batch_id'],r['batch_name'])) for r in self.store.rows()]
            if rows==self.rows and self.table.rowCount():return
            self._loading=True;self.rows=rows
            for combo,label,options in ((self.batch,'全部批次',sorted({(r['batch_id'],r['batch_name']) for r in rows},key=lambda p:p[0])),
                (self.category,'全部分类',[(n,'压缩包原件' if n=='原包待核对' else n) for n in sorted({r['package_name'] for r in rows})]),
                (self.state,'全部进度',[(n,n) for n in sorted({values(r)[5] for r in rows})])):
                old=combo.currentData();combo.blockSignals(True);combo.clear();combo.addItem(label,None)
                for key,text in options:combo.addItem(text,key)
                combo.setCurrentIndex(max(0,combo.findData(old)));combo.blockSignals(False)
            self._loading=False;self.filter_rows()
        except (OSError,ValueError,sqlite3.Error) as error:self._loading=False;self.error.setText(str(error))

    def selected(self):
        i=self.table.currentRow();item=self.table.item(i,0) if i>=0 else None
        return next((r for r in self.rows if item and r['id']==item.data(Qt.UserRole)),None)

    def filter_rows(self,*args):
        if self._loading:return
        old=self.selected();pid=old['id'] if old else None;scroll=self.table.verticalScrollBar().value();needle=self.search.text().strip().casefold()
        self.visible_rows=[r for r in self.rows if (not needle or needle in (r['name']+' '+f"WB-{r['id']:06d}").casefold()) and
            (self.batch.currentData() is None or r['batch_id']==self.batch.currentData()) and
            (self.category.currentData() is None or r['package_name']==self.category.currentData()) and
            (self.state.currentData() is None or values(r)[5]==self.state.currentData())]
        sorting=self.table.isSortingEnabled();self.table.blockSignals(True);self.table.setSortingEnabled(False);self.table.setRowCount(len(self.visible_rows))
        for i,row in enumerate(self.visible_rows):
            vals=values(row);vals[0]+=f"\nWB-{row['id']:06d}"
            for j,value in enumerate(vals):
                times=[m.get('created_at','') for m in row['materials'] if m.get('id')]+[z['created_at'] for z in row.get('original_zips',[])]
                key=(row['collection_count']/row['required_count'] if row['required_count'] else -1,row['collection_count'],row['required_count']) if j==3 else max(times,default='') if j==6 else value.casefold()
                item=SortItem(value,key);item.setData(Qt.UserRole,row['id']);item.setData(Qt.UserRole+1,(row['collection_count'],row['required_count']));item.setToolTip(row['batch_name'] if j==1 else value);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,58)
        self.table.setSortingEnabled(sorting);self.table.blockSignals(False)
        selected=next((i for i in range(self.table.rowCount()) if self.table.item(i,0).data(Qt.UserRole)==pid),0)
        if self.table.rowCount():self.table.selectRow(selected)
        self.table.verticalScrollBar().setValue(scroll);self.show_details()
        count=len(self.visible_rows);bad=sum(abnormal(r) for r in self.visible_rows);complete=sum(values(r)[5] in ('已收齐','原包已收录') and not abnormal(r) for r in self.visible_rows)
        self.statistics.setText(f'当前范围 {count} 项　　已收齐 {complete}　　待补材料 {count-complete-bad}　　异常 {bad}')
        for label,name,number,color in zip(self.stat_counts,('软件总数','已收纳','待整理','异常'),(count,complete,count-complete-bad,bad),('#009c95','#16ad52','#ed9307','#d6384a')):
            label.setText(f'{name}　<span style="font-size:19px;font-weight:600;color:{color}">{number}</span>')
        self.footer.setText(f'共 {len(self.rows)} 个软件任务 · 当前显示 {count} 项　｜　已同步 · 每 3 秒刷新 · 只读查看')

    def reset(self):
        self.search.clear()
        for c in (self.batch,self.category,self.state):c.setCurrentIndex(0)
        self.filter_rows()

    def show_details(self):
        row=self.selected();old=self.materials.currentItem();key=old.data(Qt.UserRole) if old else None
        self.materials.blockSignals(True);self.materials.clear();self.error.clear()
        if row:
            vals=values(row)
            self.software.setText(row['name']);self.identity.setText(f"WB-{row['id']:06d} · {row['batch_name']}\n{vals[2]}")
            vals=values(row);self.detail_summary.setText('已收录 '+vals[3]+' 份' if row['required_count'] else '原包已收录 · 不统计包内材料份数')
            notice=('文件异常，请查看保存位置或重新收录' if abnormal(row) else '待补：'+vals[4] if vals[4]!='—' else '')
            self.notice.setText(notice);self.notice.setVisible(bool(notice))
            for m in row['materials']:
                state=m.get('collection_issue') or ('已收录' if m.get('collection_saved') else '未收录')
                item=QListWidgetItem(m['kind']+'\n'+state+(f" · v{m['version_number']}" if m['id'] else ''));item.setData(Qt.UserRole,('material',m['kind']));item.setToolTip(m['kind']+'\n'+state+(f" · v{m['version_number']}" if m['id'] else ''));self.materials.addItem(item)
            for z in row.get('original_zips',[]):
                item=QListWidgetItem(Path(z['path']).name+'\n压缩包原件 · 包内材料未分类'+(' · 文件缺失' if not Path(z['path']).is_file() else ''));item.setData(Qt.UserRole,('zip',z['id']));item.setToolTip(z['path']);self.materials.addItem(item)
        else:self.software.setText('没有匹配的软件');self.identity.clear();self.detail_summary.clear();self.notice.hide()
        self.materials.setFixedHeight(max(39,min(234,self.materials.count()*39+2)))
        self.materials.blockSignals(False)
        selected=next((i for i in range(self.materials.count()) if self.materials.item(i).data(Qt.UserRole)==key),0)
        if self.materials.count():self.materials.setCurrentRow(selected)
        self.show_path()

    def target(self):
        from saved_locations import saved_file_location
        row=self.selected();item=self.materials.currentItem()
        if not row or not item:raise ValueError('请选择已保存的材料。')
        kind,key=item.data(Qt.UserRole)
        return saved_file_location(self.service,row['id'],kind=key if kind=='material' else None,zip_id=key if kind=='zip' else None)

    def show_path(self,*args):
        item=self.materials.currentItem();key=item.data(Qt.UserRole) if item else None
        try:path=self.target();self.path.setPlainText(str(path));self.path.setToolTip(str(path));valid=True
        except (ValueError,OSError,sqlite3.Error) as error:self.path.setPlainText(str(error));self.path.setToolTip(str(error));valid=False
        self.locate_button.setEnabled(valid);self.directory_button.setEnabled(valid);self.history_button.setEnabled(bool(key and key[0]=='material'))

    def open_saved(self,directory=False):
        from saved_locations import open_location
        try:
            row=self.selected();item=self.materials.currentItem();pid=row['id'] if row else None;key=item.data(Qt.UserRole) if item else None
            self.refresh();current=self.selected();chosen=self.materials.currentItem()
            if not current or current['id']!=pid or not chosen or chosen.data(Qt.UserRole)!=key:raise ValueError('所选任务或材料已变化，请重新选择。')
            target=self.target();open_location(target.parent if directory else target);self.error.clear()
        except (OSError,ValueError,sqlite3.Error) as error:self.error.setText(str(error))

    def show_history(self):
        row=self.selected();item=self.materials.currentItem()
        if not row or not item:return
        kind,key=item.data(Qt.UserRole)
        if kind!='material':return
        dialog=VersionHistoryDialog(self.service,row['id'],key,self)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def export(self,all_rows=False):
        target,_=QFileDialog.getSaveFileName(self,'导出全部软件' if all_rows else '导出当前筛选','软件材料总表.csv','CSV 表格 (*.csv)')
        if target:
            try:self.export_to(target,all_rows);self.error.setText('已导出：'+target)
            except OSError as error:self.error.setText(str(error))

    def export_to(self,target,all_rows=False):
        self.refresh();mapping={r['id']:r for r in self.rows};rows=self.rows if all_rows else [mapping[self.table.item(i,0).data(Qt.UserRole)] for i in range(self.table.rowCount())]
        def safe(v):return "'"+v if str(v).lstrip().startswith(('=','+','-','@')) else v
        with open(target,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['任务编号',*HEADERS])
            for row in rows:
                vals=values(row);vals[1]=row['batch_name'];writer.writerow([f"WB-{row['id']:06d}",*[safe(v) for v in vals]])
