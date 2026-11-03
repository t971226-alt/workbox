"""Explicit first receipt and leftover handling inside a software group."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QCheckBox
from workspace_chrome import WorkspaceComboBox,chrome_button
from file_families import family_for
from file_formats import recognizable


def known_format(path):
    path=Path(path)
    return bool(family_for(path.suffix) or recognizable(path) or path.suffix.casefold() in {'.exe','.db','.sql'})


class FirstReceipt(QWidget):
    def __init__(self,view,group,first):
        super().__init__();self.view=view;self.g=group;self.first=first;self.confirmed=False;self.initializing=True
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(5)
        self.title=QLabel('首次收录 · 请确认收哪些文件、放在哪里' if first else '还有文件未安排 · 请选择一起保存或本次跳过');self.title.setWordWrap(True);layout.addWidget(self.title)
        self.editor=None
        if first:
            from quick_classification_ui import QuickRuleEditor
            from name_patterns import suggest
            entries=[view.engine.proposal['entries'][i] for i in group['indices'] if known_format(view.engine.proposal['entries'][i]['path'])]
            self.editor=QuickRuleEditor([],self);layout.addWidget(self.editor)
            if entries:self.editor.apply_suggestion(suggest(entries)[0])
            else:self.editor.table.setRowCount(0)
            self.editor.name.setText(view.profile(group)['name'] if view.profile(group)['name']!='__new__' else '本次收纳')
            self.editor.changed.connect(self.invalidate)
        row=QHBoxLayout();row.addWidget(QLabel('放哪里'));self.folder=QLineEdit();self.folder.setPlaceholderText('选择已有目录，或输入新目录名称');row.addWidget(self.folder,1);layout.addLayout(row)
        self.existing=WorkspaceComboBox();self.existing.addItem('新目录名称，或直接使用当前保存位置',None)
        for key,item in view.folders.items.items():
            if not item.get('_direct'):self.existing.addItem(item['name'],key)
        layout.addWidget(self.existing);self.existing.currentIndexChanged.connect(self.select_folder)
        self.remember=QCheckBox('保存规则并绑定目录，下次自动匹配');self.remember.setChecked(True);self.remember.setVisible(first);layout.addWidget(self.remember)
        self.choices={}
        for i in group['indices']:
            entry=view.engine.proposal['entries'][i]
            if known_format(entry['path']) and (first or view.engine.chosen_kind(i)):continue
            row=QHBoxLayout();caption=QLabel(Path(entry['path']).name);caption.setWordWrap(True);row.addWidget(caption,1)
            choice=WorkspaceComboBox();choice.addItem('未安排：请选择',None);choice.addItem('按原文件一起保存','keep');choice.addItem('本次跳过','skip');row.addWidget(choice);layout.addLayout(row);self.choices[i]=choice;choice.currentIndexChanged.connect(self.invalidate)
        note=QLabel('未识别类型按原文件保存，不转换内容，也不自动加入通用规则。');note.setWordWrap(True);note.setStyleSheet('font-size:11px;');note.setVisible(bool(self.choices));layout.addWidget(note)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        self.apply_button=chrome_button('确认这套收纳方式',variant='primary');self.apply_button.clicked.connect(self.apply);layout.addWidget(self.apply_button,alignment=Qt.AlignRight)
        self.folder.textEdited.connect(self.invalidate);self.remember.toggled.connect(self.invalidate);self.initializing=False
        self.folder.hide();self.existing.hide()
        # Position is chosen in the shared, hierarchical location component.
        row=layout.itemAt(2 if first else 1)
        if row and row.layout():
            for n in range(row.layout().count()):
                if row.layout().itemAt(n).widget():row.layout().itemAt(n).widget().hide()

    def select_folder(self):
        key=self.existing.currentData()
        if key:self.folder.setText(self.view.folders.items[key]['name'])
        self.invalidate()

    def invalidate(self,*_):
        if self.initializing:return
        self.confirmed=False;self.apply_button.setText('确认这套收纳方式');self.view.schedule()

    def apply(self):
        v=self.view;g=self.g;e=v.engine
        try:
            if any(c.currentData() is None for c in self.choices.values()):raise ValueError('请逐一确认未安排文件：一起保存或本次跳过。')
            known=self.editor.draft() if self.editor else deepcopy(v.profile(g))
            if known['kinds']:e.service.classifications.validate(known['name'],known['kinds'],mode='collect')
            elif self.remember.isChecked():raise ValueError('没有已识别的文件类型，不能记住通用规则；可以仅本次原样保存。')
            from name_patterns import validate
            validate(known['kinds'],known.get('name_patterns',{}))
            full=deepcopy(known);full['name']='本次收纳 · '+g['software'].text()[:24]+' · '+str(g['indices'][0]);full['_remember']=False
            if self.remember.isChecked() and not any(c.currentData()=='keep' for c in self.choices.values()):
                full['name']=known['name'];full['_remember']=True
            for i,c in self.choices.items():
                if c.currentData()!='keep':continue
                path=Path(e.proposal['entries'][i]['path']);kind=e.proposal['entries'][i]['kind'] if e.existing_row(i) else path.name
                if kind not in full['kinds']:full['kinds'].append(kind)
                full.setdefault('name_patterns',{})[kind]=dict(mode='manual')
            e.service.classifications.validate(full['name'],full['kinds'],mode='collect')
            folder=self.folder.text().strip();key=self.existing.currentData()
            if folder:
                from intake_storage import clean_name
                clean_name(folder,'文件夹名称')
                key=next((k for k,item in v.folders.items.items() if item['name'].casefold()==folder.casefold()),None) or 'first:'+str(g['indices'][0])
                v.folders.items[key]=dict(id=key,name=folder,rule=deepcopy(known if known['kinds'] else full))
            else:key=v.folders.ensure(known)
            g['destination']=key;g['location_dirty']=True
            v.assign(g,full)
            g['routing_rule']=deepcopy(known if known['kinds'] else full)
            g['routing_remember']=not self.first or self.remember.isChecked()
            if folder:g['routing_config']=dict(root=v.folders.root.text(),name=folder)
            for i,c in self.choices.items():
                e.table.item(i,0).setCheckState(Qt.Checked if c.currentData()=='keep' else Qt.Unchecked)
                if c.currentData()=='keep':
                    kind=e.proposal['entries'][i]['kind'] if e.existing_row(i) else Path(e.proposal['entries'][i]['path']).name
                    e.kind_controls[i].setCurrentIndex(e.kind_controls[i].findData(kind))
            if self.first:
                v.first_drafts[g['indices'][0]]=deepcopy(known) if self.remember.isChecked() and full['name']!=known['name'] else None
            self.confirmed=True;self.error.clear();self.apply_button.setText('已确认收纳方式');v.schedule()
        except (ValueError,OSError,TypeError) as error:self.error.setText(str(error));self.confirmed=False

    def validate(self):
        if not self.choices and self.view.profile(self.g)['name'] in {p['name'] for p in self.view.engine.service.classifications.profiles(True)}:
            self.confirmed=True;self.hide()
        selected=any(self.view.checked(i) for i in self.g['indices'])
        if selected and not self.confirmed:raise ValueError('请先确认「'+self.g['software'].text()+'」的收纳方式，或取消勾选该软件。')


def install(view):
    view.first_drafts={}
    saved={p['name'] for p in view.engine.service.classifications.profiles(True)}
    for g in view.groups:
        first=not any(view.engine.existing_row(i) for i in g['indices']) and view.profile(g)['name'] not in saved and not view.profile(g).get('_folder_rule')
        leftover=any(not view.engine.chosen_kind(i) or not known_format(view.engine.proposal['entries'][i]['path']) for i in g['indices'])
        if not first and not leftover:continue
        panel=FirstReceipt(view,g,first);g['onboarding']=panel;g['detail'].layout().insertWidget(0,panel);g['expand'].setChecked(True)
        if first:
            name=view.profile(g)['name'];view.engine.generated_drafts=[d for d in view.engine.generated_drafts if d['name']!=name]
    view.schedule()


def confirm_first_receipts(form,*,remember=False):
    """Verification driver: perform the same explicit choices as the UI."""
    view=form.grouped_view
    if hasattr(view,'compact_confirmation'):
        from management_ui import RuleManagementDialog
        from name_patterns import suggest
        for g in view.groups:
            if any(form.existing_row(i) for i in g['indices']):continue
            saved={p['name'] for p in form.service.classifications.profiles(True)}
            if view.profile(g)['name'] in saved and all(form.chosen_kind(i) for i in g['indices']):continue
            draft=deepcopy(view.profile(g))
            if not draft.get('kinds') or draft.get('name') in ('__new__','待确认','自定义'):
                draft=suggest([form.proposal['entries'][i] for i in g['indices']])[0]
            for kind,p in draft.get('name_patterns',{}).items():
                if p.get('mode')=='manual':draft['name_patterns'][kind]=dict(mode='type')
            d=RuleManagementDialog(form.service,new=True)
            d.editor.apply_suggestion(draft);d.form.signature=None;d.form.refresh()
            d.saved.connect(view.compact_confirmation.rematch);d.save()
            if d.dirty:raise ValueError(d.status.text())
            d.deleteLater()
        view.refresh()
        return
    for g in view.groups:
        panel=g.get('onboarding')
        if not panel or panel.confirmed:continue
        panel.remember.setChecked(remember)
        for choice in panel.choices.values():choice.setCurrentIndex(choice.findData('keep'))
        panel.apply()
        if not panel.confirmed:raise ValueError(panel.error.text())
