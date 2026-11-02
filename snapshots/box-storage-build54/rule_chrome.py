"""Compact, accessible file-type summaries using the existing offline icon painter."""
from pathlib import Path
from collections import Counter
from PySide6.QtCore import Qt,QRectF,Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QLabel,QRadioButton,QScrollArea
from workspace_chrome import colors


class FileBadge(QWidget):
    def __init__(self,ext,tooltip='',parent=None):
        super().__init__(parent);self.ext=ext;self.setFixedSize(22,27);self.setToolTip(tooltip or ext);self.setAccessibleName(tooltip or ext)
    def paintEvent(self,event):
        from task_query_chrome import file_icon
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);file_icon(p,QRectF(1,1,20,24),self.ext);p.end()


class RuleTypes(QWidget):
    def __init__(self,kinds=(),*,extensions=None,parent=None):
        super().__init__(parent);line=QHBoxLayout(self);line.setContentsMargins(0,0,0,0);line.setSpacing(4)
        alternative=extensions is not None
        groups={}
        for kind in kinds:groups.setdefault(Path(kind).suffix.casefold(),[]).append(kind)
        items=[(ext,[ext]) for ext in extensions] if alternative else list(groups.items())
        for i,(ext,names) in enumerate(items[:3]):
            if i:line.addWidget(QLabel('／' if alternative else '＋'))
            line.addWidget(FileBadge(ext,'允许 '+ext if alternative else '\n'.join(names)))
            if not alternative and len(names)>1:line.addWidget(QLabel('×'+str(len(names))))
        if len(items)>3:line.addWidget(QLabel('…'))
        count=QLabel('任选 1 份' if alternative else '共 '+str(len(kinds))+' 份');count.setStyleSheet('color:'+colors()['muted']+';font-size:11px;');line.addWidget(count);line.addStretch()
        self.setToolTip('任选一种格式，只需一份压缩包' if alternative else '\n'.join(kinds))


class RulePicker(QWidget):
    changed=Signal(object)
    def __init__(self,records,parent=None):
        super().__init__(parent);self.records=list(records);self.buttons={};self.cards={}
        from PySide6.QtWidgets import QButtonGroup
        self.group=QButtonGroup(self);outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        body=QWidget();rows=QVBoxLayout(body);rows.setContentsMargins(0,0,0,0);rows.setSpacing(0)
        for i,r in enumerate(self.records):
            card=QWidget();card.setObjectName('ruleCard');line=QHBoxLayout(card);line.setContentsMargins(7,3,7,3);line.setSpacing(8)
            from PySide6.QtGui import QFontMetrics
            button=QRadioButton();button.setText(QFontMetrics(button.font()).elidedText(r['name'],Qt.ElideRight,140));button.setFixedWidth(160);button.setToolTip(r['name']);button.setAccessibleName(r['name']);self.group.addButton(button,i);line.addWidget(button)
            types=RuleTypes(r.get('kinds',[]),extensions=r.get('extensions') if r.get('intake_mode')=='original' else None);line.addWidget(types,1);rows.addWidget(card)
            self.buttons[r['name']]=button;self.cards[r['name']]=card
        self.group.idToggled.connect(self.toggled)
        scroll=QScrollArea();scroll.setFrameShape(QScrollArea.NoFrame);scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);scroll.setWidget(body);scroll.setFixedHeight(min(3,len(self.records))*34+2);outer.addWidget(scroll)
        self.scroll=scroll
    def toggled(self,index,checked):
        if not checked:return
        record=self.records[index]
        for name,card in self.cards.items():
            card.setStyleSheet('#ruleCard{background:'+('#e9f6f4' if name==record['name'] else colors()['surface'])+';border-bottom:1px solid '+colors()['border']+';}')
        self.changed.emit(record)
    def select(self,name):
        if name in self.buttons:
            self.buttons[name].setChecked(True);self.scroll.ensureWidgetVisible(self.cards[name])
    def showEvent(self,event):
        super().showEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,self.ensure_selected)
    def ensure_selected(self):
        import shiboken6
        if not shiboken6.isValid(self):return
        record=self.current()
        if record:self.scroll.ensureWidgetVisible(self.cards[record['name']])
    def current(self):
        i=self.group.checkedId();return self.records[i] if i>=0 else None
