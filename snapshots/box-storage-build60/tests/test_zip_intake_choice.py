from pathlib import Path
from zipfile import ZipFile
import pytest
from PySide6.QtCore import QTimer,QEventLoop,Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from desktop_controller import FloatingController
from tests.test_automatic_material_intake import make_files


def package(tmp_path):
    files=make_files(tmp_path/'input')
    z=tmp_path/'自动归档管理系统.zip'
    with ZipFile(z,'w') as out:
        for f in files:out.write(f,'自动归档管理系统/'+f.name)
    return z


def test_ask_does_not_open_extract_or_create_records(tmp_path,monkeypatch):
    s=DesktopService(tmp_path/'data/workbox.db')
    z=tmp_path/'损坏.zip';z.write_bytes(b'broken')
    assert s.receive_interactive([str(z)],zip_mode='ask')=={'zip_intake_choice':[str(z)]}
    assert not s.list_projects() and not (s.db.path.parent/'材料收件缓存').exists()


def test_original_keeps_complete_package_without_material_extraction(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    z=package(tmp_path);before=z.read_bytes()
    result=s.receive_interactive([str(z)],zip_mode='original')
    assert Path(result['working_zip_path']).read_bytes()==before==z.read_bytes()
    assert not (s.db.path.parent/'材料收件缓存').exists()
    assert len(s.production.rows())==1 and s.production.rows()[0]['production_state']=='原包待核对'


def test_explicit_extract_does_not_silently_store_unrecognized_archive(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');z=tmp_path/'源码.zip'
    with ZipFile(z,'w') as out:out.writestr('main.py',b'isolated code')
    with pytest.raises(ValueError,match='直接收录原包'):
        s.receive_interactive([str(z)],zip_mode='extract')
    assert not s.list_projects() and z.exists()


def test_multiple_original_packages_continue_after_a_bad_archive(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    bad=tmp_path/'bad.zip';bad.write_bytes(b'broken');good=package(tmp_path)
    results=s.receive_interactive([str(bad),str(good)],zip_mode='original')['zip_original_results']
    assert results[0]['zip_original_error'] and Path(results[1]['working_zip_path']).is_file()
    assert not (s.db.path.parent/'材料收件缓存').exists()


def test_multiple_archives_keep_separate_batch_identity(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');z=package(tmp_path)
    other=tmp_path/'另一份.zip'
    with ZipFile(other,'w') as out:
        for f in make_files(tmp_path/'other','另一材料管理系统'):out.write(f,'另一材料管理系统/'+f.name)
    plan=s.receive_interactive([str(z),str(other)],zip_mode='extract')['material_preview']
    assert plan['split_batches'] and len(plan['batch_labels'])==2
    assert len({e['batch_key'] for e in plan['entries']})==2


@pytest.mark.parametrize('choice',['自动解压并分类','直接收录原包','取消'])
def test_controller_zip_choice_before_any_write(tmp_path,choice):
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    s.set_setting('default_processing_mode','collect');z=package(tmp_path);before=z.read_bytes()
    c=FloatingController(s,tray_enabled=False);assert c.start();c.monitor_timer.stop()
    loop=QEventLoop();poll=QTimer();seen=[];errors=[]
    def tick():
        try:
            d=QApplication.activeModalWidget()
            if isinstance(d,QMessageBox) and d.windowTitle()=='ZIP 收录方式' and not seen:
                assert not s.list_projects() and not (s.db.path.parent/'材料收件缓存').exists()
                seen.append(True)
                QTest.mouseClick(next(b for b in d.buttons() if b.text()==choice),Qt.LeftButton)
            else:
                from intake_link_ui import RecognitionSummaryDialog
                if isinstance(d,RecognitionSummaryDialog):d.confirm_button.click()
                elif seen and not c.busy:loop.quit()
        except Exception as e:errors.append(e);loop.quit()
    poll.timeout.connect(tick);poll.start(10)
    timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timeout.start(20000)
    try:
        c.receive({'kind':'files','paths':[str(z)]});loop.exec()
        assert not errors and seen and not c.busy
        assert z.read_bytes()==before
        rows=s.production.rows()
        if choice=='自动解压并分类':
            assert len(rows)==1 and rows[0]['package_name']=='五件材料' and rows[0]['collection_count']==5
        else:
            assert len(rows)==(0 if choice=='取消' else 1) and not (s.db.path.parent/'材料收件缓存').exists()
            if rows:assert rows[0]['production_state']=='原包待核对' and rows[0]['collection_count']==0
            assert len(s.list_projects())==(0 if choice=='取消' else 1)
    finally:
        poll.stop();timeout.stop()
        d=QApplication.activeModalWidget()
        if d:d.reject()
        c.shutdown();c.dispose_ui()
