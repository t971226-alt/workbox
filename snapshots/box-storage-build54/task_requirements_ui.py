"""Per-task delivery requirements and grouped checklist details."""
import json,sqlite3
from pathlib import Path

from PySide6.QtCore import Qt,QSize,QUrl
from PySide6.QtGui import QIcon,QPixmap,QPainter,QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,QCheckBox,QLabel,QMessageBox,QLineEdit,QWidget)
from ui_components import button, ComboBox, LineEdit
from theme import apply_to
from production_ui import text_label, local_time, MaterialDialog
from material_templates import PRESETS, package_for, group_for


class RequirementsDialog(QDialog):
    def __init__(self, page, project, parent=None):
        super().__init__(parent or page)
        self.page, self.project = page, project
        self.setWindowTitle('调整收纳规则')
        self.resize(640, 660)
        apply_to(self)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label(project['batch_name'] + '\n' + project['name']))
        layout.addWidget(text_label('选分类或直接修改清单，只影响当前软件；已有文件保留。'))
        self.packages=page.service.classifications.packages()
        self.preset = ComboBox()
        self.preset.addItems([*self.packages, '自定义'])
        self.preset.setCurrentText(project.get('package_name', '自定义'))
        layout.addWidget(self.preset)
        self.category = LineEdit(project.get('category', ''))
        self.category.setPlaceholderText('软件业务分类，如建筑检测、设备监测')
        layout.addWidget(self.category)
        self.kinds = QPlainTextEdit()
        self.kinds.setPlainText('\n'.join(m['kind'] for m in project['materials']))
        layout.addWidget(self.kinds, 1)
        self.rematch=QCheckBox('按此规则重新整理已收文件');self.rematch.setChecked(False);self.rematch.setToolTip('已有文件和历史保留，缺少的材料继续显示待补充。');layout.addWidget(self.rematch)
        layout.addWidget(text_label('变更原因（必填）'))
        self.reason = LineEdit()
        self.reason.setPlaceholderText('例如：客户追加数据库演示视频')
        layout.addWidget(self.reason)
        with page.service.db.connect() as connection:
            records = [dict(r) for r in connection.execute('SELECT * FROM requirement_changes WHERE project_id=? ORDER BY id DESC', (project['id'],))]
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setMaximumHeight(100)
        self.history.setPlainText('\n'.join(local_time(r['created_at']) + ' ' + r['reason'] + '\n' +
            '、'.join(json.loads(r['after_json']).get('kinds', [])) for r in records))
        self.history_toggle=QCheckBox('查看以前的清单调整');layout.addWidget(self.history_toggle)
        layout.addWidget(self.history);self.history.hide();self.history_toggle.toggled.connect(self.toggle_history)
        self.error = text_label('')
        layout.addWidget(self.error)
        bottom = QHBoxLayout()
        cancel = button('取消')
        self.save = button('保存本项要求', True)
        bottom.addStretch()
        bottom.addWidget(cancel)
        bottom.addWidget(self.save)
        layout.addLayout(bottom)
        cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self.validate)
        self.preset.currentTextChanged.connect(self.apply_preset)

    def toggle_history(self,visible):
        self.history.setVisible(visible)
        if hasattr(self,'_density_filter'):self._density_filter.queue()

    def apply_preset(self, name):
        if name in self.packages:
            self.kinds.setPlainText('\n'.join(self.packages[name]))

    def validate(self):
        from production import Production
        try:
            self.required = [s.strip() for s in self.kinds.toPlainText().splitlines() if s.strip()]
            if self.required:
                Production._clean(self.required, '材料类型', 40)
            if not self.reason.text().strip():
                raise ValueError('请填写设置或变更原因。')
            if len(self.reason.text()) > 1000 or len(self.category.text()) > 100:
                raise ValueError('分类最多100字符，原因最多1000字符。')
            self.accept()
        except ValueError as error:
            self.error.setText(str(error))


class TaskMaterialsDialog(QDialog):
    def __init__(self, page, project):
        super().__init__(page)
        self.page, self.project = page, project;self.checked={};self._pending=False
        from workspace_chrome import chrome_button,WorkspaceMenu
        self.setObjectName('taskMaterials');self.setWindowTitle('材料详情')
        available=self.screen().availableGeometry();self.resize(min(560,available.width()-40),min(330,available.height()-50));self.setMinimumSize(440,0)
        apply_to(self)
        layout = QVBoxLayout(self);layout.setContentsMargins(10,8,10,8);layout.setSpacing(4)
        self.heading=text_label(project['name']);self.heading.setObjectName('materialTitle');layout.addWidget(self.heading)
        self.identity=text_label('');self.identity.setObjectName('materialMuted');layout.addWidget(self.identity)
        self.progress=text_label('');self.progress.setObjectName('materialProgress');layout.addWidget(self.progress)
        self.guidance=text_label('');self.guidance.setObjectName('materialMuted');layout.addWidget(self.guidance)
        pick=QHBoxLayout();self.all_check=QCheckBox('全选可用材料');self.all_check.setTristate(True);self.all_check.clicked.connect(self.toggle_all);pick.addWidget(self.all_check);self.picked=text_label('');self.picked.setWordWrap(False);pick.addWidget(self.picked,1);self.more_button=chrome_button('更多',icon='more');self.more_menu=WorkspaceMenu(self.more_button);self.more_button.setMenu(self.more_menu);pick.addWidget(self.more_button);layout.addLayout(pick)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['分组', '材料 / 文件', '当前状态', '操作'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setColumnHidden(0,True);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.table.setColumnWidth(2,65);self.table.setColumnWidth(3,32);self.table.setIconSize(QSize(16,20))
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False);self.table.setTextElideMode(Qt.ElideMiddle);self.table.setShowGrid(False);self.table.setAlternatingRowColors(True);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.table, 1)
        location=QHBoxLayout();self.location=QLineEdit();self.location.setFixedHeight(28);self.location.setReadOnly(True);self.location.setAccessibleName('实际材料保存位置');location.addWidget(self.location,1);self.folder_button=chrome_button('打开保存位置');self.folder_button.clicked.connect(self.open_folder);location.addWidget(self.folder_button);layout.addLayout(location)
        self.message=text_label('');self.message.setObjectName('materialError');self.message.hide();layout.addWidget(self.message)
        actions=QHBoxLayout();self.pass_button=chrome_button('标记核对通过',icon='check');self.submit_button=chrome_button('确认已提交',variant='primary');self.close_button=chrome_button('关闭');actions.addWidget(self.pass_button);actions.addWidget(self.submit_button);actions.addStretch();actions.addWidget(self.close_button);layout.addLayout(actions)
        self.requirements_button=chrome_button('调整本项要求');self.classify_button=chrome_button('按要求归类已收文件');self.requirements_button.setParent(self);self.requirements_button.hide();self.classify_button.setParent(self);self.classify_button.hide();self.more_menu.addAction('调整材料要求',self.edit_requirements);self.more_menu.addAction('按要求归类已收文件',self.classify);self.more_menu.addAction('任务编号和文件夹后缀说明',self.explain_id)
        self.table.cellDoubleClicked.connect(self.activate_file)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu);self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.itemChanged.connect(self.check_changed)
        self.requirements_button.clicked.connect(self.edit_requirements)
        self.classify_button.clicked.connect(self.classify)
        self.close_button.clicked.connect(self.reject);self.pass_button.clicked.connect(self.review_selected);self.submit_button.clicked.connect(self.submit_selected)
        page.details_changed.connect(self.refresh)
        page.error.changed.connect(self.notice)
        from theme import theme_signal
        theme_signal().connect(self.apply_theme);self.apply_theme();self.refresh()
        self.more_button.hide();self.picked.hide();self.folder_button.hide();self.guidance.hide()

    def apply_theme(self,*args):
        from theme import tokens
        c=tokens();self.setStyleSheet(f'''QDialog#taskMaterials {{background:{c['surface']};}} QDialog#taskMaterials QLabel,QDialog#taskMaterials QCheckBox,QDialog#taskMaterials QLineEdit {{font-size:11px;color:{c['ink']};}} QDialog#taskMaterials QLabel#materialTitle {{font-size:12px;font-weight:500;}} QDialog#taskMaterials QLabel#materialMuted {{font-size:11px;color:{c['muted']};}} QDialog#taskMaterials QLabel#materialProgress {{font-size:11px;}} QDialog#taskMaterials QLabel#materialError {{color:{c['error']};}} QDialog#taskMaterials QTableWidget {{font-size:11px;alternate-background-color:{c['raised']};border:1px solid {c['border']};}} QDialog#taskMaterials QHeaderView::section {{font-size:11px;padding:4px;border:none;}}''')
        for b in self.findChildren(QWidget):
            if hasattr(b,'drawing_font'):b.setProperty('fontPixelSize',11);b.setFixedHeight(28)

    @staticmethod
    def signature(m):return (m.get('id'),m.get('passed'),m.get('submission_id'),m.get('dependency_invalid'),m.get('collection_issue'),m.get('collection_saved'))

    @staticmethod
    def available(m):return bool(m.get('active') and m.get('id') and m.get('collection_saved') and not m.get('collection_issue'))

    def material_status(self,m):
        if not m['active']:return '历史附加'
        if m.get('collection_issue'):return '文件异常'
        return '已收录' if m.get('collection_saved') else '未收录'

    def refresh(self):
        try:
            with self.page.service.db.connect() as connection:
                self.page.service.db._assert_projects_editable(connection,self.project['id'])
                self.project = self.page.store._project(connection, self.project['id'])
                active = [dict(m, active=True) for m in self.project['materials']]
                old = [r[0] for r in connection.execute('SELECT DISTINCT kind FROM material_versions WHERE project_id=?', (self.project['id'],)) if r[0] not in {m['kind'] for m in active}]
                self.materials = active + [dict(self.page.store._current(connection, self.project['id'], kind), active=False) for kind in old]
        except (ValueError,OSError,sqlite3.Error) as error:self.checked.clear();self.notice(str(error));self.table.setRowCount(0);return
        self._pending=False;self.checked={m['kind']:self.signature(m) for m in self.materials if self.available(m) and self.checked.get(m['kind'])==self.signature(m)}
        p = self.project
        self.heading.setText(p['name']);self.identity.setText(f"任务 WB-{p['id']:06d} · {p['package_name']}" );self.identity.setToolTip(p['batch_name']+f"\n保存文件夹的 _WB{p['id']:06d} 是任务编号，用于区分同名软件，不属于软件名称。")
        self.progress.setText(f"已收录 {p['collection_count']}/{p['required_count']} 份 · {p['production_state']}")
        missing=[m['kind'] for m in active if not self.available(m)]
        self.guidance.setText(('待补充或重新收录：'+'、'.join(missing)) if missing else '所需材料已收齐并分类保存。收齐只表示文件已保存，不代表内容已审核。')
        self.guidance.setToolTip(self.guidance.text());self.guidance.setMaximumHeight(34)
        self.location.setText(p.get('collection_folder') or '尚未保存材料');self.location.setCursorPosition(0);self.location.setToolTip(p.get('collection_folder') or '收录后可打开保存位置');self.folder_button.setEnabled(bool(p.get('collection_folder')))
        selected=self.table.currentRow();self.table.blockSignals(True)
        self.table.setRowCount(len(self.materials))
        for i, m in enumerate(self.materials):
            values = [group_for(m['kind']) if m['active'] else '历史 / 附加', m['kind'], self.material_status(m), '']
            for col, value in enumerate(values):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,col,item)
            from theme import tokens
            from PySide6.QtGui import QColor
            from master_ledger_chrome import font
            state=self.table.item(i,2);c=tokens();state.setForeground(QColor(c['state_delivered'] if values[2]=='已收录' else c['state_pending'] if values[2] in ('未收录','文件异常') else c['state_working']));state.setFont(font(11,True));state.setTextAlignment(Qt.AlignCenter)
            item=self.table.item(i,1);item.setData(Qt.UserRole,m['kind']);item.setToolTip(m['kind']+'\n'+values[0]+('\n提交时间：'+local_time(m.get('submitted_at')) if m.get('submitted_at') else ''))
            if self.available(m) and p['processing_mode']=='review':item.setFlags(item.flags()|Qt.ItemIsUserCheckable);item.setCheckState(Qt.Checked if m['kind'] in self.checked else Qt.Unchecked)
            else:item.setFlags(item.flags()&~Qt.ItemIsUserCheckable)
            from task_query_chrome import file_icon
            pix=QPixmap(20,24);pix.fill(Qt.transparent);painter=QPainter(pix)
            from PySide6.QtCore import QRectF
            file_icon(painter,QRectF(0,0,20,24),Path(m['kind']).suffix.lower());painter.end();item.setIcon(QIcon(pix))
            from workspace_chrome import chrome_button
            more=chrome_button('···');more.setFixedSize(28,25);more.setProperty('fontPixelSize',11);more.setAccessibleName('材料操作：'+m['kind']);menu=self.file_menu(i);menu.setParent(more);more.setMenu(menu);self.table.setCellWidget(i,3,more);self.table.setRowHeight(i,30)
        if self.materials:self.table.selectRow(min(max(selected,0),len(self.materials)-1))
        self.table.blockSignals(False);self.update_selection()

    def activate_file(self,row,column):
        m=self.materials[row]
        if m.get('id'):self.open_file(m['kind'],m['id'])
        else:self.open_material(row,column)

    def file_menu(self,index):
        from material_context import MaterialMenu
        m=self.materials[index];menu=MaterialMenu(self)
        action=menu.addAction('打开文件',lambda:self.open_file(m['kind'],m.get('id')));action.setEnabled(bool(m.get('id')))
        menu.addAction('打开原始位置',lambda:self.locate_material(m,True))
        menu.addAction('打开保存位置',lambda:self.locate_material(m,False))
        menu.addSeparator();menu.addAction('文件与版本',lambda:self.open_material(index,1))
        update=menu.addAction('更新材料' if m.get('id') else '收录材料',lambda:self.receive_material(m));update.setEnabled(m['active'])
        return menu

    def receive_material(self,material):
        from PySide6.QtWidgets import QFileDialog
        source,_=QFileDialog.getOpenFileName(self,'选择'+material['kind'])
        if source:self.page.request('收录材料',lambda **kw:self.page.service.receive_interactive([source],project_id=self.project['id'],material_kind=material['kind'],**kw))

    def context_menu(self,point):
        index=self.table.indexAt(point)
        if not index.isValid():return
        self.table.selectRow(index.row());menu=self.file_menu(index.row())
        try:menu.exec(self.table.viewport().mapToGlobal(point))
        finally:menu.deleteLater()

    def locate_material(self,material,original):
        from saved_locations import original_file_location,saved_file_location,open_location
        try:
            if original:target=original_file_location(self.page.service,self.project['id'],version_id=material.get('id'),kind=material['kind'])
            elif material['active']:target=saved_file_location(self.page.service,self.project['id'],kind=material['kind'])
            else:target=Path(material['path'])
            if not target.exists():raise ValueError('文件已移动或缺失，请核对位置。')
            open_location(target);self.notice('')
        except (ValueError,OSError,sqlite3.Error) as error:self.notice(str(error))

    def check_changed(self,item):
        if item.column()!=1:return
        m=next((m for m in self.materials if m['kind']==item.data(Qt.UserRole)),None)
        if m and self.available(m) and item.checkState()==Qt.Checked:self.checked[m['kind']]=self.signature(m)
        elif m:self.checked.pop(m['kind'],None)
        self.update_selection()

    def toggle_all(self):
        available=[m for m in self.materials if self.available(m)];choose=len(self.checked)<len(available);self.checked={m['kind']:self.signature(m) for m in available} if choose else {}
        self.table.blockSignals(True)
        for i,m in enumerate(self.materials):
            if self.available(m):self.table.item(i,1).setCheckState(Qt.Checked if choose else Qt.Unchecked)
        self.table.blockSignals(False);self.update_selection()

    def update_selection(self):
        self.pass_button.hide();self.submit_button.hide();self.all_check.hide();self.checked.clear()
        self.picked.setText('文件已保存后自动更新收录数量。')

    def notice(self,message):
        self.message.setText(str(message));self.message.setVisible(bool(message))
        if message:self._pending=False;self.update_selection()

    def selected_versions(self):
        with self.page.service.db.connect() as connection:
            self.page.service.db._assert_projects_editable(connection,self.project['id']);fresh=self.page.store._project(connection,self.project['id'])
        current={m['kind']:m for m in fresh['materials']}
        if any(kind not in current or self.signature(current[kind])!=signature for kind,signature in self.checked.items()):raise ValueError('所选材料已更新，请刷新后重新勾选。')
        return [current[kind]['id'] for kind in self.checked]

    def review_selected(self):self.process_selected(False)
    def submit_selected(self):self.process_selected(True)
    def process_selected(self,submitting):
        try:
            versions=self.selected_versions()
            if not versions:return
            question=f'确认你已在外部提交所选 {len(versions)} 份材料的当前版本？' if submitting else f'确认所选 {len(versions)} 份材料均已人工检查？核对通过不代表已经提交。'
            if QMessageBox.question(self,'确认已提交' if submitting else '标记核对通过',question,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
            self._pending=True;self.update_selection();self.notice('')
            self.page.request('记录所选材料提交' if submitting else '保存所选材料核对结果',lambda **kw:self.page.store.submit(versions,**kw) if submitting else self.page.store.review_many(versions,**kw))
        except (ValueError,OSError,sqlite3.Error) as error:self.refresh();self.notice(str(error))

    def open_file(self,kind,version_id):
        try:
            with self.page.service.db.connect() as connection:
                self.page.service.db._assert_projects_editable(connection,self.project['id']);current=self.page.store._current(connection,self.project['id'],kind)
            if current.get('id')!=version_id:raise ValueError('材料已更新，请刷新后查看最新版本。')
            from saved_locations import saved_file_location,open_location
            active=any(m['kind']==kind and m['active'] for m in self.materials);path=saved_file_location(self.page.service,self.project['id'],kind=kind) if active else Path(current['path'])
            if not path.is_file():raise ValueError('文件暂不可用，请重新收录。')
            from file_formats import OFFICE,VIDEOS,AUDIO,ARCHIVES
            if path.suffix.lower() not in OFFICE|VIDEOS|AUDIO|ARCHIVES:open_location(path);return
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):raise OSError('无法打开文件，请检查默认打开软件。')
        except (ValueError,OSError,sqlite3.Error) as error:self.notice(str(error))

    def open_folder(self):
        from saved_locations import collection_location,open_location
        try:open_location(collection_location(self.page.service,self.project['id']))
        except (ValueError,OSError,sqlite3.Error) as error:self.notice(str(error))

    def explain_id(self):QMessageBox.information(self,'文件夹名称说明',"新建文件夹默认使用软件名称；同名任务需要分开保存时添加首次收录时间。BOX 内部任务编号只用于关联记录。历史文件夹保留原位置，可能仍带有旧编号。")

    def open_material(self, row, column):
        dialog = MaterialDialog(self.page, self.project, self.materials[row])
        if not self.materials[row]['active']:
            for control in (dialog.import_button, dialog.bind_button, dialog.reuse_button, dialog.pass_button, dialog.reject_button, dialog.submit_button):
                control.setEnabled(False)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def edit_requirements(self):
        self.page.edit_task(self.project)
        self.refresh()

    def classify(self):
        pid = self.project['id']
        self.page.request('归类已有材料', lambda **kw: self.page.store.classify_files(pid, **kw))
        self.accept()
