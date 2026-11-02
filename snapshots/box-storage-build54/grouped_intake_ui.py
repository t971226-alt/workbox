"""Software-first confirmation over the durable intake/duplicate engine."""
from pathlib import Path
from copy import deepcopy
from collections import OrderedDict
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QCheckBox,
 QScrollArea,QFrame)
from workspace_chrome import WorkspaceComboBox,chrome_button
from intake_folder_ui import FolderPanel
from intake_folders import title
from file_families import compatible
from name_patterns import norm,stem_of
from rule_chrome import RuleTypes,FileBadge

def text(value=''):
    label=QLabel(value);label.setTextFormat(Qt.PlainText);label.setWordWrap(True);return label

class GroupedMaterialView(QWidget):
    def __init__(self,engine):
        super().__init__(engine);self.engine=engine;self.ready=False;self.syncing=False;self.groups=[];self.queued=False
        self.setStyleSheet('QFrame#intakeSoftwareCard,QFrame#folderEditor{border:1px solid palette(mid);border-radius:6px;background:palette(base);} QPushButton#softwareDisclosure{text-align:left;border:0;background:transparent;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        line=QHBoxLayout();self.summary=text();line.addWidget(self.summary,1);self.all=QCheckBox('全选');line.addWidget(self.all);layout.addLayout(line)
        self.scroll=QScrollArea();self.scroll.setFrameShape(QFrame.NoFrame);self.scroll.setWidgetResizable(True);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body=QWidget();self.rows=QVBoxLayout(self.body);self.rows.setContentsMargins(0,0,4,0);self.rows.setSpacing(7);self.scroll.setWidget(self.body);layout.addWidget(self.scroll)
        self.folders=FolderPanel(engine.service,list(engine.profiles.values()),self.body);self.rows.addWidget(self.folders)
        # The complete destination is shown inside each software group.
        # Keep the shared root model, but expose its picker beside group settings.
        top=self.folders.layout().itemAt(0).layout()
        for n in range(top.count()):
            if top.itemAt(n).widget():top.itemAt(n).widget().hide()
        self.folders.root.setText(engine.save_location.root.text());self.folders.root.textChanged.connect(engine.save_location.root.setText);engine.save_location.root.textChanged.connect(self.folders.root.setText)
        groups=OrderedDict()
        for i,e in enumerate(engine.proposal['entries']):groups.setdefault((e.get('batch_key',''),norm(e['name'])),[]).append(i)
        for indices in groups.values():self.add_group(indices)
        self.rows.addStretch();self.all.stateChanged.connect(lambda value:self.select_all(value!=0));self.folders.changed.connect(self.schedule);self.folders.created.connect(self.created);self.folders.rule_changed.connect(self.folder_rule_changed)
        engine.table.itemChanged.connect(self.schedule)
        for combo in [*engine.class_controls,*engine.kind_controls]:combo.currentIndexChanged.connect(self.schedule)
        engine.quick_editor.changed.connect(self.schedule);engine.save_rule.toggled.connect(self.schedule)
        self.ready=True
        for g in self.groups:self.allocate(g,self.profile(g))
        engine.choose_default_versions();self.refresh();QTimer.singleShot(0,self.fit)
        from intake_onboarding import install
        install(self)
        from folder_route_ui import install_material
        install_material(self)
    def profile(self,g):
        e=self.engine;i=g['indices'][0];entry=e.proposal['entries'][i]
        if e.existing_row(i):
            return dict(name=e.class_controls[i].currentData(),kinds=entry['kinds'],family_map={**entry.get('existing_families',{}),**entry.get('extra_families',{})},name_patterns={**entry.get('existing_patterns',{}),**entry.get('extra_patterns',{})})
        return e.quick_editor.draft() if e.class_controls[i].currentData()=='__new__' else e.profiles.get(e.class_controls[i].currentData(),dict(name='待确认',kinds=entry['kinds']))
    def add_group(self,indices):
        e=self.engine;i=indices[0];entry=e.proposal['entries'][i]
        g=dict(indices=indices,location_dirty=False,custom_bases={},saved=None)
        g['card']=QFrame();g['card'].setObjectName('intakeSoftwareCard');box=QVBoxLayout(g['card']);box.setContentsMargins(9,8,9,8);box.setSpacing(5)
        row=QHBoxLayout();g['check']=QCheckBox();row.addWidget(g['check']);g['expand']=chrome_button(entry['name']);g['expand'].setObjectName('softwareDisclosure');g['expand'].setProperty('expandableGreen',True);g['expand'].setToolTip('展开或收起材料明细');g['expand'].setCheckable(True);row.addWidget(g['expand'],1);g['status']=text();row.addWidget(g['status']);box.addLayout(row)
        row=QHBoxLayout();g['rule_label']=text();row.addWidget(g['rule_label'],1);change=chrome_button('更换');change.setCheckable(True);row.addWidget(change);box.addLayout(row)
        g['options']=QWidget();opts=QVBoxLayout(g['options']);opts.setContentsMargins(0,0,0,0);opts.setSpacing(5)
        g['folder_combo']=WorkspaceComboBox();g['profile_combo']=WorkspaceComboBox();g['software']=QLineEdit(entry['name']);g['folder_name']=QLineEdit(entry['name'])
        for caption,control in [('保存文件夹',g['folder_combo']),('要收哪些文件',g['profile_combo']),('软件名称',g['software']),('软件子文件夹',g['folder_name'])]:
            line=QHBoxLayout();label=text(caption);label.setFixedWidth(86);line.addWidget(label);line.addWidget(control,1);opts.addLayout(line)
        g['options'].hide();change.toggled.connect(g['options'].setVisible);change.toggled.connect(self.fit_later);box.addWidget(g['options'])
        location=chrome_button('修改上级保存位置');location.clicked.connect(self.folders.browse);opts.addWidget(location,alignment=Qt.AlignLeft)
        manage=chrome_button('管理保存文件夹');manage.clicked.connect(lambda:self.folders.toggle.setChecked(not self.folders.toggle.isChecked()));opts.addWidget(manage,alignment=Qt.AlignLeft)
        g['detail']=QWidget();details=QVBoxLayout(g['detail']);details.setContentsMargins(0,2,0,0);details.setSpacing(5);g['detail'].hide();box.addWidget(g['detail']);g['expand'].toggled.connect(g['detail'].setVisible);g['expand'].toggled.connect(self.fit_later);g['expand'].toggled.connect(self.schedule)
        g['files']=[]
        for j in indices:
            item=e.proposal['entries'][j];file=QWidget();fl=QVBoxLayout(file);fl.setContentsMargins(0,0,0,0);fl.setSpacing(3)
            line=QHBoxLayout();check=QCheckBox();line.addWidget(check);line.addWidget(FileBadge(Path(item['path']).suffix));name=text(Path(item['path']).name);name.setToolTip(item['path']);line.addWidget(name,1);fl.addLayout(line)
            controls=QHBoxLayout();role=WorkspaceComboBox();controls.addWidget(role,1);base=QLineEdit();base.setPlaceholderText('保存文件名（自动使用软件名称）');controls.addWidget(base,1);fl.addLayout(controls);details.addWidget(file)
            check.toggled.connect(lambda checked,row=j:self.file_checked(row,checked));role.currentIndexChanged.connect(lambda _,row=j,c=role:self.role_changed(row,c));base.textEdited.connect(lambda value,row=j,g=g:self.base_edited(g,row,value));g['files'].append(dict(row=j,check=check,role=role,base=base,name=name))
        g['missing']=text();details.addWidget(g['missing']);g['path']=text();details.addWidget(g['path']);self.rows.addWidget(g['card'])
        with e.service.db.connect() as c:
            from intake_storage import get
            if entry.get('existing'):
                g['saved']=get(c,entry['existing']['id'])
                if not g['saved']:
                    old=c.execute('SELECT kind,path FROM collected_materials WHERE project_id=?',(entry['existing']['id'],)).fetchall()
                    if old:g['saved']=dict(directory=str(Path(old[0]['path']).parent),filenames={r['kind']:Path(r['path']).stem for r in old})
        if g['saved']:
            g['folder_name'].setText(Path(g['saved']['directory']).name)
        profile=self.profile(g);g['destination']=self.folders.ensure(profile)
        g['check'].stateChanged.connect(lambda value,g=g:self.group_checked(g,value!=0));g['software'].editingFinished.connect(lambda g=g:self.rename(g));g['folder_name'].textEdited.connect(lambda _,g=g:self.location_edited(g));g['folder_combo'].currentIndexChanged.connect(lambda _,g=g:self.directory_changed(g));g['profile_combo'].currentIndexChanged.connect(lambda _,g=g:self.profile_changed(g))
        self.groups.append(g)
    def register(self,profile):
        e=self.engine;p=deepcopy(profile);name=p['name']
        current=next((r for r in e.service.classifications.profiles(True) if r['name']==name),None)
        if current and all(current.get(k,{})==p.get(k,{}) for k in ('kinds','family_map','name_patterns')):p=current
        elif not p.get('_remember',True):p['_transient']=True
        e.profiles[name]=p
        e.proposal['profiles']=[r for r in e.proposal['profiles'] if r['name']!=name]+[p]
        if p.get('_remember',True) and not any(r['name']==name for r in e.service.classifications.profiles(True)):
            e.generated_drafts=[r for r in e.generated_drafts if r['name']!=name]+[p]
        for combo in e.class_controls:
            if combo.findData(name)<0:combo.addItem(title(name),name)
    def assign(self,g,profile):
        if any(self.engine.existing_row(i) for i in g['indices']):return
        if not profile.get('_transient') and not profile['name'].startswith('本次收纳 · '):g.pop('routing_rule',None)
        self.register(profile);self.syncing=True
        try:
            for i in g['indices']:
                combo=self.engine.class_controls[i];combo.blockSignals(True);combo.setCurrentIndex(combo.findData(profile['name']));combo.blockSignals(False);self.engine.fill_kinds(i)
            self.allocate(g,profile)
        finally:self.syncing=False
        if g.get('onboarding') and not g['onboarding'].choices:
            g['onboarding'].confirmed=True
        self.engine.choose_default_versions();self.schedule()
    def allocate(self,g,p):
        """Only explicit type-only slots may match arbitrary names; variants share a slot."""
        e=self.engine;type_slots=[k for k,v in p.get('name_patterns',{}).items() if v.get('mode')=='type'];used={};identities={}
        for i in g['indices']:
            path=e.proposal['entries'][i]['path'];current=e.chosen_kind(i)
            if current and current not in type_slots:used[current]=norm(stem_of(path))
        for i in g['indices']:
            path=e.proposal['entries'][i]['path'];key=(norm(stem_of(path)),Path(path).suffix.casefold())
            family=next((p.get('family_map',{}).get(k) for k in type_slots if compatible(k,path,p.get('family_map'))),None)
            ident=(norm(stem_of(path)),family or Path(path).suffix.casefold())
            options=[k for k in type_slots if compatible(k,path,p.get('family_map'))]
            if not options:continue
            chosen=identities.get(ident) or next((k for k in options if k not in used),None)
            if chosen:used[chosen]=ident;identities[ident]=chosen;c=e.kind_controls[i];c.setCurrentIndex(c.findData(chosen))
            else:e.kind_controls[i].setCurrentIndex(0)
    def created(self,key,profile,apply):
        self.register(profile)
        if apply:
            for g in self.groups:
                if any(self.checked(i) for i in g['indices']):g['destination']=key;g['location_dirty']=True;self.assign(g,profile)
        self.schedule()
    def folder_rule_changed(self,key,profile):
        for g in self.groups:
            if g['destination']==key and not g['saved']:self.assign(g,profile)
        self.schedule()
    def checked(self,i):return self.engine.table.item(i,0).checkState()==Qt.Checked
    def file_checked(self,row,checked):
        if not self.syncing:self.engine.table.item(row,0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
    def group_checked(self,g,checked):
        if self.syncing:return
        for i in g['indices']:self.file_checked(i,checked)
    def select_all(self,checked):
        if self.syncing:return
        for g in self.groups:self.group_checked(g,checked)
    def role_changed(self,row,combo):
        if self.syncing:return
        c=self.engine.kind_controls[row];c.setCurrentIndex(max(0,c.findData(combo.currentData())));self.schedule()
    def base_edited(self,g,row,value):g['custom_bases'][row]=value;self.schedule()
    def location_edited(self,g):g['location_dirty']=True;self.schedule()
    def rename(self,g):
        e=self.engine;value=g['software'].text().strip();old=e.table.item(g['indices'][0],2).text();e.loading=True
        try:
            for i in g['indices']:e.table.item(i,2).setText(value)
        finally:e.loading=False
        if g['folder_name'].text()==old:g['folder_name'].setText(value)
        if e.single:e.software.setText(value)
        if len(self.groups)==1:e.quick_editor.set_software_name(value)
        for i in g['indices']:e.class_controls[i].setEnabled(not e.existing_row(i));e.fill_kinds(i,e.chosen_kind(i))
        self.schedule()
    def directory_changed(self,g):
        if self.syncing:return
        key=g['folder_combo'].currentData()
        if key not in self.folders.items:return
        g['destination']=key;g['location_dirty']=True
        if not self.folders.items[key].get('_direct'):self.assign(g,self.folders.items[key]['rule'])
        self.schedule()
    def profile_changed(self,g):
        if self.syncing:return
        name=g['profile_combo'].currentData()
        if name in self.engine.profiles:self.assign(g,self.engine.profiles[name])
    def output(self,g,i,rows=None):
        e=self.engine;entry=e.proposal['entries'][i];software=e.table.item(i,2).text();kind=e.chosen_kind(i);suffix=Path(entry['path']).suffix
        if i in g['custom_bases']:base=g['custom_bases'][i]
        elif g['saved'] and kind in g['saved']['filenames']:base=g['saved']['filenames'][kind]
        elif e.single and e.save_location.base_dirty:base=e.save_location.base.text()
        else:
            peers={e.chosen_kind(j) for j in g['indices'] if self.checked(j) and Path(e.proposal['entries'][j]['path']).suffix.casefold()==suffix.casefold()}
            p=self.profile(g).get('name_patterns',{}).get(kind,{})
            if len(peers)<=1:base=software
            elif p.get('mode')=='same':base=software
            elif p.get('mode')=='marker':base=software+'_'+p['marker']
            else:
                stem=Path(entry['path']).stem
                if norm(stem)==norm(software):base=software
                elif norm(stem).startswith(norm(software)+' '):base=stem
                else:base=software+'_'+Path(kind or '材料').stem
        if g['saved'] and not g['location_dirty']:directory=Path(g['saved']['directory']);root=directory.parent;folder=directory.name
        else:
            root=Path(self.folders.root.text())/self.folders.items[g['destination']]['name'];folder=g['folder_name'].text()
            if e.single and e.save_location.folder_dirty and not g['location_dirty']:folder=e.save_location.folder.text()
        if getattr(self,'routes_ready',False):return g['route'].storage(base,checked=False)
        return dict(root=str(root),folder=folder,base=base)
    def apply_rows(self,rows):
        for g in self.groups:
            if g.get('onboarding'):g['onboarding'].validate()
        for indices in getattr(self.engine,'_keyword_choice_groups',[]):
            g=next(g for g in self.groups if indices[0] in g['indices'])
            if any(self.checked(i) for i in g['indices']) and not any(self.checked(i) for i in indices):raise ValueError('有多份匹配文件，请先选择需要收录的一份。')
        from intake_storage import validate
        by_path={self.engine.proposal['entries'][i]['path']:(g,i) for g in self.groups for i in g['indices']}
        for r in rows:g,i=by_path[r['path']];r['storage']=validate(self.engine.service,self.output(g,i,rows))
        return rows
    def schedule(self,*_):
        if not self.ready or self.queued:return
        self.queued=True;QTimer.singleShot(0,self.refresh)
    def refresh(self):
        self.queued=False
        if not self.ready:return
        import shiboken6
        if not shiboken6.isValid(self):return
        self.syncing=True;e=self.engine;files=0;tasks=0;pending=0
        if getattr(self,'routes_ready',False):
            from folder_route_ui import sync_material
            sync_material(self)
        if len(self.groups)==1:e.quick_editor.set_software_name(self.groups[0]['software'].text())
        try:
            for g in self.groups:
                p=self.profile(g);selected=[i for i in g['indices'] if self.checked(i)];found={e.chosen_kind(i) for i in selected if e.chosen_kind(i)};unmatched=[i for i in selected if not e.chosen_kind(i)];missing=[k for k in p['kinds'] if k not in found]
                panel=g.get('onboarding')
                if panel and p['name'] in {r['name'] for r in e.service.classifications.profiles(True)} and not unmatched and not panel.choices:
                    panel.confirmed=True;panel.hide()
                group_pending=bool(unmatched or missing)
                if selected:tasks+=1;files+=len(selected)
                g['check'].setTristate(True);g['check'].setCheckState(Qt.PartiallyChecked if 0<len(selected)<len(g['indices']) else Qt.Checked if selected else Qt.Unchecked);g['expand'].setText(('▾ ' if g['expand'].isChecked() else '▸ ')+e.table.item(g['indices'][0],2).text());g['status'].setText(str(len(unmatched))+' 份待对应' if unmatched else str(len(found))+' / '+str(len(p['kinds']))+' 份');g['rule_label'].setText('材料：'+title(p['name']))
                g['profile_combo'].blockSignals(True);g['profile_combo'].clear()
                for name in e.profiles:g['profile_combo'].addItem(title(name),name)
                if g['profile_combo'].findData(p['name'])<0:g['profile_combo'].addItem(title(p['name']),p['name'])
                g['profile_combo'].setCurrentIndex(g['profile_combo'].findData(p['name']));g['profile_combo'].setEnabled(not e.existing_row(g['indices'][0]));g['profile_combo'].blockSignals(False)
                g['folder_combo'].blockSignals(True);g['folder_combo'].clear()
                for key,item in self.folders.items.items():g['folder_combo'].addItem('当前保存位置（不加分类层）' if item.get('_direct') else item['name'],key)
                g['folder_combo'].setCurrentIndex(g['folder_combo'].findData(g['destination']));g['folder_combo'].blockSignals(False)
                for f in g['files']:
                    i=f['row'];f['check'].setChecked(self.checked(i));c=f['role'];backend=e.kind_controls[i];c.blockSignals(True);c.clear()
                    for j in range(backend.count()):c.addItem(backend.itemText(j),backend.itemData(j))
                    c.setCurrentIndex(backend.currentIndex());c.blockSignals(False)
                    if i not in g['custom_bases']:
                        try:f['base'].setText(self.output(g,i)['base'])
                        except ValueError:f['base'].setText(g['software'].text())
                g['missing'].setText(('有文件尚未对应，请选择材料项；文件会保留在本清单中。' if unmatched else '缺少：'+'、'.join(missing)+'。已有文件可先收录。') if missing or unmatched else '材料已收齐。')
                choices=[indices for indices in getattr(e,'_keyword_choice_groups',[]) if indices[0] in g['indices'] and not any(self.checked(i) for i in indices)]
                if choices:
                    g['status'].setText('多份匹配，需选择');g['missing'].setText('有多份文件符合相同关键词，请在上方勾选需要收录的1份，或点击“选择匹配文件”。')
                    if not g.get('choice_expanded'):g['expand'].setChecked(True);g['choice_expanded']=True
                if g.get('onboarding') and not g['onboarding'].confirmed:
                    g['status'].setText('首次收录，待确认' if g['onboarding'].first else '有文件未安排');group_pending=True
                if g.get('route') and g['route'].needs_binding():
                    group_pending=True
                    if not unmatched and not choices:g['status'].setText('请选择收纳目录' if g['route'].unresolved else '目录待确认')
                if selected:pending+=group_pending
                extras={k for i in g['indices'] for k in e.proposal['entries'][i].get('extra_kinds',[])}&found
                if extras:
                    note='本次新增 '+str(len(extras))+' 项，只补充本任务清单。';g['missing'].setText(g['missing'].text()+' '+note);g['rule_label'].setText(g['rule_label'].text()+' · '+note)
                try:
                    spec=self.output(g,g['indices'][0]);g['path'].setText('保存至：'+str(Path(spec['root'])/spec['folder']))
                except ValueError:g['path'].setText('请确认收纳目录。')
                if not g.get('initial_expanded'):
                    if (missing or unmatched) and self.groups.index(g)==0:g['expand'].setChecked(True)
                    g['initial_expanded']=True
            self.summary.setText(f'{tasks} 个软件 · {files} 份文件 · {pending} 个待补齐 / 待确认');self.all.setTristate(True);self.all.setCheckState(Qt.Checked if files==e.table.rowCount() else Qt.PartiallyChecked if files else Qt.Unchecked)
        finally:self.syncing=False
        self.fit_later()
    def fit_later(self,*_):QTimer.singleShot(0,self.fit)
    def fit(self):
        import shiboken6
        if not shiboken6.isValid(self):return
        self.body.layout().activate();self.scroll.setFixedHeight(min(560,max(180,self.screen().availableGeometry().height()-220),max(180,self.body.sizeHint().height()+8)))
        if hasattr(self.window(),'fit_content'):self.window().fit_content()
    def snapshot(self):
        for g in self.groups:
            if g.get('onboarding'):g['onboarding'].validate()
        if getattr(self,'routes_ready',False):
            from folder_route_ui import material_snapshot
            return material_snapshot(self)
        return self.folders.snapshot(used={g['destination'] for g in self.groups if any(self.checked(i) for i in g['indices'])})

def install(engine):
    hidden=QWidget(engine);hidden.setLayout(engine.layout());hidden.hide();engine._legacy_controls=hidden
    layout=QVBoxLayout(engine);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
    view=GroupedMaterialView(engine);engine.grouped_view=view;layout.addWidget(view);engine.setMaximumHeight(16777215)
