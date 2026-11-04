"""Mode-aware software counts. Current states and dated activity are separate."""
from PySide6.QtCore import Qt,Signal,QSize
from PySide6.QtWidgets import QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QBoxLayout,QSizePolicy
from workspace_chrome import colors

METRICS={
    'collect':[('total','软件总数','当前范围的软件'),('collecting','待补材料','包括缺失或要求未确定'),('organized','已整理完成','材料收齐即完成整理')],
    'review':[('collecting','待收齐','包括缺失、待更新和要求未确定'),('checking','待检查','收齐后待人工核对'),('submitting','待提交','检查后待登记提交'),('submitted','已提交','已登记全部当前材料提交')],
}
TITLES={'collect':'统计整理已完成材料','review':'统计初始未提交材料'}


def state_key(row):
    if row['collection_state']!='已收齐':return 'collecting'
    if row['processing_mode']=='collect':return 'organized'
    if row['required_count'] and row['submitted_count']==row['required_count']:return 'submitted'
    if row['reviewed_count']<row['required_count']:return 'checking'
    return 'submitting'


def matches_metric(row,mode,metric):
    return row['processing_mode']==mode and (metric=='total' or state_key(row)==metric)


def summaries(rows,default_mode='review'):
    modes=[mode for mode in ('collect','review') if any(r['processing_mode']==mode for r in rows)] or [default_mode if default_mode in METRICS else 'review']
    return {mode:{metric:sum(matches_metric(r,mode,metric) for r in rows) for metric,_,_ in METRICS[mode]} for mode in modes}


def activity(rows,day,monthly,local_date):
    period=day[:7] if monthly else day
    def count(group,field):return sum(bool(r.get(field)) and local_date(r[field])[:len(period)]==period for r in group)
    result={}
    for mode in ('collect','review'):
        group=[r for r in rows if r['processing_mode']==mode]
        if not group:continue
        fields={'added':'created_at','collected':'collected_at'}
        if mode=='review':fields.update(checked='checked_at',submitted='completed_submission_at')
        result[mode]={name:count(group,field) for name,field in fields.items()}
    return result


class MetricButton(QPushButton):
    def __init__(self,name,hint):
        super().__init__();self.setCursor(Qt.PointingHandCursor);self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);self.setMinimumWidth(62)
        self.setToolTip(hint+'；点击数字查看对应软件，再次点击取消筛选。')
        self.card_height=58
        layout=QBoxLayout(QBoxLayout.LeftToRight,self);layout.setContentsMargins(5,0,3,0);layout.setSpacing(4)
        self.number=QLabel('0');self.label=QLabel(name);self.description=hint
        for label in (self.number,self.label):label.setAttribute(Qt.WA_TransparentForMouseEvents);layout.addWidget(label)
        layout.addStretch()

    def sizeHint(self):return QSize(max(60,self.layout().minimumSize().width()),self.card_height)

    def minimumSizeHint(self):return self.sizeHint()

    def update_value(self,value,selected,compact,stacked=False):
        c=colors();self.number.setText(str(value));self.setChecked(selected);self.layout().setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight);self.layout().setSpacing(0 if stacked else 7)
        self.card_height=58 if stacked else 23 if compact else 30;self.setFixedHeight(self.card_height);self.updateGeometry()
        self.setAccessibleName(self.label.text()+' '+str(value)+' 个软件')
        self.setStyleSheet('MetricButton {padding:0;min-width:0;min-height:0;background:transparent;border:1px solid transparent;border-radius:7px;} MetricButton:hover,MetricButton:checked {background:'+c['hover']+';border:1px solid '+c['border']+';} MetricButton:focus {border:1px solid '+c['muted']+';}')
        self.number.setStyleSheet('background:transparent;border:none;color:'+c['ink']+';font-family:"Segoe UI";font-size:18px;font-weight:600;')
        self.label.setStyleSheet('background:transparent;border:none;color:'+c['muted']+';font-family:"Microsoft YaHei UI";font-size:11px;')


class StatisticsOverview(QWidget):
    metric_clicked=Signal(str,str)
    def __init__(self,parent=None):
        super().__init__(parent);self.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Fixed);self.panels={};self.buttons={};self.counts={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        for mode in ('collect','review'):
            panel=QWidget();body=QBoxLayout(QBoxLayout.TopToBottom,panel);body.setContentsMargins(0,0,0,0);body.setSpacing(4)
            title=QLabel(TITLES[mode]+' · 当前状态');body.addWidget(title)
            frame=QFrame();row=QHBoxLayout(frame);row.setContentsMargins(3,2,3,2);row.setSpacing(0)
            for metric,name,hint in METRICS[mode]:
                button=MetricButton(name,hint);button.clicked.connect(lambda checked=False,m=mode,k=metric:self.metric_clicked.emit(m,k));row.addWidget(button,1);self.buttons[(mode,metric)]=button
            body.addWidget(frame);layout.addWidget(panel);self.panels[mode]=(panel,title,frame)

    def update_rows(self,rows,default_mode='review',selected=None):
        c=colors();self.counts=summaries(rows,default_mode);mixed=len(self.counts)>1
        for mode,(panel,title,frame) in self.panels.items():
            panel.setVisible(mode in self.counts)
            panel.layout().setDirection(QBoxLayout.LeftToRight if mixed else QBoxLayout.TopToBottom)
            title.setMinimumWidth(54 if mixed else 0);title.setMaximumWidth(54 if mixed else 16777215)
            title.setText(('整理' if mode=='collect' else '检查提交') if mixed else TITLES[mode]+' · 当前状态')
            title.setToolTip(TITLES[mode]+'；当前范围内的软件数量，不是材料份数。')
            title.setStyleSheet('color:'+c['muted']+';background:transparent;font-size:11px;')
            frame.setStyleSheet('QFrame {background:'+c['field']+';border:none;border-radius:9px;}')
            stacked=max(self.counts.get(mode,{}).values(),default=0)>=100
            for metric,_,_ in METRICS[mode]:self.buttons[(mode,metric)].update_value(self.counts.get(mode,{}).get(metric,0),selected==(mode,metric),mixed,stacked)
            frame_height=(58 if stacked else 23 if mixed else 30)+2
            frame.setFixedHeight(frame_height)
            title.ensurePolished()
            panel.setFixedHeight(frame_height if mixed else title.sizeHint().height()+4+frame_height)
            panel.layout().invalidate();frame.layout().invalidate();panel.updateGeometry()
        visible=[self.panels[mode][0] for mode in self.counts]
        self.setFixedHeight(sum(panel.height() for panel in visible)+4*(len(visible)-1))
        self.layout().invalidate();self.updateGeometry()
