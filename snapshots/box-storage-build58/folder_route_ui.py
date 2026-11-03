"""Choose a bound collection directory without scanning or importing its contents."""
from pathlib import Path
from copy import deepcopy
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QFileDialog
from workspace_chrome import chrome_button,WorkspaceComboBox
from intake_folders import records,same_rule,folder_name
from intake_storage import validate,clean_name
from native_files import plain_path


def resolved_profile(service,profile):
    value=deepcopy(profile)
    current=next((p for p in service.classifications.profiles(False,include_archives=True) if p['name'].casefold()==value['name'].casefold()),None)
    if current:value['id']=current['id']
    return value


class FolderRoute(QWidget):
    changed=Signal()
    def __init__(self,service,profile,software,*,pinned=None,parent=None):
        super().__init__(parent);self.service=service;profile=resolved_profile(service,profile);self.profile=profile;self.pinned=pinned;self.dirty=False;self.loading=True;self.unresolved=False;self.catalog=records(service)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,3,0,0);outer.setSpacing(5)
        self.toggle=chrome_button('保存位置与名称 ▾');self.toggle.setCheckable(True);self.toggle.setProperty('expandableGreen',True);outer.addWidget(self.toggle)
        self.body=QWidget();layout=QVBoxLayout(self.body);layout.setContentsMargins(3,2,3,0);layout.setSpacing(5);outer.addWidget(self.body);self.toggle.toggled.connect(self.body.setVisible);self.toggle.toggled.connect(self.changed)
        self.hint=QLabel();self.hint.setWordWrap(True);layout.addWidget(self.hint)
        self.choice=WorkspaceComboBox();self.choice.addItem('新建目录，或选用已有文件夹',None)
        for r in self.catalog:
            if same_rule(r['rule'],profile):self.choice.addItem(str(Path(r['root'])/r['name']),r['id'])
        self.choice.addItem('其他位置…（选择已有收纳文件夹）','__browse__');layout.addWidget(self.choice)
        self.root=QLineEdit(str(service.workspaces.settings()['work_root']));self.root.setReadOnly(True)
        self.directory=QLineEdit();self.directory.setPlaceholderText('收纳目录名称');self.software=QLineEdit(software)
        line=QHBoxLayout();line.addWidget(QLabel('① 保存到'));line.addWidget(self.root,1);self.browse_button=chrome_button('选择上级位置');line.addWidget(self.browse_button);layout.addLayout(line)
        line=QHBoxLayout();line.addWidget(QLabel('② 收纳目录'));line.addWidget(self.directory,1);layout.addLayout(line)
        self.tree=QLabel();self.tree.setTextFormat(Qt.PlainText);self.tree.setWordWrap(True);self.tree.setStyleSheet('font-size:11px;');layout.addWidget(self.tree)
        self.names_toggle=chrome_button('调整名称 · 默认使用软件名称 ›');self.names_toggle.setCheckable(True);self.names_toggle.setProperty('expandableGreen',True);layout.addWidget(self.names_toggle)
        self.names=QWidget();names=QVBoxLayout(self.names);names.setContentsMargins(0,0,0,0);name_line=QHBoxLayout();name_line.addWidget(QLabel('软件文件夹'));name_line.addWidget(self.software,1);names.addLayout(name_line);layout.addWidget(self.names);self.names.hide();self.names_toggle.toggled.connect(self.names.setVisible);self.names_toggle.toggled.connect(self.changed)
        self.summary=QLabel();self.summary.setWordWrap(True);self.summary.setStyleSheet('font-size:11px;');outer.addWidget(self.summary)
        self.browse_button.clicked.connect(lambda:self.browse(as_parent=True));self.choice.currentIndexChanged.connect(self.select)
        self.root.textChanged.connect(self.edited);self.directory.textChanged.connect(self.edited);self.software.textChanged.connect(self.edited)
        matches=[r for r in self.catalog if same_rule(r['rule'],profile)];available=[r for r in matches if (Path(r['root'])/r['name']).is_dir()]
        if pinned:
            path=Path(pinned);self.root.setText(str(path.parent));self.directory.setText(path.name);self.software.setText(software);self.hint.setText('已有任务保留原保存位置。');self.choice.hide();self.directory.hide();self.tree.hide();self.names_toggle.hide();self.summary.setText('保存文件夹：'+str(path));self.toggle.setChecked(False);self.body.hide()
        elif len(available)==1:
            r=available[0];self.root.setText(r['root']);self.directory.setText(r['name']);self.choice.setCurrentIndex(self.choice.findData(r['id']));self.hint.setText('已根据收纳规则自动匹配目录。');self.toggle.setChecked(False);self.body.hide()
        else:
            self.directory.setText('单手册' if profile['name']=='单手册 Word' else folder_name(profile));self.unresolved=bool(matches)
            self.hint.setText('同一规则有多个目录，请选择本次使用哪个。' if len(available)>1 else '原绑定目录不存在，请重新选择位置。' if matches else '这条规则还没有目录，请确认位置和名称；确认收录后才建立并绑定。')
            self.toggle.setChecked(True);self.body.show()
        self.loading=False;self.update_preview()

    def update_preview(self):
        folder=self.directory.text().strip();software=self.software.text().strip()
        if not self.pinned or self.dirty:self.summary.setText('保存文件夹：'+str(Path(self.root.text())/folder/software))
        self.tree.setText(str(self.root.text())+'\n└─ '+(folder or '收纳目录')+' 〔绑定：'+self.profile['name']+'〕\n    └─ '+(software or '软件名称')+'\n        └─ 文件（保留原后缀）')

    def edited(self,*_):
        if self.loading:return
        self.dirty=True;self.unresolved=False;self.update_preview();self.changed.emit()

    def set_profile(self,profile):
        profile=resolved_profile(self.service,profile)
        if same_rule(self.profile,profile):return
        self.loading=True;self.profile=deepcopy(profile);self.catalog=records(self.service);self.choice.clear();self.choice.addItem('新建目录，或选用已有文件夹',None)
        candidates=[r for r in self.catalog if same_rule(r['rule'],profile)];available=[r for r in candidates if (Path(r['root'])/r['name']).is_dir()]
        for r in candidates:self.choice.addItem(str(Path(r['root'])/r['name']),r['id'])
        self.choice.addItem('其他位置…（选择已有收纳文件夹）','__browse__')
        if not self.pinned:
            self.unresolved=bool(candidates) and len(available)!=1
            if len(available)==1:
                r=available[0];self.root.setText(r['root']);self.directory.setText(r['name']);self.choice.setCurrentIndex(self.choice.findData(r['id']));self.hint.setText('已根据规则匹配绑定目录。')
            else:self.directory.setText('单手册' if profile['name']=='单手册 Word' else folder_name(profile));self.hint.setText('请确认新规则的保存位置；有多个绑定目录时需要选择。')
            self.toggle.setChecked(len(available)!=1)
        self.loading=False;self.update_preview();self.changed.emit()

    def select(self):
        if self.loading:return
        key=self.choice.currentData()
        if key=='__browse__':self.browse();return
        r=next((r for r in self.catalog if r['id']==key),None)
        if r:
            path=Path(r['root'])/r['name']
            if not path.is_dir():self.unresolved=True;self.hint.setText('该目录不存在，请重新选择位置。');self.changed.emit();return
            self.root.setText(r['root']);self.directory.setText(r['name']);self.unresolved=False;self.hint.setText('已选择绑定目录；只收录本次拖入的文件。')
        else:self.edited()

    def browse(self,*,as_parent=False):
        path=QFileDialog.getExistingDirectory(self,'选择上级保存位置' if as_parent else '选择已有收纳文件夹',self.root.text())
        if not path:return
        if as_parent:
            self.root.setText(str(plain_path(path)));self.dirty=True;self.unresolved=False;self.hint.setText('将在这个位置下创建上面填写的收纳目录；确认收录前不创建。');self.update_preview();self.changed.emit()
        else:self.choose_path(path)

    def choose_path(self,path):
        selected=plain_path(path);bindings=[r for r in self.catalog if plain_path(Path(r['root'])/r['name'])==selected]
        if bindings and not same_rule(bindings[0]['rule'],self.profile):self.hint.setText('该目录已绑定“'+bindings[0]['rule']['name']+'”，请选择其他目录，或在目录管理中明确更换绑定。');return False
        # Selecting the current parent or a drive root keeps the proposed name.
        if selected==plain_path(self.root.text()) or selected==Path(selected.anchor):
            self.root.setText(str(selected))
        else:
            with self.service.db.connect() as c:
                owners=[]
                for table,column,file in [('task_storage_preferences','directory',False),('task_raw_rules','directory',False),('collected_materials','path',True),('original_zip_receipts','path',True)]:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():owners.extend(plain_path(Path(r[0]).parent if file else r[0]) for r in c.execute('SELECT '+column+' FROM '+table))
            if selected in owners:self.hint.setText('这是软件任务的子文件夹，请选择它的上级收纳目录。');return False
            self.root.setText(str(selected.parent));self.directory.setText(selected.name)
        self.dirty=True;self.unresolved=False
        self.hint.setText('已找到绑定目录。' if bindings else '该文件夹尚未绑定规则；确认收录后绑定“'+self.profile['name']+'”。原有文件不作处理。')
        self.toggle.setChecked(True);self.update_preview();self.changed.emit();return True

    def storage(self,base,*,checked=True):
        if self.pinned and not self.dirty:
            path=Path(self.pinned);spec=dict(root=str(path.parent),folder=path.name,base=base,directory=str(path))
            return validate(self.service,spec) if checked else spec
        if self.unresolved:raise ValueError('请先为“'+self.profile['name']+'”选择本次使用的收纳目录。')
        directory=clean_name(self.directory.text(),'收纳目录名称');software=clean_name(self.software.text(),'软件文件夹')
        path=plain_path(Path(self.root.text())/directory)
        if path.exists() and not path.is_dir():raise ValueError('收纳目录位置已有同名文件，请换一个名称。')
        bindings=[r for r in self.catalog if plain_path(Path(r['root'])/r['name'])==path]
        if bindings and not same_rule(bindings[0]['rule'],self.profile):raise ValueError('这个目录已绑定其他规则，请选择别的目录。')
        spec=dict(root=str(path),folder=software,base=base,directory=str(path/software))
        return validate(self.service,spec) if checked else spec

    def binding(self):
        if self.pinned and not self.dirty:return None
        self.storage('软件名称')
        return dict(root=self.root.text(),name=self.directory.text().strip(),rule=deepcopy(self.profile))

    def needs_binding(self):
        if self.pinned and not self.dirty:return False
        path=plain_path(Path(self.root.text())/self.directory.text().strip())
        return self.unresolved or not any(same_rule(r['rule'],self.profile) and plain_path(Path(r['root'])/r['name'])==path and path.is_dir() for r in self.catalog)


def install_material(view):
    view.routes_ready=False
    for g in view.groups:
        p=g.get('routing_rule') or view.profile(g);route=FolderRoute(view.engine.service,p,g['folder_name'].text(),pinned=g['saved']['directory'] if g['saved'] else None,parent=g['detail']);g['route']=route
        g['path'].hide();g['detail'].layout().addWidget(route);route.changed.connect(view.schedule);route.changed.connect(view.fit_later)
        for f in g['files']:
            line=QHBoxLayout();line.addWidget(f['role'],1);line.addWidget(f['base'],1);line.addWidget(QLabel(Path(view.engine.proposal['entries'][f['row']]['path']).suffix));route.names.layout().addLayout(line)
        g['folder_name'].textEdited.connect(lambda value,g=g:g['route'].software.setText(value))
        if view.engine.single:view.engine.save_location.folder.textChanged.connect(lambda value,g=g:g['route'].software.setText(value))
        if route.toggle.isChecked():g['expand'].setChecked(True)
    view.routes_ready=True
    def legacy_root(path):
        selected=Path(path)
        for g in view.groups:
            route=g['route']
            if not route.pinned:
                route.root.setText(str(selected.parent));route.directory.setText(selected.name);route.dirty=True;route.unresolved=False;route.update_preview()
        view.schedule()
    view.folders.root.textChanged.connect(legacy_root)


def sync_material(view):
    for g in view.groups:
        p=g.get('routing_rule') or view.profile(g);route=g['route']
        if not same_rule(route.profile,p):
            route.hide();g['detail'].layout().removeWidget(route);route.deleteLater()
            route=FolderRoute(view.engine.service,p,g['folder_name'].text(),pinned=g['saved']['directory'] if g['saved'] else None,parent=g['detail']);g['route']=route;g['detail'].layout().addWidget(route);route.changed.connect(view.schedule);route.changed.connect(view.fit_later)
            for f in g['files']:
                line=QHBoxLayout();line.addWidget(f['role'],1);line.addWidget(f['base'],1);line.addWidget(QLabel(Path(view.engine.proposal['entries'][f['row']]['path']).suffix));route.names.layout().addLayout(line)
            if route.toggle.isChecked():g['expand'].setChecked(True)
        item=view.folders.items.get(g['destination'],{})
        if g['location_dirty'] and not item.get('_direct'):
            signature=repr((item.get('root',view.folders.root.text()),item.get('name')))
            if g.get('_route_adopted')!=signature:
                route.root.setText(item.get('root',view.folders.root.text()));route.directory.setText(item['name']);route.dirty=True;route.unresolved=False;g['_route_adopted']=signature;route.update_preview()
        if g.get('routing_config'):
            config=g.pop('routing_config');route.root.setText(config['root']);route.directory.setText(config['name']);route.dirty=True;route.unresolved=False;route.update_preview()


def material_snapshot(view):
    from intake_folders import validate_records
    values={}
    for g in view.groups:
        if not any(view.checked(i) for i in g['indices']):continue
        if g.get('routing_remember') is False:continue
        r=g['route'].binding()
        if r:
            key=str(plain_path(Path(r['root'])/r['name'])).casefold()
            if key in values and not same_rule(values[key]['rule'],r['rule']):raise ValueError('同一个收纳目录只能绑定一种规则，请调整位置。')
            values[key]=r
    return validate_records(view.engine.service,view.folders.root.text(),list(values.values()))


def install_raw(dialog):
    for item in dialog.raw_controls:
        old=item['save_location'];pinned=None;pid=item['target'].currentData()
        if type(pid) is int:
            from intake_storage import get
            with dialog.service.db.connect() as c:
                record=get(c,pid);raw=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? ORDER BY id DESC LIMIT 1',(pid,)).fetchone()
                pinned=record['directory'] if record else str(Path(raw[0]).parent) if raw else None
        route=FolderRoute(dialog.service,dialog.archive_draft(),old.folder.text(),pinned=pinned,parent=old.parentWidget());item['route']=route
        parent_layout=old.parentWidget().layout();parent_layout.addWidget(route);old.hide()
        line=QHBoxLayout();line.addWidget(QLabel('文件名'));line.addWidget(old.base,1);line.addWidget(old.extension);route.names.layout().addLayout(line)
        route.software.textEdited.connect(old.folder.setText);old.folder.textChanged.connect(route.software.setText)
        route.changed.connect(dialog.fit_content);item['name'].textChanged.connect(lambda name,r=route,o=old:r.software.setText(name) if not o.folder_dirty else None)
        def legacy_root(path,r=route):
            if getattr(dialog,'_setting_raw_root',False):return
            p=Path(path);r.root.setText(str(p.parent));r.directory.setText(p.name);r.dirty=True;r.unresolved=False;r.update_preview()
        old.root.textChanged.connect(legacy_root)
    def parent_changed(path):
        p=Path(path)
        for item in dialog.raw_controls:
            route=item['route']
            if not route.pinned:route.root.setText(str(p.parent));route.directory.setText(p.name);route.dirty=True;route.unresolved=False;route.update_preview()
    dialog.raw_folders.root.textChanged.connect(parent_changed)
    dialog.raw_folders.hide()


def raw_snapshot(dialog):
    from intake_folders import validate_records
    items={}
    for item in dialog.raw_controls:
        if item.get('skip_button') and item['skip_button'].isChecked() or item['action'].currentData()=='skip':continue
        route=item['route'];current=dialog.archive_draft()
        selected=dialog.raw_folders.items[dialog.raw_destination]
        if not selected.get('_direct'):
            route.root.setText(selected.get('root',dialog.raw_folders.root.text()));route.directory.setText(selected['name']);route.profile=deepcopy(current);route.unresolved=False;route.dirty=True
        if not same_rule(current,route.profile):raise ValueError('规则已更换，请重新确认保存位置。')
        value=route.binding()
        if value:items[str(plain_path(Path(value['root'])/value['name'])).casefold()]=value
    return validate_records(dialog.service,dialog.raw_folders.root.text(),list(items.values()))
