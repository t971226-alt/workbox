"""Small native review steps connecting pasted names and saved materials to the ledger."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView
from workspace_chrome import chrome_button,WorkspaceComboBox
from theme import apply_to
from dialog_lifecycle import transient_dialog
from intake_link import recognition_summary,import_receipt


def note(text):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setWordWrap(True);return w


class ClassificationRuleDialog(QDialog):
    def __init__(self,service,parent=None,current=None):
        super().__init__(parent);self.service=service;self.setWindowTitle('选择名称分类规则');self.resize(540,280);apply_to(self)
        layout=QVBoxLayout(self);layout.addWidget(note('选择每个软件需要的材料清单；没有合适的就新建。'))
        self.rules=WorkspaceComboBox();self.rules.setAccessibleName('本次名称分类规则');layout.addWidget(self.rules)
        self.detail=note('');layout.addWidget(self.detail);self.rules.currentIndexChanged.connect(self.describe)
        self.new_button=chrome_button('新建规则…');self.new_button.clicked.connect(self.create_rule);layout.addWidget(self.new_button)
        layout.addWidget(note('下一步核对软件名称，确认后才建立任务。'))
        actions=QHBoxLayout();actions.addStretch();self.cancel_button=chrome_button('取消');self.confirm_button=chrome_button('使用此规则',variant='primary')
        actions.addWidget(self.cancel_button);actions.addWidget(self.confirm_button);layout.addLayout(actions)
        self.cancel_button.clicked.connect(self.reject);self.confirm_button.clicked.connect(self.confirm);self.reload(current)

    def reload(self,current=None):
        self.profiles=self.service.classifications.profiles(True);self.rules.clear()
        self.rules.addItem('请选择规则','')
        for p in self.profiles:self.rules.addItem(p['name'],p['name'])
        self.rules.setCurrentIndex(max(0,self.rules.findData(current)));self.describe()

    def describe(self,*_):
        p=next((p for p in getattr(self,'profiles',[]) if p['name']==self.rules.currentData()),None)
        self.detail.setText(('每个软件需要 '+str(len(p['kinds']))+' 份材料：'+ '、'.join(p['kinds'])) if p else '选择已有规则，或新建符合本批材料要求的规则。')
        if hasattr(self,'confirm_button'):self.confirm_button.setEnabled(bool(p))

    def create_rule(self):
        from quick_classification_ui import QuickClassificationDialog
        with transient_dialog(QuickClassificationDialog(self.service,self)) as d:
            chosen=d.editor.name.text().strip() if d.exec()==QDialog.Accepted else None
        self.reload(chosen)

    def confirm(self):
        p=next((p for p in self.service.classifications.profiles(True) if p['name']==self.rules.currentData()),None)
        if p:self.rule=p['name'];self.accept()
        else:self.reload()


class RecognitionSummaryDialog(QDialog):
    def __init__(self,plan,parent=None):
        super().__init__(parent);self.setWindowTitle('识别摘要 · 收录前核对');self.resize(530,260);apply_to(self);self.review_details=False
        layout=QVBoxLayout(self);layout.addWidget(note(recognition_summary(plan)))
        layout.addWidget(note('确认后按软件分类保存，并更新收录数量。原始文件保留。'))
        actions=QHBoxLayout();self.cancel_button=chrome_button('取消');self.details_button=chrome_button('核对明细');self.confirm_button=chrome_button('确认收录',variant='primary')
        actions.addWidget(self.cancel_button);actions.addStretch();actions.addWidget(self.details_button);actions.addWidget(self.confirm_button);layout.addLayout(actions)
        self.cancel_button.clicked.connect(self.reject);self.confirm_button.clicked.connect(self.accept);self.details_button.clicked.connect(self.details)

    def details(self):self.review_details=True;self.accept()


class IntakeReceiptDialog(QDialog):
    def __init__(self,page,result,parent=None):
        super().__init__(parent or page);self.page=page;self.result=result;self.setWindowTitle('本次收录结果');self.resize(900,510);apply_to(self)
        token=result.get('import_token');self.receipt=import_receipt(page.store,token) if token else result
        self.files=self.receipt.get('files',[]);layout=QVBoxLayout(self)
        paused=sum(f['state']=='paused' for f in self.files);obsolete=sum(f['state']=='superseded' for f in self.files);failed=sum(f['state'] not in ('complete','paused','superseded') for f in self.files);complete=len(self.files)-failed-paused-obsolete
        software_count=len(set(f['project_id'] for f in self.files)) or len(set(result.get('project_ids',[])))
        summary=(f'压缩包原件保存 {complete} 份，未完成 {failed} 份，暂停 {paused} 份，旧项停止 {obsolete} 份。包内材料未分类。' if result.get('raw_zip_intake') else f'成功收录 {complete} 份，未完成 {failed} 份，已暂停 {paused} 份，旧项停止 {obsolete} 份；涉及 {software_count} 个软件。原件、旧版本和历史记录保留。')
        if not self.files:summary+= '尚未收录材料。'
        layout.addWidget(note(summary))
        self.table=QTableWidget(len(self.files),4);self.table.setHorizontalHeaderLabels(['软件 / 材料','结果','实际保存位置','未完成原因'])
        self.table.setWordWrap(False);self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for i,f in enumerate(self.files):
            for col,value in enumerate((f['name']+' / '+f['kind'],'已收录' if f['state']=='complete' else '收录失败' if f['state']=='failed' else '已被新版替代' if f['state']=='superseded' else '已暂停' if f['state']=='paused' else '需核对来源' if f['state']=='source_changed' else '待重试',f['saved_path'],f['error'])):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,col,item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,250);self.table.setColumnWidth(2,280);layout.addWidget(self.table,1)
        self.error=note('');layout.addWidget(self.error)
        actions=QHBoxLayout();self.locate=chrome_button('定位软件');self.open=chrome_button('打开保存目录');self.retry=chrome_button('重试选中失败项');self.undo=chrome_button('撤销本次新增任务…');close=chrome_button('关闭')
        for b in (self.locate,self.open,self.retry,self.undo,close):actions.addWidget(b)
        layout.addLayout(actions);self.locate.clicked.connect(self.focus_task);self.open.clicked.connect(self.open_saved);self.retry.clicked.connect(self.retry_selected);self.undo.clicked.connect(self.undo_created);close.clicked.connect(self.accept)
        self.undo.setVisible(bool(result.get('created_project_ids')));self.undo.setToolTip('仅将本次新建任务移入已删除；材料和历史保留，可恢复。已更新的原任务不受影响。')
        self.retry.setEnabled(bool(failed));self.table.selectRow(0)

    def selected(self):
        row=self.table.currentRow();return self.files[row] if 0<=row<len(self.files) else None

    def focus_task(self):
        f=self.selected()
        if f:self.page.focus_intake_projects([f['project_id']]);self.accept()

    def open_saved(self):
        from saved_locations import collection_location,open_location
        f=self.selected()
        if not f:return
        try:
            if not f['saved_path']:raise ValueError('此历史记录没有可确认的保存位置。')
            from pathlib import Path
            if not Path(f['saved_path']).is_file():raise ValueError('本次保存的文件已移动或缺失，请核对原保存位置。')
            open_location(f['saved_path'])
        except (ValueError,OSError) as e:self.error.setText(str(e))

    def retry_selected(self):
        f=self.selected()
        if f and f['state']=='superseded':self.error.setText('已有新版材料，旧失败项停止重试；如需使用旧文件，请重新拖入并选择替换。');return
        if not f or f['state']=='complete':self.error.setText('请选择一份未完成材料；成功文件不会重复处理。');return
        if not f['project_id']:self.error.setText('此文件尚未建立任务；请修复 ZIP 后重新拖入，或重新选择所属任务。');return
        if f['state']=='paused':self.error.setText('请先恢复任务后再重试。');return
        from production import source_digest
        digest=source_digest(f['source_path']);accepted=None
        if digest and (not f.get('source_sha256') or digest!=f['source_sha256']):
            from PySide6.QtWidgets import QMessageBox
            question='源文件内容已变化。' if f.get('source_sha256') else '旧记录没有源文件校验信息。'
            box=QMessageBox(self);box.setWindowTitle('核对重试来源');box.setText(question+'是否将当前文件作为本次重试来源？\n'+f['source_path']);box.setDetailedText('当前 SHA256：'+digest)
            confirm=box.addButton('确认使用当前文件',QMessageBox.AcceptRole);cancel=box.addButton('取消',QMessageBox.RejectRole);box.setDefaultButton(cancel);box.exec()
            if box.clickedButton()!=confirm:return
            accepted=digest
        self.page.request('重试指定材料',lambda **kw:self.page.store.retry_import_files(self.result['import_token'],project_id=f['project_id'],source_path=f['source_path'],accepted_source=accepted,**kw));self.accept()

    def undo_created(self):
        ids=set(self.result.get('created_project_ids',[]));rows=[r for r in self.page.store.rows() if r['id'] in ids]
        if rows:self.page.delete_tasks(rows);self.accept()


class ReclassificationDialog(QDialog):
    def __init__(self,preview,parent=None):
        super().__init__(parent);self.setWindowTitle('重新识别分类 · 核对后调整');self.resize(650,410);apply_to(self)
        layout=QVBoxLayout(self);layout.addWidget(note('软件：'+preview['name']+'\n原分类：'+preview['package']+'。仅调整这一条任务，不新增重复行。'))
        self.rules=WorkspaceComboBox()
        for p in preview['profiles']:self.rules.addItem(p['name'],p['name'])
        self.rules.setCurrentIndex(max(0,self.rules.findData(preview['selected'])));layout.addWidget(self.rules)
        from PySide6.QtWidgets import QPlainTextEdit
        details=QPlainTextEdit();details.setReadOnly(True);details.setPlainText('\n'.join(m['old_kind']+' → '+m['kind'] for m in preview['mapping']));layout.addWidget(details)
        layout.addWidget(note('原文件、旧分类材料和历史记录保留。按新规则整理已收文件；未提供的必需材料继续显示缺失。'))
        actions=QHBoxLayout();actions.addStretch();cancel=chrome_button('取消');self.confirm_button=chrome_button('按此规则重新分类',variant='primary');actions.addWidget(cancel);actions.addWidget(self.confirm_button);layout.addLayout(actions)
        cancel.clicked.connect(self.reject);self.confirm_button.clicked.connect(self.accept)

