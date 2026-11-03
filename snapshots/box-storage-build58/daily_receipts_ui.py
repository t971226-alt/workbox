"""Compact daily summary above the conversation and its right-side drilldown."""
import sqlite3
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView
from workspace_chrome import chrome_button,WorkspaceMenu
from daily_receipts import snapshot,today,category_title


class DailySummary(QWidget):
    def __init__(self,service,activate,parent=None):
        super().__init__(parent);self.service=service;self.activate=activate;self._key=None
        self.setObjectName('dailySummary')
        self.root=QVBoxLayout(self);self.root.setContentsMargins(0,0,0,4);self.root.setSpacing(2)
        self.overview=QFrame(self);self.overview.setObjectName('dailyOverview')
        overview_layout=QVBoxLayout(self.overview);overview_layout.setContentsMargins(10,8,10,7);overview_layout.setSpacing(2)
        self.root.addWidget(self.overview)
        heading=QHBoxLayout();heading.setContentsMargins(0,0,0,0)
        self.title=QLabel('今日收录');self.date=QLabel();self.date.setObjectName('subtle')
        heading.addWidget(self.title);heading.addStretch();heading.addWidget(self.date);overview_layout.addLayout(heading)
        self.total=chrome_button('正在读取…',icon='arrow');self.total.setProperty('fontPixelSize',12);self.total.setFixedHeight(30)
        self.total.clicked.connect(lambda:self.open());overview_layout.addWidget(self.total)
        self.category_buttons=[]
        from theme import theme_signal
        theme_signal().connect(self.apply_theme);self.apply_theme()

    def apply_theme(self,*_):
        from theme import tokens
        c=tokens()
        self.overview.setStyleSheet('QFrame#dailyOverview{background:'+c['notice_bg']+';border:1px solid '+c['accent']+';border-radius:9px;} QFrame#dailyOverview QLabel{background:transparent;border:none;color:'+c['ink']+';font-size:12px;} QFrame#dailyOverview QLabel#subtle{color:'+c['muted']+';font-size:11px;}')

    def open(self,category=None):
        self.activate(dict(intent='daily_receipts',day=today(),category=category))

    def refresh(self):
        try:data=snapshot(self.service)
        except (sqlite3.Error,OSError,ValueError):
            self.total.setText('统计暂不可用 · 点击重试');self._key=None;return
        key=(data['day'],data['materials'],data['software'],tuple((c['name'],c['materials'],c['software']) for c in data['categories']))
        if key==self._key:return
        self._key=key;self.date.setText(data['day'][5:].replace('-','月')+'日')
        self.total.setText(f"{data['materials']} 份材料 · {data['software']} 个软件  明细")
        self.total.setToolTip('今天成功收录的材料；同一材料重复更新只计一份。点击查看文件明细。')
        for b in self.category_buttons:self.root.removeWidget(b);b.hide();b.deleteLater()
        self.category_buttons=[]
        for c in data['categories'][:4]:
            b=chrome_button(f"{c['title']}  {c['software']} 个软件 · {c['materials']} 份")
            b.setProperty('fontPixelSize',11);b.setFixedHeight(26);b.setToolTip(c['title']+'：点击查看今天收录的文件')
            b.clicked.connect(lambda checked=False,n=c['name']:self.open(n));self.root.addWidget(b);self.category_buttons.append(b)
        if len(data['categories'])>4:
            b=chrome_button('更多分类');b.setProperty('fontPixelSize',12);b.setFixedHeight(26);menu=WorkspaceMenu(b);b.setMenu(menu)
            for c in data['categories'][4:]:menu.addAction(f"{c['title']} · {c['software']} 个软件 · {c['materials']} 份",lambda n=c['name']:self.open(n))
            self.root.addWidget(b);self.category_buttons.append(b)


class DailyReceiptsPage(QWidget):
    def __init__(self,page):
        super().__init__(page);self.page=page;self.service=page.service;self.spec={};self.entries=[]
        self.setObjectName('dailyReceiptsPage')
        self.setAttribute(Qt.WA_StyledBackground,True)
        root=QVBoxLayout(self);root.setContentsMargins(12,4,10,6);root.setSpacing(6)
        line=QHBoxLayout();self.back=chrome_button('返回材料表',icon='back');self.back.clicked.connect(page.show_task_workspace)
        line.addWidget(self.back);line.addStretch();self.reload=chrome_button('刷新',icon='refresh');self.reload.clicked.connect(self.refresh);line.addWidget(self.reload);root.addLayout(line)
        self.heading=QLabel();root.addWidget(self.heading);self.count=QLabel();self.count.setObjectName('subtle');root.addWidget(self.count)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['软件 / 材料','文件名','收录时间','保存位置'])
        self.table.verticalHeader().hide();self.table.setWordWrap(False);self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.table.setAccessibleName('当天实际收录材料明细')
        self.table.cellDoubleClicked.connect(self.open_file);root.addWidget(self.table,1)
        self.empty=QLabel('今天还没有成功收录的材料。');root.addWidget(self.empty)
        self.location=chrome_button('打开所选文件位置',icon='categories');self.location.clicked.connect(self.open_location);root.addWidget(self.location)
        self.note=QLabel('按成功收录记录统计，同一材料当天重复更新只计一份。双击文件可打开。');self.note.setObjectName('subtle');self.note.setWordWrap(True);root.addWidget(self.note)
        self.error=QLabel();self.error.setObjectName('error');root.addWidget(self.error);self.error.hide()
        from theme import theme_signal
        theme_signal().connect(self.apply_theme);self.apply_theme()

    def apply_theme(self,*_):
        from theme import tokens
        c=tokens()
        self.setStyleSheet('QWidget#dailyReceiptsPage{background:'+c['surface']+';} QWidget#dailyReceiptsPage QLabel{font-size:12px;color:'+c['ink']+';} QWidget#dailyReceiptsPage QLabel#subtle{font-size:11px;color:'+c['muted']+';} QWidget#dailyReceiptsPage QTableWidget{font-size:12px;background:'+c['surface']+';color:'+c['ink']+';border:1px solid '+c['border']+';border-radius:5px;} QWidget#dailyReceiptsPage QHeaderView::section{font-size:11px;padding:6px 4px;}')

    def apply(self,spec):self.spec=dict(spec);self.refresh()

    def refresh(self):
        try:
            data=snapshot(self.service,self.spec.get('day'));cat=self.spec.get('category')
            self.entries=[v for v in data['entries'] if cat is None or v['category']==cat]
            self.heading.setText(data['day']+' · 收录明细'+(' · '+category_title(cat) if cat else ''))
            self.count.setText(f"{len(self.entries)} 份材料 · {len({v['project_id'] for v in self.entries})} 个软件")
            self.table.setRowCount(len(self.entries))
            from production_ui import local_time
            for i,v in enumerate(self.entries):
                for j,text in enumerate([v['name']+' · '+v['kind'],v['filename'],local_time(v['created_at']),v['path']]):
                    item=QTableWidgetItem(text);item.setToolTip(text);self.table.setItem(i,j,item)
                self.table.setRowHeight(i,34)
            self.empty.setVisible(not self.entries);self.location.setEnabled(bool(self.entries));self.error.hide()
            if self.entries:self.table.selectRow(0)
        except (sqlite3.Error,OSError,ValueError) as e:self.error.setText('暂时无法读取收录明细：'+str(e));self.error.show()

    def open_file(self,row,*_):
        if 0<=row<len(self.entries):self.open_path(self.entries[row]['path'])

    def open_location(self):
        row=self.table.currentRow()
        if 0<=row<len(self.entries):self.open_path(str(Path(self.entries[row]['path']).parent))

    def open_path(self,path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.error.setText('无法打开：'+path);self.error.show()
