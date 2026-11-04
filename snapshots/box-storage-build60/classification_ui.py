"""Guided one-page material preset editor; old tasks retain their snapshots."""
import re
import sqlite3
from pathlib import Path
from collections import Counter
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (QDialog,QWidget,QHBoxLayout,QVBoxLayout,QListWidget,QLineEdit,QLabel,
    QTableWidget,QTableWidgetItem,QHeaderView,QCheckBox,QMessageBox,QAbstractItemView,QInputDialog)
from workspace_chrome import chrome_button,WorkspaceComboBox,colors
from theme import apply_to
from classification_rules import MODES

FORMATS={'.docx':'Word','.pdf':'PDF','.mp4':'视频','.txt':'文本','.xlsx':'Excel',
         '.exe':'程序','.msi':'安装包','.db':'数据库','.sql':'SQL脚本','.zip':'压缩包',
         '.doc':'旧版Word','.csv':'CSV表格','.pptx':'演示文稿','.wmv':'WMV视频','.mov':'MOV视频','.mkv':'MKV视频'}
from file_formats import LABELS
from file_families import TYPES
FORMATS={ext:title for title,ext,_ in TYPES.values()}
TITLES={'单手册 Word':'单 Word','桌面14件':'14件材料','14件＋双视频':'14件＋视频'}

class ClassificationDialog(QDialog):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;self.catalog=service.classifications
        self.current=None;self.dirty=False;self.loading=True;self._suggested_name='';self._starter_last=0;self._starter_payload=None
        self.setWindowTitle('管理收录规则');self.resize(840,620);self.setMinimumSize(760,560);self.setProperty('intakeSheet',True);apply_to(self)
        c=colors()
        self.setStyleSheet('QDialog,QWidget#classificationForm { background:'+c['surface']+'; color:'+c['ink']+'; } QLabel { font-size:12px; color:'+c['ink']+'; } QListWidget,QTableWidget,QLineEdit { background:'+c['surface']+'; color:'+c['ink']+'; border:1px solid '+c['border']+'; border-radius:6px; font-size:12px; selection-background-color:'+c['hover']+'; selection-color:'+c['ink']+'; } QLineEdit { padding:6px; } QLineEdit:focus { border:1px solid '+c['muted']+'; } QListWidget::item { padding:9px 6px; margin:2px; border:none; border-radius:4px; } QListWidget::item:selected { background:'+c['hover']+'; color:'+c['ink']+'; } QTableWidget::item { padding:2px; } QHeaderView::section { background:'+c['field']+'; color:'+c['ink']+'; border:none; border-bottom:1px solid '+c['border']+'; padding:7px; font-size:12px; }')
        root=QVBoxLayout(self);root.setContentsMargins(12,12,12,12);root.setSpacing(8)
        scope=QLabel('这里设置每类材料清单，只影响以后新建的任务。')
        scope.setWordWrap(True);root.addWidget(scope)
        body=QHBoxLayout();body.setSpacing(14);root.addLayout(body,1)
        left=QVBoxLayout();left.setSpacing(6)
        self.new_button=chrome_button('新建规则',icon='plus');self.new_button.clicked.connect(self.new);left.addWidget(self.new_button)
        self.profiles=QListWidget();self.profiles.setMinimumWidth(150);self.profiles.setMaximumWidth(180);self.profiles.setAccessibleName('已有材料分类');left.addWidget(self.profiles,1)
        order=QHBoxLayout();order.setSpacing(2)
        for label,slot in [('上移',lambda:self.move(-1)),('下移',lambda:self.move(1))]:
            b=chrome_button(label);b.clicked.connect(slot);order.addWidget(b)
        left.addLayout(order)
        self.delete_button=chrome_button('停用 / 删除');self.delete_button.clicked.connect(self.remove);left.addWidget(self.delete_button)
        body.addLayout(left)
        form=QWidget();form.setObjectName('classificationForm');edit=QVBoxLayout(form);edit.setContentsMargins(0,0,0,0);edit.setSpacing(7);body.addWidget(form,1)
        self.title=QLabel();self.title.setStyleSheet('font-size:14px;font-weight:600;');edit.addWidget(self.title)
        from quick_classification_ui import QuickRuleEditor
        self.archive_editor=QuickRuleEditor(['.zip'],form);self.archive_editor.hide();self.archive_editor.changed.connect(self.changed);edit.addWidget(self.archive_editor)
        self.starter_section=QWidget();start=QHBoxLayout(self.starter_section);start.setContentsMargins(0,0,0,0);start.setSpacing(7)
        start.addWidget(QLabel('从模板开始'))
        self.starter=WorkspaceComboBox();self.starter.setAccessibleName('选择分类起始模板');self.starter.setToolTip('选一个接近的模板，材料自动填好，再按需要调整');start.addWidget(self.starter,1);edit.addWidget(self.starter_section)
        name_row=QHBoxLayout();self.name_label=QLabel('清单名称');name_row.addWidget(self.name_label)
        self.name=QLineEdit();self.name.setMaxLength(40);self.name.setPlaceholderText('例如：验收材料、说明书＋视频');self.name.setAccessibleName('清单名称');name_row.addWidget(self.name,1);edit.addLayout(name_row)
        mode_row=QHBoxLayout()
        self.mode=WorkspaceComboBox();self.mode.setAccessibleName('新分类默认处理方式')
        self.mode.addItem('收录整理','collect')
        self.mode.setParent(self);self.mode.hide()
        self.mode_help=QLabel(form);self.mode_help.hide()
        self.material_hint=QLabel('每个软件需要哪些材料？');edit.addWidget(self.material_hint)
        self.materials=QTableWidget(0,3);self.materials.setHorizontalHeaderLabels(['材料名称与格式','识别关键词（选填）',''])
        self.materials.setAccessibleName('分类必需材料清单');self.materials.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.materials.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.materials.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.materials.horizontalHeader().setSectionResizeMode(2,QHeaderView.Fixed);self.materials.setColumnWidth(2,34)
        self.materials.verticalHeader().hide();self.materials.setMinimumHeight(110);edit.addWidget(self.materials,1)
        self.empty_hint=QLabel('选上面的模板最快，也可以从下面逐份添加。');self.empty_hint.setWordWrap(True);edit.addWidget(self.empty_hint)
        quick=QHBoxLayout();quick.setSpacing(2);self.quick_buttons=[]
        for label,kind in [('Word','.docx'),('PDF','.pdf'),('视频','.mp4'),('其他','')]:
            b=chrome_button(label,icon='plus');b.clicked.connect(lambda _,k=kind:self.quick_add(k));self.quick_buttons.append(b);quick.addWidget(b)
        quick.addStretch()
        self.advanced_button=chrome_button('高级设置（通常不用改）',icon='settings');self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self.set_advanced);quick.addWidget(self.advanced_button);edit.addLayout(quick)
        self.advanced_panel=QWidget();advanced=QVBoxLayout(self.advanced_panel);advanced.setContentsMargins(0,0,0,0);advanced.setSpacing(4)
        tip=QLabel('文件叫法不同才需要填关键词。例如“软件说明书”可填“操作手册，用户手册”。只匹配同一种文件格式。')
        tip.setWordWrap(True);advanced.addWidget(tip)
        self.active=QCheckBox('启用这个分类，供新批次使用');self.active.setChecked(True);advanced.addWidget(self.active);edit.addWidget(self.advanced_panel)
        self.guide=QLabel();self.guide.setWordWrap(True);root.addWidget(self.guide)
        self.summary=QLabel();self.summary.setWordWrap(True)
        self.summary.setStyleSheet('background:'+c['field']+';border:1px solid '+c['border']+';border-radius:8px;padding:8px 10px;');root.addWidget(self.summary)
        self.error=QLabel();self.error.setWordWrap(True);self.error.hide();root.addWidget(self.error)
        actions=QHBoxLayout();actions.addStretch()
        self.cancel_button=chrome_button('关闭');self.save_button=chrome_button('保存规则',variant='primary')
        actions.addWidget(self.cancel_button);actions.addWidget(self.save_button);root.addLayout(actions)
        self.save_button.clicked.connect(self.save);self.cancel_button.clicked.connect(self.close)
        self.profiles.currentRowChanged.connect(self.select);self.starter.currentIndexChanged.connect(self.use_starter)
        for signal in (self.name.textChanged,self.mode.currentIndexChanged,self.active.toggled,self.materials.itemChanged):signal.connect(self.changed)
        self.reload()

    def show_error(self,message):
        self.error.setText(message);self.error.setVisible(bool(message))

    def changed(self,*_):
        if not self.loading:self.dirty=True;self.show_error('');self.update_summary()

    def may_leave(self):
        return not self.dirty or QMessageBox.question(self,'未保存的修改','离开将放弃尚未保存的分类修改，是否继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes

    def reload(self,selected=None):
        self.loading=True;self.records=self.catalog.profiles(include_archives=True);self.profiles.blockSignals(True);self.profiles.clear()
        for r in self.records:self.profiles.addItem(TITLES.get(r['name'],r['name'])+('（已停用）' if not r['active'] else ''))
        index=next((i for i,r in enumerate(self.records) if r['id']==selected),0) if self.records else -1
        self.profiles.setCurrentRow(index);self.profiles.blockSignals(False);self.loading=False;self.dirty=False
        self.load(self.records[index] if index>=0 else None)

    def select(self,index):
        if self.loading:return
        if not self.may_leave():
            self.profiles.blockSignals(True);self.profiles.setCurrentRow(next((i for i,r in enumerate(self.records) if self.current and r['id']==self.current['id']),-1));self.profiles.blockSignals(False);return
        self.load(self.records[index] if index>=0 else None)

    def load(self,record):
        self.loading=True;self.current=record;self.name.setText(record['name'] if record else '')
        self.title.setText('修改规则' if record else '新建规则')
        self.starter_section.setVisible(record is None);self.starter.blockSignals(True);self.starter.clear()
        self.starter.addItem('选择模板，自动填好材料…',None)
        self.starter.addItem('自己添加材料（空白清单）','__blank__')
        for r in self.records:
            if r['active']:self.starter.addItem(f"{TITLES.get(r['name'],r['name'])} · {len(r['kinds'])} 份材料",r['id'])
        self.starter.setCurrentIndex(0);self.starter.blockSignals(False);self._starter_last=0;self._suggested_name='';self._starter_payload=None
        self.mode.setCurrentIndex(max(0,self.mode.findData(record['mode'] if record else 'review')))
        self.active.setChecked(bool(record['active']) if record else True);self.materials.setRowCount(0)
        for kind in record['kinds'] if record else []:self.add_material(kind,','.join(record['aliases'].get(kind,[])))
        self.advanced_button.setChecked(False);self.set_advanced(False)
        self.delete_button.setEnabled(record is not None);self.show_error('');self.loading=False;self.dirty=False;self.update_summary()
        self.cancel_button.setText('关闭' if record else '取消')
        raw=bool(record and record.get('intake_mode')=='original')
        self.archive_editor.setVisible(raw)
        for widget in [self.name,self.name_label,self.material_hint,self.materials,self.starter_section,self.empty_hint,self.advanced_button,*self.quick_buttons]:widget.setVisible(not raw)
        if raw:
            self.loading=True;self.archive_editor.name.setText(record['name'])
            ext='__archive__' if set(record['extensions'])=={'.zip','.7z','.rar'} else record['extensions'][0]
            self.archive_editor.format.setCurrentIndex(self.archive_editor.format.findData(ext));self.loading=False;self.dirty=False;self.update_summary()


    def new(self):
        if not self.may_leave():return
        from quick_classification_ui import QuickClassificationDialog
        d=QuickClassificationDialog(self.service,self)
        try:
            if d.exec()==QDialog.Accepted:self.dirty=False;self.reload(d.profile_id)
        finally:d.deleteLater()

    def use_starter(self,index):
        if self.loading:return
        if self.materials.rowCount() and self.dirty and self.material_state()!=self._starter_payload and QMessageBox.question(self,'更换起始模板','会替换当前材料清单和默认处理方式，已填写的清单名称保留。是否继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            self.starter.blockSignals(True);self.starter.setCurrentIndex(self._starter_last);self.starter.blockSignals(False);return
        record=next((r for r in self.records if r['id']==self.starter.itemData(index)),None)
        self.loading=True;self.materials.setRowCount(0)
        if record:
            for kind in record['kinds']:self.add_material(kind,','.join(record['aliases'].get(kind,[])))
            self.mode.setCurrentIndex(self.mode.findData(record['mode']))
            if not self.name.text().strip() or self.name.text()==self._suggested_name:
                base=('我的'+TITLES.get(record['name'],record['name']))[:34];suggestion=base;number=2
                while any(r['name'].casefold()==suggestion.casefold() for r in self.records):suggestion=base+str(number);number+=1
                self._suggested_name=suggestion;self.name.setText(suggestion)
        self._starter_last=index;self._starter_payload=self.material_state();self.loading=False;self.changed();self.name.setFocus();self.name.selectAll()

    def material_state(self):
        return (tuple((self.materials.item(i,0).text(),self.materials.item(i,1).text()) for i in range(self.materials.rowCount())),self.mode.currentData())

    def set_advanced(self,expanded):
        self.advanced_panel.setVisible(expanded);self.materials.setColumnHidden(1,not expanded)
        self.advanced_button.setText('收起高级设置' if expanded else '高级设置')
        if self.isVisible() and hasattr(self,'_density_filter'):self._density_filter.queue()

    def add_material(self,kind='',keywords=''):
        if self.materials.rowCount()>=40:self.show_error('一个分类最多40份材料，请先移除不需要的材料。');return
        blocked=self.materials.blockSignals(True);i=self.materials.rowCount();self.materials.insertRow(i)
        raw=QTableWidgetItem(kind);raw.setData(Qt.UserRole,kind);raw.setFlags(raw.flags()&~Qt.ItemIsEditable);raw.setForeground(QBrush(Qt.transparent));self.materials.setItem(i,0,raw)
        self.materials.setItem(i,1,QTableWidgetItem(keywords))
        box=QWidget();box.setAutoFillBackground(True);row=QHBoxLayout(box);row.setContentsMargins(4,2,4,2);row.setSpacing(5)
        box.name_edit=QLineEdit();box.name_edit.setPlaceholderText('材料名称，例如：验收报告');box.name_edit.setAccessibleName('材料名称')
        box.name_edit.setStyleSheet('QLineEdit { padding:0 6px; min-height:0; }');box.name_edit.setFixedHeight(28)
        suffix=kind if kind.startswith('.') and kind.count('.')==1 else Path(kind).suffix;box.name_edit.setText('' if kind==suffix else Path(kind).stem if suffix else kind)
        box.format_combo=WorkspaceComboBox();box.format_combo.setFixedWidth(96);box.format_combo.setFixedHeight(28);box.format_combo.setAccessibleName('材料文件格式')
        for ext,label in FORMATS.items():box.format_combo.addItem(label,ext)
        if suffix and suffix.casefold() not in FORMATS:box.format_combo.addItem(suffix,suffix.casefold())
        box.format_combo.addItem('其他格式…',None)
        box.format_combo.setCurrentIndex(max(0,box.format_combo.findData(suffix.casefold() or '.docx')))
        box.last_format=box.format_combo.currentData();box.format_combo.setToolTip(box.format_combo.currentText()+' · '+box.last_format)
        row.addWidget(box.name_edit,1);row.addWidget(box.format_combo);self.materials.setCellWidget(i,0,box)
        remove=chrome_button(icon='close',icon_only=True,label='移除这一份材料');self.materials.setCellWidget(i,2,remove)
        remove.clicked.connect(lambda:self.remove_one(remove));self.materials.setRowHeight(i,40)
        box.name_edit.textChanged.connect(lambda:self.sync_row(box));box.format_combo.currentIndexChanged.connect(lambda:self.sync_row(box))
        self.materials.blockSignals(blocked);self.changed()

    def sync_row(self,box):
        index=next((i for i in range(self.materials.rowCount()) if self.materials.cellWidget(i,0) is box),None)
        if index is None:return
        if box.format_combo.currentData() is None:
            value,ok=QInputDialog.getText(self,'其他文件格式','填写扩展名，例如 json 或 .log：')
            ext='.'+value.strip().lstrip('.').casefold()
            box.format_combo.blockSignals(True)
            if ok and re.fullmatch(r'\.[a-z0-9]{1,12}',ext):
                if box.format_combo.findData(ext)<0:box.format_combo.insertItem(box.format_combo.count()-1,ext,ext)
                box.format_combo.setCurrentIndex(box.format_combo.findData(ext))
            else:
                box.format_combo.setCurrentIndex(box.format_combo.findData(box.last_format))
                if ok:self.show_error('格式只需填写字母或数字，例如 .log。')
            box.format_combo.blockSignals(False)
            if not ok or not re.fullmatch(r'\.[a-z0-9]{1,12}',ext):return
        name=box.name_edit.text().strip();suffix=Path(name).suffix.casefold()
        if suffix and box.format_combo.findData(suffix)>=0:
            box.name_edit.blockSignals(True);box.name_edit.setText(Path(name).stem);box.name_edit.blockSignals(False)
            box.format_combo.blockSignals(True);box.format_combo.setCurrentIndex(box.format_combo.findData(suffix));box.format_combo.blockSignals(False);name=Path(name).stem
        full=name+box.format_combo.currentData() if name else ''
        box.last_format=box.format_combo.currentData();box.format_combo.setToolTip(box.format_combo.currentText()+' · '+box.last_format)
        self.materials.item(index,0).setText(full);self.materials.item(index,0).setToolTip(full);self.changed()

    def quick_add(self,kind):
        if kind and any(self.materials.item(i,0).text()==kind for i in range(self.materials.rowCount())):kind=''
        count=self.materials.rowCount();self.add_material(kind)
        if self.materials.rowCount()>count:
            self.materials.selectRow(count);self.materials.scrollToBottom();edit=self.materials.cellWidget(count,0).name_edit;edit.setFocus();edit.selectAll()

    def remove_one(self,button):
        index=next((i for i in range(self.materials.rowCount()) if self.materials.cellWidget(i,2) is button),None)
        if index is not None:self.materials.removeRow(index);self.changed()

    def remove_material(self):
        rows=sorted({i.row() for i in self.materials.selectedIndexes()},reverse=True)
        for row in rows:self.materials.removeRow(row)
        if rows:self.changed()

    def values(self):
        kinds=[];aliases={}
        for i in range(self.materials.rowCount()):
            kind=self.materials.item(i,0).text().strip()
            if not kind:raise ValueError(f'第{i+1}份材料还没有名称，请在这一行填好。')
            kinds.append(kind);aliases[kind]=[t.strip() for t in re.split('[,，;；]',self.materials.item(i,1).text()) if t.strip()]
        return kinds,aliases

    def validate(self):
        if not self.name.text().strip():raise ValueError('先给分类起个名字，例如“验收材料”。')
        if not self.materials.rowCount():raise ValueError('还没有材料。先选模板，或点击“＋ Word”等按钮添加。')
        kinds,aliases=self.values()
        try:self.catalog.validate(self.name.text(),kinds,aliases,self.mode.currentData())
        except ValueError as e:
            if '清单不能重复' in str(e):raise ValueError('有两份材料的名称和格式相同，请改名、换格式或移除重复项。') from e
            if '关键词' in str(e):raise ValueError(str(e)+' 请展开高级设置检查。') from e
            raise
        if any(r['name'].casefold()==self.name.text().strip().casefold() and r['id']!=(self.current or {}).get('id') for r in self.records):raise ValueError('这个清单名称已经有了，换个名字即可。')
        return kinds,aliases

    def update_summary(self):
        if self.current and self.current.get('intake_mode')=='original':
            self.summary.setText('压缩包格式任选一种，只收录原包一份。已有任务继续沿用原规则和保存位置。')
            self.guide.setText('修改只用于之后的新任务。');self.save_button.setEnabled(self.dirty);self.save_button.setText('保存修改');return
        count=self.materials.rowCount();self.empty_hint.setVisible(count==0)
        self.mode_help.setText('文件分类保存后自动统计收录数量。')
        self.mode.setToolTip(self.mode_help.text())
        counts=Counter(FORMATS.get(Path(self.materials.item(i,0).text()).suffix.casefold(),'其他') for i in range(count) if self.materials.item(i,0).text())
        detail='、'.join(f'{label} {n}份' for label,n in counts.items())
        finish='所需文件全部保存成功后显示“已收齐”。'
        self.summary.setText((f'需要 {count} 份：'+detail+'。保存齐全后显示“已收齐”。') if count else '选择模板，或添加需要收纳的材料。')
        try:self.validate();hint='可以保存了。新建分类会直接保存；修改已有分类会先说明变更。';valid=True
        except ValueError as e:hint=str(e);valid=False
        if valid and not self.dirty:hint='这个分类已保存。需要调整时，直接修改上面的内容。'
        if valid and not self.active.isChecked():hint='这个分类已停用。需要重新使用时，展开高级设置并勾选“启用”。'
        self.guide.setText(hint);self.guide.setVisible(not(valid and self.dirty and self.active.isChecked()));self.save_button.setEnabled(valid and self.dirty)
        self.save_button.setToolTip('保存规则，之后的新批次可使用' if valid and self.dirty else hint if not valid else '没有尚未保存的修改')
        self.save_button.setText('保存修改' if self.current else '创建规则')

    def save(self):
        if self.current and self.current.get('intake_mode')=='original':
            try:
                d=self.archive_editor.draft()
                if d.get('intake_mode')!='original':raise ValueError('这是压缩包规则，请选择压缩包格式；普通材料请新建规则。')
                pid=self.catalog.save(d['name'],d['kinds'],mode='collect',profile_id=self.current['id'],active=self.active.isChecked(),intake_mode='original',extensions=d['extensions'])
                self.dirty=False;self.reload(pid);self.show_error('已保存。已有任务和文件保留。')
            except (ValueError,OSError,sqlite3.Error) as e:self.show_error(str(e))
            return
        try:kinds,aliases=self.validate()
        except ValueError as e:self.show_error(str(e));return
        old=self.current or {};added=[k for k in kinds if k not in old.get('kinds',[])];removed=[k for k in old.get('kinds',[]) if k not in kinds]
        if self.current:
            description=f"分类：{old['name']} → {self.name.text().strip()}\n每个软件需要的材料：{len(old['kinds'])}份 → {len(kinds)}份\n处理方式：{MODES[old['mode']]} → {MODES[self.mode.currentData()]}"
            if added:description+='\n新增：'+'、'.join(added[:8])+('等' if len(added)>8 else '')
            if removed:description+='\n移除：'+'、'.join(removed[:8])+('等' if len(removed)>8 else '')
            if bool(old['active'])!=self.active.isChecked():description+='\n启用状态：'+('停用 → 启用' if self.active.isChecked() else '启用 → 停用')
            description+='\n识别关键词及启用状态一并保存。\n\n仅用于之后的新批次，已有文件、清单与完成记录保留。'
            if QMessageBox.question(self,'保存规则修改',description,QMessageBox.Save|QMessageBox.Cancel,QMessageBox.Cancel)!=QMessageBox.Save:return
        try:
            from file_families import family_for
            families={};patterns={}
            for i,k in enumerate(kinds):
                previous=self.materials.item(i,0).data(Qt.UserRole)
                if previous in old.get('family_map',{}) and family_for(Path(k).suffix):families[k]=family_for(Path(k).suffix)
                if previous in old.get('name_patterns',{}):patterns[k]=old['name_patterns'][previous]
            pid=self.catalog.save(self.name.text(),kinds,aliases,self.mode.currentData(),old.get('id'),self.active.isChecked(),family_map=families,name_patterns=patterns)
            self.dirty=False;self.reload(pid);self.show_error('已保存。现在关闭窗口，即可在总表分类栏使用。')
        except ValueError as e:self.show_error(str(e))
        except (OSError,sqlite3.Error) as e:self.show_error('暂时没能保存，请稍后再试。填写内容仍保留。');self.error.setToolTip(str(e))

    def remove(self):
        if not self.current:return
        if QMessageBox.question(self,'停用 / 删除分类','有历史批次使用的分类会停用，历史清单和文件保留；尚未使用的分类会删除。是否继续？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        try:message=self.catalog.remove(self.current['id']);self.dirty=False;self.reload();self.show_error(message)
        except (ValueError,OSError,sqlite3.Error) as e:self.show_error(str(e))

    def move(self,direction):
        if self.current and self.may_leave():
            try:pid=self.current['id'];self.catalog.move(pid,direction);self.dirty=False;self.reload(pid)
            except (ValueError,OSError,sqlite3.Error) as e:self.show_error(str(e))

    def closeEvent(self,event):
        if self.may_leave():event.accept()
        else:event.ignore()

    def reject(self):
        if self.may_leave():super().reject()
