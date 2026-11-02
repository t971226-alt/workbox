"""Grouped master ledger and tactile batch switch. Data stays in the production store."""
from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import Qt,QRectF,Signal,QTimer,QUrl,QPointF,QSize
from PySide6.QtGui import QPainter,QColor,QPen,QLinearGradient,QRadialGradient,QPainterPath,QFont,QFontMetrics,QDesktopServices
from PySide6.QtWidgets import (QAbstractButton,QWidget,QVBoxLayout,QHBoxLayout,QScrollArea,QLabel,
    QProgressBar,QSizePolicy,QMessageBox,QInputDialog,QPushButton,QFrame,QTabBar,QLayout,QToolButton)
from workspace_chrome import chrome_button,draw_icon,colors,outline_icon,WorkspaceMenu
from material_templates import group_for


class MaterialTypeTabs(QTabBar):
    """Full labels measured with the same font used to paint; overflow uses a menu."""
    def __init__(self,parent=None):
        super().__init__(parent);font=QFont('Microsoft YaHei UI');font.setPixelSize(11);font.setHintingPreference(QFont.PreferFullHinting);self.setFont(font)
        self.setElideMode(Qt.ElideNone);self.setFixedHeight(26);self.setUsesScrollButtons(False)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);self._fitting=False
        self.currentChanged.connect(lambda *_:QTimer.singleShot(0,self.refresh_scroll_controls))

    def label_width(self,index):
        font=QFont(self.font());font.setWeight(QFont.DemiBold)
        return QFontMetrics(font).horizontalAdvance(self.tabText(index))+16
    def tabSizeHint(self,index):return QSize(min(self.label_width(index),max(1,self.width())),self.height())
    def minimumTabSizeHint(self,index):return self.tabSizeHint(index)
    def minimumSizeHint(self):return QSize(0,26)

    def resizeEvent(self,event):
        super().resizeEvent(event);QTimer.singleShot(0,self.refresh_scroll_controls)

    def refresh_scroll_controls(self):
        if self._fitting or self.width()<=0:return
        self._fitting=True;was=self.blockSignals(True)
        try:
            current=self.currentIndex();selected={current} if current>=0 else set()
            self.setFixedHeight(44 if current>=0 and self.label_width(current)>self.width() else 26)
            used=sum(self.tabSizeHint(i).width() for i in selected)
            # Current selection always stays on the strip; the menu exposes every class.
            for i in range(self.count()):
                if i in selected:continue
                width=self.tabSizeHint(i).width()
                if used+width<=self.width():selected.add(i);used+=width
            for i in selected:self.setTabVisible(i,True)
            for i in range(self.count()):
                if i not in selected:self.setTabVisible(i,False)
            for child in self.findChildren(QToolButton):child.hide()
            self.update()
        finally:self.blockSignals(was);self._fitting=False

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing);c=colors();p.setFont(self.font())
        for i in range(self.count()):
            if not self.isTabVisible(i):continue
            rect=self.tabRect(i);selected=i==self.currentIndex();p.setPen(QColor(c['ink'] if selected else c['muted']))
            f=QFont(self.font());f.setWeight(QFont.DemiBold if selected else QFont.Normal);p.setFont(f)
            p.drawText(QRectF(rect.adjusted(8,0,-8,-1)),Qt.AlignCenter|Qt.TextWordWrap,self.tabText(i))
            if selected:p.setPen(QPen(QColor(c['ink']),1.5));p.drawLine(rect.bottomLeft(),rect.bottomRight())
        p.end()


class ProcessingSwitch(QAbstractButton):
    def __init__(self,parent=None):
        super().__init__(parent);self.setCheckable(True);self.setFixedSize(220,40)
        self.keyboard_focus=False
        self.setCursor(Qt.PointingHandCursor);self.setAccessibleName('当前批次处理方式：统计整理已完成材料 / 统计初始未提交材料')

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter):self.click();event.accept()
        else:super().keyPressEvent(event)

    def focusInEvent(self,event):
        self.keyboard_focus=event.reason() in (Qt.TabFocusReason,Qt.BacktabFocusReason,Qt.ShortcutFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self,event):
        self.keyboard_focus=False
        super().focusOutEvent(event)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing);reviewing=self.isChecked();c=colors()
        if not self.isEnabled():p.setOpacity(.5)
        compact=bool(self.property('compact'))
        x,y=(78,6) if compact else (86,9)
        for left,label in [(True,'统计整理\n已完成材料'),(False,'统计初始\n未提交材料')]:
            f=QFont('Microsoft YaHei UI');f.setPixelSize(11 if compact else 12);f.setWeight(QFont.DemiBold if (not reviewing)==left else QFont.Normal)
            p.setFont(f);p.setPen(QColor(c['ink'] if (not reviewing)==left else c['muted']))
            p.drawText(QRectF(0 if left else 130 if compact else 142,0,70 if compact else 78,34 if compact else 40),Qt.AlignCenter,label)
        frame=QLinearGradient(x,y,x,y+24)
        for t,color in [(0,'#E4E5E7'),(.18,'#F3F4F5'),(.58,'#D1D3D5'),(1,'#FFFFFF')]:frame.setColorAt(t,QColor(color))
        p.setBrush(frame);p.setPen(QPen(QColor('#CACDD0'),.55));p.drawRoundedRect(QRectF(x,y-1,44,24),12,12)
        slot=QPainterPath();slot.addRoundedRect(QRectF(x+1.7,y+.7,40.6,20.6),10.3,10.3)
        well=QLinearGradient(x,y+1,x,y+21)
        for t,color in [(0,'#A4A9AE'),(.25,'#C1C5C9'),(.60,'#DFE2E4'),(1,'#F3F4F5')]:well.setColorAt(t,QColor(color))
        p.fillPath(slot,well);p.save();p.setClipPath(slot);p.setBrush(Qt.NoBrush)
        for offset,alpha in [(0,24),(.65,19),(1.3,11)]:
            p.setPen(QPen(QColor(45,49,54,alpha),1));p.drawRoundedRect(QRectF(x+1.7,y+.7+offset,40.6,20.6),10.3,10.3)
        p.restore();tx=x+21 if reviewing else x+1;p.setPen(Qt.NoPen)
        for inset,alpha in [(1.2,11),(.6,19),(0,28)]:
            p.setBrush(QColor(25,28,32,alpha));p.drawEllipse(QRectF(tx-inset,y+.8-inset+1.2,22+inset*2,22+inset*2))
        edge=QLinearGradient(tx,y,tx,y+22)
        for t,color in [(0,'#FFFFFF'),(.5,'#E5E7E9'),(1,'#AEB3B8')]:edge.setColorAt(t,QColor(color))
        ceramic=QRadialGradient(QPointF(tx+10,y+9),16,QPointF(tx+7,y+4))
        for t,color in [(0,'#FFFFFF'),(.45,'#FFFFFF'),(.75,'#F3F4F5'),(1,'#CFD2D5')]:ceramic.setColorAt(t,QColor(color))
        p.setBrush(ceramic);p.setPen(QPen(edge,.8));p.drawEllipse(QRectF(tx,y,22,22))
        face=QLinearGradient(tx,y+1,tx,y+21)
        for t,color in [(0,'#FFFFFF'),(.45,'#FCFCFD'),(1,'#E7E9EC')]:face.setColorAt(t,QColor(color))
        p.setBrush(face);p.setPen(QPen(QColor(255,255,255,215),.5));p.drawEllipse(QRectF(tx+1.2,y+1.2,19.6,19.6))
        p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor(255,255,255,230),.65,Qt.SolidLine,Qt.RoundCap));p.drawArc(QRectF(tx+1.7,y+1.6,18.5,18.5),35*16,115*16)
        if self.hasFocus() and self.keyboard_focus:
            p.setPen(QPen(QColor(c['muted']),1,Qt.DotLine));p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),5,5)
        p.end()


class FileLink(QPushButton):
    def __init__(self,text,parent=None):
        super().__init__(text,parent);self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor);self.setAccessibleName(text);self.setMinimumHeight(28)
        self.setStyleSheet('border:none;background:transparent;padding:0;text-align:left;')

    def paintEvent(self,event):
        p=QPainter(self);f=QFont('Microsoft YaHei UI');f.setPixelSize(10);p.setFont(f)
        p.setPen(QColor(colors()['ink' if self.isEnabled() else 'muted']))
        p.drawText(QRectF(self.rect()),Qt.AlignVCenter,QFontMetrics(f).elidedText(self.text(),Qt.ElideRight,self.width()))
        p.end()


def caption(text,small=False):
    w=QLabel(text);w.setTextFormat(Qt.PlainText);w.setObjectName('subtle' if small else 'ledgerCaption')
    w.setStyleSheet('background:transparent;border:none;font-family:Microsoft YaHei UI;font-size:'+('11' if small else '12')+'px;color:'+colors()['muted' if small else 'ink']+';')
    return w


class HierarchyRow(QFrame):
    """Keep tree guides in the gutter, away from selection and material controls."""
    def __init__(self,*,branch=False,last=False,through=False,detail=False,grouped=False):
        super().__init__();self.branch=branch;self.last=last;self.through=through;self.detail=detail;self.grouped=grouped

    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setPen(QPen(QColor(colors()['border']),1))
        if self.branch:
            mid=self.height()//2
            p.drawLine(19,0,19,mid if self.last else self.height())
            p.drawLine(19,mid,28,mid)
        elif self.through:p.drawLine(19,0,19,self.height())
        if self.detail:
            x=43 if self.grouped else 19
            p.drawLine(x,0,x,max(0,self.height()-9))
        p.end()


class GroupedLedger(QScrollArea):
    batch_selected=Signal(object)
    def __init__(self,page):
        super().__init__(page);self.page=page;self.open_tasks=set();self.closed_batches=set();self.selected_batch=None
        self.fingerprint=None;self.setWidgetResizable(True);self.setFrameShape(QFrame.NoFrame)
        self.setAccessibleName('按批次与软件展开的材料总表');self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body=QWidget();self.body.setObjectName('groupedLedger');self.stack=QVBoxLayout(self.body);self.stack.setContentsMargins(0,0,0,0);self.stack.setSpacing(0);self.setWidget(self.body)
        self.stack.setSizeConstraint(QLayout.SetMinimumSize)
        self.file_controls={};self.task_buttons={};self.batch_buttons={};self.task_checks={};self.batch_checks={};self.all_check=None
        self.pending_focus=None
        self.highlighted_tasks=set()
        self.scroll_timer=QTimer(self);self.scroll_timer.setSingleShot(True);self.scroll_timer.timeout.connect(self.restore_position)

    def scroll_to_task(self,pid):
        self.pending_focus=pid;self.scroll_timer.start(0)

    def restore_position(self):
        if self.pending_focus in self.task_buttons:
            button=self.task_buttons[self.pending_focus]
            self.verticalScrollBar().setValue(max(0,button.mapTo(self.body,button.rect().topLeft()).y()-40))
        else:self.verticalScrollBar().setValue(getattr(self,'restore_scroll',0))
        self.pending_focus=None

    def select_batch(self,bid):
        self.selected_batch=bid;self.batch_selected.emit(bid)

    def toggle_task(self,pid,bid):
        self.select_batch(bid)
        for i,r in enumerate(self.page.rows):
            if r['id']==pid:self.page.table.setCurrentCell(i,1);break
        if pid in self.open_tasks:self.open_tasks.remove(pid)
        else:self.open_tasks.add(pid)
        self.rebuild(True)

    def toggle_batch(self,bid):
        self.select_batch(bid)
        self.page.show_batch_statistics()
        if bid in self.closed_batches:self.closed_batches.remove(bid)
        else:self.closed_batches.add(bid)
        self.rebuild(True)

    def rebuild(self,force=False):
        rows=self.page.rows
        fingerprint=repr([(r,tuple(m['id'] for m in r['materials'])) for r in rows])+repr(self.page.checked())+repr(colors())
        if not force and fingerprint==self.fingerprint:return
        self.fingerprint=fingerprint;scroll=self.verticalScrollBar().value()
        while self.stack.count():
            item=self.stack.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        self.file_controls={};self.task_buttons={};self.batch_buttons={};self.task_checks={};self.batch_checks={}
        groups=OrderedDict()
        for row in rows:groups.setdefault(row['batch_id'],[]).append(row)
        available=list(dict.fromkeys(r['batch_id'] for r in getattr(self.page,'overview_base_rows',rows)))
        if groups and self.selected_batch not in groups:self.selected_batch=next(iter(groups))
        elif not groups and self.selected_batch not in available:self.selected_batch=next(iter(available),None)
        self.batch_selected.emit(self.selected_batch)
        checked={r['id'] for r in self.page.checked()}
        all_rows=self.page.store.rows()
        header=QWidget();header.setFixedHeight(20);head=QHBoxLayout(header);head.setContentsMargins(7,0,7,0);head.setSpacing(5)
        self.all_check=self.selection_box('全选当前筛选结果（含收起的批次）')
        self.all_check.clicked.connect(lambda _:self.select_rows(self.page.rows))
        head.addWidget(self.all_check)
        head.addWidget(caption('软件名称',True),1)
        for label,width in [('材料',42),('进度',60)]:
            cell=caption(label,True);cell.setAlignment(Qt.AlignCenter);cell.setFixedWidth(width);head.addWidget(cell)
        head.addSpacing(24);self.stack.addWidget(header)
        for bid,group in groups.items():
            originals=[r for r in all_rows if r['batch_id']==bid]
            if len(originals)>1:
                bar=QWidget();bar.setObjectName('ledgerBatchHeading');bar.setFixedHeight(28)
                bar.setStyleSheet('QWidget#ledgerBatchHeading {background:'+colors()['field']+';border-radius:4px;}')
                line=QHBoxLayout(bar);line.setContentsMargins(7,0,7,0);line.setSpacing(5)
                batch_check=self.selection_box('勾选本批次当前筛选的软件')
                batch_check.clicked.connect(lambda _,b=bid:self.select_rows([r for r in self.page.rows if r['batch_id']==b]))
                line.addWidget(batch_check);self.batch_checks[bid]=batch_check
                title=f"批次 · {originals[0]['name']}"
                heading=chrome_button(title,icon='chevron' if bid not in self.closed_batches else 'expand');heading.setFixedHeight(28);heading.setProperty('fontPixelSize',13)
                heading.setProperty('leftAligned',True);heading.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);heading.setMinimumWidth(90)
                heading.setToolTip(group[0]['batch_name']+'\n点击展开或收起；顶部开关只调整当前批次。')
                heading.clicked.connect(lambda _,b=bid:self.toggle_batch(b));line.addWidget(heading,1);self.batch_buttons[bid]=heading
                count=caption(f'{len(originals)} 个软件',True);count.setStyleSheet('font-size:10px;color:'+colors()['muted']+';background:transparent;');line.addWidget(count)
                done=sum(r['production_state'] in ('已提交','已收纳') for r in group)
                state=f"{'已整理' if group[0]['processing_mode']=='collect' else '已提交'} {done}/{len(group)}"
                if len(group)!=len(originals):state+=' · 显示 '+str(len(group))+'/'+str(len(originals))
                label=caption(state,True);label.setStyleSheet('font-size:10px;color:'+colors()['muted']+';background:transparent;');line.addWidget(label)
                self.stack.addWidget(bar)
                if bid in self.closed_batches:continue
            for index,row in enumerate(group):
                grouped=len(originals)>1;last=index==len(group)-1
                self.stack.addWidget(self.software(row,row['id'] in checked,grouped=grouped,last=last))
                if len(originals)==1:self.batch_buttons[bid]=self.task_buttons[row['id']]
                if row['id'] in self.open_tasks:self.stack.addWidget(self.materials(row,grouped=grouped,last=last))
        self.stack.addStretch()
        self.sync_selection()
        self.restore_scroll=scroll;self.scroll_timer.start(0)

    def selection_box(self,label):
        box=chrome_button(icon_only=True,label=label,variant='secondary');box.setFixedSize(16,16)
        box.setProperty('checkbox',True);box.setCheckable(True)
        return box

    def sync_selection(self):
        checked={r['id'] for r in self.page.checked()}
        def show(box,ids):
            count=len(checked.intersection(ids));full=bool(ids) and count==len(ids)
            box.setChecked(full);box.setProperty('mixed',bool(count) and not full)
            box.symbol='check' if full else 'minus' if count else None
            box.variant='primary' if count else 'secondary';box.setEnabled(bool(ids));box.update()
        if self.all_check:show(self.all_check,{r['id'] for r in self.page.rows})
        for pid,box in self.task_checks.items():show(box,{pid})
        for bid,box in self.batch_checks.items():show(box,{r['id'] for r in self.page.rows if r['batch_id']==bid})

    def select_rows(self,rows):
        ids={r['id'] for r in rows};checked={r['id'] for r in self.page.checked()}
        self.page.set_checks(ids,not ids.issubset(checked))

    def software(self,row,checked,*,grouped=False,last=False):
        frame=HierarchyRow(branch=grouped,last=last);frame.setObjectName('ledgerTaskRow');frame.setFixedHeight(28)
        c=colors();frame.setStyleSheet('QFrame#ledgerTaskRow {background:'+ (c['hover'] if row['id'] in self.open_tasks or row['id'] in self.highlighted_tasks else c['surface'])+';border:none;border-bottom:1px solid '+c['border']+';}')
        line=QHBoxLayout(frame);line.setContentsMargins(31 if grouped else 7,0,7,0);line.setSpacing(5)
        select=chrome_button(icon='check' if checked else None,icon_only=True,label='勾选 '+row['name'],variant='primary' if checked else 'secondary');select.setFixedSize(16,16)
        select.setProperty('checkbox',True);select.setCheckable(True);select.setChecked(checked)
        self.task_checks[row['id']]=select
        select.clicked.connect(lambda value,pid=row['id']:self.select_task(pid,value));line.addWidget(select)
        button=chrome_button(row['name'],icon='chevron' if row['id'] in self.open_tasks else 'expand');button.setFixedHeight(26)
        button.setProperty('fontPixelSize',11);button.setProperty('leftAligned',True);button.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred);button.setMinimumWidth(80)
        button.setToolTip(row['name']+f"\nWB-{row['id']:06d}\n"+row['package_name']+'\n批次：'+row['batch_name'])
        if row.get('legacy_status'):button.setToolTip(button.toolTip()+'\n旧版状态：'+row['legacy_status']+'\n原批次：'+row.get('legacy_batch',''))
        button.clicked.connect(lambda _,r=row:self.toggle_task(r['id'],r['batch_id']));self.task_buttons[row['id']]=button;line.addWidget(button,1)
        for value,width in [('原包' if row.get('original_zips') and not row['materials'] else f"{row['collection_count']}/{row['required_count'] or '?'}",42),(f"失败 {row['failed_import_count']}" if row.get('failed_import_count') else row['production_state'],60)]:
            label=caption(value,True);label.setFixedWidth(width);label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet('background:transparent;border:none;font-size:10px;color:'+c['muted']+';')
            label.setToolTip(row['package_name']+f"\n检查 {row['reviewed_count']}/{row['required_count']} · 提交 {row['submitted_count']}/{row['required_count']}")
            if row.get('import_issues'):label.setToolTip(label.toolTip()+'\n'+'\n'.join(i['source_path']+'：'+(i['error'] or '尚未完成收录') for i in row['import_issues']))
            line.addWidget(label)
        more=chrome_button(icon='more',icon_only=True,label='软件操作：'+row['name']);more.setFixedSize(24,26)
        menu=WorkspaceMenu(more);more.setMenu(menu)
        menu.addAction('查看当前批次',lambda checked=False,r=row:self.focus_row(r,True))
        menu.addAction('材料要求',lambda checked=False,r=row:self.focus_row(r,False,self.page.edit_task))
        menu.addAction('重新识别分类…',lambda checked=False,r=row:self.page.reclassify_task(r))
        menu.addAction('修改软件名称',lambda checked=False,r=row:self.focus_row(r,False,self.page.rename_task))
        menu.addSeparator()
        menu.addAction('打开保存目录',lambda checked=False,r=row:self.open_saved_location(r))
        menu.addSeparator()
        menu.addAction('删除任务…',lambda checked=False,r=row:self.page.delete_tasks([r]))
        line.addWidget(more);return frame

    def focus_row(self,row,statistics=False,action=None):
        self.select_batch(row['batch_id'])
        for i,r in enumerate(self.page.rows):
            if r['id']==row['id']:self.page.table.setCurrentCell(i,1);break
        if statistics:self.page.show_batch_statistics()
        if action:action()

    def select_task(self,pid,checked):
        for i,row in enumerate(self.page.rows):
            if row['id']==pid:
                self.select_batch(row['batch_id']);self.page.table.setCurrentCell(i,1)
                self.page.table.item(i,0).setCheckState(Qt.Checked if checked else Qt.Unchecked);break
        self.sync_selection()

    def materials(self,row,*,grouped=False,last=False):
        wrap=HierarchyRow(through=grouped and not last,detail=True,grouped=grouped);layout=QVBoxLayout(wrap);layout.setContentsMargins(54 if grouped else 30,1,6,9);layout.setSpacing(1);layout.setSizeConstraint(QLayout.SetMinimumSize)
        review=row['processing_mode']=='review';heading=QHBoxLayout();heading.addWidget(caption('材料名称',True),1)
        for name in (['收录','检查','提交'] if review else ['收录情况']):
            label=caption(name,True);label.setAlignment(Qt.AlignCenter);label.setFixedWidth(40 if review else 67);heading.addWidget(label)
        heading.addSpacing(24);layout.addLayout(heading)
        previous=None
        materials=row['materials']
        if len(materials)>6:
            order=['文档材料','程序与运行包','数据库与脚本','信息资料','视频']
            materials=sorted(materials,key=lambda m:order.index(group_for(m['kind'])))
        for m in materials:
            category=group_for(m['kind'])
            if len(row['materials'])>6 and category!=previous:
                layout.addWidget(caption(category,True));previous=category
            row_widget=QWidget();row_widget.setFixedHeight(30)
            line=QHBoxLayout(row_widget);line.setContentsMargins(0,0,0,0);line.setSpacing(5)
            suffix=Path(m['kind']).suffix.lower().lstrip('.')
            badge=caption({'docx':'W','doc':'W','mp4':'视频','wmv':'视频'}.get(suffix,suffix.upper()),True)
            badge.setFixedSize(28,18);badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet('font-size:9px; border-radius:3px; background:'+colors()['hover']+'; color:'+colors()['muted']+';')
            line.addWidget(badge)
            link=FileLink(m['kind']);link.setEnabled(bool(m['id']));link.setToolTip((m.get('filename') or '尚未收录')+f"\n版本 v{m.get('version_number',0)}")
            link.clicked.connect(lambda _,m=m:self.open_file(m));line.addWidget(link,1)
            issue=m.get('import_issue')
            state='副本异常' if m.get('collection_issue') else ('更新失败' if m.get('collection_saved') else '收录失败') if issue and issue['state']=='failed' else '待重试' if issue else '待更新' if review and m.get('dependency_invalid') else '已收录' if m.get('collection_saved') else '未保存' if m['id'] else '缺失'
            receipt=caption(state,True);receipt.setAlignment(Qt.AlignCenter);receipt.setFixedWidth(40 if review else 67);line.addWidget(receipt)
            if m.get('collection_issue'):receipt.setToolTip(m['collection_issue'])
            elif issue:receipt.setToolTip(issue['error']+'\n来源：'+issue['source_path'])
            if review:
                for field,passed in [('review',m.get('passed')==1),('submit',m['state']=='已提交')]:
                    box=chrome_button(icon='check' if passed else None,icon_only=True,variant='primary' if passed else 'secondary',label=('检查通过 ' if field=='review' else '确认已提交 ')+m['kind'])
                    box.setProperty('checkbox',True);box.setFixedSize(17,17);box.setCheckable(True);box.setChecked(passed)
                    allowed=bool(m['id']) and not m.get('collection_issue') and not m.get('dependency_invalid') and (not m.get('submission_id') if field=='review' else m.get('passed')==1)
                    box.setEnabled(allowed);box.clicked.connect(lambda checked,r=row,m=m,f=field:self.action(r,m,f,checked))
                    holder=QWidget();cell=QHBoxLayout(holder);cell.setContentsMargins(0,0,0,0);cell.addStretch();cell.addWidget(box);cell.addStretch();holder.setFixedWidth(40);line.addWidget(holder)
                    self.file_controls[(row['id'],m['kind'],field)]=box
            more=chrome_button(icon='more',icon_only=True,label='材料详情与版本：'+m['kind']);more.setFixedSize(26,28)
            menu=WorkspaceMenu(more);more.setMenu(menu)
            menu.addAction('打开保存文件位置',lambda checked=False,r=row,m=m:self.open_saved_location(r,m))
            menu.addAction('材料详情与版本',lambda checked=False,r=row,m=m:self.details(r,m))
            if issue:menu.addAction('重试这份材料',lambda checked=False,i=dict(issue):self.page.request('重试指定材料',lambda **kw:self.page.store.retry_import_files(i['token'],project_id=i['project_id'],source_path=i['source_path'],**kw)))
            line.addWidget(more)
            layout.addWidget(row_widget)
        for archive in row.get('original_zips',[]):
            info=caption('ZIP 原包已收录：'+Path(archive['path']).name,True);info.setWordWrap(True);layout.addWidget(info)
            open_zip=chrome_button('打开原包保存目录');open_zip.clicked.connect(lambda _,a=archive:self.open_original_zip(a));layout.addWidget(open_zip)
        if not row['materials'] and not row.get('original_zips'):layout.addWidget(caption('材料要求尚未确定，可从总表操作中设置本项要求。',True))
        return wrap

    def open_original_zip(self,archive):
        from saved_locations import open_location
        try:open_location(archive['path'])
        except (OSError,ValueError) as error:self.page.error.setText(str(error))

    def details(self,row,material):
        from production_ui import MaterialDialog
        dialog=MaterialDialog(self.page,row,material)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def open_saved_location(self,row,material=None):
        from saved_locations import collection_location,open_location
        try:
            target=collection_location(self.page.service,row['id'],material['kind'] if material else None)
            open_location(target);self.page.error.setText('')
        except (ValueError,OSError) as error:self.page.error.setText(str(error))

    def open_file(self,material):
        path=material.get('path')
        if path and Path(path).is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:self.page.error.setText('文件暂不可用，请查看材料详情或重新收录。')

    def action(self,row,m,field,checked):
        try:
            if field=='review':
                note=''
                if not checked:
                    note,ok=QInputDialog.getText(self,'需要修改','请填写修改原因：')
                    if not ok:return
                    if not note.strip():raise ValueError('修改原因不能为空。')
                self.page.request('保存材料检查',lambda **kw:self.page.store.review(m['id'],checked,note,**kw))
            elif checked:
                if QMessageBox.question(self,'确认材料已提交','确认你已在外部提交此材料的当前版本？',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes:
                    self.page.request('记录材料提交',lambda **kw:self.page.store.submit([m['id']],**kw))
            else:
                note,ok=QInputDialog.getText(self,'撤销提交记录','请填写撤销原因，文件和历史保留：')
                if not ok:return
                if not note.strip():raise ValueError('撤销原因不能为空。')
                self.page.request('撤销提交记录',lambda **kw:self.page.store.void_submission(m['submission_id'],note,version_ids=[m['id']],**kw))
        except (ValueError,OSError) as error:self.page.error.setText(str(error))
        finally:self.rebuild(True)
