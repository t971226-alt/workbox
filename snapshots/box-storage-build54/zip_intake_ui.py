"""A single, compact confirmation window for raw and classified ZIP intake."""
from pathlib import Path
from threading import Event
from copy import deepcopy
from PySide6.QtCore import Qt,QThread,Signal,QTimer
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QRadioButton,
    QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QScrollArea,QFrame,QFileDialog,QCheckBox)
from workspace_chrome import chrome_button,WorkspaceComboBox
from theme import apply_to
from direct_material_intake import DirectMaterialIntake,identity
from direct_material_ui import DirectMaterialDialog
from production import normalized,infer_kind
from quick_classification_ui import QuickRuleEditor
from rule_chrome import RulePicker,RuleTypes,FileBadge
from intake_controls import SaveLocationEditor
from file_families import compatible,infer,coalesce


def label(text=''):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setWordWrap(True);return w


class BatchArchiveLocations(QWidget):
    """One parent directory, independently editable software subfolders."""
    def __init__(self,service,items,parent=None):
        super().__init__(parent);self.items=items
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        self.root=QLineEdit(str(service.workspaces.settings()['work_root']));self.root.setObjectName('batchSaveRoot');self.root.setReadOnly(True)
        self.browse_button=chrome_button('选择目录');self.browse_button.clicked.connect(self.browse)
        row=QHBoxLayout();caption=label('统一保存到');caption.setFixedWidth(76);row.addWidget(caption);row.addWidget(self.root,1);row.addWidget(self.browse_button);layout.addLayout(row)
        hint=label('所有文件共用上级目录，每个软件单独建文件夹。');hint.setObjectName('subtle');layout.addWidget(hint)
        self.scroll=QScrollArea();self.scroll.setFrameShape(QScrollArea.NoFrame);self.scroll.setWidgetResizable(True)
        body=QWidget();cards=QVBoxLayout(body);cards.setContentsMargins(0,0,0,0);cards.setSpacing(8);self.scroll.setWidget(body);layout.addWidget(self.scroll)
        existing={}
        from intake_storage import get
        with service.db.connect() as c:
            for i,item in enumerate(items):
                pid=item['target'].currentData()
                if type(pid) is not int:continue
                saved=get(c,pid)
                old=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id DESC LIMIT 1',(pid,)).fetchone()
                if saved:existing[i]=(Path(saved['directory']),saved['filenames'].get('__original__'))
                elif old:existing[i]=(Path(old[0]).parent,None)
        parents={str(v[0].parent) for v in existing.values()}
        if len(existing)==len(items) and len(parents)==1:self.root.setText(next(iter(parents)))
        from intake_controls import type_icon
        for i,item in enumerate(items):
            card=QFrame();card.setObjectName('batchArchiveCard');c=QVBoxLayout(card);c.setContentsMargins(10,8,10,8);c.setSpacing(7)
            header=QHBoxLayout();icon=QLabel();icon.setPixmap(type_icon(Path(item['path']).suffix).pixmap(19,24));header.addWidget(icon)
            item['name'].setObjectName('batchSoftwareName');item['name'].setToolTip('软件名称；原文件：'+Path(item['path']).name);header.addWidget(item['name'],1)
            skip=chrome_button('跳过');skip.setCheckable(True);header.addWidget(skip);item['skip_button']=skip;c.addLayout(header)
            task_row=QHBoxLayout();task_row.addWidget(item['target'],1);task_row.addWidget(item['action'],1);c.addLayout(task_row)
            item['target'].setVisible(item['has_matches'])
            output=SaveLocationEditor(service,item['name'].text(),Path(item['path']).suffix,card,shared_root=self.root)
            item['save_location']=output;item['name'].textChanged.connect(output.set_software)
            if i in existing:output.set_existing(*existing[i])
            c.addWidget(output);cards.addWidget(card)
            def skipped(checked,item=item,output=output):
                item['skip_button'].setText('恢复' if checked else '跳过')
                for control in (item['name'],item['target'],item['action'],output):control.setEnabled(not checked)
            skip.toggled.connect(skipped)
        cards.addStretch()
        self.scroll.setMinimumHeight(110);self.scroll.setMaximumHeight(380)
        QTimer.singleShot(0,self.fit_cards)
        summary=label('文件夹名、文件名默认使用软件名称，可分别修改。'+(' 本次新副本使用上方目录，历史版本保留原处。' if existing else ''));summary.setObjectName('subtle');layout.addWidget(summary)
    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'选择批量材料的上级目录',self.root.text())
        if path:self.root.setText(path)
    def fit_cards(self):
        import shiboken6
        if not shiboken6.isValid(self):return
        self.scroll.setFixedHeight(min(380,max(110,self.screen().availableGeometry().height()-340),self.scroll.widget().sizeHint().height()+6))
        if hasattr(self.window(),'fit_content'):self.window().fit_content()


class ZipScan(QThread):
    ready=Signal(object);failed=Signal(str)
    def __init__(self,service,paths,parent,*,whole_files=False):
        super().__init__(parent);self.service=service;self.paths=paths;self.cancel=Event();self.whole_files=whole_files
    def run(self):
        try:
            plan=DirectMaterialIntake(self.service).prepare(self.paths,require_material_zip=not self.whole_files,include_known=True,allow_saved_zip=True,keep_archives=self.whole_files,cancel_event=self.cancel)
            if not plan:raise ValueError('此 ZIP 位于 BOX 管理目录，请先复制到其他目录后分类。')
            if not self.cancel.is_set():self.ready.emit(plan)
        except Exception as error:
            if not self.cancel.is_set():self.failed.emit(str(error))


class CompactMaterialForm(DirectMaterialDialog):
    def __init__(self,service,plan,parent):
        self.quick_editor=None;self._compact_ready=False
        super().__init__(service,plan,parent)
        self.setWindowFlags(Qt.Widget);self.setMinimumSize(0,0);self.setMaximumHeight(650)
        self.layout().setContentsMargins(0,0,0,0);self.layout().setSpacing(5)
        # Keep the proven row/type and version selection code, remove the old
        # onboarding paragraphs and batch configuration from everyday intake.
        for w in self.findChildren(QLabel,options=Qt.FindDirectChildrenOnly):w.hide()
        self.batch.hide();self.mode.hide();self.assign_button.hide();self.versions_button.hide();self.cancel_button.hide();self.confirm_button.hide()
        self.error.hide();self.summary.hide()
        self.table.setHorizontalHeaderLabels(['收录','文件','软件名称','分类规则','对应材料','批次'])
        self.table.setColumnHidden(5,True);self.table.setMinimumHeight(66);self.table.setMaximumHeight(205)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.table.setColumnWidth(0,38);self.table.setColumnWidth(4,145)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Fixed);self.table.setColumnWidth(4,185)
        self.single=len({(e.get('batch_key',''),normalized(e['name'])) for e in plan['entries']})==1
        self.software=QLineEdit(plan['entries'][0]['name']);self.software.setPlaceholderText('填写软件名称')
        name_row=QHBoxLayout();name_row.addWidget(label('软件名称'));name_row.addWidget(self.software,1)
        if self.single:
            self.layout().insertLayout(0,name_row);self.table.setColumnHidden(2,True);self.table.setColumnHidden(3,True)
            category_row=QHBoxLayout();category_row.addWidget(label('分类规则'));self.category_selector=WorkspaceComboBox();category_row.addWidget(self.category_selector,1);self.layout().insertLayout(1,category_row)
            self.software.textChanged.connect(self.set_software)
        else:self.software.hide()
        self.generated_drafts=[]
        self.quick_editor=QuickRuleEditor(list(dict.fromkeys(e['kind'] for e in plan['entries'])),self,collapsed=True);self.quick_editor.hide();self.layout().insertWidget(2,self.quick_editor)
        if self.single and plan['entries'][0].get('suggested_draft'):self.quick_editor.apply_suggestion(plan['entries'][0]['suggested_draft'])
        self.quick_editor.changed.connect(self.draft_changed)
        for combo in self.class_controls:
            combo.addItem('＋按这些文件新建分类','__new__')
            index=combo.findData('自定义')
            if index>=0:combo.setItemText(index,'按现有文件收录（不建分类）')
        if not self.single:
            from intake_rule_options import suggested_name
            generated={}
            for i,combo in enumerate(self.class_controls):
                if combo.currentData()!='自定义' or not combo.isEnabled():continue
                proposed=plan['entries'][i].get('suggested_draft')
                coalesced,families=coalesce(plan['entries'][i]['kinds']);kinds=tuple(proposed['kinds'] if proposed else coalesced)
                if kinds not in generated:
                    name=suggested_name(service,kinds);base=name;n=2
                    while name in self.profiles:name=base+'（'+str(n)+'）';n+=1
                    draft=dict(proposed or {},name=name,kinds=list(kinds),family_map=proposed['family_map'] if proposed else families);profile=dict(draft,aliases={},mode='collect',active=1)
                    generated[kinds]=name;self.generated_drafts.append(draft);self.profiles[name]=profile;self.proposal['profiles'].append(profile)
                    for c in self.class_controls:c.addItem(name+'（本次新建）',name)
                combo.setCurrentIndex(combo.findData(generated[kinds]))
            for combo in self.class_controls:
                index=combo.findData('自定义')
                if index>=0:combo.removeItem(index)
        if self.single:
            source=self.class_controls[0]
            for i in range(source.count()):self.category_selector.addItem(source.itemText(i),source.itemData(i))
            self.category_selector.setCurrentIndex(self.category_selector.findData(source.currentData()));self.category_selector.setEnabled(source.isEnabled())
            self.category_selector.setToolTip('沿用已有任务的材料要求；修改软件名称可建立独立任务。' if not source.isEnabled() else '选择完整材料要求，或按包内文件新建分类。')
            self.category_selector.currentIndexChanged.connect(lambda *_:source.setCurrentIndex(source.findData(self.category_selector.currentData())))
            def sync_selector(*args):
                self.category_selector.blockSignals(True);self.category_selector.setCurrentIndex(self.category_selector.findData(source.currentData()));self.category_selector.blockSignals(False)
                if hasattr(self,'category_row'):self.sync_rule_visibility()
            source.currentIndexChanged.connect(sync_selector)
        if self.single:
            self.category_selector.show()
            records=plan['profiles']
            if not self.category_selector.isEnabled():records=[dict(name=self.category_selector.currentData(),kinds=plan['entries'][0]['kinds'])]
            self.rule_picker=RulePicker([dict(name='按本次材料新建规则',key='__new__',kinds=list(dict.fromkeys(e['kind'] for e in plan['entries']))),*records],self)
            self.rule_picker.scroll.setFixedHeight(72);self.rule_picker.hide()
            self.layout().insertWidget(2,self.rule_picker)
            self.rule_picker.changed.connect(lambda r:self.category_selector.setCurrentIndex(self.category_selector.findData(r.get('key',r['name']))))
            def sync_rule(*args):
                value=self.category_selector.currentData();self.rule_picker.select('按本次材料新建规则' if value=='__new__' else value)
            self.category_selector.currentIndexChanged.connect(sync_rule)
            current=self.category_selector.currentData()
            if current=='自定义':
                from intake_rule_options import suggested_name
                proposed=plan['entries'][0].get('suggested_draft')
                name=proposed['name'] if proposed else suggested_name(self.service,[e['kind'] for e in plan['entries']])
                if name in self.profiles:name=suggested_name(self.service,self.quick_editor.kinds())
                self.quick_editor.name.setText(name)
                if self.quick_editor.table.rowCount()==1 and Path(plan['entries'][0]['kind']).stem==plan['entries'][0]['name']:
                    self.quick_editor.table.item(0,0).setText('')
                self.category_selector.setCurrentIndex(self.category_selector.findData('__new__'))
            sync_rule()
            self.quick_editor.choose_existing.show();self.quick_editor.choose_existing.clicked.connect(self.choose_existing_rule)
            self.category_row=category_row
            self.sync_rule_visibility()
            self.rule_picker.setEnabled(self.category_selector.isEnabled())
        self.table.setFixedHeight(min(205,max(66,28+32*self.table.rowCount())))
        for i in range(self.table.rowCount()):self.table.setRowHeight(i,32)
        self.file_summary=label();self.layout().addWidget(self.file_summary)
        self.match_hint=label();self.match_hint.setObjectName('subtle');self.layout().addWidget(self.match_hint)
        self.extra_hint=label();self.extra_hint.setObjectName('subtle');self.extra_hint.hide();self.layout().addWidget(self.extra_hint)
        self.save_rule=QCheckBox('保存此规则，其他软件也能自动匹配');self.save_rule.setChecked(True);self.layout().addWidget(self.save_rule);self.save_rule.setVisible(False)
        self.save_rule.toggled.connect(self.update_summary)
        self.quick_editor.settings_button.toggled.connect(self.update_summary)
        from intake_controls import type_icon
        for i,e in enumerate(plan['entries']):self.table.item(i,1).setIcon(type_icon(Path(e['path']).suffix))
        if self.single:self.quick_editor.brief.hide();self.file_summary.hide()
        self.destination=label();self.destination.hide();self.layout().addWidget(self.destination)
        self.save_location=SaveLocationEditor(service,self.software.text(),Path(plan['entries'][0]['path']).suffix,self,multiple=not self.single);self.layout().addWidget(self.save_location)
        self.software.textChanged.connect(self.save_location.set_software)
        self.save_location.changed.connect(self.update_summary)
        if self.single:
            existing=plan['entries'][0].get('existing')
            if existing:
                task=next((r for r in service.production.rows() if r['id']==existing['id']),None)
                if task and task.get('collection_folder'):self.save_location.set_existing(task['collection_folder'])
        else:
            self.table.setColumnCount(8);self.table.setHorizontalHeaderLabels(['收录','文件','软件名称','收录规则','对应材料','批次','保存文件名','文件夹名称'])
            from intake_storage import basename
            for i,e in enumerate(plan['entries']):
                total=len({x['kind'] for x in plan['entries'] if x['name']==e['name'] and Path(x['path']).suffix.casefold()==Path(e['path']).suffix.casefold()})
                self.table.setItem(i,6,QTableWidgetItem(basename(e['name'],e['kind'],total)));self.table.setItem(i,7,QTableWidgetItem(e['name']))
            self.table.setColumnWidth(6,165);self.table.setColumnWidth(7,165)
        self.choose_default_versions()

        self._compact_ready=True;self.update_summary()
        from grouped_intake_ui import install
        install(self)

    def set_software(self,text):
        self.loading=True
        for i in range(self.table.rowCount()):self.table.item(i,2).setText(text)
        self.loading=False
        for i,c in enumerate(self.class_controls):c.setEnabled(not self.existing_row(i));self.fill_kinds(i,self.chosen_kind(i))
        if self.single:
            self.category_selector.setEnabled(self.class_controls[0].isEnabled());self.rule_picker.setEnabled(self.class_controls[0].isEnabled())
        self.update_summary()

    def change_class(self,row):
        super().change_class(row)
        if self.quick_editor is not None:
            self.quick_editor.setVisible(any(c.currentData()=='__new__' for c in self.class_controls))
            self.draft_changed()
            if self.single and hasattr(self,'category_row'):self.sync_rule_visibility()
            QTimer.singleShot(0,self.quick_editor.fit_window)

    def draft_changed(self):
        if not self.quick_editor:return
        for i,c in enumerate(self.class_controls):
            if c.currentData()=='__new__':self.fill_kinds(i,self.chosen_kind(i))
        self.choose_default_versions()
        self.update_summary()

    def fill_kinds(self,row,preferred=None):
        if self.quick_editor is None or self.class_controls[row].currentData()!='__new__':return super().fill_kinds(row,preferred)
        combo=self.kind_controls[row];draft=self.quick_editor.draft();options=[k for k in draft['kinds'] if compatible(k,self.proposal['entries'][row]['path'],draft.get('family_map'))]
        combo.blockSignals(True);combo.clear();combo.setEditable(False);combo.addItem('请选择材料类型','')
        from file_families import TYPES
        for kind in options:
            family=draft.get('family_map',{}).get(kind)
            combo.addItem(Path(kind).stem+' · '+TYPES[family][0] if family else kind,kind)
        chosen=preferred if preferred in options else infer(Path(self.proposal['entries'][row]['path']).name,options,draft.get('family_map'),project_name=self.table.item(row,2).text(),patterns=draft.get('name_patterns')) or (options[0] if len(options)==1 and not draft.get('name_patterns') else None)
        combo.setCurrentIndex(max(0,combo.findData(chosen)));combo.blockSignals(False)

    def values(self):
        rows=super().values()
        originals={e['path']:e for e in self.proposal['entries']}
        for r in rows:
            if r['kind'] in originals[r['path']].get('extra_kinds',[]):r['confirm_extra_materials']=True
        if self.quick_editor and any(r['package']=='__new__' for r in rows):
            draft=self.quick_editor.draft();self.service.classifications.validate(draft['name'],draft['kinds'],mode='collect')
            for r in rows:
                if r['package']=='__new__':r['package']=draft['name'] if not hasattr(self,'save_rule') or self.save_rule.isChecked() else '自定义';r['kinds']=draft['kinds']
        if hasattr(self,'save_location'):
            from intake_storage import basename
            by_path={e['path']:i for i,e in enumerate(self.proposal['entries'])}
            for r in rows:
                if self.single:
                    count=len({x['kind'] for x in rows if x['name']==r['name'] and Path(x['path']).suffix.casefold()==Path(r['path']).suffix.casefold()});base=self.save_location.base.text()
                    if count>1 and not self.save_location.base_dirty:
                        profile=self.quick_editor.draft() if self.class_controls[0].currentData()=='__new__' else self.profiles.get(r['package'],{})
                        p=profile.get('name_patterns',{}).get(r['kind'],{})
                        base=r['name'] if p.get('mode')=='same' else r['name']+'_'+p['marker'] if p.get('mode')=='marker' else basename(r['name'],r['kind'],count)
                    r['storage']=self.save_location.spec(base=base)
                else:
                    i=by_path[r['path']];r['storage']=self.save_location.spec(folder=self.table.item(i,7).text(),base=self.table.item(i,6).text())
        if hasattr(self,'grouped_view') and self.grouped_view.ready:rows=self.grouped_view.apply_rows(rows)
        return rows

    def choose_existing_rule(self):
        first=next((i for i in range(self.category_selector.count()) if self.category_selector.itemData(i) in self.profiles),None)
        if first is not None:self.category_selector.setCurrentIndex(first)
        self.sync_rule_visibility();self.category_selector.showPopup()

    def sync_rule_visibility(self):
        is_new=self.category_selector.currentData()=='__new__'
        self.category_selector.setVisible(not is_new)
        for i in range(self.category_row.count()):
            w=self.category_row.itemAt(i).widget()
            if w:w.setVisible(not is_new)
        self.quick_editor.setVisible(is_new);self.rule_picker.hide()

    def choose_default_versions(self):
        if not hasattr(self,'table') or not hasattr(self,'version_groups'):return
        groups=self.version_groups()
        contexts=[]
        for indices in groups:
            first=indices[0];package=self.class_controls[first].currentData()
            profile=self.quick_editor.draft() if package=='__new__' else self.profiles.get(package,{})
            contexts.append((package,self.chosen_kind(first),profile.get('name_patterns',{}),self.proposal['entries'][first].get('existing_patterns',{})))
        signature=repr((groups,contexts))
        if getattr(self,'_versions_signature',None)==signature:return
        self._versions_signature=signature
        from direct_material_intake import ranked_versions
        self._keyword_choice_groups=[]
        for indices in groups:
            first=indices[0];package=self.class_controls[first].currentData()
            profile=self.quick_editor.draft() if package=='__new__' else self.profiles.get(package,{})
            patterns={**profile.get('name_patterns',{}),**self.proposal['entries'][first].get('existing_patterns',{})}
            if patterns.get(self.chosen_kind(first),{}).get('mode') in ('keywords','type'):
                self._keyword_choice_groups.append(indices)
                for i in indices:self.table.item(i,0).setCheckState(Qt.Unchecked)
                continue
            latest=ranked_versions(self.proposal['entries'],indices)[0]
            for i in indices:self.table.item(i,0).setCheckState(Qt.Checked if i==latest else Qt.Unchecked)
        self.versions_button.setVisible(bool(groups));self.versions_button.setText('选择匹配文件' if self._keyword_choice_groups else '更换同项文件')

    def drafts(self):
        used={c.currentData() for i,c in enumerate(self.class_controls) if self.table.item(i,0).checkState()==Qt.Checked}
        extra=[d for key,d in getattr(getattr(self,'grouped_view',None),'first_drafts',{}).items() if d and any(self.table.item(i,0).checkState()==Qt.Checked for g in self.grouped_view.groups if g['indices'][0]==key for i in g['indices'])]
        return [d for d in self.generated_drafts if d['name'] in used]+extra+([self.quick_editor.draft()] if self.quick_editor and '__new__' in used and (not hasattr(self,'save_rule') or self.save_rule.isChecked()) else [])

    def preview_plan(self):
        plan=deepcopy(self.proposal)
        for draft in self.drafts():plan['profiles'].append(dict(draft,aliases={},mode='collect',active=1))
        if hasattr(self,'save_rule') and not self.save_rule.isChecked() and any(c.currentData()=='__new__' for c in self.class_controls):plan['profiles'].append(dict(self.quick_editor.draft(),name='自定义',aliases={},mode='collect',active=1))
        return plan

    def update_summary(self,*args):
        if not self._compact_ready or getattr(self,'_updating_summary',False):return
        self._updating_summary=True
        try:
            if hasattr(self,'save_location') and self.single:
                selected=next((e for i,e in enumerate(self.proposal['entries']) if self.table.item(i,0).checkState()==Qt.Checked),None)
                if selected:
                    suffix=Path(selected['path']).suffix
                    if self.save_location.suffix!=suffix:
                        self.save_location.suffix=suffix;self.save_location.extension.setText(suffix);self.save_location.refresh()
            rows=self.values();groups={}
            if not rows:raise ValueError('请至少选择一份文件。')
            if self.single and len(rows)>1:
                names=list(dict.fromkeys(r['storage']['base']+Path(r['path']).suffix for r in rows))
                self.save_location.extension.setText('原后缀' if len({Path(r['path']).suffix.casefold() for r in rows})>1 else self.save_location.suffix)
                self.save_location.preview.setText('保存文件：\n'+'\n'.join(names[:3])+('\n另有 '+str(len(names)-3)+' 份文件' if len(names)>3 else ''))
                self.save_location.preview.setMinimumHeight(18*min(5,len(names)+1)+14)
                self.save_location.preview.setToolTip('\n'.join(str(Path(r['storage']['directory'])/(r['storage']['base']+Path(r['path']).suffix)) for r in rows))
            for r in rows:groups.setdefault(r['name'],dict(required=r['kinds'],found=set()))['found'].add(r['kind'])
            bits=[]
            for name,g in groups.items():
                absent=[k for k in g['required'] if k not in g['found']]
                bits.append(f"{name or '待填写名称'}：找到 {len(g['found'])}/{len(g['required'])} 份"+('；缺 '+ '、'.join(absent) if absent else ''))
            self.file_summary.setText('\n'.join(bits))
            is_new=self.single and self.class_controls[0].currentData()=='__new__'
            profile=self.quick_editor.draft() if is_new else self.profiles.get(rows[0]['package'],{})
            patterned=bool(profile.get('name_patterns'))
            self.save_rule.setVisible(is_new)
            self.match_hint.setVisible(patterned)
            self.match_hint.setText(('软件名称自动代入 · 已对应 '+str(len(next(iter(groups.values()))['found']))+'/'+str(len(next(iter(groups.values()))['required']))+' 份') if self.single else '按软件名称自动分组；请核对本批所有文件的对应项。')
            extras={k for e in self.proposal['entries'] for k in e.get('extra_kinds',[])}&{r['kind'] for r in rows}
            self.extra_hint.setVisible(bool(extras));self.extra_hint.setText('本次新增 '+str(len(extras))+' 项材料，确认后补充到本任务清单；通用规则保持不变。')
            if is_new and patterned:
                settings=self.quick_editor.settings_button.isChecked()
                self.quick_editor.table.setVisible(settings);self.quick_editor.simple_row.setVisible(False)
                self.quick_editor.add_button.setVisible(settings)
                self.quick_editor.tools.show()
            from material_collection import segment,category_for
            root=Path(self.service.workspaces.settings()['work_root'])
            self.destination.setText('保存位置：'+str(root/segment(category_for(rows[0]['package']) if rows[0]['package'] in ('单手册 Word','五件材料','桌面14件','14件＋双视频') else rows[0]['package']))+' · 按软件分组保存')
            self.destination.setToolTip('显示所属分类目录；确认后在任务详情查看实际批次、软件子目录。')
        except (ValueError,TypeError):
            self.file_summary.setText('请确认软件名称、规则和每份文件对应的材料。')
            if hasattr(self,'match_hint'):self.match_hint.setText('仍有待确认文件，请选择对应项或取消勾选后再收录。');self.match_hint.show()
        finally:self._updating_summary=False


class ZipIntakeDialog(QDialog):
    def __init__(self,service,paths,parent=None,*,start_mode='original'):
        super().__init__(parent);self.service=service;self.paths=list(map(str,paths));self.scan=None;self.form=None;self.operation=None;self._closing=False;self.conflict_signature=None;self.conflict_controls=[]
        self.batch_raw=len(self.paths)>1;self._intake_width=860 if self.batch_raw else 680
        self.setWindowTitle('确认收录');self.resize(620,360);self.setMinimumWidth(560);self.setProperty('intakeSheet',True);apply_to(self)
        root=QVBoxLayout(self);root.setContentsMargins(12,10,12,10);root.setSpacing(7)
        modes=QHBoxLayout();self.raw_radio=QRadioButton('保存原包');self.extract_radio=QRadioButton('解压后分类');modes.addWidget(self.raw_radio);modes.addWidget(self.extract_radio);modes.addStretch();root.addLayout(modes)
        self.note=label('核对软件、材料清单和保存位置');root.insertWidget(0,self.note)
        self.raw_body=QWidget();raw=QVBoxLayout(self.raw_body);raw.setContentsMargins(0,0,0,0);raw.setSpacing(5);self.raw_controls=[]
        tasks=service.production.rows()
        from original_zip_intake import OriginalZipIntake
        self.raw_snapshots={t['id']:OriginalZipIntake(service).snapshot([t]) for t in tasks}
        for path in self.paths:
            p=Path(path);file_label=label('文件：'+p.name);file_label.setToolTip(str(p));raw.addWidget(file_label)
            line=QHBoxLayout();name=QLineEdit(p.stem);name.setPlaceholderText('软件名称');target=WorkspaceComboBox();target.addItem('新建收纳任务',None)
            for task in tasks:target.addItem(f"WB-{task['id']:06d} · {task['name']}",task['id'])
            matches=[t for t in tasks if normalized(t['name'])==normalized(p.stem)]
            if len(matches)==1:target.setCurrentIndex(target.findData(matches[0]['id']))
            if len(matches)>1:target.insertItem(0,'请选择同名任务…','choose');target.setCurrentIndex(0)
            action=WorkspaceComboBox();action.addItem('替换当前原包','overwrite');action.addItem('新建独立任务','new');action.addItem('跳过','skip')
            target.setMinimumContentsLength(12);target.setSizeAdjustPolicy(target.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);target.setMaximumWidth(240)
            line.addWidget(label('软件名称'));line.addWidget(name,2);line.addWidget(target,3);target.setVisible(bool(tasks));target.setToolTip('选择已有任务，或新建收纳任务');line.addWidget(action,2);raw.addLayout(line)
            item=dict(path=str(p),name=name,target=target,action=action,identity=identity(p),has_matches=bool(matches));self.raw_controls.append(item)
            def chosen(*args,item=item):
                task=next((t for t in tasks if t['id']==item['target'].currentData()),None)
                item['name'].setReadOnly(bool(task));item['action'].setVisible(bool(task))
                if task:
                    item['name'].setText(task['name']);item['action'].setItemText(0,'替换当前原包' if task.get('original_zips') else '存入此任务')
            target.currentIndexChanged.connect(chosen);chosen()
        from intake_rule_options import archive_rules
        raw_rules=[r for r in archive_rules(service) if all(Path(p).suffix.casefold() in r['extensions'] for p in self.paths)]
        if not raw_rules:
            name='压缩包收纳';used={r['name'] for r in service.classifications.profiles(include_archives=True)};number=2
            while name in used:name='压缩包收纳（'+str(number)+'）';number+=1
            raw_rules=[dict(name=name,kinds=['压缩包.zip'],intake_mode='original',extensions=['.zip','.7z','.rar'])]
        self.archive_picker=RulePicker([*raw_rules,*service.classifications.profiles(True)],self)
        self.archive_picker.hide()
        self.archive_selection=QWidget();selection=QHBoxLayout(self.archive_selection);selection.setContentsMargins(0,0,0,0);selection.addWidget(label('收录规则'))
        self.archive_selector=WorkspaceComboBox()
        for record in raw_rules:self.archive_selector.addItem(record['name'],record['name'])
        self.archive_selector.addItem('＋ 新建规则','__new__');selection.addWidget(self.archive_selector,1);root.addWidget(self.archive_selection)
        self.archive_selector.currentIndexChanged.connect(self.select_archive_rule)

        self.archive_editor=QuickRuleEditor(['.zip'],self,archive_only=True);self.archive_editor.format.setCurrentIndex(self.archive_editor.format.findData('__archive__'));self.archive_editor.name.setText('软件运行包');self.archive_editor.hide();root.addWidget(self.archive_editor)
        self.new_rule=chrome_button('选择已有规则');self.new_rule.hide()
        self.new_rule.clicked.connect(self.start_archive_rule)
        self.archive_picker.changed.connect(self.archive_rule_changed)
        self.archive_editor.changed.connect(self.refresh_location)
        self.archive_editor.choose_existing.show();self.archive_editor.choose_existing.clicked.connect(self.start_archive_rule)
        self.archive_picker.select(raw_rules[0]['name'])
        self.location=label();self.location.hide();root.addWidget(self.location)
        self.raw_locations=QWidget();locations=QVBoxLayout(self.raw_locations);locations.setContentsMargins(0,0,0,0);locations.setSpacing(6)
        if self.batch_raw:
            self.batch_locations=BatchArchiveLocations(service,self.raw_controls,self.raw_locations);locations.addWidget(self.batch_locations)
        else:
            for item in self.raw_controls:
                output=SaveLocationEditor(service,item['name'].text(),Path(item['path']).suffix,self.raw_locations)
                item['save_location']=output;item['name'].textChanged.connect(output.set_software)
                pid=item['target'].currentData()
                if type(pid) is int:
                    with service.db.connect() as c:
                        old=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id LIMIT 1',(pid,)).fetchone()
                    if old:output.set_existing(Path(old[0]).parent)
                locations.addWidget(output)
        root.addWidget(self.raw_locations)
        for item in self.raw_controls:
            item['name'].textChanged.connect(self.refresh_location);item['target'].currentIndexChanged.connect(self.refresh_location)
        self.refresh_location()
        self.raw_scroll=QScrollArea();self.raw_scroll.setWidgetResizable(True);self.raw_scroll.setFrameShape(QScrollArea.NoFrame);self.raw_scroll.setWidget(self.raw_body);self.raw_scroll.setFixedHeight(min(160,62*len(self.paths)));root.insertWidget(1,self.raw_scroll)
        self.scan_note=label('');root.addWidget(self.scan_note);self.scan_note.hide()
        self.extract_body=QWidget();self.extract_layout=QVBoxLayout(self.extract_body);self.extract_layout.setContentsMargins(0,0,0,0);root.addWidget(self.extract_body);self.extract_body.hide()
        self.conflicts=QWidget();self.conflict_layout=QVBoxLayout(self.conflicts);self.conflict_layout.setContentsMargins(0,0,0,0);root.addWidget(self.conflicts);self.conflicts.hide()
        self.error=label();root.addWidget(self.error);self.error.hide()
        actions=QHBoxLayout();self.all_replace=chrome_button('全部替换');self.all_replace.clicked.connect(self.replace_all);self.all_replace.hide();actions.addWidget(self.all_replace);actions.addStretch();self.cancel_button=chrome_button('取消');self.save_button=chrome_button('保存原包',variant='primary');actions.addWidget(self.cancel_button);actions.addWidget(self.save_button);root.addLayout(actions)
        self.cancel_button.clicked.connect(self.reject);self.save_button.clicked.connect(self.confirm)
        self.raw_radio.toggled.connect(self.mode_changed);self.raw_radio.setChecked(True)
        if any(Path(p).suffix.casefold()=='.rar' for p in self.paths):
            self.extract_radio.setEnabled(False);self.note.setText('RAR 可直接保存原包；需要分类时，请先手工解压再拖入文件夹。')
            for record in self.archive_picker.records:
                if record.get('intake_mode')!='original':
                    b=self.archive_picker.buttons[record['name']];b.setEnabled(False);b.setToolTip('RAR 请先手工解压，再拖入材料分类。')
        if start_mode=='extract' and self.extract_radio.isEnabled():self.extract_radio.setChecked(True)
        from intake_folder_ui import FolderPanel
        self.raw_folders=FolderPanel(service,[*raw_rules,*service.classifications.profiles(True)],self)
        self.raw_destination=self.raw_folders.ensure(raw_rules[0]);root.insertWidget(1,self.raw_folders)
        self.raw_folders.created.connect(self.raw_folder_created);self.raw_folders.rule_changed.connect(self.raw_folder_rule_changed);self.raw_folders.changed.connect(self.raw_folder_paths)
        self.raw_folder_paths()
        self.archive_selection.hide()
        for item in self.raw_controls:item['save_location'].root.textChanged.connect(self.raw_root_edited)
        if self.batch_raw:
            row=self.batch_locations.layout().itemAt(0).layout()
            for i in range(row.count()):
                w=row.itemAt(i).widget()
                if w:w.hide()

        from folder_route_ui import install_raw
        install_raw(self)

    def raw_root_edited(self,*_):
        if not getattr(self,'_setting_raw_root',False):self._raw_location_explicit=True;self._legacy_raw_root_changed=True

    def raw_folder_created(self,key,profile,apply):
        if apply:self.raw_destination=key;self.raw_folder_rule_changed(key,profile,explicit=True)

    def raw_folder_rule_changed(self,key,profile,explicit=False):
        if key!=self.raw_destination:return
        if getattr(self,'_whole_files_profile',None)==profile and not self.raw_radio.isChecked():return
        from file_families import family_for
        pure_archive=len(profile['kinds'])==1 and all(profile.get('family_map',{}).get(k)=='archive' or family_for(Path(k).suffix)=='archive' for k in profile['kinds'])
        if not pure_archive or not profile.get('_remember',True):
            if self.scan and self.scan.isRunning():self.fail('请等待读取结束后再选择材料清单。');return
            self._whole_files_profile=deepcopy(profile)
            if self.form is not None:self.form.deleteLater();self.form=None
            self.scan=None;self.extract_radio.setText('按材料清单收录（原包不解压）');self.extract_radio.setEnabled(True);self.extract_radio.setChecked(True)
        self.raw_folder_paths(explicit=explicit)

    def raw_folder_paths(self,*_,explicit=False):
        if not hasattr(self,'raw_folders'):return
        if explicit:self._raw_location_explicit=True
        directory=Path(self.raw_folders.root.text())/self.raw_folders.items[self.raw_destination]['name']
        self._setting_raw_root=True
        try:
            for item in self.raw_controls:
                if explicit or item['target'].currentData() is None:item['save_location'].root.setText(str(directory))
                if item.get('route') and not self.raw_folders.items[self.raw_destination].get('_direct'):
                    route=item['route'];route.root.setText(str(directory.parent));route.directory.setText(directory.name);route.dirty=True;route.unresolved=False;route.update_preview()
        finally:self._setting_raw_root=False
        if self.raw_radio.isChecked():
            editing=self.raw_folders.editor.isVisible();self.raw_locations.setVisible(not editing);self.raw_scroll.setVisible(not editing and not self.batch_raw);self.save_button.setEnabled(not editing)
        self.fit_content()

    def select_archive_rule(self,*_):
        value=self.archive_selector.currentData();is_new=value=='__new__'
        self.archive_selection.setVisible(not is_new);self.archive_editor.setVisible(is_new)
        if not is_new:self.archive_picker.select(value)
        self.refresh_location();self.fit_content()

    def archive_draft(self):
        from intake_rule_options import validate_archive_rule
        if hasattr(self,'raw_folders') and self.archive_selector.currentData()!='__new__':
            selected=self.raw_folders.items[self.raw_destination]['rule']
            from file_formats import ARCHIVES
            return validate_archive_rule(selected['name'],selected.get('extensions') or list(ARCHIVES))
        selected=self.archive_editor.draft() if self.archive_selector.currentData()=='__new__' else self.archive_picker.current()
        if not selected or selected.get('intake_mode')!='original':raise ValueError('请选择压缩包收录规则，或切换解压后分类。')
        return validate_archive_rule(selected['name'],selected['extensions'])

    def start_archive_rule(self):
        self.archive_selector.setCurrentIndex(0);self.archive_selection.show();self.archive_editor.hide()
        self.refresh_location();self.fit_content()

    def archive_rule_changed(self,record):
        if record.get('intake_mode')!='original':
            self._pending_material_rule=record['name']
            if hasattr(self,'save_button'):self.extract_radio.setChecked(True)
        elif hasattr(self,'save_button'):self.raw_radio.setChecked(True)
        self.refresh_location()

    def refresh_location(self,*args):
        if not hasattr(self,'location'):return
        from material_collection import segment
        try:
            rule=self.archive_draft();paths=[]
            for item in self.raw_controls:
                if item.get('route'):item['route'].set_profile(rule)
                target=item['target'].currentData()
                with self.service.db.connect() as c:
                    existing=c.execute('SELECT directory FROM task_raw_rules WHERE project_id=?',(target,)).fetchone() if type(target) is int else None
                    old=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id LIMIT 1',(target,)).fetchone() if type(target) is int else None
                path=Path(existing[0]) if existing else Path(old[0]).parent if old else Path(self.service.workspaces.settings()['work_root'])/segment(rule['name'])/segment(item['name'].text() or '软件名称')
                paths.append(str(path))
            self.location.setText('保存位置：'+('\n'.join(dict.fromkeys(paths))));self.location.setToolTip('同名独立任务自动使用不同文件夹；原文件名保留。')
        except ValueError:self.location.setText('请填写规则名称并选择允许的格式。')

    def fail(self,message):self.error.setText(str(message));self.error.setVisible(bool(message))

    def mode_changed(self,*args):
        raw=self.raw_radio.isChecked();self.raw_scroll.setVisible(raw and not self.batch_raw);self.raw_body.setVisible(raw and not self.batch_raw);self.extract_body.setVisible(not raw);self.conflicts.hide();self.all_replace.setVisible(raw and self.batch_raw and sum(type(i['target'].currentData()) is int for i in self.raw_controls)>1);self.save_button.setText('保存原包' if raw else '确认收录');self.fail('')
        if raw and (self.archive_picker.current() or {}).get('intake_mode')!='original':
            self.archive_picker.select(next(r['name'] for r in self.archive_picker.records if r.get('intake_mode')=='original'))
        self.note.setText('核对软件、材料清单和保存位置' if raw else '确认包内材料；缺少的材料可稍后补录。')
        self.archive_picker.hide();self.new_rule.hide();self.location.hide();is_new=self.archive_selector.currentData()=='__new__'
        self.archive_selection.setVisible(raw and not is_new and not hasattr(self,'raw_folders'));self.archive_editor.setVisible(raw and is_new);self.raw_locations.setVisible(raw)
        self.save_button.setText('确认批量收录' if raw and self.batch_raw else '确认收录')
        if hasattr(self,'raw_folders'):self.raw_folders.setVisible(raw and not any(item.get('route') for item in self.raw_controls))
        self.save_button.setEnabled(raw or self.form is not None);self.scan_note.setVisible(not raw and self.form is None)
        if not raw and self.form is None and self.scan is None:
            self.scan_note.setText('正在识别材料，请稍候…');self.scan=ZipScan(self.service,self.paths,self,whole_files=bool(getattr(self,'_whole_files_profile',None)));self.scan.ready.connect(self.scanned);self.scan.failed.connect(self.scan_failed);self.scan.finished.connect(self.scan_finished);self.scan.start()
        QTimer.singleShot(0,self.fit_content)

    def scanned(self,plan):
        self.form=CompactMaterialForm(self.service,plan,self.extract_body);self.extract_layout.addWidget(self.form);self.form.show();self.scan_note.hide();self.save_button.setEnabled(True)
        if hasattr(self,'raw_folders') or hasattr(self,'batch_locations'):
            common=self.batch_locations.root if hasattr(self,'batch_locations') and getattr(self,'_legacy_raw_root_changed',False) else self.raw_folders.root if hasattr(self,'raw_folders') else self.batch_locations.root;out=self.form.save_location.root
            out.setText(common.text());common.textChanged.connect(out.setText);out.textChanged.connect(common.setText)
        selected=getattr(self,'_pending_material_rule',None)
        if selected and self.form.single:self.form.category_selector.setCurrentIndex(self.form.category_selector.findData(selected))
        whole=getattr(self,'_whole_files_profile',None)
        if whole:
            view=self.form.grouped_view;view.folders.items.update(deepcopy(self.raw_folders.items));view.folders.profiles.update(deepcopy(self.raw_folders.profiles));view.folders.root.setText(self.raw_folders.root.text());view.folders.refresh_rows()
            for g in view.groups:g['destination']=self.raw_destination;g['location_dirty']=True;view.assign(g,whole)
            view.refresh()
        QTimer.singleShot(0,self.fit_content)

    def fit_content(self):
        width=min(getattr(self,'_intake_width',680),self.screen().availableGeometry().width()-32)
        self.layout().activate();self.resize(width,min(self.screen().availableGeometry().height()-60,max(180,self.layout().totalHeightForWidth(width) if self.layout().hasHeightForWidth() else self.sizeHint().height())))

    def scan_failed(self,message):
        self.scan_note.setText('包内识别未完成。可切换“原包直接保存”，或重新选择文件。');self.fail(message)

    def discard_preview(self):
        if self.form is not None:
            from storage_management import finish_plan
            try:finish_plan(self.service,self.form.proposal,discard=True)
            except (ValueError,OSError):pass

    def scan_finished(self):
        if self._closing:self.discard_preview();super().reject()
        elif getattr(self,'_pending_accept',False):self.discard_preview();super().accept()

    def accept(self):
        if self.scan and self.scan.isRunning():
            self._pending_accept=True;self.scan.cancel.set();self.save_button.setEnabled(False);self.cancel_button.setEnabled(False);return
        super().accept()

    def reject(self):
        if self.scan and self.scan.isRunning():
            self._closing=True;self.scan.cancel.set();self.cancel_button.setEnabled(False);self.save_button.setEnabled(False);self.note.setText('正在停止读取，原文件保留…');return
        self.discard_preview();super().reject()

    def closeEvent(self,event):
        if self.scan and self.scan.isRunning():event.ignore();self.reject()
        else:super().closeEvent(event)

    def raw_rows(self):
        result=[]
        for item in self.raw_controls:
            if item.get('skip_button') and item['skip_button'].isChecked():continue
            target=item['target'].currentData()
            if target is not None and item['action'].currentData()=='skip':continue
            if target=='choose':raise ValueError('请选择原包所属的同名任务，或选择新建收纳任务。')
            name=item['name'].text().strip()
            if not name or len(name)>100 or any(c in name for c in '/\\<>:"|?*'):raise ValueError('请填写有效的软件名称，最多100字。')
            storage=item['route'].storage(item['save_location'].base.text()) if item.get('route') else item['save_location'].spec()
            if type(target) is int and not getattr(self,'_raw_location_explicit',False) and not (item.get('route') and item['route'].dirty):
                from intake_storage import get
                with self.service.db.connect() as c:
                    prior=get(c,target)
                    if not prior:
                        old=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id DESC LIMIT 1',(target,)).fetchone()
                        if old:prior=dict(directory=str(Path(old[0]).parent))
                if prior:
                    directory=Path(prior['directory']);storage.update(root=str(directory.parent),folder=directory.name,directory=str(directory))
            result.append(dict(path=item['path'],name=name,project_id=target,action=item['action'].currentData() if target is not None else 'new',identity=item['identity'],expected=self.raw_snapshots.get(target),storage=storage))
        if not result:raise ValueError('本次已全部跳过，可直接取消。')
        return result

    def show_conflicts(self,groups):
        while self.conflict_layout.count():
            child=self.conflict_layout.takeAt(0)
            if child.widget():child.widget().deleteLater()
        self.conflict_controls=[];self.conflict_layout.addWidget(label('以下软件已有材料，请选择如何保存：'))
        for group in groups:
            line=QWidget();layout=QHBoxLayout(line);layout.setContentsMargins(0,0,0,0);layout.addWidget(label(group['name']),1);tasks=WorkspaceComboBox()
            for task in group['candidates']:tasks.addItem(f"WB-{task['id']:06d} · {task['batch_name']}",task['id'])
            action=WorkspaceComboBox();action.addItem('请选择处理方式…',None);action.addItem('替换已有材料','overwrite');action.addItem('新建独立任务','new');action.addItem('跳过','skip');layout.addWidget(tasks);layout.addWidget(action);self.conflict_layout.addWidget(line);self.conflict_controls.append((group,tasks,action))
        self.conflicts.show();self.all_replace.setVisible(len(groups)>1 or self.form.table.rowCount()>1);self.save_button.setText('按选择收录');self.resize(700,min(700,self.screen().availableGeometry().height()-60))

    def replace_all(self):
        if self.raw_radio.isChecked():
            for item in self.raw_controls:
                if type(item['target'].currentData()) is int:item['action'].setCurrentIndex(item['action'].findData('overwrite'))
            return
        for group,tasks,action in self.conflict_controls:
            if len(group['candidates'])==1:action.setCurrentIndex(action.findData('overwrite'))
        if any(len(g['candidates'])>1 for g,_,_ in self.conflict_controls):self.fail('同名任务有多条，请确认所属任务后选择替换。')

    def confirm(self):
        try:
            if self.raw_radio.isChecked():
                from zip_intake import confirm_raw
                self.discard_preview()
                from folder_route_ui import raw_snapshot
                service=self.service;folders=raw_snapshot(self);rows=self.raw_rows();rule=self.archive_draft()
                from zip_intake import confirm_grouped_raw
                self.operation=lambda **kw:confirm_grouped_raw(service,rows,archive_rule=rule,folders=folders,**kw)
            else:
                if self.form is None:raise ValueError('请等待包内识别完成，或切换原包直接保存。')
                from zip_intake import extraction_conflicts,confirm_materials
                rows=self.form.values();plan=self.form.preview_plan();batch=self.form.batch.text();groups=extraction_conflicts(self.service,plan,rows,batch)
                signature=repr([(g['key'],g['snapshot']) for g in groups])
                if groups and signature!=self.conflict_signature:self.conflict_signature=signature;self.show_conflicts(groups);return
                decisions={};expected={};skip=set()
                for group,tasks,action in self.conflict_controls if groups else []:
                    selected=action.currentData()
                    if not selected:raise ValueError('请选择重复材料的处理方式。')
                    if selected=='skip':skip.add(group['key']);continue
                    decisions[group['key']]=dict(action=selected,project_id=tasks.currentData());expected[group['key']]=group['snapshot']
                rows=[r for r in rows if ((normalized(r.get('batch',batch))+'\x1f' if plan.get('split_batches') else '')+normalized(r['name'])) not in skip]
                if not rows:raise ValueError('本次已全部跳过，可直接取消。')
                service=self.service;drafts=self.form.drafts();folders=self.form.grouped_view.snapshot()
                from zip_intake import confirm_grouped_materials
                self.operation=lambda **kw:confirm_grouped_materials(service,plan,rows,batch,folders=folders,drafts=drafts,decisions=decisions or None,expected=expected or None,**kw)
            self.accept()
        except (ValueError,OSError,TypeError) as error:self.fail(error)


class MaterialIntakeDialog(ZipIntakeDialog):
    def __init__(self,service,plan,parent=None):
        QDialog.__init__(self,parent);self.service=service;self.paths=[];self.scan=None;self.form=None;self.operation=None;self._closing=False;self.conflict_signature=None;self.conflict_controls=[]
        self._intake_width=800 if any(e.get('suggested_draft') for e in plan['entries']) else 680
        self.setWindowTitle('确认收录');self.resize(620,350);self.setProperty('intakeSheet',True);apply_to(self)
        root=QVBoxLayout(self);root.setContentsMargins(10,8,10,8);root.setSpacing(5)
        self.raw_radio=QRadioButton(self);self.raw_radio.hide();self.extract_radio=QRadioButton(self);self.extract_radio.hide()
        self.note=label('核对软件、材料清单和保存位置');root.addWidget(self.note)
        self.extract_body=QWidget();self.extract_layout=QVBoxLayout(self.extract_body);self.extract_layout.setContentsMargins(0,0,0,0);root.addWidget(self.extract_body)
        self.form=CompactMaterialForm(service,plan,self.extract_body);self.extract_layout.addWidget(self.form)
        self.conflicts=QWidget();self.conflict_layout=QVBoxLayout(self.conflicts);self.conflict_layout.setContentsMargins(0,0,0,0);root.addWidget(self.conflicts);self.conflicts.hide()
        self.error=label();self.error.hide();root.addWidget(self.error)
        actions=QHBoxLayout();self.all_replace=chrome_button('全部替换');self.all_replace.clicked.connect(self.replace_all);self.all_replace.hide();actions.addWidget(self.all_replace);actions.addStretch()
        self.cancel_button=chrome_button('取消');self.save_button=chrome_button('确认收录',variant='primary');actions.addWidget(self.cancel_button);actions.addWidget(self.save_button);root.addLayout(actions)
        self.cancel_button.clicked.connect(self.reject);self.save_button.clicked.connect(self.confirm);QTimer.singleShot(0,self.fit_content)


class OriginalZipDetailsDialog(QDialog):
    def __init__(self,page,row):
        from production_ui import local_time
        super().__init__(page);self.page=page;self.row=row;self.archives=row['original_zips'];self.setWindowTitle('材料详情');self.resize(560,220);self.setObjectName('archiveDetails');self.setProperty('intakeSheet',True);apply_to(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,8,10,8);layout.setSpacing(5)
        self.heading=label(row['name']);self.heading.setObjectName('archiveTitle');layout.addWidget(self.heading)
        layout.addWidget(label(f"已收纳 · 压缩包收纳 · WB-{row['id']:06d}"))
        self.table=QTableWidget(len(self.archives),3);self.table.setHorizontalHeaderLabels(['版本','压缩包文件','收录时间']);self.table.verticalHeader().hide();self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.table.setColumnWidth(0,40);self.table.setColumnWidth(2,92);self.table.setWordWrap(False);self.table.setTextElideMode(Qt.ElideMiddle)
        for i,z in enumerate(self.archives):
            for j,text in enumerate([f'v{len(self.archives)-i}',Path(z['path']).name,local_time(z['created_at'])]):
                item=QTableWidgetItem(text);item.setToolTip(z['path']);self.table.setItem(i,j,item)
            self.table.setRowHeight(i,30)
        layout.addWidget(self.table);self.location=QLineEdit();self.location.setReadOnly(True);self.location.setFixedHeight(25);layout.addWidget(self.location);self.error=label();self.error.hide();layout.addWidget(self.error)
        actions=QHBoxLayout();self.open_button=chrome_button('打开原包');self.folder_button=chrome_button('打开保存位置');self.extract_button=chrome_button('解压并分类',variant='primary');close=chrome_button('关闭')
        for b in (self.open_button,self.folder_button,self.extract_button):b.setParent(self);b.hide()
        hint=label('双击打开文件 · 右键查看操作');hint.setWordWrap(False);actions.addWidget(hint,1);actions.addWidget(close);close.setProperty('fontPixelSize',11);close.setFixedHeight(26)
        layout.addLayout(actions);close.clicked.connect(self.reject);self.open_button.clicked.connect(self.open_original);self.folder_button.clicked.connect(self.open_folder);self.extract_button.clicked.connect(self.extract);self.table.itemSelectionChanged.connect(self.selected);self.table.cellDoubleClicked.connect(lambda *_:self.open_file())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu);self.table.customContextMenuRequested.connect(self.context_menu)
        from theme import theme_signal
        theme_signal().connect(self.apply_theme);self.apply_theme()
        if self.archives:self.table.selectRow(0)

    def apply_theme(self,*_):
        from theme import tokens
        c=tokens();self.setStyleSheet(f"QDialog#archiveDetails {{background:{c['surface']};}} QDialog#archiveDetails QLabel,QDialog#archiveDetails QLineEdit,QDialog#archiveDetails QTableWidget,QDialog#archiveDetails QHeaderView::section {{font-size:11px;color:{c['ink']};}} QDialog#archiveDetails QLabel#archiveTitle {{font-size:12px;font-weight:500;}} QDialog#archiveDetails QHeaderView::section {{padding:4px;}}")
        for b in (self.open_button,self.folder_button,self.extract_button):b.setProperty('fontPixelSize',11)

    def context_menu(self,point):
        index=self.table.indexAt(point)
        if not index.isValid():return
        self.table.selectRow(index.row());menu=self.file_menu()
        try:menu.exec(self.table.viewport().mapToGlobal(point))
        finally:menu.deleteLater()

    def file_menu(self):
        from material_context import MaterialMenu
        menu=MaterialMenu(self);menu.addAction('打开文件',self.open_file);menu.addAction('打开原始位置',self.open_original);menu.addAction('打开保存位置',self.open_folder);menu.addSeparator();menu.addAction('解压并整理',self.extract)
        return menu

    def report_error(self,error):self.error.setText(str(error));self.error.setVisible(bool(str(error)))

    def open_original(self):
        from saved_locations import original_file_location,open_location
        try:
            i=self.table.currentRow()
            if not 0<=i<len(self.archives):raise ValueError('请选择一个压缩包。')
            open_location(original_file_location(self.page.service,self.row['id'],zip_id=self.archives[i]['id']));self.report_error('')
        except (ValueError,OSError) as error:self.report_error(error)

    def path(self):
        i=self.table.currentRow()
        if not 0<=i<len(self.archives):raise ValueError('请选择一个压缩包。')
        path=Path(self.archives[i]['path'])
        if not path.is_file():raise ValueError('该原包已移动或缺失，请重新收录。')
        return path

    def selected(self):
        i=self.table.currentRow();self.location.setText(self.archives[i]['path'] if 0<=i<len(self.archives) else '');self.location.setCursorPosition(0)

    def open_file(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        try:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path()))):raise OSError('无法打开压缩包，请检查默认打开软件。')
            self.report_error('')
        except (ValueError,OSError) as error:self.report_error(error)

    def open_folder(self):
        from saved_locations import open_location
        try:open_location(self.path());self.report_error('')
        except (ValueError,OSError) as error:self.report_error(error)

    def extract(self):
        try:
            path=self.path()
            from file_formats import EXTRACTABLE
            if path.suffix.casefold() not in EXTRACTABLE:raise ValueError('此原包请先手工解压，再拖入文件夹收录。')
            dialog=ZipIntakeDialog(self.page.service,[str(path)],self,start_mode='extract')
            try:
                if dialog.exec()!=QDialog.Accepted:return
                operation=dialog.operation
            finally:dialog.deleteLater()
            self.page.request('原包解压分类收录',operation);self.accept()
        except (ValueError,OSError) as error:self.report_error(error)
