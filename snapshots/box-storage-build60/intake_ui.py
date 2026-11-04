"""Editable intake preview for text, spreadsheet, and existing material folders."""
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import Qt,QTimer,Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QMessageBox, QApplication, QInputDialog)
from ui_components import button, ComboBox, LineEdit
from theme import apply_to
from production_ui import text_label
from material_templates import PRESETS, package_for
from requirement_intake import parse_text, read_sheet, folder_proposals, write_template, validate_rows


class NamesPasteEdit(QPlainTextEdit):
    names_pasted=Signal()
    def insertFromMimeData(self,source):
        super().insertFromMimeData(source)
        if source.hasText() and source.text().strip():QTimer.singleShot(0,self.names_pasted.emit)


class IntakeDialog(QDialog):
    def __init__(self, parent=None, *, rows=None, default_batch=None, review_only=False):
        super().__init__(parent)
        self.setWindowTitle('导入制作需求 · 核对后建立任务')
        self.resize(1040, 780)
        self.setMinimumSize(760, 600)
        apply_to(self)
        self.token = uuid4().hex
        self.proposals = []
        self._rendering = False
        self.source_text = ''
        self.review_only = review_only
        host=parent
        while host is not None and not hasattr(host,'service'):host=host.parentWidget()
        self.catalog=getattr(getattr(host,'service',None),'classifications',None)
        self.service=getattr(host,'service',None)
        self.packages=self.catalog.packages() if self.catalog else PRESETS
        layout = QVBoxLayout(self)
        layout.addWidget(text_label('粘贴需求文字、导入表格，或选择已有材料文件夹。预览可逐行修改，确认前不建立任务。'))
        defaults = QHBoxLayout()
        self.commission = LineEdit()
        self.commission.setPlaceholderText('默认委托项目（可空）')
        self.batch = LineEdit(default_batch or datetime.now().strftime('%m-%d %H:%M:%S 收件'))
        self.batch.setPlaceholderText('未注明时使用的批次')
        self.preset = ComboBox()
        self.preset.addItems(list(self.packages))
        defaults.addWidget(self.commission, 1)
        defaults.addWidget(self.batch, 1)
        defaults.addWidget(self.preset)
        layout.addLayout(defaults)
        self.source = NamesPasteEdit()
        self.source.names_pasted.connect(self.choose_rule_and_parse)
        self.source.setPlaceholderText('项目：建筑检测材料\n第一批：只做说明书Word\n1. 建筑检测管理系统\n2. 工程质量分析软件\n\n项目：设备监测材料\n第二批：14件材料＋两个视频\n1. 设备异常监测系统')
        self.source.setMaximumHeight(140)
        layout.addWidget(self.source)
        sources = QHBoxLayout()
        self.paste_button = button('粘贴需求')
        self.parse_button = button('整理文字', True)
        self.sheet_button = button('导入 Excel / CSV')
        self.folder_button = button('识别文件夹')
        self.template_button = button('下载表格模板')
        for control in (self.paste_button, self.parse_button, self.sheet_button, self.folder_button, self.template_button):
            sources.addWidget(control)
        layout.addLayout(sources)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['委托项目', '批次', '软件名称', '软件分类', '材料套餐', '具体要求（每行一项）', '待核对'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate([130, 125, 180, 95, 165, 190, 140]):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table, 1)
        self.notice = text_label('未说明材料要求的任务保留为待确认；同批次可为每个软件选择不同套餐。')
        layout.addWidget(self.notice)
        self.error = text_label('')
        layout.addWidget(self.error)
        bottom = QHBoxLayout()
        self.remove_button = button('移除选中行')
        self.add_button = button('添加一行')
        self.cancel_button = button('取消')
        self.confirm_button = button('核对无误，建立任务', True)
        bottom.addWidget(self.add_button)
        bottom.addWidget(self.remove_button)
        bottom.addStretch()
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.confirm_button)
        layout.addLayout(bottom)
        self.parse_button.clicked.connect(self.parse)
        self.paste_button.clicked.connect(self.paste)
        self.sheet_button.clicked.connect(self.import_sheet)
        self.folder_button.clicked.connect(self.import_folder)
        self.template_button.clicked.connect(self.template)
        self.remove_button.clicked.connect(self.remove)
        self.add_button.clicked.connect(self.add)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.confirm)
        self.table.itemChanged.connect(self.item_changed)
        self.table.cellDoubleClicked.connect(self.edit_required)
        self.source.textChanged.connect(self.source_changed)
        if rows:
            self.render(rows, ['复用的仅为名称与要求，新任务不会继承检查或提交。'])

    def defaults(self):
        return dict(default_batch=self.batch.text().strip(), default_commission=self.commission.text().strip(),
                    default_requirement=self.preset.currentText())

    def render(self, rows, notes=()):
        self._rendering = True
        rows=self.catalog.resolve(rows) if self.catalog and not self.review_only else rows
        self.proposals = [dict(r) for r in rows]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, key in enumerate(('commission', 'batch', 'name', 'category')):
                self.table.setItem(i, col, QTableWidgetItem(row.get(key, '')))
            requirement = QTableWidgetItem('\n'.join(row.get('kinds', [])))
            requirement.setToolTip(requirement.text() or '双击填写具体材料要求')
            requirement.setFlags(requirement.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 5, requirement)
            warning = QTableWidgetItem(row.get('warning', ''))
            warning.setFlags(warning.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 6, warning)
            preset = ComboBox()
            preset.addItems(list(dict.fromkeys([*self.packages, row.get('package','自定义'), '自定义'])))
            preset.setCurrentText(row.get('package', package_for(row.get('kinds', []))))
            self.table.setCellWidget(i, 4, preset)
            preset.currentTextChanged.connect(lambda value, combo=preset: self.preset_changed(combo, value))
            self.table.setRowHeight(i, 80)
        self._rendering = False
        self.notice.setText(f'预览 {len(rows)} 个任务。' + ('\n' + '\n'.join(notes[:8]) if notes else ''))
        self.error.setText('')
        self.confirm_button.setEnabled(True)

    def values(self):
        result = []
        for i, original in enumerate(self.proposals):
            row = dict(original)
            for col, key in enumerate(('commission', 'batch', 'name', 'category')):
                row[key] = self.table.item(i, col).text().strip()
            row['kinds'] = [s.strip() for s in self.table.item(i, 5).text().splitlines() if s.strip()]
            row['package'] = self.table.cellWidget(i, 4).currentText()
            result.append(row)
        return result

    def preset_changed(self, combo, value):
        if self._rendering or value == '自定义':
            return
        row = next((i for i in range(self.table.rowCount()) if self.table.cellWidget(i, 4) is combo), None)
        if row is not None:
            self._rendering = True
            self.table.item(row, 5).setText('\n'.join(self.packages[value]))
            self.table.item(row, 6).setText('材料要求待确认' if value == '待确认' else '')
            self._rendering = False

    def item_changed(self, item):
        if self._rendering or item.column() != 5:
            return
        self.table.cellWidget(item.row(), 4).setCurrentText('自定义')

    def source_changed(self):
        if self.proposals:
            self.error.setText('需求原文已修改，请点击「整理文字」更新预览后再确认。')
            self.confirm_button.setEnabled(False)

    def edit_required(self, row, column):
        if column != 5:
            return
        value, okay = QInputDialog.getMultiLineText(self, '本项具体材料', '每行一项；文件职责与格式分别区分。', self.table.item(row, 5).text())
        if okay:
            self.table.item(row, 5).setText(value)
            self.table.item(row, 5).setToolTip(value)

    def replace_preview(self, rows, notes):
        if self.proposals and QMessageBox.question(self, '替换预览', '重新整理会替换当前预览及手工修改，是否继续？') != QMessageBox.Yes:
            return False
        self.render(rows, notes)
        return True

    def paste(self):
        self.source.setPlainText(QApplication.clipboard().text())
        self.choose_rule_and_parse()

    def choose_rule_and_parse(self):
        if self.service and self.source.toPlainText().strip():
            from intake_link_ui import ClassificationRuleDialog
            from dialog_lifecycle import transient_dialog
            with transient_dialog(ClassificationRuleDialog(self.service,self,current=self.preset.currentText())) as dialog:
                if dialog.exec()!=QDialog.Accepted:return
                rule=dialog.rule
            self.packages=self.catalog.packages();self.preset.clear();self.preset.addItems(list(self.packages));self.preset.setCurrentText(rule)
        self.parse()

    def parse(self):
        try:
            rows, notes = parse_text(self.source.toPlainText(), **self.defaults())
            if self.replace_preview(rows, notes):
                self.source_text = self.source.toPlainText()
        except ValueError as error:
            self.error.setText(str(error))

    def import_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入需求表', '', '需求表 (*.xlsx *.csv)')
        if path:
            try:
                if self.replace_preview(*read_sheet(path, **self.defaults())):
                    self.source_text = '表格导入：' + path
            except (ValueError, OSError, KeyError) as error:
                self.error.setText(str(error))

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择一项软件目录，或包含多个软件目录的文件夹')
        if path:
            try:
                if self.replace_preview(*folder_proposals(path, **self.defaults())):
                    self.source_text = '文件夹导入：' + path
            except (ValueError, OSError) as error:
                self.error.setText(str(error))

    def template(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存需求导入模板', 'BOX需求导入模板.xlsx', 'Excel (*.xlsx);;CSV (*.csv)')
        if path:
            try:
                write_template(path)
                self.error.setText('模板已保存：' + path)
            except OSError as error:
                self.error.setText(str(error))

    def remove(self):
        removed = {index.row() for index in self.table.selectionModel().selectedRows()}
        rows = [r for i, r in enumerate(self.values()) if i not in removed]
        self.render(rows)

    def add(self):
        from requirement_intake import proposal
        row = proposal('', commission=self.commission.text(), batch=self.batch.text(), requirement=self.preset.currentText())
        self.render([*self.values(), row])

    def confirm(self):
        try:
            self.plan = validate_rows(self.values())
            if not self.review_only and hasattr(getattr(self.parent(), 'store', None), 'check_import_conflicts'):
                self.parent().store.check_import_conflicts(self.plan)
            pending = sum(not r['kinds'] for r in self.plan)
            if not self.review_only and pending and QMessageBox.question(self, '保留待确认要求', f'{pending} 个软件尚未说明材料要求。先建立待确认任务，之后再补齐？') != QMessageBox.Yes:
                return
            self.accept()
        except ValueError as error:
            self.error.setText(str(error))
