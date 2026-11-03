"""Compact task cells and offline file-type icons, including keyboard checkboxes."""
from collections import Counter
from pathlib import Path
from PySide6.QtCore import Qt,QRectF,QPointF,QEvent
from PySide6.QtGui import QPainter,QColor,QPolygonF
from PySide6.QtWidgets import QStyledItemDelegate,QStyle,QStyleOptionButton,QApplication,QToolTip
from master_ledger_chrome import palette,font,text,chip


def file_icon(painter,rect,ext):
    from file_formats import badge
    color,word=badge(ext)
    painter.save();painter.setPen(Qt.NoPen);painter.setBrush(QColor(color));painter.drawRoundedRect(rect,2,2)
    painter.setBrush(QColor(255,255,255,110));painter.drawPolygon(QPolygonF([QPointF(rect.right()-7,rect.top()),rect.topRight(),QPointF(rect.right(),rect.top()+7)]))
    text(painter,rect.adjusted(0,4,0,0),word,'white',12 if word=='W' else 7,True,Qt.AlignCenter);painter.restore()


def icon_groups(row):
    from task_query import missing
    groups={}
    for m in missing(row):groups.setdefault(Path(m['kind']).suffix.lower(),[]).append(m['kind']+('（文件异常）' if m.get('collection_issue') else ''))
    return groups


class TaskDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        from task_query import status
        painter.save();painter.setRenderHint(QPainter.Antialiasing);c=palette();r=QRectF(option.rect);row=index.data(Qt.UserRole+1) or {}
        painter.fillRect(r,QColor(c['selected'] if option.state&QStyle.State_Selected else c['alternate'] if index.row()%2 else c['bg']))
        painter.setPen(QColor(c['line']));painter.drawLine(r.bottomLeft(),r.bottomRight());painter.drawLine(r.topRight(),r.bottomRight())
        inner=r.adjusted(8,0,-7,0);value=index.data(Qt.DisplayRole) or '';column=index.column()
        if column==0:
            check=QStyleOptionButton()
            from PySide6.QtCore import QRect
            check.rect=QRect(option.rect.left()+4,option.rect.center().y()-7,14,14);check.state=QStyle.State_Enabled|(QStyle.State_On if index.data(Qt.CheckStateRole)==Qt.Checked.value else QStyle.State_Off)
            QApplication.style().drawControl(QStyle.CE_CheckBox,check,painter)
            inner.adjust(18,0,0,0);lines=value.split('\n',1)
            text(painter,QRectF(inner.left(),r.top()+5,inner.width(),18),lines[0],c['ink'],11)
            text(painter,QRectF(inner.left(),r.top()+24,inner.width(),16),lines[1] if len(lines)>1 else '',c['muted'],10)
        elif column==2:
            text(painter,QRectF(inner.left(),r.top()+6,inner.width(),18),value,c['ink'],11)
            if row.get('required_count'):
                bar=QRectF(inner.left(),r.top()+31,inner.width(),4);painter.setPen(Qt.NoPen);painter.setBrush(QColor(c['track']));painter.drawRoundedRect(bar,2,2)
                if row['collection_count']:
                    painter.setBrush(QColor(c['green'] if row['collection_count']==row['required_count'] else c['orange']));bar.setWidth(bar.width()*min(1,row['collection_count']/row['required_count']));painter.drawRoundedRect(bar,2,2)
        elif column==3 and not index.model().parent().property('completedView'):
            groups=icon_groups(row);x=inner.left()
            for i,(ext,names) in enumerate(groups.items()):
                if x+22>inner.right():
                    text(painter,QRectF(max(inner.left(),inner.right()-21),r.top(),21,r.height()),'…',c['muted'],12);break
                file_icon(painter,QRectF(x,r.center().y()-13,22,26),ext);x+=28
                if len(names)>1:
                    text(painter,QRectF(x-3,r.top(),21,r.height()),'×'+str(len(names)),c['muted'],10);x+=21
            if not groups:text(painter,inner,value,c['muted'],11)
        elif column==4:chip(painter,QRectF(inner.left(),r.center().y()-10,inner.width(),20),value)
        else:text(painter,inner,value,c['muted'] if value=='—' else c['ink'],11)
        painter.restore()

    def editorEvent(self,event,model,option,index):
        if index.column()==0 and ((event.type()==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton and event.position().x()-option.rect.left()<24) or (event.type()==QEvent.KeyPress and event.key()==Qt.Key_Space)):
            value=Qt.Unchecked.value if index.data(Qt.CheckStateRole)==Qt.Checked.value else Qt.Checked.value
            table=self.parent();anchor=getattr(table,'_check_anchor',index.row())
            if event.modifiers()&Qt.ShiftModifier:
                for row in range(min(anchor,index.row()),max(anchor,index.row())+1):model.setData(model.index(row,0),value,Qt.CheckStateRole)
            else:model.setData(index,value,Qt.CheckStateRole)
            table._check_anchor=index.row();return True
        return False

    def helpEvent(self,event,view,option,index):
        if index.column()==3 and not index.model().parent().property('completedView'):
            x=option.rect.left()+8;row=index.data(Qt.UserRole+1) or {}
            for ext,names in icon_groups(row).items():
                width=28+(21 if len(names)>1 else 0)
                if x<=event.pos().x()<x+width:
                    QToolTip.showText(event.globalPos(),'\n'.join(names),view);return True
                x+=width
        return super().helpEvent(event,view,option,index)
