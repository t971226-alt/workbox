"""Small material-rule editor shared by new-rule entry points and ZIP intake."""
from pathlib import Path
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView
from workspace_chrome import chrome_button,WorkspaceComboBox
from theme import apply_to
from intake_controls import TypeCombo
from file_families import TYPES,coalesce


class QuickRuleEditor(QWidget):
    changed=Signal()
    def __init__(self,kinds=(),parent=None,*,collapsed=False,archive_only=False):
        super().__init__(parent);self._ready=False;self.archive_only=archive_only
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        top=QHBoxLayout();top.addWidget(QLabel('规则名称'));self.name=QLineEdit();self.name.setMaxLength(40);self.name.setPlaceholderText('例如：软件运行包、验收材料');top.addWidget(self.name,1);self.choose_existing=chrome_button('选择已有清单');self.choose_existing.setProperty('intakeLink',True);self.choose_existing.hide();top.addWidget(self.choose_existing);layout.addLayout(top)
        self.simple_row=QWidget();simple=QHBoxLayout(self.simple_row);simple.setContentsMargins(0,0,0,0);simple.addWidget(QLabel('文件类型'))
        self.format=TypeCombo(archive_only=archive_only);simple.addWidget(self.format,1);layout.addWidget(self.simple_row)
        self.brief=QLabel();self.brief.setWordWrap(True);layout.addWidget(self.brief)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['显示名称（可选）','文件类型','','文件名包含']);self.table.verticalHeader().hide();self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.table.setColumnWidth(1,148);self.table.setColumnWidth(2,26);self.table.setColumnWidth(3,270);self.table.setColumnHidden(3,True);layout.addWidget(self.table)
        self.software_name='';self.keyword_hint=QLabel('当前软件名自动代入；关键词用逗号、空格或 + 分隔，需全部满足。多个文件匹配同一项时，需手动选择。');self.keyword_hint.setWordWrap(True);self.keyword_hint.setStyleSheet('font-size:11px;');self.keyword_hint.hide();layout.addWidget(self.keyword_hint)
        self.resolved=QLabel('收录时自动代入确认清单中的软件名称。');self.resolved.setWordWrap(True);self.resolved.setStyleSheet('font-size:11px;');self.resolved.hide();layout.addWidget(self.resolved)
        self.tools=QWidget();actions=QHBoxLayout(self.tools);actions.setContentsMargins(0,0,0,0)
        self.add_button=chrome_button('添加其他材料',icon='plus');self.add_button.clicked.connect(lambda:self.add('.docx'));actions.addWidget(self.add_button)
        self.toggle=chrome_button('分别设置材料名称');self.toggle.setCheckable(True);self.toggle.hide();actions.addStretch();layout.addWidget(self.tools)
        self.settings_button=chrome_button('识别设置 ›');self.settings_button.setCheckable(True);self.settings_button.toggled.connect(self.show_settings);actions.addWidget(self.settings_button)
        initial=list(dict.fromkeys(kinds or ['.docx']));initial,_=coalesce(initial) if all(not k.startswith('.') for k in initial) else (initial,{})
        for kind in initial:self.add(kind)
        self.format.set_suffix(self.table.cellWidget(0,1).suffix())
        self.format.currentIndexChanged.connect(self.simple_format_changed)
        self.name.textChanged.connect(self.changed);self.table.itemChanged.connect(self.changed);self.changed.connect(self.summarize)
        self.table.itemChanged.connect(self.label_changed)
        self.toggle.toggled.connect(self.show_materials);self._ready=True
        self.toggle.setChecked(self.table.rowCount()>1);self.show_materials(self.toggle.isChecked());self.summarize()
        from simple_rule_ui import install
        install(self)

    def simple_format_changed(self):
        if self.table.rowCount()==1:
            combo=self.table.cellWidget(0,1);combo.set_suffix(self.format.suffix(),strict=self.format.strict)
        self.changed.emit()

    def summarize(self):
        if not self._ready:return
        self.brief.hide()
        self.fit_table()
        self.simple_row.setVisible(self.table.rowCount()==1 and not self.toggle.isChecked() and not self.settings_button.isChecked())
        if self.table.rowCount()>1 and not self.toggle.isChecked():self.toggle.setChecked(True)
        self.fit_window()
        self.update_resolved()

    def fit_table(self):
        header=self.table.horizontalHeader().sizeHint().height()
        rows=sum(self.table.rowHeight(i) for i in range(self.table.rowCount()))
        width=sum(self.table.columnWidth(i) for i in range(4) if not self.table.isColumnHidden(i))
        scroll=self.table.horizontalScrollBar().sizeHint().height() if width>max(100,self.table.width()-4) else 0
        self.table.setFixedHeight(min(220,max(76,header+rows+self.table.frameWidth()*2+scroll+6)))

    def show_materials(self,visible):
        if hasattr(self,'simple_presentation'):
            self.table.hide();self.simple_row.hide();self.simple_presentation.refresh();return
        settings=self.settings_button.isChecked()
        self.table.setVisible(settings or visible and self.table.rowCount()>1);self.simple_row.setVisible(not settings and not visible and self.table.rowCount()==1)
        self.toggle.setText('收起材料名称' if visible else '分别设置材料名称')
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,self.fit_window)

    def show_settings(self,checked):
        self.table.setColumnHidden(3,not checked);self.table.setColumnHidden(0,checked and self.table.rowCount()==1);self.keyword_hint.setVisible(checked);self.resolved.setVisible(checked);self.show_materials(self.toggle.isChecked())
        self.update_resolved()
        self.fit_table()

    def set_software_name(self,name):self.software_name=name;self.update_resolved()

    def update_resolved(self):
        from name_patterns import SOFTWARE_TOKEN
        bits=[]
        for i in range(self.table.rowCount()):
            condition=self.table.cellWidget(i,3)
            if condition is None:continue
            p=condition.pattern()
            if p['mode']=='keywords':
                terms=[(self.software_name or '当前软件名（收录时自动识别）') if t==SOFTWARE_TOKEN else t for t in p['terms']]
                bits.append('文件名同时包含：'+' ＋ '.join(terms) if terms else '不限文件名，只按左侧类型识别。')
        self.resolved.setText('\n'.join(bits) or ('已识别软件：'+self.software_name if self.software_name else '已有规则保留原识别方式；收录时自动代入确认清单中的软件名称。'))

    def label_changed(self,item):
        if not self._ready or item.column()!=0:return
        from name_patterns import placeholder
        if placeholder(item.text()):self.table.cellWidget(item.row(),3).setText(item.text().strip())

    def apply_suggestion(self,draft):
        self._ready=False;self.table.setRowCount(0)
        for kind in draft['kinds']:
            self.add(kind);p=draft.get('name_patterns',{}).get(kind)
            if p:self.table.cellWidget(self.table.rowCount()-1,3).set_pattern(p)
        self.name.setText(draft['name']);self._ready=True;self.toggle.setChecked(self.table.rowCount()>1);self.show_materials(self.toggle.isChecked());self.changed.emit()

    def fit_window(self):
        import shiboken6
        if not shiboken6.isValid(self):return
        window=self.window()
        if hasattr(window,'fit_content'):window.fit_content()
        elif hasattr(window,'_density_filter'):window._density_filter.queue()

    def add(self,kind):
        self.table.blockSignals(True);i=self.table.rowCount()
        self.table.insertRow(i)
        ext=kind if kind.startswith('.') and kind.count('.')==1 else Path(kind).suffix.lower()
        stem='' if kind==ext else Path(kind).stem
        self.table.setItem(i,0,QTableWidgetItem(stem));combo=TypeCombo(archive_only=self.archive_only)
        combo.set_suffix(ext or '.docx');combo.currentIndexChanged.connect(self.changed);self.table.setCellWidget(i,1,combo)
        from name_patterns import placeholder
        from keyword_rule_ui import KeywordCondition
        pattern=KeywordCondition();pattern.setText(stem if placeholder(stem) else '当前软件名' if not stem and i==0 else '手动对应' if not stem else stem)
        if not stem:pattern.set_pattern(dict(mode='type'))
        pattern.textChanged.connect(self.changed);self.table.setCellWidget(i,3,pattern)
        remove=chrome_button('×');remove.setFixedSize(23,24);remove.clicked.connect(lambda checked=False,w=remove:self.remove(w));self.table.setCellWidget(i,2,remove);self.table.setRowHeight(i,32);self.table.blockSignals(False);self.changed.emit()

    def remove(self,control):
        if self.table.rowCount()==1:return
        for i in range(self.table.rowCount()):
            if self.table.cellWidget(i,2) is control:
                self.table.removeRow(i)
                if self.table.rowCount()==1:
                    ext=self.table.cellWidget(0,1).suffix();self.format.set_suffix(ext);self.toggle.setChecked(False)
                self.changed.emit();return

    def kinds(self):
        result=[]
        for i in range(self.table.rowCount()):
            name=self.table.item(i,0).text().strip() or (self.name.text().strip() if self.table.rowCount()==1 else '材料 '+str(i+1))
            ext=self.table.cellWidget(i,1).suffix();result.append(name+ext)
        return result

    def draft(self):
        kinds=self.kinds();families={}
        for i,kind in enumerate(kinds):
            combo=self.table.cellWidget(i,1)
            if combo.family() and not combo.strict:families[kind]=combo.family()
        from name_patterns import placeholder,validate
        patterns={}
        for i,kind in enumerate(kinds):
            patterns[kind]=self.table.cellWidget(i,3).pattern()
        d=dict(name=self.name.text().strip(),kinds=kinds,family_map=families,name_patterns=patterns)
        if len(kinds)==1 and self.table.cellWidget(0,1).family()=='archive':
            combo=self.table.cellWidget(0,1);d.update(intake_mode='original',extensions=[combo.suffix()] if combo.strict else ['.zip','.7z','.rar'])
        return d


class QuickClassificationDialog(QDialog):
    def __init__(self,service,parent=None,kinds=()):
        super().__init__(parent);self.service=service;self.profile_id=None;self.setWindowTitle('新建收纳规则');self.resize(480,250);self.setProperty('intakeSheet',True);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(12,10,12,10);layout.setSpacing(6)
        hint=QLabel('');hint.hide()
        self.editor=QuickRuleEditor(kinds or ['.docx'],self);layout.addWidget(self.editor)
        self.error=QLabel('');self.error.setWordWrap(True);self.error.hide();layout.addWidget(self.error)
        actions=QHBoxLayout();actions.addStretch();cancel=chrome_button('取消');self.save_button=chrome_button('保存规则',variant='primary');actions.addWidget(cancel);actions.addWidget(self.save_button);layout.addLayout(actions)
        cancel.clicked.connect(self.reject);self.save_button.clicked.connect(self.save)

    def save(self):
        try:
            d=self.editor.draft();self.profile_id=self.service.classifications.save(d['name'],d['kinds'],mode='collect',intake_mode=d.get('intake_mode','materials'),extensions=d.get('extensions'),family_map=d.get('family_map',{}),name_patterns=d.get('name_patterns',{}));self.accept()
        except (ValueError,OSError) as error:self.error.setText(str(error));self.error.show()
