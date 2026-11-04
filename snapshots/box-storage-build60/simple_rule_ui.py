"""Small type/count presentation backed by the existing durable rule model."""
from pathlib import Path
from collections import OrderedDict
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QSpinBox,QCheckBox
from workspace_chrome import chrome_button,WorkspaceComboBox
from intake_controls import TypeCombo
from file_families import TYPES

PLACEHOLDER='可不填，多关键词分隔请用：逗号/空格/+（例：代码+手册）'


class SimpleRulePresentation(QWidget):
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.loading=False;self.signature=None;self.controls=[];self.expanded=set()
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        line=QHBoxLayout();line.addWidget(QLabel('规则名称'));line.addWidget(editor.name,1);layout.addLayout(line)
        layout.addWidget(QLabel('收哪些文件'))
        self.rows=QVBoxLayout();self.rows.setSpacing(5);layout.addLayout(self.rows)
        line=QHBoxLayout();line.addWidget(QLabel('＋ 添加文件类型'));self.add=WorkspaceComboBox();self.add.addItem('选择类型…',None)
        for family,(title,ext,_) in TYPES.items():
            if not editor.archive_only or family=='archive':self.add.addItem(title,ext)
        if not editor.archive_only:self.add.addItem('其他格式…','__other__')
        line.addWidget(self.add,1);layout.addLayout(line);self.add.currentIndexChanged.connect(self.add_selected)
        self.note=QLabel('自动按本次软件名称分组；同类后缀均支持。多份匹配时，由你选择。');self.note.setWordWrap(True);self.note.setStyleSheet('font-size:11px;');layout.addWidget(self.note)
        self.example=chrome_button('查看固定示例 ›');self.example.setCheckable(True);layout.addWidget(self.example,alignment=Qt.AlignLeft)
        self.example.setProperty('expandableGreen',True)
        self.example_text=QLabel('软件名称：全源人才聚合研判招聘系统\n第1份不填：全源人才聚合研判招聘系统.docx\n第2份填代码：全源人才聚合研判招聘系统_代码.docx\n逗号、空格或 + 分隔多个关键词，需全部包含，顺序不限。');self.example_text.setWordWrap(True);self.example_text.setStyleSheet('font-size:11px;');self.example_text.hide();layout.addWidget(self.example_text);self.example.toggled.connect(self.example_text.setVisible);self.example.toggled.connect(editor.fit_window)
        editor.changed.connect(self.refresh);self.refresh()

    def groups(self):
        result=OrderedDict();e=self.editor
        for i in range(e.table.rowCount()):
            c=e.table.cellWidget(i,1);key=(c.family() or c.suffix(),c.strict,c.suffix() if c.strict else '')
            result.setdefault(key,[]).append(i)
        return list(result.values())

    def refresh(self):
        if self.loading:return
        groups=self.groups();signature=repr([(indices,self.editor.table.cellWidget(indices[0],1).suffix(),self.editor.table.cellWidget(indices[0],1).strict,[self.editor.table.cellWidget(i,3).pattern() for i in indices]) for indices in groups])
        if signature==self.signature:return
        self.signature=signature;self.loading=True
        try:
            while self.rows.count():
                item=self.rows.takeAt(0)
                if item.widget():item.widget().hide();item.widget().deleteLater()
            self.controls=[]
            for indices in groups:self.add_group(indices)
        finally:self.loading=False
        if type(self.editor.window()).__name__=='QuickClassificationDialog':
            self.editor.window()._intake_width=650 if any(c['toggle'].isChecked() for c in self.controls) else 450
        QTimer.singleShot(0,self.editor.fit_window)

    def add_group(self,indices):
        e=self.editor;widget=QWidget();box=QVBoxLayout(widget);box.setContentsMargins(0,2,0,3);box.setSpacing(4)
        line=QHBoxLayout();source=e.table.cellWidget(indices[0],1);combo=TypeCombo(archive_only=e.archive_only);combo.set_suffix(source.suffix(),strict=source.strict);line.addWidget(combo,1)
        count=QSpinBox();count.setRange(1,40);count.setValue(len(indices));count.setFixedWidth(54);line.addWidget(count);line.addWidget(QLabel('份'));remove=chrome_button('×');line.addWidget(remove);box.addLayout(line)
        toggle=chrome_button('关键词（可选，用于区分同类文件） ›');toggle.setCheckable(True);box.addWidget(toggle,alignment=Qt.AlignLeft)
        toggle.setProperty('expandableGreen',True)
        body=QWidget();rows=QVBoxLayout(body);rows.setContentsMargins(0,0,0,0);rows.setSpacing(4);box.addWidget(body)
        for position,i in enumerate(indices):
            p=e.table.cellWidget(i,3).pattern();terms=p.get('terms',[]);value=' + '.join(t for t in terms if t!='{software}') if p['mode']=='keywords' else p.get('marker','')
            row=QHBoxLayout();row.addWidget(QLabel('第 '+str(position+1)+' 份'));token=QLabel('软件名称');token.setToolTip('自动代入本次收录的软件名称');row.addWidget(token);row.addWidget(QLabel('+'))
            edit=QLineEdit(value);edit.setPlaceholderText(PLACEHOLDER);edit.setToolTip(PLACEHOLDER+'；多个关键词需全部包含。');row.addWidget(edit,1);rows.addLayout(row)
            edit.editingFinished.connect(lambda i=i,w=edit:self.keyword(i,w.text()))
        hint=QLabel('不填：文件名与软件名相同；填写：同时包含软件名和所填关键词。');hint.setWordWrap(True);hint.setStyleSheet('font-size:11px;');rows.addWidget(hint)
        plain=chrome_button('不区分用途，仅按类型收录');rows.addWidget(plain,alignment=Qt.AlignLeft)
        enabled=tuple(indices) in self.expanded;body.setVisible(enabled);toggle.setChecked(enabled)
        toggle.toggled.connect(body.setVisible);toggle.toggled.connect(lambda checked,ids=indices:self.expand(ids,checked));toggle.toggled.connect(e.fit_window)
        plain.clicked.connect(lambda checked=False,ids=indices:self.type_only(ids))
        combo.currentIndexChanged.connect(lambda _,ids=indices,w=combo:self.format_changed(ids,w))
        count.valueChanged.connect(lambda n,ids=indices:self.count_changed(ids,n))
        remove.clicked.connect(lambda checked=False,ids=indices:self.remove(ids))
        self.controls.append(dict(indices=indices,type=combo,count=count,toggle=toggle,body=body));self.rows.addWidget(widget)

    def keyword(self,i,value):
        from name_patterns import keyword_terms,SOFTWARE_TOKEN
        terms=[t for t in keyword_terms(value) if t!=SOFTWARE_TOKEN]
        self.editor.table.cellWidget(i,3).set_pattern(dict(mode='keywords',terms=[SOFTWARE_TOKEN,*terms]) if terms else dict(mode='same'))

    def expand(self,indices,checked):
        if checked:self.expanded.add(tuple(indices))
        else:self.expanded.discard(tuple(indices))
        if checked:
            self.loading=True
            try:
                for i in indices:
                    if self.editor.table.cellWidget(i,3).pattern()['mode']=='type':self.keyword(i,'')
            finally:self.loading=False
            self.refresh()

    def type_only(self,indices):
        self.expanded.discard(tuple(indices))
        self.loading=True
        try:
            for i in indices:self.editor.table.cellWidget(i,3).set_pattern(dict(mode='type'))
        finally:self.loading=False
        self.signature=None;self.refresh();self.editor.changed.emit()

    def add_selected(self):
        value=self.add.currentData()
        if not value:return
        if value=='__other__':
            from PySide6.QtWidgets import QInputDialog
            import re
            value,ok=QInputDialog.getText(self,'其他格式','文件后缀，例如 dwg：')
            if not ok or not re.fullmatch(r'[a-zA-Z0-9]{1,12}',value.strip().lstrip('.')):self.add.setCurrentIndex(0);return
            value='.'+value.strip().lstrip('.').lower()
        self.editor.add(value);i=self.editor.table.rowCount()-1;self.editor.table.cellWidget(i,3).set_pattern(dict(mode='type'));self.add.setCurrentIndex(0)

    def format_changed(self,indices,combo):
        self.loading=True
        try:
            for i in indices:self.editor.table.cellWidget(i,1).set_suffix(combo.suffix(),strict=combo.strict)
        finally:self.loading=False
        self.editor.changed.emit()

    def count_changed(self,indices,n):
        e=self.editor;self.loading=True
        try:
            if n>len(indices):
                ext=e.table.cellWidget(indices[0],1).suffix()
                for _ in range(n-len(indices)):
                    e.add(ext);e.table.cellWidget(e.table.rowCount()-1,3).set_pattern(dict(mode='type'))
            else:
                for i in reversed(indices[n:]):e.table.removeRow(i)
        finally:self.loading=False
        e.changed.emit()

    def remove(self,indices):
        self.loading=True
        try:
            for i in reversed(indices):self.editor.table.removeRow(i)
        finally:self.loading=False
        self.editor.changed.emit()


def install(editor):
    from PySide6.QtWidgets import QWidget,QVBoxLayout
    hidden=QWidget(editor);hidden.setLayout(editor.layout());hidden.hide();editor._legacy_rule_controls=hidden
    editor.simple_presentation=SimpleRulePresentation(editor)
    layout=QVBoxLayout(editor);layout.setContentsMargins(0,0,0,0);layout.addWidget(editor.simple_presentation)
    editor.table.hide();editor.simple_row.hide();editor.tools.hide()
