from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from desktop_service import DesktopService
from desktop_controller import FloatingController
from duplicate_intake_ui import DuplicateMaterialDialog
from requirement_intake import proposal


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True);return s


def prepare(s,root,raw=False,ambiguous=False):
    paths=[];ids=[];original=[]
    for i in range(3):
        name=f'全部覆盖{i}系统'
        p=root/(name+('.zip' if raw else '.docx'))
        if raw:
            with ZipFile(p,'w') as z:z.writestr('readme.txt',b'A')
            first=s.receive_interactive([str(p)],zip_mode='original');pid=first['project_ids'][0];original.append(Path(first['working_zip_path']))
            with ZipFile(p,'w') as z:z.writestr('readme.txt',b'B')
        else:
            first=s.production.import_plan([proposal(name,batch='准备'+str(i),requirement='单手册 Word')],token='seed'+str(i));pid=first['project_ids'][0]
            p.write_bytes(b'A');s.production.receive(pid,'软件说明书.docx',p)
            row=next(r for r in s.production.rows() if r['id']==pid);original.append(Path(row['materials'][0]['path']));p.write_bytes(b'B')
        paths.append(str(p));ids.append(pid)
    if ambiguous:s.production.import_plan([proposal('全部覆盖1系统',batch='另一个同名任务',requirement='单手册 Word')],token='ambiguous')
    if raw:pending=s.receive_interactive(paths,zip_mode='original')
    else:
        plan=s.receive_interactive(paths,linked=True)['material_preview'];pending=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
    assert len(pending['material_conflicts'])==3
    return pending,paths,ids,original


@pytest.mark.parametrize('raw',[False,True])
@pytest.mark.parametrize('ambiguous,cancel',[(False,False),(True,False),(True,True)])
def test_all_overwrite_uses_one_dialog_and_still_resolves_ambiguity(service,tmp_path,app,raw,ambiguous,cancel):
    pending,paths,ids,original=prepare(service,tmp_path,raw,ambiguous);c=FloatingController(service,tray_enabled=False);assert c.start();c.monitor_timer.stop();queued=[];seen=[];errors=[]
    def answer():
        d=QApplication.activeModalWidget()
        if not isinstance(d,DuplicateMaterialDialog):return
        try:
            seen.append(d.group['name']);assert d.overwrite_all_button.isVisible()
            if len(seen)==1:d.overwrite_all_button.click()
            else:
                assert len(d.group['candidates'])==2
                if cancel:d.cancel_button.click()
                else:d.tasks.setCurrentIndex(d.tasks.findData(ids[1]));d.overwrite_button.click()
        except Exception as error:errors.append(error);d.reject()
    timer=QTimer();timer.timeout.connect(answer);timer.start(15)
    try:
        with patch.object(c,'enqueue',side_effect=lambda title,fn,**kw:queued.append(fn)):
            c._resolve_material_conflicts(pending)
        assert not errors and len(seen)==(2 if ambiguous else 1)
        assert len(queued)==(0 if cancel else 1)
        if queued:
            result=queued[0]();assert result.get('copied_count',result.get('copied'))==3
            assert len(service.production.rows())==(4 if ambiguous else 3)
            for pid,p in zip(ids,paths):
                row=next(r for r in service.production.rows() if r['id']==pid)
                target=Path(row['original_zips'][0]['path'] if raw else row['materials'][0]['path'])
                assert target.read_bytes()==Path(p).read_bytes()
        assert all(p.is_file() for p in original)
        if cancel:
            for row in service.production.rows():
                if row['id'] in ids:
                    if raw:assert len(row['original_zips'])==1
                    else:assert Path(row['materials'][0]['path']).read_bytes()==b'A'
    finally:timer.stop();c.shutdown();c.dispose_ui()


def test_button_is_hidden_for_single_file_and_never_default(service,tmp_path,app):
    pending,paths,ids,original=prepare(service,tmp_path)
    d=DuplicateMaterialDialog(pending['material_conflicts'][0]);d.show();app.processEvents()
    assert not d.overwrite_all_button.isVisible() and not d.overwrite_all_button.isDefault()
    d.close()
