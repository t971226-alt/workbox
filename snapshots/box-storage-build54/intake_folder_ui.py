"""Compact folder + material list setup shared by intake sheets."""
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QCheckBox,
    QSpinBox,QFileDialog,QFrame)
from workspace_chrome import WorkspaceComboBox,chrome_button
from file_families import TYPES
from rule_chrome import RuleTypes
from intake_folders import title,folder_name,type_draft,type_info

class FolderPanel(QWidget):
    changed=Signal();created=Signal(str,object,bool);rule_changed=Signal(str,object)
    def __init__(self,service,profiles,parent=None):
        super().__init__(parent);self.service=service;self.profiles={p['name']:deepcopy(p) for p in profiles};self.items={};self.counts={};self.edit_id=None;self.loading=False
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(6)
        line=QHBoxLayout();line.addWidget(QLabel('保存到'));self.root=QLineEdit(str(service.workspaces.settings()['work_root']));self.root.setReadOnly(True);line.addWidget(self.root,1)
        browse=chrome_button('选择位置');browse.clicked.connect(self.browse);line.addWidget(browse)
        self.toggle=chrome_button('保存文件夹');self.toggle.setCheckable(True);self.toggle.setChecked(False);line.addWidget(self.toggle);outer.addLayout(line)
        self.toggle.setProperty('expandableGreen',True)
        self.body=QWidget();body=QVBoxLayout(self.body);body.setContentsMargins(0,0,0,0);body.setSpacing(6);outer.addWidget(self.body);self.body.hide();self.toggle.toggled.connect(self.body.setVisible);self.toggle.toggled.connect(self.changed)
        self.rows=QVBoxLayout();self.rows.setSpacing(5);body.addLayout(self.rows)
        add=chrome_button('新建保存文件夹',icon='plus');add.clicked.connect(lambda:self.open_editor());body.addWidget(add,alignment=Qt.AlignLeft)
        self.editor=QFrame();self.editor.setObjectName('folderEditor');edit=QVBoxLayout(self.editor);edit.setContentsMargins(8,7,8,7);edit.setSpacing(5);self.editor.hide();body.addWidget(self.editor)
        self.folder=QLineEdit();self.folder.setMaxLength(80);self.folder.setPlaceholderText('例如：运行交付');self.choice=WorkspaceComboBox()
        for caption,control in [('文件夹名称',self.folder),('要收哪些文件',self.choice)]:
            row=QHBoxLayout();label=QLabel(caption);label.setFixedWidth(86);row.addWidget(label);row.addWidget(control,1);edit.addLayout(row)
        self.types_label=QLabel();self.types_label.setWordWrap(True);edit.addWidget(self.types_label)
        self.type_rows=QVBoxLayout();self.type_rows.setSpacing(4);edit.addLayout(self.type_rows)
        buttons=QHBoxLayout()
        for family in ('word','pdf','archive','sheet'):
            from intake_controls import type_icon
            button=chrome_button('+ '+TYPES[family][0]);button.setIcon(type_icon(TYPES[family][1]));button.clicked.connect(lambda checked=False,f=family:self.add_type(f));buttons.addWidget(button)
        more=WorkspaceComboBox();more.addItem('更多类型…',None)
        for family in ('slides','text','image','video','audio'):more.addItem(TYPES[family][0],family)
        for ext in ('.exe','.db','.sql'):more.addItem(type_info(ext)[0],ext)
        more.currentIndexChanged.connect(lambda _:self.more_type(more));buttons.addWidget(more);edit.addLayout(buttons)
        self.remember=QCheckBox('记住这套要求，下次直接使用');self.remember.setChecked(True);edit.addWidget(self.remember)
        self.name_toggle=chrome_button('修改清单名称（默认与文件夹同名）');self.name_toggle.setCheckable(True);edit.addWidget(self.name_toggle,alignment=Qt.AlignLeft)
        self.name_toggle.setProperty('expandableGreen',True)
        self.rule_name=QLineEdit();self.rule_name.setMaxLength(40);self.rule_name.setPlaceholderText('留空时使用文件夹名称');edit.addWidget(self.rule_name);self.rule_name.hide();self.name_toggle.toggled.connect(self.rule_name.setVisible)
        self.remember.toggled.connect(lambda checked:(self.name_toggle.setVisible(checked),self.rule_name.setVisible(checked and self.name_toggle.isChecked())))
        self.apply=QCheckBox('用于当前勾选的软件');self.apply.setChecked(True);edit.addWidget(self.apply)
        self.preview=QLabel();self.preview.setWordWrap(True);edit.addWidget(self.preview)
        self.error=QLabel();self.error.setWordWrap(True);edit.addWidget(self.error);self.error.hide()
        actions=QHBoxLayout();actions.addStretch();cancel=chrome_button('取消');cancel.clicked.connect(self.cancel_editor);actions.addWidget(cancel);self.save=chrome_button('创建并使用',variant='primary');self.save.clicked.connect(self.save_editor);actions.addWidget(self.save);edit.addLayout(actions)
        self.choice.currentIndexChanged.connect(self.refresh_types);self.folder.textChanged.connect(self.refresh_preview);self.root.textChanged.connect(self.root_changed)
        self.load()
    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'选择材料保存位置',self.root.text())
        if path:self.root.setText(path)
    def cancel_editor(self):self.editor.hide();self.changed.emit()
    def load(self):
        from intake_folders import records
        self.items={r['id']:r for r in records(self.service,self.root.text())}
        for r in self.items.values():
            current=self.profiles.get(r['rule']['name'])
            if current:r['rule']=deepcopy(current)
            else:self.profiles.setdefault(r['rule']['name'],r['rule'])
        self.refresh_rows()
    def root_changed(self):
        self.changed.emit();self.refresh_preview()
    def ensure(self,profile):
        key='__root__'
        if key not in self.items:self.items[key]=dict(id=key,name='',rule=deepcopy(profile),_direct=True)
        return key
    def refresh_rows(self):
        self.loading=True
        while self.rows.count():
            item=self.rows.takeAt(0)
            if item.widget():item.widget().deleteLater()
        for key,r in self.items.items():
            if r.get('_direct'):continue
            widget=QWidget();line=QHBoxLayout(widget);line.setContentsMargins(0,0,0,0);name=QLineEdit(r['name']);name.setToolTip('保存文件夹名称；修改后仅用于本次新副本。');line.addWidget(name,1)
            choice=WorkspaceComboBox()
            for p in self.profiles.values():choice.addItem(title(p['name']),p['name'])
            if choice.findData(r['rule']['name'])<0:choice.addItem(title(r['rule']['name']),r['rule']['name'])
            choice.addItem('自己添加文件类型…','__new__');choice.setCurrentIndex(choice.findData(r['rule']['name']));line.addWidget(choice,1)
            name.textEdited.connect(lambda text,k=key:self.rename(k,text));choice.currentIndexChanged.connect(lambda _,k=key,c=choice:self.select_rule(k,c));self.rows.addWidget(widget)
        self.loading=False
    def rename(self,key,text):self.items[key]['name']=text;self.items[key]['_auto']=False;self.changed.emit()
    def select_rule(self,key,combo):
        if self.loading:return
        if combo.currentData()=='__new__':self.open_editor(key);return
        self.items[key]['rule']=deepcopy(self.profiles[combo.currentData()]);self.items[key]['_auto']=False;self.rule_changed.emit(key,self.items[key]['rule']);self.changed.emit()
    def open_editor(self,key=None):
        self.edit_id=key;self.counts={};self.toggle.setChecked(True);self.editor.show();self.error.hide();self.folder.setReadOnly(bool(key));self.folder.setText(self.items[key]['name'] if key else '');self.rule_name.clear();self.remember.setChecked(True);self.name_toggle.setChecked(False)
        self.choice.blockSignals(True);self.choice.clear();self.choice.addItem('自己添加文件类型',None)
        for p in self.profiles.values():self.choice.addItem(title(p['name']),p['name'])
        self.choice.setCurrentIndex(0);self.choice.blockSignals(False);self.save.setText('保存设置' if key else '创建并使用');self.apply.setChecked(key is None);self.refresh_types();self.refresh_preview()
    def more_type(self,combo):
        family=combo.currentData()
        if family:self.add_type(family);combo.setCurrentIndex(0)
    def add_type(self,family):self.counts[family]=self.counts.get(family,0)+1;self.refresh_types()
    def refresh_types(self,*_):
        while self.type_rows.count():
            item=self.type_rows.takeAt(0)
            if item.widget():item.widget().deleteLater()
        base=self.profiles.get(self.choice.currentData());total=len(base['kinds']) if base else 0
        for family,count in self.counts.items():
            from rule_chrome import FileBadge
            caption,ext=type_info(family);row=QWidget();line=QHBoxLayout(row);line.setContentsMargins(0,0,0,0);line.addWidget(FileBadge(ext));line.addWidget(QLabel(caption),1);spin=QSpinBox();spin.setRange(1,40);spin.setValue(count);line.addWidget(spin);line.addWidget(QLabel('份'));remove=chrome_button('×');line.addWidget(remove)
            spin.valueChanged.connect(lambda n,f=family:self.set_count(f,n));remove.clicked.connect(lambda checked=False,f=family:self.remove_type(f));self.type_rows.addWidget(row);total+=count
        self.types_label.setText((title(base['name'])+' · ' if base else '')+('共需 '+str(total)+' 份材料' if total else '点击类型添加，每类可设置份数；同类型兼容不同后缀。'))
        self.changed.emit()
    def set_count(self,family,count):
        self.counts[family]=count;base=self.profiles.get(self.choice.currentData());total=(len(base['kinds']) if base else 0)+sum(self.counts.values());self.types_label.setText((title(base['name'])+' · ' if base else '')+'共需 '+str(total)+' 份材料')
    def remove_type(self,family):self.counts.pop(family,None);self.refresh_types()
    def refresh_preview(self,*_):self.preview.setText('保存位置：'+str(Path(self.root.text())/(self.folder.text() or '文件夹名称')/'软件名称'))
    def save_editor(self):
        from intake_folders import validate_records
        try:
            name=self.folder.text().strip();base=self.profiles.get(self.choice.currentData());is_new=not base or bool(self.counts)
            rule_name=self.rule_name.text().strip() or name[:40]
            if is_new:
                if not self.rule_name.text().strip():
                    n=2
                    while rule_name in self.profiles:rule_name=name[:34]+'（'+str(n)+'）';n+=1
                elif rule_name in self.profiles:raise ValueError('已有同名材料清单，请直接选用或修改名称。')
                rule=type_draft(rule_name,self.counts,base)
            else:rule=deepcopy(base)
            key=self.edit_id or uuid4().hex
            proposed=[r for k,r in self.items.items() if k!=key and not r.get('_direct')]+[dict(name=name,rule=rule)]
            validate_records(self.service,self.root.text(),proposed)
            rule['_remember']=self.remember.isChecked() if is_new else True
            self.items[key]=dict(id=key,name=name,rule=deepcopy(rule));self.profiles[rule['name']]=rule;self.editor.hide();self.refresh_rows();self.created.emit(key,deepcopy(rule),self.apply.isChecked());self.rule_changed.emit(key,deepcopy(rule));self.changed.emit()
        except (ValueError,OSError,TypeError) as e:self.error.setText(str(e));self.error.show()
    def snapshot(self,used=None):
        from intake_folders import validate_records
        if self.editor.isVisible():raise ValueError('请先点击“创建并使用”保存当前文件夹设置，或取消这次编辑。')
        return validate_records(self.service,self.root.text(),[r for k,r in self.items.items() if not r.get('_direct') and (not r.get('_auto') or used is None or k in used)])
