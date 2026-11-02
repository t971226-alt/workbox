"""A compact confirmation sheet for materials dropped before names are registered."""
from pathlib import Path
from collections import defaultdict
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QTableWidget,
    QTableWidgetItem,QHeaderView,QAbstractItemView,QInputDialog)
from workspace_chrome import chrome_button,WorkspaceComboBox,colors,MaterialCheckDelegate
from theme import apply_to
from production import infer_kind,normalized
from direct_material_intake import DirectMaterialIntake,ranked_versions


class DirectMaterialDialog(QDialog):
    def __init__(self,service,plan,parent=None):
        super().__init__(parent);self.service=service;self.proposal=plan;self.loading=False
        self.setWindowTitle('直接收录材料 · 核对名称与分类');self.resize(850,560);self.setMinimumSize(720,460);apply_to(self)
        c=colors();self.setStyleSheet('QDialog,QTableWidget,QLineEdit { background:'+c['surface']+'; color:'+c['ink']+'; } QLabel { color:'+c['ink']+'; font-size:12px; } QLineEdit { padding:6px; border:1px solid '+c['border']+'; border-radius:6px; } QTableWidget { border:1px solid '+c['border']+'; gridline-color:'+c['border']+'; font-size:12px; selection-background-color:'+c['hover']+'; selection-color:'+c['ink']+'; } QHeaderView::section { background:'+c['field']+'; color:'+c['ink']+'; border:none; padding:7px; font-size:12px; }')
        layout=QVBoxLayout(self);layout.setSpacing(10)
        note=QLabel('核对软件名称 → 选择材料分类 → 确认收录。原始文件保留。')
        note.setWordWrap(True);layout.addWidget(note)
        top=QHBoxLayout();top.addWidget(QLabel('新批次'))
        self.batch=QLineEdit(plan['batch']);self.batch.setAccessibleName('新材料批次名称');top.addWidget(self.batch,1)
        if plan.get('split_batches'):
            self.batch.setText('按一级子文件夹建立 '+str(len(plan['batch_labels']))+' 个批次');self.batch.setReadOnly(True)
            self.batch.setToolTip('\n'.join(plan['batch_labels']))
        self.mode=WorkspaceComboBox();self.mode.addItem('收录整理','collect')
        self.mode.setCurrentIndex(max(0,self.mode.findData(plan['mode'])));self.mode.setAccessibleName('新批次处理方式');self.mode.setParent(self);self.mode.hide()
        layout.addLayout(top)
        self.table=QTableWidget(len(plan['entries']),6);self.table.setHorizontalHeaderLabels(['收录','来源文件','软件名称','材料分类','材料类型','所属批次'])
        self.table.setAccessibleName('直接拖入材料核对清单');self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setItemDelegateForColumn(0,MaterialCheckDelegate(self.table));self.table.verticalHeader().hide()
        self.table.setColumnWidth(0,42);self.table.setColumnWidth(1,165);self.table.setColumnWidth(2,150);self.table.setColumnWidth(3,145);self.table.setColumnWidth(4,165)
        self.table.horizontalHeader().setSectionResizeMode(5,QHeaderView.Stretch)
        if not plan.get('split_batches'):
            self.table.setColumnHidden(5,True);self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch)
        self.class_controls=[];self.kind_controls=[]
        self.profiles={p['name']:p for p in plan['profiles']}
        for i,e in enumerate(plan['entries']):
            keep=QTableWidgetItem();keep.setFlags(Qt.ItemIsUserCheckable|Qt.ItemIsEnabled|Qt.ItemIsSelectable);keep.setCheckState(Qt.Checked);self.table.setItem(i,0,keep)
            file=QTableWidgetItem(e.get('relative_path',Path(e['path']).name));file.setFlags(file.flags()&~Qt.ItemIsEditable);file.setToolTip(e['path']);self.table.setItem(i,1,file)
            name=QTableWidgetItem(e['name']);name.setToolTip(e['warning']);self.table.setItem(i,2,name)
            batch_item=QTableWidgetItem(e.get('batch_label') if plan.get('split_batches') else plan['batch'])
            if not plan.get('split_batches'):batch_item.setFlags(batch_item.flags()&~Qt.ItemIsEditable)
            batch_item.setToolTip(batch_item.text()+'\n双击改名；同批次所有行同步。' if plan.get('split_batches') else batch_item.text());self.table.setItem(i,5,batch_item)
            profile=WorkspaceComboBox()
            profile.addItem('请选择材料分类','待确认')
            for label in self.profiles:profile.addItem(label,label)
            profile.addItem('自定义（按实际文件）','自定义');profile.setCurrentIndex(profile.findData(e['package']))
            if profile.findData(e['package'])<0:profile.addItem(e['package']+'（已有任务）',e['package']);profile.setCurrentIndex(profile.count()-1)
            profile.setEnabled(not bool(e.get('existing')))
            self.table.setCellWidget(i,3,profile);self.class_controls.append(profile)
            role=WorkspaceComboBox();self.table.setCellWidget(i,4,role);self.kind_controls.append(role)
            self.fill_kinds(i,e['kind']);profile.currentIndexChanged.connect(lambda _,row=i:self.change_class(row))
            if not Path(e['path']).suffix:
                self.table.item(i,0).setCheckState(Qt.Unchecked);self.table.item(i,1).setToolTip('文件没有后缀，无法自动确认格式；请单独核对。')
            self.table.setRowHeight(i,38)
        layout.addWidget(self.table,1)
        tools=QHBoxLayout();self.assign_button=chrome_button('填写同一软件名称');self.assign_button.clicked.connect(self.assign_name);tools.addWidget(self.assign_button)
        self.versions_button=chrome_button('选择同名版本');self.versions_button.clicked.connect(self.choose_versions);tools.addWidget(self.versions_button);tools.addStretch();layout.addLayout(tools)
        hint=QLabel('同名文件默认选最近修改的一份；可用“选择同名版本”更换。遇到已有材料会询问替换或新建。')
        hint.setToolTip('修改时间相同时按路径排序，不按“修订版”等文件夹名判断。未选原件保留。')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.error=QLabel('');self.error.setWordWrap(True);layout.addWidget(self.error)
        self.summary=QLabel();layout.addWidget(self.summary);bottom=QHBoxLayout();bottom.addStretch()
        self.cancel_button=chrome_button('取消');self.confirm_button=chrome_button('确认收录',variant='primary')
        self.cancel_button.clicked.connect(self.reject);self.confirm_button.clicked.connect(self.confirm)
        bottom.addWidget(self.cancel_button);bottom.addWidget(self.confirm_button);layout.addLayout(bottom)
        for indices in self.version_groups():
            latest=ranked_versions(plan['entries'],indices)[0]
            for i in indices:
                self.table.item(i,0).setCheckState(Qt.Checked if i==latest else Qt.Unchecked)
                self.table.item(i,1).setToolTip(plan['entries'][i]['path']+('\n修改时间最近，默认选中。' if i==latest else '\n同名材料有更新版本，默认未选。')+'可点击“选择同名版本”调整。')
        self.table.itemChanged.connect(self.item_changed);self.update_summary()
        for control in self.kind_controls:control.currentIndexChanged.connect(self.update_summary)

    def version_groups(self):
        groups=defaultdict(list)
        for i,e in enumerate(self.proposal['entries']):
            name=normalized(self.table.item(i,2).text());kind=self.chosen_kind(i)
            if name and self.kind_controls[i].currentIndex()>0:groups[(e.get('batch_key',''),name,kind)].append(i)
        return [indices for indices in groups.values() if len(indices)>1]

    def chosen_kind(self,row):
        control=self.kind_controls[row]
        return control.currentText() if control.isEditable() else control.currentData() or ''

    def choose_versions(self):
        from intake_versions_ui import IntakeVersionsDialog
        groups=self.version_groups();selected={i for i in range(self.table.rowCount()) if self.table.item(i,0).checkState()==Qt.Checked}
        entries=[dict(e,name=self.table.item(i,2).text(),kind=self.chosen_kind(i),batch_label=self.table.item(i,5).text()) for i,e in enumerate(self.proposal['entries'])]
        try:dialog=IntakeVersionsDialog(entries,groups,selected,self)
        except OSError as error:self.error.setText('来源文件已变化，请重新拖入：'+str(error));return
        if dialog.exec()!=QDialog.Accepted:return
        for indices,combo in dialog.choices:
            for i in indices:self.table.item(i,0).setCheckState(Qt.Checked if combo.currentData()==i else Qt.Unchecked)
        self.update_summary()

    def existing_row(self,row):
        e=self.proposal['entries'][row]
        return bool(e.get('existing') and normalized(self.table.item(row,2).text())==normalized(e['existing']['name']))

    def item_changed(self,item):
        if self.loading:return
        if item.column()==5 and self.proposal.get('split_batches'):
            self.loading=True
            try:
                key=self.proposal['entries'][item.row()].get('batch_key','')
                for i,e in enumerate(self.proposal['entries']):
                    if e.get('batch_key','')==key:self.table.item(i,5).setText(item.text())
            finally:self.loading=False
        if item.column()==2:
            row=item.row();self.class_controls[row].setEnabled(not self.existing_row(row));self.fill_kinds(row,self.chosen_kind(row))
        self.update_summary()

    def fill_kinds(self,row,preferred=None):
        combo=self.kind_controls[row];package=self.class_controls[row].currentData();entry=self.proposal['entries'][row]
        options=entry['kinds'] if self.existing_row(row) else self.profiles[package]['kinds'] if package in self.profiles else entry['kinds']
        combo.blockSignals(True);combo.clear();combo.setEditable(package=='自定义')
        combo.addItem('请选择材料类型','')
        for kind in options:
            from file_families import compatible
            families=entry.get('existing_families',{}) if self.existing_row(row) else self.profiles.get(package,{}).get('family_map',{})
            if self.existing_row(row):families={**families,**entry.get('extra_families',{})}
            if compatible(kind,entry['path'],families):
                from file_families import TYPES
                caption=Path(kind).stem+' · '+TYPES[families[kind]][0] if kind in families else kind
                combo.addItem(caption,kind)
        from file_families import infer
        patterns={**entry.get('existing_patterns',{}),**entry.get('extra_patterns',{})} if self.existing_row(row) else self.profiles.get(package,{}).get('name_patterns',{})
        match=preferred if combo.findData(preferred)>0 else infer(Path(entry['path']).name,options,families if options else {},project_name=self.table.item(row,2).text(),patterns=patterns)
        if not match and combo.count()==2 and not patterns:match=combo.itemData(1)
        combo.setCurrentIndex(max(0,combo.findData(match)));combo.blockSignals(False)

    def change_class(self,row):
        if self.loading:return
        self.loading=True
        try:
            name=normalized(self.table.item(row,2).text());package=self.class_controls[row].currentData()
            for i,combo in enumerate(self.class_controls):
                if i==row or name and normalized(self.table.item(i,2).text())==name and self.proposal['entries'][i].get('batch_key','')==self.proposal['entries'][row].get('batch_key',''):
                    previous=self.chosen_kind(i);combo.setCurrentIndex(combo.findData(package));self.fill_kinds(i,previous)
        finally:self.loading=False
        self.update_summary()

    def assign_name(self):
        rows=sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:self.error.setText('请先选择需要归到同一软件的文件行。');return
        value,ok=QInputDialog.getText(self,'软件名称','将选中行归到同一个软件：')
        if ok:
            for row in rows:self.table.item(row,2).setText(value.strip())

    def values(self):
        rows=[];custom=defaultdict(list);by_path={e['path']:e for e in self.proposal['entries']}
        for i,e in enumerate(self.proposal['entries']):
            if self.table.item(i,0).checkState()!=Qt.Checked:continue
            name=self.table.item(i,2).text().strip();package=self.class_controls[i].currentData();combo=self.kind_controls[i]
            if package=='待确认':raise ValueError('有文件符合多个分类，请先选择本次材料分类。')
            kind=combo.currentText().strip() if package=='自定义' else combo.currentData()
            if not kind or kind=='请选择材料类型':
                if combo.count()==1:raise ValueError('文件格式 '+Path(e['path']).suffix+' 不在当前材料清单中。请选择同格式分类，或调整该软件的材料清单。')
                raise ValueError('请为所有勾选文件选择材料类型。')
            kinds=list(e['kinds']) if self.existing_row(i) else list(self.profiles[package]['kinds']) if package in self.profiles else [kind]
            if package=='自定义':custom[(e.get('batch_key',''),normalized(name))].append(kind)
            rows.append(dict(path=e['path'],name=name,package=package,kind=kind,kinds=kinds,batch=self.table.item(i,5).text().strip()))
        for row in rows:
            if row['package']=='自定义':
                e=by_path[row['path']];row['kinds']=list(dict.fromkeys(custom[(e.get('batch_key',''),normalized(row['name']))]))
        return rows

    def update_summary(self,*_):
        count=sum(self.table.item(i,0).checkState()==Qt.Checked for i in range(self.table.rowCount()))
        missing=sum(not self.table.item(i,2).text().strip() for i in range(self.table.rowCount()) if self.table.item(i,0).checkState()==Qt.Checked)
        self.summary.setText(f'选中 {count} 个文件'+(f' · {missing} 项待补名称' if missing else ''))
        from intake_link import recognition_summary
        entries=[dict(e,name=self.table.item(i,2).text(),kind=self.kind_controls[i].currentText(),package=self.class_controls[i].currentData()) for i,e in enumerate(self.proposal['entries']) if self.table.item(i,0).checkState()==Qt.Checked]
        self.summary.setText(recognition_summary(self.proposal,entries));self.summary.setWordWrap(True)
        groups=self.version_groups();skipped=sum(not any(self.table.item(i,0).checkState()==Qt.Checked for i in indices) for indices in groups)
        latest=sum([i for i in indices if self.table.item(i,0).checkState()==Qt.Checked]==[ranked_versions(self.proposal['entries'],indices)[0]] for indices in groups)
        self.versions_button.setVisible(bool(groups));self.versions_button.setText(f'选择同名版本（{len(groups)}组）')
        if latest:self.summary.setText(self.summary.text()+f' · {latest} 组已选最近修改版本')
        if skipped:self.summary.setText(self.summary.text()+f' · {skipped} 组版本暂不收录，原件保留')

    def confirm(self):
        try:
            self.rows=self.values();DirectMaterialIntake(self.service).validate(self.proposal,self.rows,self.batch.text(),self.mode.currentData());self.accept()
        except (ValueError,TypeError) as error:self.error.setText(str(error))
