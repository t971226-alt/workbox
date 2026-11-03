"""Compact, explicit software-name token and AND keyword editor."""
from copy import deepcopy
import re
from PySide6.QtCore import Signal,Qt
from PySide6.QtWidgets import QWidget,QHBoxLayout,QCheckBox,QLineEdit
from name_patterns import SOFTWARE_TOKEN,keyword_terms,placeholder


class KeywordCondition(QWidget):
    textChanged=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self._loading=False;self._original=None;self.manual=False
        row=QHBoxLayout(self);row.setContentsMargins(2,1,2,1);row.setSpacing(4)
        self.software=QCheckBox('当前软件名');self.software.setChecked(True);self.software.setToolTip('自动代入确认清单里的软件名称；识别不准时可在清单修改。');row.addWidget(self.software)
        self.plus=QLineEdit();self.plus.setPlaceholderText('其他关键词，如：代码');self.plus.setToolTip('关键词用中文/英文逗号、空格或 + 分隔，必须全部包含；顺序不限。');self.plus.setFixedHeight(24);self.plus.setStyleSheet('font-size:11px;padding:2px 5px;min-height:16px;border-radius:4px;');row.addWidget(self.plus,1)
        self.software.toggled.connect(self._edited);self.plus.textChanged.connect(self._edited)
        self.setStyleSheet('QWidget{font-size:11px;} QCheckBox{spacing:3px;} QLineEdit{padding:3px 5px;min-height:18px;}')
    def _edited(self,*_):
        if self._loading:return
        if SOFTWARE_TOKEN in keyword_terms(self.plus.text()) and not self.software.isChecked():
            self._loading=True;self.software.setChecked(True);self._loading=False
        self._original=None;self.manual=False;self.textChanged.emit(self.text())
    def text(self):
        if self.manual:return '手动对应'
        terms=['当前软件名'] if self.software.isChecked() else []
        if self.plus.text().strip():terms.append(self.plus.text().strip())
        return ' + '.join(terms)
    def setText(self,value):
        if value=='手动对应':self.set_pattern(dict(mode='manual'));return
        old=placeholder(value)
        if old and not re.search(r'[，,+\s]',value):self.set_pattern(old);return
        terms=([SOFTWARE_TOKEN]+([old['marker']] if old['mode']=='marker' else [])) if old else keyword_terms(value)
        self.set_pattern(dict(mode='keywords',terms=terms))
    def set_pattern(self,pattern):
        self._loading=True;self._original=deepcopy(pattern);mode=pattern['mode'];self.manual=mode=='manual'
        if mode=='keywords':terms=pattern['terms']
        elif mode=='same':terms=[SOFTWARE_TOKEN]
        elif mode=='marker':terms=[SOFTWARE_TOKEN,pattern['marker']]
        else:terms=[]
        self.software.setChecked(SOFTWARE_TOKEN in terms);self.plus.setText(' + '.join(t for t in terms if t!=SOFTWARE_TOKEN));self.plus.setPlaceholderText('手动对应（修改可启用识别）' if self.manual else '其他关键词，如：代码');self._loading=False
        self.textChanged.emit(self.text())
    def pattern(self):
        if self._original is not None:return deepcopy(self._original)
        terms=keyword_terms(self.plus.text());terms=[t for t in terms if t!=SOFTWARE_TOKEN]
        return dict(mode='keywords',terms=([SOFTWARE_TOKEN] if self.software.isChecked() else [])+terms)
