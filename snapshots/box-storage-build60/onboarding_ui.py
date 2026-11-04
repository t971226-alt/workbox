"""首次设置与只读使用指南，沿用工作设置的保存与目录校验。"""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from compact_ui import button, label
from theme import apply_to
from ui_components import LineEdit as QLineEdit
from ui import app_icon


def _identify(widget, name):
    if not widget.objectName():
        widget.setObjectName(name)
    widget.setAccessibleName(name)
    return widget


def _dialog_layout(dialog, title):
    dialog.setWindowTitle(title)
    dialog.setWindowIcon(app_icon())
    apply_to(dialog)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(8)
    return layout


def _fit_screen(dialog, width, height):
    available = dialog.screen().availableGeometry()
    dialog.resize(min(width, max(280, available.width() - 32)),
                  min(height, max(240, available.height() - 32)))


def _scroll_content(content):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return scroll


def _instructions(layout):
    for title, text in (
        ('1. 名称入总表', '在首页会话输入一批软件名称，核对后追加到同一张软件材料总表。有材料要求可一起填写；只给名称时收到成品后识别类型。'),
        ('2. 拖入成品', '将文件或文件夹拖进 BOX。按软件名称匹配，按单 Word、五件材料、14件＋视频分类收录并回填原表；明确匹配的材料ZIP会解开收录。有同名批次时选择归属，原件保留。'),
        ('3. 整理与查询', '待整理保留缺材料、待确认和有异常的任务，跨天不清空。收录查询默认显示最近一天有收录的任务，可按日期和分类查找历史。材料按规则分类保存，文件保存成功后更新数量，收齐即显示已收齐。'),
    ):
        layout.addWidget(label(title, 'title', True))
        layout.addWidget(label(text, '', True))


class FirstRunDialog(QDialog):
    """Choose storage once; the production workflow always uses human confirmation."""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit as NativeLineEdit
        from workspace_chrome import chrome_button
        self.service = service
        self._initial_settings = service.workspaces.settings()
        settings = self._initial_settings
        self.setObjectName('initialSetup')
        layout = _dialog_layout(self, 'BOX · 保存位置')
        title = label('设置保存位置', 'setupTitle')
        layout.addWidget(title)
        layout.addWidget(label('选择材料保存目录，保存后即可进入工作台。', 'subtle', True))
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)
        self.work_root = _identify(NativeLineEdit(settings['work_root']), '材料保存目录')
        for caption, edit, attribute in (
            ('材料保存目录', self.work_root, 'work_browse_button'),
        ):
            heading = label(caption)
            heading.setBuddy(edit)
            body.addWidget(heading)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            browse = chrome_button('选择文件夹', variant='secondary')
            browse.setAccessibleName('选择' + caption)
            browse.clicked.connect(lambda _, target=edit: self.browse(target))
            setattr(self, attribute, browse)
            row.addWidget(browse)
            body.addLayout(row)
        body.addWidget(label('成品将按类型和批次存入这个目录下的“分类收录”。之后可在工作设置中修改。', 'subtle', True))
        body.addWidget(label('进入工作台后，拖入材料即可识别分类；缺失或异常会显示在待整理中，文件收齐后自动更新状态。', 'subtle', True))
        body.addStretch()
        self.scroll = _scroll_content(content)
        layout.addWidget(self.scroll, 1)
        self.error = _identify(label('', 'error', True), '保存位置错误提示')
        self.error.hide()
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.cancel_button = _identify(chrome_button('取消'), '取消首次设置')
        self.finish_button = _identify(chrome_button('开始使用', icon='arrow', variant='primary'), '保存目录并开始使用')
        self.cancel_button.clicked.connect(self.reject)
        self.finish_button.clicked.connect(self.save)
        self.finish_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        actions.addWidget(self.finish_button)
        layout.addLayout(actions)
        _fit_screen(self, 560, 280)

    def browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, '选择本地目录', edit.text())
        if path:
            edit.setText(path)

    def save(self):
        try:
            self.service.workspaces.configure_materials(
                self.work_root.text(), self._initial_settings['stable_seconds'],
                auto_create_projects=self._initial_settings.get('auto_create_projects', True),
                auto_deliver_zip=False,
                zip_delivery_min_files=self._initial_settings.get('zip_delivery_min_files', 4), complete_setup=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error.setText('保存位置未能保存：' + str(error))
            self.error.show()
            return
        self.accept()

class QuickStartDialog(QDialog):
    """A short read-only guide to the current materials workflow."""
    def __init__(self, service, parent=None):
        super().__init__(parent)
        from workspace_chrome import chrome_button
        layout = _dialog_layout(self, 'BOX · 使用说明')
        content = QWidget(); body = QVBoxLayout(content); body.setContentsMargins(4, 4, 4, 4)
        _instructions(body)
        body.addWidget(label('“待整理”保留往日未收齐、待确认或异常的任务；“收录查询”默认显示最近一天有收录的任务，包括未收齐的材料，可按分类、状态和日期筛选。', 'subtle', True))
        self.work_root = _identify(QLineEdit(service.workspaces.settings()['work_root']), '材料保存目录')
        self.work_root.setReadOnly(True)
        body.addWidget(label('材料保存位置')); body.addWidget(self.work_root)
        body.addWidget(label('同名材料再次收录时可选择新建或覆盖，旧版本与记录保留。', 'subtle', True))
        body.addWidget(label('在“设置 → 规则管理”调整文件类型和关键词；在“设置 → 文件夹管理”选择保存位置并绑定规则，历史批次保留原清单。设置中的“开机自启”可在登录后显示悬浮球和托盘。', 'subtle', True))
        body.addStretch(); layout.addWidget(_scroll_content(content), 1)
        actions = QHBoxLayout(); actions.addStretch()
        self.close_button = _identify(chrome_button('关闭'), '关闭使用说明')
        self.close_button.setDefault(True); self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button); layout.addLayout(actions)
        _fit_screen(self, 540, 470)
