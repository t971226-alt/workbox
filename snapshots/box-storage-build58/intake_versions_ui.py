"""Choose one incoming revision per material; unchosen source files stay intact."""
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView
from workspace_chrome import WorkspaceComboBox,chrome_button,colors
from theme import apply_to
from direct_material_intake import ranked_versions

class IntakeVersionsDialog(QDialog):
    def __init__(self,entries,groups,selected,parent=None):
        super().__init__(parent);apply_to(self);self.setWindowTitle('选择同名材料版本');self.resize(820,430)
        layout=QVBoxLayout(self)
        note=QLabel('按修改时间从新到旧排列，默认预选最近的一份；可换选或暂不收录。手动选择会保留，未选原件不删除。')
        note.setWordWrap(True);layout.addWidget(note)
        table=QTableWidget(len(groups),2);table.setHorizontalHeaderLabels(['软件 / 批次 / 材料','本次收录的版本'])
        table.verticalHeader().hide();table.setColumnWidth(0,280);table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.choices=[]
        for row,indices in enumerate(groups):
            e=entries[indices[0]]
            item=QTableWidgetItem(e['name']+'\n'+e.get('batch_label','')+' · '+e['kind']);item.setToolTip(item.text())
            item.setFlags(item.flags() & ~Qt.ItemIsEditable);table.setItem(row,0,item)
            combo=WorkspaceComboBox();combo.addItem('暂不收录，稍后处理',None)
            ordered=ranked_versions(entries,indices)
            for i in ordered:
                entry=entries[i];p=Path(entry['path']);stat=p.stat()
                stamp=entry.get('modified_ns',stat.st_mtime_ns)/1_000_000_000
                text=('最近修改 · ' if i==ordered[0] else '')+datetime.fromtimestamp(stamp).strftime('%m-%d %H:%M:%S')+' · '+entry.get('relative_path',p.name)
                combo.addItem(text,i);combo.setItemData(combo.count()-1,str(p)+'\n修改时间：'+datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')+f' · {stat.st_size:,} 字节',Qt.ToolTipRole)
            chosen=[i for i in indices if i in selected]
            combo.setCurrentIndex(combo.findData(chosen[0]) if len(chosen)==1 else 0)
            combo.setAccessibleName(e['name']+'收录版本');table.setCellWidget(row,1,combo);table.setRowHeight(row,58)
            self.choices.append((indices,combo))
        layout.addWidget(table,1);bottom=QHBoxLayout();bottom.addStretch()
        cancel=chrome_button('取消');self.apply_button=chrome_button('应用选择',variant='primary')
        cancel.clicked.connect(self.reject);self.apply_button.clicked.connect(self.accept)
        bottom.addWidget(cancel);bottom.addWidget(self.apply_button);layout.addLayout(bottom)
        c=colors();self.setStyleSheet('QDialog,QTableWidget {background:'+c['surface']+';color:'+c['ink']+';} QLabel {color:'+c['ink']+';font-size:12px;} QTableWidget {font-size:12px;gridline-color:'+c['border']+';} QHeaderView::section {background:'+c['field']+';color:'+c['ink']+';border:none;padding:7px;}')
