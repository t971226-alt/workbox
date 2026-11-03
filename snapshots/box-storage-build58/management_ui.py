"""Two independent settings windows for rules and collection folders."""
from pathlib import Path
from copy import deepcopy
import sqlite3
from PySide6.QtCore import Qt,Signal,QTimer
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,
    QPushButton,QListWidget,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QSpinBox,QScrollArea,QSizePolicy,QInputDialog)
from workspace_chrome import chrome_button,WorkspaceComboBox,colors
from theme import apply_to
from intake_controls import type_icon
from file_families import TYPES


def note(text=''):
    w=QLabel(text);w.setWordWrap(True);w.setTextFormat(Qt.PlainText);return w


def dialog_style(d):
    apply_to(d);c=colors()
    d.setStyleSheet('QLabel,QLineEdit,QComboBox,QSpinBox,QListWidget,QTableWidget{font-size:12px;} QLineEdit{padding:5px;border:1px solid '+c['border']+';border-radius:5px;}')


class RuleForm(QWidget):
    """Compact type buttons and same-type keyword slots, always visible."""
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.busy=False;self.signature=None
        self.layout_=QVBoxLayout(self);self.layout_.setContentsMargins(0,0,0,0);self.layout_.setSpacing(6)
        top=QHBoxLayout();name=QVBoxLayout();name.addWidget(QLabel('规则名称'));name.addWidget(editor.name);top.addLayout(name,1)
        types=QVBoxLayout();types.addWidget(QLabel('文件类型'));self.types=QLineEdit();self.types.setReadOnly(True);types.addWidget(self.types);top.addLayout(types,1);self.layout_.addLayout(top)
        self.folder_button=chrome_button('请为规则选择文件夹',icon='folder');self.folder_button.setProperty('expandableGreen',True);self.layout_.addWidget(self.folder_button)
        self.layout_.addWidget(QLabel('添加文件类型'))
        tools=QGridLayout();tools.setSpacing(3)
        for index,family in enumerate(('word','pdf','sheet','slides','archive','video','image','text','audio')):
            title,ext,_=TYPES[family];b=QPushButton(title);b.setIcon(type_icon(ext));b.setMinimumHeight(28);b.setToolTip('添加一份'+title)
            b.clicked.connect(lambda checked=False,x=ext:self.add(x));tools.addWidget(b,index//5,index%5)
        other=QPushButton('其他文件');other.setIcon(type_icon(''));other.setMinimumHeight(28);other.clicked.connect(self.other);tools.addWidget(other,1,4)
        self.layout_.addLayout(tools);self.rows=QVBoxLayout();self.rows.setSpacing(7);self.layout_.addLayout(self.rows)
        editor.changed.connect(self.refresh);self.refresh()

    def add(self,ext):
        if self.editor.table.rowCount()>=40:return
        self.editor.add(ext);self.editor.table.cellWidget(self.editor.table.rowCount()-1,3).set_pattern(dict(mode='type'))
        self.signature=None;self.refresh()

    def other(self):
        value,ok=QInputDialog.getText(self,'其他文件类型','填写后缀，例如 exe、db 或 sql：')
        if not ok:return
        import re
        suffix=value.strip().lstrip('.').lower()
        if re.fullmatch('[a-z0-9]{1,12}',suffix):self.add('.'+suffix)
        else:QMessageBox.information(self,'文件类型','请填写有效后缀，例如 db。')

    def groups(self):
        from collections import OrderedDict
        groups=OrderedDict()
        for i in range(self.editor.table.rowCount()):
            c=self.editor.table.cellWidget(i,1);key=(c.family() or c.suffix(),c.strict,c.suffix() if c.strict else '')
            groups.setdefault(key,[]).append(i)
        return groups

    def refresh(self):
        if self.busy:return
        groups=self.groups();sig=repr([(key,ids) for key,ids in groups.items()])
        if sig==self.signature:return
        self.signature=sig
        while self.rows.count():
            item=self.rows.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        names=[]
        for (family,strict,ext),indices in groups.items():
            title=TYPES[family][0] if family in TYPES else family.upper()+' 文件';names.append(title+' ×'+str(len(indices)))
            box=QWidget();layout=QVBoxLayout(box);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
            line=QHBoxLayout();line.addWidget(QLabel(title),1);count=QSpinBox();count.setRange(1,40);count.setValue(len(indices));count.setFixedWidth(52);line.addWidget(count);line.addWidget(QLabel('份'));remove=chrome_button('×');line.addWidget(remove);layout.addLayout(line)
            count.valueChanged.connect(lambda n,ids=indices:self.count(ids,n));remove.clicked.connect(lambda checked=False,ids=indices:self.count(ids,0))
            slots=QGridLayout();slots.setHorizontalSpacing(10);slots.setVerticalSpacing(4)
            for pos,i in enumerate(indices):
                p=self.editor.table.cellWidget(i,3).pattern();value=' + '.join(t for t in p.get('terms',[]) if t!='{software}') if p['mode']=='keywords' else p.get('marker','')
                item=QWidget();row=QHBoxLayout(item);row.setContentsMargins(0,0,0,0);row.setSpacing(4)
                token=QLabel(title+' '+str(pos+1)+' +');token.setToolTip('软件名称自动代入');row.addWidget(token)
                field=QLineEdit(value);field.setPlaceholderText('关键词（选填）');field.setToolTip('软件名称自动代入。多关键词用逗号/空格/+，需全部包含。例如：代码+手册。');row.addWidget(field,1)
                field.setAccessibleName(title+'第'+str(pos+1)+'份关键词');field.editingFinished.connect(lambda i=i,w=field:self.keyword(i,w.text()))
                slots.addWidget(item,pos//2,pos%2)
            layout.addLayout(slots);self.rows.addWidget(box)
        self.types.setText(' / '.join(names));self.types.setToolTip(self.types.text())

    def keyword(self,index,text):
        from name_patterns import keyword_terms,SOFTWARE_TOKEN
        terms=[t for t in keyword_terms(text) if t!=SOFTWARE_TOKEN]
        self.editor.table.cellWidget(index,3).set_pattern(dict(mode='keywords',terms=[SOFTWARE_TOKEN,*terms]) if terms else dict(mode='same'))

    def count(self,indices,n):
        self.busy=True
        try:
            if n>len(indices):
                source=self.editor.table.cellWidget(indices[0],1)
                for _ in range(n-len(indices)):
                    self.editor.add(source.suffix());self.editor.table.cellWidget(self.editor.table.rowCount()-1,1).set_suffix(source.suffix(),strict=source.strict)
            else:
                for i in reversed(indices[n:]):self.editor.table.removeRow(i)
        finally:self.busy=False
        self.signature=None;self.refresh();self.editor.changed.emit()


class RuleManagementDialog(QDialog):
    saved=Signal(object)
    def __init__(self,service,parent=None,*,folder=None,new=False,intake_context=None):
        super().__init__(parent);self.service=service;self.intake_context=intake_context;self.current=None;self.pending_folder=Path(folder) if folder else None;self.dirty=False;self.loading=False;self.setWindowTitle('规则管理');self.resize(760,450);dialog_style(self)
        root=QVBoxLayout(self);root.setContentsMargins(12,10,12,10);root.setSpacing(7)
        line=QHBoxLayout();self.new_button=chrome_button('新建规则',icon='plus');line.addWidget(self.new_button);line.addStretch();root.addLayout(line)
        body=QHBoxLayout();self.list=QListWidget();self.list.setFixedWidth(140);body.addWidget(self.list)
        self.editor_host=QWidget();self.form_layout=QVBoxLayout(self.editor_host);self.form_layout.setContentsMargins(0,0,0,0)
        from quick_classification_ui import QuickRuleEditor
        self.editor=QuickRuleEditor(['.docx'],self.editor_host);self.editor.simple_presentation.hide();self.editor.simple_presentation.setParent(self.editor._legacy_rule_controls)
        self.form=RuleForm(self.editor);self.editor.layout().addWidget(self.form);self.form_layout.addWidget(self.editor)
        hint=note('软件名称自动代入；关键词可不填，多关键词用逗号/空格/+（例：代码+手册）。');hint.setStyleSheet('font-size:11px;');self.form_layout.addWidget(hint);self.form_layout.addStretch()
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.NoFrame);scroll.setWidget(self.editor_host);body.addWidget(scroll,1);root.addLayout(body,1)
        self.status=note('');root.addWidget(self.status);buttons=QHBoxLayout();self.remove_button=chrome_button('停用 / 删除');buttons.addWidget(self.remove_button);buttons.addStretch();close=chrome_button('关闭');self.save_button=chrome_button('保存规则',variant='primary');buttons.addWidget(close);buttons.addWidget(self.save_button);root.addLayout(buttons)
        self.new_button.clicked.connect(self.new);self.list.currentRowChanged.connect(self.select);self.form.folder_button.clicked.connect(self.choose_folder);self.save_button.clicked.connect(self.save);self.remove_button.clicked.connect(self.remove);close.clicked.connect(self.reject);self.editor.changed.connect(self.changed)
        self.reload();
        if new or intake_context is not None:self.new()
        if intake_context is not None:
            self.setWindowTitle('新建收纳规则');self.list.hide();self.new_button.hide();self.remove_button.hide()
            self.editor.apply_suggestion(intake_context['draft'])
            for i,kind in enumerate(intake_context['draft']['kinds']):
                self.editor.table.cellWidget(i,1).set_suffix(Path(kind).suffix,strict=kind not in intake_context['draft'].get('family_map',{}))
            self.form.signature=None;self.form.refresh();self.dirty=False
            self.save_button.setText('保存并重新匹配');self.resize(550,400)
            self.status.setText('按本次文件预填。请选择保存文件夹；已有规则和绑定保持不变。'+('已有同名任务保留，本次另建收录记录。' if intake_context.get('existing') else ''))

    def changed(self):
        if not self.loading:self.dirty=True

    def may_leave(self):
        return not self.dirty or QMessageBox.question(self,'未保存的规则','放弃尚未保存的修改？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes

    def reload(self,ident=None):
        self.profiles=self.service.classifications.profiles(False,include_archives=True);self.list.blockSignals(True);self.list.clear()
        for p in self.profiles:self.list.addItem(p['name']+('（已停用）' if not p['active'] else ''))
        index=next((i for i,p in enumerate(self.profiles) if p['id']==ident),0) if self.profiles else -1
        self.list.setCurrentRow(index);self.list.blockSignals(False);self.load(self.profiles[index] if index>=0 else None)

    def load(self,profile):
        self.loading=True;self.current=deepcopy(profile);self.pending_folder=None
        self.editor._ready=False;self.editor.table.setRowCount(0)
        for kind in profile['kinds'] if profile else ['.docx']:
            self.editor.add(kind);i=self.editor.table.rowCount()-1
            if profile:
                combo=self.editor.table.cellWidget(i,1);combo.set_suffix(Path(kind).suffix,strict=kind not in profile.get('family_map',{}))
                self.editor.table.cellWidget(i,3).set_pattern(profile.get('name_patterns',{}).get(kind,dict(mode='keywords',terms=[Path(kind).stem])))
        self.editor.name.setText(profile['name'] if profile else '');self.editor._ready=True;self.form.signature=None;self.form.refresh();self.remove_button.setEnabled(bool(profile));self.loading=False;self.dirty=False;self.status.setText('修改仅用于以后收录；已有任务和材料保留。')

    def select(self,index):
        if not self.may_leave():
            self.list.blockSignals(True);self.list.setCurrentRow(next((i for i,p in enumerate(self.profiles) if self.current and p['id']==self.current['id']),-1));self.list.blockSignals(False);return
        self.load(self.profiles[index] if index>=0 else None)

    def new(self):
        if not self.may_leave():return
        self.list.blockSignals(True);self.list.setCurrentRow(-1);self.list.blockSignals(False);self.load(None);self.editor.name.setFocus()

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'请为规则选择文件夹（可新建文件夹）',str(self.pending_folder or self.service.workspaces.settings()['work_root']))
        if not path:return
        try:
            from folder_management import checked_path,folders
            from intake_folders import same_rule
            path=checked_path(self.service,path);binding=next((r['rule'] for r in folders(self.service) if Path(r['path'])==path),None)
            if binding and self.intake_context is not None:
                raise ValueError('此文件夹已绑定规则，请选择其他文件夹，原绑定保持不变。')
            if binding and (not self.current or not same_rule(binding,self.current)):
                if QMessageBox.question(self,'更换绑定规则','该文件夹已绑定“'+binding['name']+'”。保存后改为当前规则，已有材料保留。继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
            self.pending_folder=path;self.status.setText('已选：'+str(path)+'；保存规则后绑定。');self.dirty=True
            if self.intake_context is not None:
                self.form.folder_button.setText('已选择文件夹 · 重新选择');self.form.folder_button.setToolTip(str(path))
        except (ValueError,OSError) as e:self.status.setText(str(e))

    def save(self):
        try:
            d=self.editor.draft();old=self.current or {};self.service.classifications.validate(d['name'],d['kinds'],mode='collect')
            if len(set(d['kinds']))!=len(d['kinds']):raise ValueError('文件项目重复，请使用不同关键词区分。')
            if self.intake_context is not None:
                from unmatched_intake import mapping,save
                if d.get('intake_mode')!='original' and not all(mapping(d,self.intake_context['entries'],self.intake_context['software']).values()):
                    raise ValueError('新规则仍有文件无法匹配，请检查文件类型和关键词。')
                profile=save(self.service,d,self.pending_folder)
                self.dirty=False;self.saved.emit(profile);self.status.setText('已新建规则并绑定文件夹。');return
            pid=self.service.classifications.save(d['name'],d['kinds'],aliases={k:v for k,v in old.get('aliases',{}).items() if k in d['kinds']},mode='collect',profile_id=old.get('id'),active=True,intake_mode=d.get('intake_mode','materials'),extensions=d.get('extensions'),family_map=d['family_map'],name_patterns=d['name_patterns'])
            profile=next(p for p in self.service.classifications.profiles(True,include_archives=True) if p['id']==pid)
            selected=self.pending_folder
            # Refresh existing binding snapshots by stable rule ID; do not move tasks.
            from intake_folders import records,same_rule
            from folder_management import bind
            for r in records(self.service):
                if same_rule(r['rule'],profile):
                    target=Path(r['root'])/r['name']
                    if target.is_dir():bind(self.service,target,profile)
            if selected:bind(self.service,selected,profile)
            self.dirty=False;self.reload(pid);self.saved.emit(profile);self.status.setText('已保存规则'+('并绑定文件夹。' if selected else '。'))
        except (ValueError,OSError,sqlite3.Error) as e:self.status.setText('保存未完成：'+str(e))

    def remove(self):
        if not self.current:return
        if QMessageBox.question(self,'停用 / 删除规则','已使用的规则会停用；已有材料和任务保留。继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:self.service.classifications.remove(self.current['id']);self.dirty=False;self.reload()
        except (ValueError,OSError,sqlite3.Error) as e:self.status.setText(str(e))

    def reject(self):
        if self.may_leave():super().reject()

    def closeEvent(self,event):
        if self.may_leave():event.accept()
        else:event.ignore()


class FolderManagementDialog(QDialog):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;self.draft=None;self.setWindowTitle('文件夹管理');self.resize(800,340);dialog_style(self)
        root=QVBoxLayout(self);root.setContentsMargins(12,10,12,10);root.setSpacing(7)
        header=QHBoxLayout();header.addWidget(QLabel('已管理的文件夹'));header.addStretch();self.create_button=chrome_button('创建新文件夹',icon='folder');self.create_button.clicked.connect(self.create);header.addWidget(self.create_button);root.addLayout(header)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['文件夹 / 保存位置','收纳规则','软件数','状态','操作']);self.table.verticalHeader().hide();self.table.setEditTriggers(QTableWidget.NoEditTriggers);self.table.setShowGrid(False);self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for i,w in [(1,125),(2,55),(3,70),(4,100)]:self.table.setColumnWidth(i,w)
        root.addWidget(self.table);self.status=note('移除管理会保留文件和材料。');root.addWidget(self.status);line=QHBoxLayout();line.addStretch();close=chrome_button('关闭');close.clicked.connect(self.reject);line.addWidget(close);root.addLayout(line);self.refresh()

    def refresh(self):
        from folder_management import folders
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                old=self.table.cellWidget(row,column)
                if old:old.hide();self.table.removeCellWidget(row,column);old.setParent(None);old.deleteLater()
        self.table.clearContents()
        self.items=folders(self.service);self.rules=self.service.classifications.profiles(True,include_archives=True)
        self.table.setRowCount(len(self.items)+(1 if self.draft else 0));offset=0
        if self.draft:self.draft_row();offset=1
        all_rows=self.service.production.rows()
        for index,item in enumerate(self.items,offset):
            path=Path(item['path']);rule=item['rule'];widget=QWidget();line=QVBoxLayout(widget);line.setContentsMargins(3,3,3,3);line.setSpacing(1)
            name=chrome_button(path.name);name.setProperty('leftAligned',True);name.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);name.clicked.connect(lambda checked=False,p=path:self.details(p));line.addWidget(name)
            from PySide6.QtGui import QFontMetrics
            caption=QLabel(QFontMetrics(self.font()).elidedText(str(path),Qt.ElideMiddle,max(100,self.table.columnWidth(0)-20)));caption.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);caption.setStyleSheet('font-size:11px;');caption.setToolTip(str(path));line.addWidget(caption);self.table.setCellWidget(index,0,widget)
            combo=WorkspaceComboBox();combo.setAccessibleName(path.name+'的收纳规则')
            if not rule:combo.addItem('未绑定',None);combo.setItemData(0,0,Qt.UserRole-1)
            for p in self.rules:combo.addItem(p['name'],p['id'])
            if rule:
                selected=combo.findData(rule.get('id'))
                if selected<0:combo.addItem(rule['name']+'（已停用）',rule.get('id'));selected=combo.count()-1
                combo.setCurrentIndex(selected)
            combo.addItem('＋ 绑定新规则','__new__');combo.currentIndexChanged.connect(lambda _,c=combo,p=path,r=rule:self.bind(p,c,r));self.table.setCellWidget(index,1,combo)
            count=sum(1 for r in all_rows if r.get('collection_folder') and Path(r['collection_folder']).is_relative_to(path));b=chrome_button(str(count));b.clicked.connect(lambda checked=False,p=path:self.details(p));self.table.setCellWidget(index,2,b)
            state=chrome_button('重新定位') if not path.is_dir() else QLabel('正常' if rule else '未绑定');self.table.setCellWidget(index,3,state)
            if not path.is_dir():state.clicked.connect(lambda checked=False,p=path:self.locate(p))
            actions=QWidget();row=QHBoxLayout(actions);row.setContentsMargins(0,0,0,0);row.setSpacing(1)
            for icon,label,slot in [('folder','打开位置',lambda p=path:self.open(p)),('edit','修改名称',lambda p=path,i=index:self.rename(p,i)),('close','移除管理',lambda p=path:self.remove(p))]:
                b=chrome_button(icon=icon,icon_only=True,label=label);b.setFixedSize(28,28);b.setToolTip(label);b.clicked.connect(lambda checked=False,s=slot:s());row.addWidget(b)
            self.table.setCellWidget(index,4,actions);self.table.setRowHeight(index,64)

    def run(self,operation):
        try:result=operation();self.refresh();return result
        except (ValueError,OSError,sqlite3.Error) as e:self.status.setText(str(e));return None

    def create(self):
        if self.draft:return
        self.draft=dict(parent=Path(self.service.workspaces.settings()['work_root']),name='');self.refresh();self.name_edit.setFocus()

    def draft_row(self):
        box=QWidget();layout=QVBoxLayout(box);layout.setContentsMargins(3,3,3,3);layout.setSpacing(3);self.name_edit=QLineEdit(self.draft['name']);self.name_edit.setPlaceholderText('新文件夹名称');self.name_edit.setAccessibleName('新文件夹名称');layout.addWidget(self.name_edit)
        choose=chrome_button('保存到：'+str(self.draft['parent']),icon='folder');choose.setToolTip('选择保存位置');choose.clicked.connect(self.choose_parent);layout.addWidget(choose);self.table.setCellWidget(0,0,box)
        self.name_edit.textChanged.connect(lambda value:self.draft.update(name=value) if self.draft else None)
        actions=QWidget();line=QHBoxLayout(actions);line.setContentsMargins(0,0,0,0)
        for label,slot in [('创建',self.confirm_create),('取消',self.cancel_create)]:b=chrome_button(label);line.addWidget(b);b.clicked.connect(slot)
        self.table.setCellWidget(0,4,actions);self.table.setRowHeight(0,74)

    def choose_parent(self):
        path=QFileDialog.getExistingDirectory(self,'选择新文件夹的保存位置',str(self.draft['parent']))
        if path:self.draft['parent']=Path(path);self.refresh()

    def cancel_create(self):self.draft=None;self.refresh()

    def confirm_create(self):
        from folder_management import create
        if self.run(lambda:create(self.service,self.draft['parent'],self.draft['name'])):
            self.draft=None;self.refresh();self.status.setText('已创建文件夹，可在这一行选择收纳规则。')

    def bind(self,path,combo,old):
        choice=combo.currentData()
        if choice=='__new__':
            d=RuleManagementDialog(self.service,self,new=True);d.pending_folder=path;d.status.setText('保存后绑定到：'+str(path));d.saved.connect(lambda _:d.accept())
            d.exec();d.deleteLater();self.refresh();return
        profile=next((p for p in self.rules if p['id']==choice),None)
        if not profile:return
        if old and old.get('id')!=choice and QMessageBox.question(self,'更换收纳规则','仅改变以后收录的规则，已有任务和材料保留。继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:self.refresh();return
        from folder_management import bind
        self.run(lambda:bind(self.service,path,profile))

    def rename(self,path,index):
        edit=QLineEdit(path.name);edit.setAccessibleName('修改文件夹名称');self.table.setCellWidget(index,0,edit);edit.setFocus();edit.selectAll()
        actions=QWidget();row=QHBoxLayout(actions);row.setContentsMargins(0,0,0,0)
        def save():
            from folder_management import relocate
            from intake_storage import clean_name
            self.run(lambda:relocate(self.service,path,path.parent/clean_name(edit.text(),'文件夹名称'),rename=True))
        for label,slot in [('保存',save),('取消',self.refresh)]:b=chrome_button(label);row.addWidget(b);b.clicked.connect(slot)
        self.table.setCellWidget(index,4,actions);edit.returnPressed.connect(save)

    def locate(self,path):
        selected=QFileDialog.getExistingDirectory(self,'选择移动后的原文件夹',str(path.parent))
        if selected:
            from folder_management import relocate
            self.run(lambda:relocate(self.service,path,selected))

    def remove(self,path):
        if QMessageBox.question(self,'移除管理','解除“'+path.name+'”的规则绑定？\n文件、已收录材料及历史记录均保留。',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        from folder_management import remove
        self.run(lambda:remove(self.service,path))

    def open(self,path):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        if path.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:self.status.setText('文件夹不存在，请重新定位。')

    def details(self,path):
        d=QDialog(self);d.setWindowTitle(path.name+' · 已收录材料');d.resize(540,280);dialog_style(d);layout=QVBoxLayout(d);table=QTableWidget(0,2);table.setHorizontalHeaderLabels(['软件名称','已收录 / 所需']);table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);table.setEditTriggers(QTableWidget.NoEditTriggers);layout.addWidget(table)
        rows=[r for r in self.service.production.rows() if r.get('collection_folder') and Path(r['collection_folder']).is_relative_to(path)];table.setRowCount(len(rows))
        for i,r in enumerate(rows):table.setItem(i,0,QTableWidgetItem(r['name']));table.setItem(i,1,QTableWidgetItem(str(r['collection_count'])+' / '+str(r['required_count'])))
        close=chrome_button('关闭');close.clicked.connect(d.accept);layout.addWidget(close);d.exec();d.deleteLater()
