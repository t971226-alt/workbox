"""Shared small controls for rule types and independently editable output names."""
from pathlib import Path
from PySide6.QtCore import Qt,Signal,QSize,QRectF
from PySide6.QtGui import QIcon,QPixmap,QPainter
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QFileDialog,QInputDialog,QMenu
from workspace_chrome import WorkspaceComboBox,chrome_button
from file_families import TYPES,family_for


def type_icon(ext):
    from task_query_chrome import file_icon
    pix=QPixmap(20,25);pix.fill(Qt.transparent);p=QPainter(pix);file_icon(p,QRectF(0,0,19,24),ext);p.end();return QIcon(pix)


class TypeCombo(WorkspaceComboBox):
    def __init__(self,parent=None,*,archive_only=False):
        super().__init__(parent);self.strict=False;self.actual_suffix=None;self._previous=0
        for key,(title,ext,extensions) in TYPES.items():
            if archive_only and key!='archive':continue
            self.addItem(type_icon(ext),title,'__archive__' if key=='archive' else ext)
            self.setItemData(self.count()-1,'允许 '+ ' / '.join(sorted(x.lstrip('.') for x in extensions)),Qt.ToolTipRole)
        if not archive_only:self.addItem('其他格式…','__other__')
        self.setIconSize(QSize(16,21));self.setProperty('fontPixelSize',12);self.setFixedHeight(30);self.currentIndexChanged.connect(self.selected)
        self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(self.limits)
    def set_suffix(self,ext,*,strict=False):
        self.strict=strict;self.actual_suffix=ext
        family=family_for(ext);data='__archive__' if family=='archive' else TYPES[family][1] if family else ext
        if self.findData(data)<0:self.insertItem(max(0,self.count()-1),ext.upper().lstrip('.'),ext)
        self.setCurrentIndex(self.findData(data));self.setToolTip(self.itemData(self.currentIndex(),Qt.ToolTipRole) or ext)
    def family(self):
        data=self.currentData();return 'archive' if data=='__archive__' else family_for(data or '')
    def suffix(self):
        return self.actual_suffix if self.strict and self.actual_suffix else '.zip' if self.currentData()=='__archive__' else self.currentData()
    def selected(self,index):
        if self.currentData()=='__other__':
            value,ok=QInputDialog.getText(self,'其他格式','输入文件后缀，例如 dwg：')
            value='.'+value.strip().lstrip('.').lower()
            import re
            if not ok or not re.fullmatch(r'\.[a-z0-9]{1,12}',value):self.setCurrentIndex(self._previous);return
            if self.findData(value)<0:self.insertItem(self.count()-1,value[1:].upper(),value)
            self.setCurrentIndex(self.findData(value));self.strict=True;self.actual_suffix=value;return
        self._previous=index;self.setToolTip(self.itemData(index,Qt.ToolTipRole) or '')
        family=self.family()
        if family and self.actual_suffix not in TYPES[family][2]:self.actual_suffix=TYPES[family][1]
    def limits(self,pos):
        menu=QMenu(self);action=menu.addAction('仅限当前后缀 '+(self.actual_suffix or self.suffix() or ''))
        action.setCheckable(True);action.setChecked(self.strict)
        action.toggled.connect(lambda checked:setattr(self,'strict',checked));menu.exec(self.mapToGlobal(pos))
        self.currentIndexChanged.emit(self.currentIndex())


class SaveLocationEditor(QWidget):
    changed=Signal()
    def __init__(self,service,software,suffix,parent=None,*,multiple=False,shared_root=None):
        super().__init__(parent);self.service=service;self.software=software;self.suffix=suffix;self.multiple=multiple;self.shared_root=shared_root;self._syncing=True;self.folder_dirty=False;self.base_dirty=False
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        if shared_root is None:
            heading=QLabel('保存位置与名称');heading.setObjectName('intakeSection');layout.addWidget(heading)
        self.root=shared_root if shared_root is not None else QLineEdit(str(service.workspaces.settings()['work_root']));self.root.setReadOnly(True)
        self.folder=QLineEdit(software);self.base=QLineEdit(software);self.extension=QLabel(suffix);self.extension.setObjectName('intakeSuffix')
        def row(caption,widget,extra=None):
            line=QHBoxLayout();line.setSpacing(8);label=QLabel(caption);label.setFixedWidth(88);line.addWidget(label);line.addWidget(widget,1)
            if extra:line.addWidget(extra)
            layout.addLayout(line)
        if shared_root is None:
            browse=chrome_button('选择保存目录',icon='categories',icon_only=True);browse.setFixedWidth(30);browse.clicked.connect(self.browse)
            row('统一保存到' if multiple else '保存到',self.root,browse)
        if shared_root is not None:
            line=QHBoxLayout();line.setSpacing(7);line.addWidget(QLabel('文件夹'));line.addWidget(self.folder,1);line.addSpacing(8);line.addWidget(QLabel('文件名'));line.addWidget(self.base,1);line.addWidget(self.extension);layout.addLayout(line)
        elif not multiple:row('文件夹名称',self.folder);row('保存文件名',self.base,self.extension)
        else:self.folder.hide();self.base.hide();self.extension.hide()
        self.preview=QLabel();self.preview.setObjectName('batchEntryPath' if shared_root is not None else 'intakePath');self.preview.setWordWrap(True);self.preview.setMinimumHeight(18 if shared_root is not None else 46);layout.addWidget(self.preview)
        self.folder.textChanged.connect(self.folder_changed);self.base.textChanged.connect(self.base_changed);self.root.textChanged.connect(self.refresh)
        self._syncing=False;self.refresh()
    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'选择材料保存目录',self.root.text())
        if path:self.root.setText(path)
    def folder_changed(self):
        if not self._syncing:self.folder_dirty=True
        self.refresh()
    def base_changed(self):
        if not self._syncing:self.base_dirty=True
        self.refresh()
    def set_software(self,name):
        self.software=name;self._syncing=True
        if not self.folder_dirty:self.folder.setText(name)
        if not self.base_dirty:self.base.setText(name)
        self._syncing=False;self.refresh()
    def set_existing(self,directory,base=None):
        self._syncing=True;directory=Path(directory)
        if self.shared_root is None:self.root.setText(str(directory.parent))
        self.folder.setText(directory.name);self.folder_dirty=True
        if base is not None:self.base.setText(base);self.base_dirty=True
        self._syncing=False;self.refresh()
    def refresh(self,*_):
        path=Path(self.root.text())/(self.folder.text() or '软件名称')/((self.base.text() or '软件名称')+self.suffix)
        text='保存为：'+str(Path(self.folder.text() or '软件名称')/((self.base.text() or '软件名称')+self.suffix)) if self.shared_root is not None else '按软件名称分别建文件夹，可在清单中修改名称。' if self.multiple else '最终位置：'+str(path)
        self.preview.setText(text);self.preview.setToolTip(str(path));self.changed.emit()
    def spec(self,*,folder=None,base=None):
        from intake_storage import validate
        name=base if base is not None else self.base.text()
        from file_families import family_for
        if Path(name).suffix and family_for(Path(name).suffix):
            if Path(name).suffix.casefold()!=self.suffix.casefold():raise ValueError('请只修改文件名称，实际后缀 '+self.suffix+' 会自动保留。')
            name=Path(name).stem
        return validate(self.service,dict(root=self.root.text(),folder=folder if folder is not None else self.folder.text(),base=name))
