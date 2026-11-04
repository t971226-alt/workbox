"""Simple storage overview with a bounded, reference-checked staging cleanup."""
from pathlib import Path
from threading import Event
from PySide6.QtCore import QThread,Signal,Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView
from workspace_chrome import chrome_button
from theme import apply_to


def size(value):
    return f'{value/1024**3:.2f} GB' if value>=1024**3 else f'{value/1024**2:.1f} MB' if value>=1024**2 else f'{value/1024:.1f} KB' if value>=1024 else f'{value} B'


class StorageJob(QThread):
    ready=Signal(object);failed=Signal(str);areaReady=Signal(object)
    def __init__(self,service,remove,parent):super().__init__(parent);self.service=service;self.remove=remove;self.cancel=Event()
    def run(self):
        try:
            from storage_management import describe,cleanup
            result=cleanup(self.service,cancel_event=self.cancel) if self.remove else None
            rows=describe(self.service,cancel_event=self.cancel,progress=self.areaReady.emit);preview=cleanup(self.service,dry_run=True,cancel_event=self.cancel)
            self.ready.emit(dict(rows=rows,preview=preview,cleanup=result))
        except Exception as error:self.failed.emit(str(error))


class StorageManagementDialog(QDialog):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;self.job=None;self.rows=[];self.pending_close=False;self.setWindowTitle('存储管理');self.resize(660,355);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(12,10,12,10);layout.setSpacing(7)
        hint=QLabel('暂存随材料目录保存。双击一行可打开对应目录。');hint.setWordWrap(True);layout.addWidget(hint)
        self.table=QTableWidget(4,4);self.table.setHorizontalHeaderLabels(['用途','文件','占用','位置']);self.table.verticalHeader().hide();self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);self.table.setColumnWidth(0,125);self.table.setColumnWidth(1,55);self.table.setColumnWidth(2,80);self.table.setWordWrap(False);layout.addWidget(self.table)
        for i,name in enumerate(['导入暂存区','材料处理区','归档与历史版本','正式材料']):
            for j,value in enumerate([name,'…','…','正在统计']):self.table.setItem(i,j,QTableWidgetItem(value))
            self.table.setRowHeight(i,30)
        self.path=QLineEdit(self);self.path.setReadOnly(True);self.path.setAccessibleName('所选存储目录');self.path.hide();self.table.itemSelectionChanged.connect(self.selected);self.table.cellDoubleClicked.connect(lambda *_:self.open_selected())
        self.status=QLabel('正在统计，可随时关闭…');self.status.setWordWrap(True);layout.addWidget(self.status)
        note=QLabel('仅清理无引用的过期暂存；正式材料和历史版本保留。');note.setToolTip('过期暂存：超过一天、当前未使用且没有收录或重试记录引用的文件。');note.setWordWrap(True);layout.addWidget(note)
        line=QHBoxLayout();self.open_button=chrome_button('打开目录');self.refresh_button=chrome_button('刷新');self.clean_button=chrome_button('清理暂存',variant='primary');close=chrome_button('关闭')
        for b in (self.open_button,self.refresh_button,self.clean_button,close):line.addWidget(b)
        layout.addLayout(line);self.open_button.clicked.connect(self.open_selected);self.refresh_button.clicked.connect(lambda:self.start(False));self.clean_button.clicked.connect(lambda:self.start(True));close.clicked.connect(self.reject);self.start(False)

    def start(self,remove):
        if self.job and self.job.isRunning():return
        for b in (self.open_button,self.refresh_button,self.clean_button):b.setEnabled(False)
        if self.job:self.job.deleteLater()
        self.job=StorageJob(self.service,remove,self);self.job.ready.connect(self.loaded);self.job.areaReady.connect(self.area_loaded);self.job.failed.connect(self.failed);self.job.finished.connect(self.job_finished);self.job.start()

    def area_loaded(self,row):
        names=['导入暂存区','材料处理区','归档与历史版本','正式材料'];i=names.index(row['name'])
        for j,value in enumerate([row['name'],str(row['files']),size(row['bytes']),row['paths'][0]]):
            item=QTableWidgetItem(value);item.setToolTip('；'.join(row['paths']) if j==3 else value);self.table.setItem(i,j,item)

    def loaded(self,result):
        self.rows=result['rows'];self.table.setRowCount(len(self.rows))
        for i,row in enumerate(self.rows):
            for j,text in enumerate([row['name'],str(row['files']),size(row['bytes']),row['paths'][0]]):
                item=QTableWidgetItem(text);item.setToolTip(text);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,30)
        self.table.selectRow(0);preview=result['preview'];done=result['cleanup']
        message=f"可释放 {preview['files']} 个文件，约 {size(preview['bytes'])}。"
        if done:message=f"已清理 {done['files']} 个文件，释放 {size(done['bytes'])}。"+message
        if preview['errors'] or (done and done['errors']):message+='部分位置不可访问，已保留。'
        self.status.setText(message);self.open_button.setEnabled(True);self.refresh_button.setEnabled(True);self.clean_button.setEnabled(bool(preview['files']))
        self.selected()

    def selected(self):
        i=self.table.currentRow()
        if 0<=i<len(self.rows):self.path.setText(self.rows[i]['paths'][0]);self.path.setToolTip('；'.join(self.rows[i]['paths']));self.path.setCursorPosition(0)

    def failed(self,message):self.status.setText(message);self.refresh_button.setEnabled(True)
    def job_finished(self):
        if self.pending_close:super().reject()

    def reject(self):
        if self.job and self.job.isRunning():self.pending_close=True;self.job.cancel.set();self.hide();return
        super().reject()

    def closeEvent(self,event):
        if self.job and self.job.isRunning():event.ignore();self.reject()
        else:super().closeEvent(event)

    def open_selected(self):
        from saved_locations import open_location
        i=self.table.currentRow()
        if not 0<=i<len(self.rows):return
        paths=[Path(p) for p in self.rows[i]['paths']];path=next((p for p in paths if p.is_dir()),None)
        if path is None:self.status.setText('此区域尚未生成文件。');return
        try:open_location(path)
        except (ValueError,OSError) as error:self.status.setText(str(error))


def show_storage(service,parent=None):
    dialog=StorageManagementDialog(service,parent)
    try:dialog.exec()
    finally:
        if dialog.job and dialog.job.isRunning():dialog.job.finished.connect(dialog.deleteLater)
        else:dialog.deleteLater()
