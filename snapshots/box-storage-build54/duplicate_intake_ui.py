"""Reviewable choice before a duplicate material changes an existing task."""
from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout,QPlainTextEdit
from ui_components import button, ComboBox
from production_ui import text_label,local_time
from theme import apply_to


class DuplicateMaterialDialog(QDialog):
    def __init__(self, group, parent=None, *, allow_overwrite_all=False):
        super().__init__(parent)
        self.setWindowTitle('已存在同名文件')
        self.resize(620, 370)
        self.decision = None
        self.apply_to_all = False
        apply_to(self)
        layout = QVBoxLayout(self)
        title = text_label('已存在同名材料：' + group['name'])
        title.setObjectName('title')
        layout.addWidget(title)
        files = list(dict.fromkeys(Path(p).name for p in group['files']))
        preview = text_label(f'本次共 {len(files)} 个文件。同一软件的本次材料一起处理。\n' + '\n'.join(n if len(n) <= 60 else n[:57] + '…' for n in files[:5]))
        preview.setToolTip('\n'.join(files[:20]))
        layout.addWidget(preview)
        layout.addWidget(text_label('选择要替换的任务；选择“新建”则另存一份。'))
        self.tasks = ComboBox()
        self.tasks.setMinimumContentsLength(22)
        self.tasks.setSizeAdjustPolicy(ComboBox.AdjustToMinimumContentsLengthWithIcon)
        for row in group['candidates']:
            self.tasks.addItem(f"WB-{row['id']:06d} · {row['batch_name']} · {row['production_state']}", row['id'])
        self.tasks.setToolTip(self.tasks.currentText())
        self.tasks.currentTextChanged.connect(self.tasks.setToolTip)
        layout.addWidget(self.tasks)
        self.details_button=button('查看新旧文件对比');self.details_button.setCheckable(True);layout.addWidget(self.details_button)
        self.comparison=QPlainTextEdit();self.comparison.setReadOnly(True);self.comparison.setMaximumHeight(110);layout.addWidget(self.comparison);self.comparison.hide();self.details_button.toggled.connect(self.show_comparison)
        self.group=group;self.tasks.currentIndexChanged.connect(self.compare_files);self.compare_files()
        layout.addWidget(text_label('替换：更新已有材料，保留旧版本。新建：另建任务，单独保存。'))
        layout.addStretch()
        actions = QHBoxLayout()
        self.cancel_button = button('取消')
        self.skip_button=button('跳过这些材料');self.skip_button.clicked.connect(lambda:self.choose('skip'));self.skip_button.setVisible(not group.get('raw_original'));actions.addWidget(self.skip_button)
        self.overwrite_button = button('替换已有材料')
        self.new_button = button('新建并另存', True)
        self.overwrite_all_button = button('全部覆盖')
        self.overwrite_all_button.setVisible(allow_overwrite_all)
        self.overwrite_all_button.setToolTip('仅对本次收录生效；后续归属唯一的同名材料一起替换。同名任务有多条时仍需选择。旧版本保留。')
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        actions.addWidget(self.overwrite_button)
        actions.addWidget(self.overwrite_all_button)
        actions.addWidget(self.new_button)
        layout.addLayout(actions)
        for control in (self.new_button, self.overwrite_button, self.overwrite_all_button, self.cancel_button):
            control.setAutoDefault(False)
            control.setDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.new_button.clicked.connect(lambda: self.choose('new'))
        self.overwrite_all_button.clicked.connect(self.choose_all)
        self.overwrite_button.clicked.connect(lambda: self.choose('overwrite'))

    def show_comparison(self,visible):
        self.comparison.setVisible(visible)
        if hasattr(self,'_density_filter'):self._density_filter.queue()

    def compare_files(self,*_):
        from production import infer_kind
        from datetime import datetime
        row=next(r for r in self.group['candidates'] if r['id']==self.tasks.currentData());lines=[]
        for path in self.group['files']:
            p=Path(path);kind=infer_kind(p.name,[m['kind'] for m in row['materials']],row['name'])
            old=next((m for m in row['materials'] if m['kind']==kind and m['id']),None)
            if self.group.get('raw_original'):
                zips=row.get('original_zips',[]);old=dict(zips[0],size=Path(zips[0]['path']).stat().st_size) if zips and Path(zips[0]['path']).is_file() else None
            try:
                stat=p.stat();fresh=f'{stat.st_size:,} 字节 · '+datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            except OSError:fresh='来源暂不可用'
            lines.append(p.name+'\n本次（修改时间）：'+fresh+'\n来源：'+str(p)+'\n已有（入库时间）：'+(f"{old['size']:,} 字节 · {local_time(old['created_at'])}\n版本位置：{old['path']}" if old else '没有对应的当前材料'))
        self.comparison.setPlainText('\n\n'.join(lines))

    def choose_all(self):
        self.apply_to_all=True
        self.choose('overwrite')

    def choose(self, action):
        self.decision = dict(action=action, project_id=self.tasks.currentData())
        self.accept()
