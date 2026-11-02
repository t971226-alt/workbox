"""Exercise the distributed batch dialog and production resolver with isolated data."""
from pathlib import Path


def verify(root):
    from PySide6.QtWidgets import QApplication,QWidget
    from PySide6.QtCore import QTimer
    from desktop_service import DesktopService
    from desktop_controller import FloatingController
    from duplicate_intake_ui import DuplicateMaterialDialog
    from requirement_intake import proposal
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True);paths=[]
    for i in range(3):
        name=f'批量覆盖验证{i}系统';r=s.production.import_plan([proposal(name,batch='批量覆盖验证'+str(i),requirement='单手册 Word')],token='seed'+str(i))
        p=root/(name+'.docx');p.write_bytes(b'old');s.production.receive(r['project_ids'][0],'软件说明书.docx',p);p.write_bytes(b'new');paths.append(str(p))
    plan=s.receive_interactive(paths,linked=True)['material_preview'];pending=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
    class Host:
        def __init__(self):self.service=s;self.panel=QWidget();self.quitting=False;self.closed=False;self.queued=[]
        def _sync_panel_busy(self):pass
        def show_panel(self):self.panel.show()
        def notify(self,*args):pass
        def enqueue(self,title,fn):self.queued.append(fn)
    host=Host();seen=[];errors=[]
    def answer():
        d=QApplication.activeModalWidget()
        if isinstance(d,DuplicateMaterialDialog):
            try:
                assert d.overwrite_all_button.isVisible();seen.append(d.group['name']);d.overwrite_all_button.click()
            except Exception as error:errors.append(str(error));d.reject()
    timer=QTimer();timer.timeout.connect(answer);timer.start(10)
    try:
        FloatingController._resolve_material_conflicts(host,pending)
        assert not errors and len(seen)==1 and len(host.queued)==1
        result=host.queued[0]();assert result['copied_count']==3 and len(s.production.rows())==3
        assert all(Path(r['materials'][0]['path']).read_bytes()==b'new' for r in s.production.rows())
    finally:timer.stop();host.panel.close()
    return dict(batch_overwrite_one_dialog_updates_all_tasks=True)
