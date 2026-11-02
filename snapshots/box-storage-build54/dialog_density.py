"""Consistent compact dialogs; preserve controls, selections and business actions."""
from PySide6.QtCore import QObject,QEvent,QTimer,Qt
from PySide6.QtWidgets import QDialog,QWidget,QLayout,QTableWidget,QAbstractItemView,QPlainTextEdit,QListWidget
import shiboken6

# Widths are logical pixels and scale normally with Windows display settings.
SHEETS={'StorageManagementDialog':620,'ClassificationDialog':740,'DirectMaterialDialog':800,
    'IntakeReceiptDialog':800,'IntakeVersionsDialog':740,'VersionHistoryDialog':720,
    'TaskMaterialsDialog':560,'OriginalZipDetailsDialog':560,'ReclassificationDialog':550,'MaterialDialog':560,
    'FileIntakeLogDialog':720,'DuplicateMaterialDialog':670,'RequirementsDialog':550,
    'NewProductionDialog':550,'ClassificationRuleDialog':470,'RecognitionSummaryDialog':470,
    'QuickClassificationDialog':450,'ZipIntakeDialog':680,'MaterialIntakeDialog':680,'DirectorySettingsDialog':500,'ProjectDetailsDialog':520,
    'IntakeDialog':880,'LegacyImportDialog':690,'LocationDialog':600,'ProjectDeliveryDialog':500}


class Density(QObject):
    def __init__(self,dialog):
        super().__init__(dialog);self.dialog=dialog;self.done=False;self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.apply)
    def queue(self,*_):self.timer.start(0)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Show and not self.done and obj.isWindow():
            self.done=True;self.queue()
        if (event.type()==QEvent.LayoutRequest and self.done and obj is self.dialog
                and type(obj).__name__=='ClassificationDialog'
                and (obj.height()>570 or obj.minimumSizeHint().height()>570)):
            self.queue()
        return False
    def apply(self):
        d=self.dialog
        if not shiboken6.isValid(d):return
        # Do not resize the separate ledger or embedded ZIP confirmation form.
        for layout in d.findChildren(QLayout):
            if layout.spacing()>6:layout.setSpacing(6)
            m=layout.contentsMargins();layout.setContentsMargins(*(min(v,10) for v in (m.left(),m.top(),m.right(),m.bottom())))
        for button in d.findChildren(QWidget):
            if hasattr(button,'drawing_font'):
                button.setProperty('fontPixelSize',11 if type(d).__name__ in ('TaskMaterialsDialog','OriginalZipDetailsDialog','MaterialDialog') else 12)
                if button.minimumHeight()>29 and button.maximumHeight()>29:button.setMinimumHeight(28)
                button.updateGeometry()
        name=type(d).__name__
        if name not in SHEETS:return
        if name=='ClassificationDialog':
            d.profiles.setMinimumHeight(70);d.profiles.setMaximumHeight(max(80,min(220,d.profiles.count()*38+8)))
            d.findChild(QWidget,'classificationForm').layout().setAlignment(Qt.AlignTop)
        for table in d.findChildren(QTableWidget):
            if table.window()!=d or not table.isVisibleTo(d):continue
            if not getattr(table,'_density_connected',False):
                table._density_connected=True;table.model().rowsInserted.connect(self.queue);table.model().rowsRemoved.connect(self.queue)
            rows=table.rowCount();visible=min(max(rows,1),6)
            header=table.horizontalHeader().sizeHint().height()
            row_height=30 if name in ('TaskMaterialsDialog','OriginalZipDetailsDialog','MaterialDialog') else max(29,min(38,table.verticalHeader().defaultSectionSize()))
            if any(table.cellWidget(r,c) for r in range(min(rows,6)) for c in range(table.columnCount())):row_height=30 if name in ('TaskMaterialsDialog','OriginalZipDetailsDialog','MaterialDialog') else 38
            for r in range(rows):
                if table.rowHeight(r)<row_height:table.setRowHeight(r,row_height)
            height=header+sum(table.rowHeight(r) for r in range(min(rows,visible)))+12
            if not rows:height=header+32
            if name=='ClassificationDialog':
                # Wrapped guidance finishes laying out on a later Qt event.
                # Reserve its measured space before fixing the table height.
                overhead=max(0,d.minimumSizeHint().height()-table.minimumHeight())
                height=min(height,max(header+42,min(570,d.screen().availableGeometry().height()-60)-overhead-4))
            table.setMinimumHeight(height);table.setMaximumHeight(height)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for edit in d.findChildren(QPlainTextEdit):
            if edit.window()!=d or not edit.isVisibleTo(d):continue
            if name in ('DuplicateMaterialDialog','RequirementsDialog','ReclassificationDialog','FileIntakeLogDialog','NewProductionDialog','IntakeDialog','ProjectDetailsDialog'):
                lines=max(3,min(6,edit.toPlainText().count('\n')+1))
                edit.setMinimumHeight(68);edit.setMaximumHeight(24+lines*18)
        area=d.screen().availableGeometry();width=min(getattr(d,'_intake_width',SHEETS[name]),area.width()-32)
        d.setMinimumSize(min(width,440),0)
        d.resize(width,d.height())
        if d.layout():d.layout().activate()
        # Layout sizeHint now reflects the actual rows instead of a default tall table.
        wanted=d.layout().totalHeightForWidth(width) if d.layout() and d.layout().hasHeightForWidth() else d.sizeHint().height()
        if name=='ClassificationDialog':
            limit=min(570,area.height()-60)
            if wanted>limit:
                table=d.materials
                height=max(table.horizontalHeader().sizeHint().height()+42,table.height()-(wanted-limit)-4)
                table.setMinimumHeight(height);table.setMaximumHeight(height)
                if d.layout():d.layout().activate()
                wanted=d.sizeHint().height()
        height=min(max(120,wanted),area.height()-60)
        d.resize(width,height)
        frame=d.frameGeometry()
        if frame.right()>area.right() or frame.bottom()>area.bottom():
            d.move(max(area.left(),min(d.x(),area.right()-frame.width())),max(area.top(),min(d.y(),area.bottom()-frame.height())))


def install(dialog):
    if isinstance(dialog,QDialog) and not hasattr(dialog,'_density_filter'):
        dialog.setProperty('compactPopup',True);dialog._density_filter=Density(dialog);dialog.installEventFilter(dialog._density_filter)
