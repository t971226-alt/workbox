"""Compact ledger rendering using the bundled Qt controls and local icons."""
from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont,QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from theme import resolved_mode


def palette():
    dark=resolved_mode()=='dark'
    return dict(bg='#222833' if dark else '#ffffff', alternate='#252e3a' if dark else '#fafbfd',
        ink='#f0f4f8' if dark else '#172333', muted='#b7c3cf' if dark else '#758294',
        line='#465365' if dark else '#e5eaf0', selected='#2d4645' if dark else '#e8f7f5',
        track='#465365' if dark else '#e4e9ed', green='#69d5be' if dark else '#18b45c',
        orange='#f4b65c' if dark else '#f29a05', accent='#69d5be' if dark else '#009c95')


def tone(text):
    if any(x in text for x in ('异常','损坏','缺失文件')):return '#fde5e7','#c73546'
    if any(x in text for x in ('缺失','待补','待更新','未齐','未保存')):return '#fff1d8','#bd7100'
    if any(x in text for x in ('已收齐','已提交','已收录','已收纳')):return '#def6eb','#11765d'
    if any(x in text for x in ('核对','检查')):return '#e3efff','#2568b8'
    return '#edf0f4','#536173'


def font(size=12,bold=False):
    f=QFont('Microsoft YaHei UI');f.setPixelSize(size);f.setBold(bold);return f


def text(painter,rect,value,color,size=12,bold=False,align=Qt.AlignVCenter|Qt.AlignLeft):
    painter.setFont(font(size,bold));painter.setPen(QColor(color))
    value=painter.fontMetrics().elidedText(str(value),Qt.ElideRight,max(0,int(rect.width())))
    painter.drawText(rect,align,value)


def chip(painter,rect,value):
    bg,fg=tone(value)
    if resolved_mode()=='dark':bg=palette()['track'];fg=palette()['ink']
    painter.setPen(Qt.NoPen);painter.setBrush(QColor(bg));painter.drawRoundedRect(rect,4,4)
    text(painter,rect.adjusted(4,0,-4,0),value,fg,11,False,Qt.AlignCenter)


class LedgerDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(QPainter.Antialiasing)
        c=palette();r=QRectF(option.rect);selected=bool(option.state&QStyle.State_Selected)
        painter.fillRect(r,QColor(c['selected'] if selected else c['alternate'] if index.row()%2 else c['bg']))
        painter.setPen(QColor(c['line']));painter.drawLine(r.bottomLeft(),r.bottomRight());painter.drawLine(r.topRight(),r.bottomRight())
        inner=r.adjusted(12,0,-10,0);value=index.data(Qt.DisplayRole) or '';column=index.column()
        if column==0:
            if selected:painter.fillRect(QRectF(r.left(),r.top(),3,r.height()),QColor(c['accent']))
            parts=value.split('\n',1)
            text(painter,QRectF(inner.left(),r.top()+9,inner.width(),21),parts[0],c['ink'],12,True)
            text(painter,QRectF(inner.left(),r.top()+30,inner.width(),18),parts[1] if len(parts)>1 else '',c['muted'],11)
        elif column==3:
            counts=index.data(Qt.UserRole+1) or (0,0)
            count,required=counts
            text(painter,QRectF(inner.left(),r.top()+8,inner.width(),21),value,c['ink'])
            if required:
                bar=QRectF(inner.left(),r.top()+35,inner.width(),5)
                painter.setPen(Qt.NoPen);painter.setBrush(QColor(c['track']));painter.drawRoundedRect(bar,2.5,2.5)
                if count:
                    painter.setBrush(QColor(c['green'] if count>=required else c['orange']))
                    filled=QRectF(bar);filled.setWidth(bar.width()*min(1,count/required));painter.drawRoundedRect(filled,2.5,2.5)
        elif column==5:chip(painter,QRectF(inner.left(),r.center().y()-12,inner.width(),24),value)
        elif column==4 and value=='副本异常':chip(painter,QRectF(inner.left(),r.center().y()-12,inner.width(),24),value)
        else:text(painter,inner,value,c['ink'])
        painter.restore()


class MaterialDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):return QSize(260,39)

    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(QPainter.Antialiasing)
        c=palette();r=QRectF(option.rect)
        painter.fillRect(r,QColor(c['selected'] if option.state&QStyle.State_Selected else c['bg']))
        painter.setPen(QColor(c['line']));painter.drawLine(r.bottomLeft(),r.bottomRight())
        parts=(index.data(Qt.DisplayRole) or '').split('\n',1);name=parts[0];state=parts[1] if len(parts)>1 else ''
        ext=Path(name).suffix.lower();color,label=('#2b82e9','W') if ext in ('.doc','.docx') else ('#f04d58','PDF') if ext=='.pdf' else ('#84909e','TXT') if ext=='.txt' else ('#ba8b3f','ZIP')
        icon=QRectF(r.left()+5,r.center().y()-11,19,23)
        painter.setPen(Qt.NoPen);painter.setBrush(QColor(color));painter.drawRoundedRect(icon,2,2)
        text(painter,icon,label,'#ffffff',8,True,Qt.AlignCenter)
        badge=QRectF(r.right()-87,r.center().y()-10,81,21)
        chip(painter,badge,state)
        text(painter,QRectF(r.left()+33,r.top(),max(0,r.width()-124),r.height()),name,c['ink'],11)
        painter.restore()
