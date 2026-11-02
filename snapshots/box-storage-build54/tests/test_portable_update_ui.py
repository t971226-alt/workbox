from pathlib import Path
import json
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QDialog
import portable_update as update
import portable_update_ui as interface
from portable_release import RELEASE_KEY
from test_portable_update import pair,fixture_release


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


def test_same_version_can_open_original_without_creating_download_data(pair,app,monkeypatch):
    source,target,index=pair;fixture_release(target,RELEASE_KEY,'same release');update.register(target,index);calls=[]
    monkeypatch.setattr(interface,'launch',lambda *args,**kw:calls.append((args,kw)))
    monkeypatch.setattr(interface,'choose',lambda *a:'open')
    assert not interface.preflight(source,index=index,roots=[])
    assert calls[0][0]==(target,) and not(source/'data').exists()


def test_newer_download_confirm_builds_update_job_in_original_location(pair,app,monkeypatch):
    source,target,index=pair;update.register(target,index);spawned=[]
    before=(target/'data/workbox.db').read_bytes()
    monkeypatch.setattr(interface,'choose',lambda *a:'update');monkeypatch.setattr(interface,'spawn_helper',lambda *a,**kw:spawned.append(a))
    assert not interface.preflight(source,index=index,roots=[])
    path,token=spawned[0];plan=json.loads(path.read_text(encoding='utf-8'))
    assert plan['target']==str(target) and plan['token']==token
    assert (target/'data/workbox.db').read_bytes()==before and not (source/'data').exists()


def test_cancel_update_does_not_create_job_or_blank_ledger(pair,app,monkeypatch):
    source,target,index=pair;update.register(target,index)
    monkeypatch.setattr(interface,'choose',lambda *a:None)
    monkeypatch.setattr(interface,'spawn_helper',lambda *a,**kw:pytest.fail('取消后不能开始更新'))
    assert not interface.preflight(source,index=index,roots=[]) and not(source/'data').exists()
    assert not update.read_index(index)['pending']


def test_older_download_only_offers_opening_newer_box(pair,app,monkeypatch):
    source,target,index=pair;newer=RELEASE_KEY[:3]+(RELEASE_KEY[3]+1,);fixture_release(target,newer,'newer');update.register(target,index)
    calls=[];options=[]
    def choose(title,body,choices):options.extend(choices);return 'open'
    monkeypatch.setattr(interface,'choose',choose);monkeypatch.setattr(interface,'launch',lambda *a,**kw:calls.append(a))
    assert not interface.preflight(source,index=index,roots=[])
    assert [v for v,label in options]==['open','independent'] and calls==[(target,)] and not (source/'data').exists()


def test_extra_database_is_offered_for_import_instead_of_silently_merged(pair,app,monkeypatch):
    source,target,index=pair;fixture_release(target,RELEASE_KEY,'same');update.register(target,index)
    (source/'data').mkdir();extra=source/'data/workbox.db';extra.write_bytes(b'keep separate database');calls=[]
    monkeypatch.setattr(interface,'launch',lambda *a,**kw:calls.append(kw))
    assert not interface.preflight(source,index=index,roots=[])
    assert extra in calls[0]['sources'] and extra.read_bytes()==b'keep separate database'


def test_data_marker_stops_current_program_before_starting_its_service(pair,app):
    source,target,index=pair;update.register(source,index)
    update.write_json(source/'data/BOX-Data-Version.json',dict(version='9.0.0',build=1))
    with pytest.raises(ValueError,match='较新'):interface.preflight(source,index=index,roots=[])
    assert not (source/'data/workbox.db').exists()


def test_first_use_explicit_choice_does_not_initialize_database(pair,app,monkeypatch):
    source,target,index=pair;monkeypatch.setattr(interface,'choose',lambda *a:'new')
    assert interface.preflight(source,index=index,roots=[]) and not(source/'data').exists()


def test_prompt_buttons_and_multiple_location_selection_are_operable(pair,app):
    source,target,index=pair
    d=interface.ChoiceDialog('发现已有 BOX，是否更新？','先备份，再更新原位置。',[('update','备份、更新并打开原 BOX'),('open','打开原 BOX')]);d.show();app.processEvents()
    assert d.buttons['update'].isDefault();d.buttons['update'].click();assert d.choice=='update'
    d.close();d.deleteLater()
    locations=interface.LocationDialog([update.read_release(target),update.read_release(source)]);locations.show();app.processEvents()
    locations.list.setCurrentRow(1);locations.confirm();assert locations.selected.root==source
    locations.close();locations.deleteLater()


def test_ipc_update_waits_for_foreground_work_and_only_requests_graceful_exit(app,tmp_path,monkeypatch):
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    c=FloatingController(DesktopService(tmp_path/'data/workbox.db'),tray_enabled=False);assert c.start();c.monitor_timer.stop();calls=[]
    monkeypatch.setattr(c,'request_quit',lambda:calls.append('quit'))
    monkeypatch.setattr(c,'_valid_update_request',lambda message:True)
    class Socket:
        _box_handled=False;_box_message=b''
        def __init__(self):self.reply=b''
        def readAll(self):return b'{"command":"prepare-update"}\n'
        def write(self,value):self.reply+=value
        def flush(self):pass
    try:
        c.choosing_intake=True;busy=Socket();c._ipc_message(busy);app.processEvents();assert busy.reply==b'busy' and not calls
        c.choosing_intake=False;idle=Socket();c._ipc_message(idle);app.processEvents();assert idle.reply==b'ready' and calls==['quit']
    finally:c.choosing_intake=False;c.shutdown();c.dispose_ui()


def test_existing_instance_receives_additional_database_hint(app,tmp_path,monkeypatch):
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    c=FloatingController(DesktopService(tmp_path/'data/workbox.db'),tray_enabled=False);assert c.start();c.monitor_timer.stop();calls=[]
    monkeypatch.setattr(c,'offer_legacy_import',lambda path:calls.append(path));monkeypatch.setattr(c,'show_panel',lambda:None)
    class Socket:
        _box_handled=False;_box_message=b''
        def readAll(self):return json.dumps(dict(command='activate',legacy_sources=['D:/other/data/workbox.db'])).encode()+b'\n'
        def write(self,data):assert data==b'activated\n'
        def flush(self):pass
    try:c._ipc_message(Socket());assert calls==['D:/other/data/workbox.db']
    finally:c.shutdown();c.dispose_ui()


def test_two_data_directories_allow_explicit_daily_location_and_offer_other_for_import(pair,app):
    source,target,index=pair;(source/'data').mkdir();original=source/'data/workbox.db';original.write_bytes(b'source data stays')
    update.write_json(index,dict(schema=1,preferred='',locations=[str(target)],pending=''))
    timer=QTimer();timer.setInterval(10)
    def select():
        dialog=QApplication.activeModalWidget()
        if isinstance(dialog,interface.LocationDialog):dialog.list.setCurrentRow(0);dialog.confirm()
    timer.timeout.connect(select);timer.start();sources=[]
    try:assert interface.preflight(source,index=index,roots=[],import_sources=sources)
    finally:timer.stop()
    assert sources==[target/'data/workbox.db'] and original.read_bytes()==b'source data stays'


def test_ipc_exit_requires_a_verified_update_request(pair):
    from types import SimpleNamespace
    from desktop_controller import FloatingController
    source,target,index=pair;path,token=update.make_job(source,target,index=index)
    controller=SimpleNamespace(service=SimpleNamespace(db=SimpleNamespace(path=(target/'data/workbox.db').resolve())))
    assert not FloatingController._valid_update_request(controller,dict(command='prepare-update'))
    assert FloatingController._valid_update_request(controller,dict(path=str(path),token=token))
    assert not FloatingController._valid_update_request(controller,dict(path=str(path),token='bad'))


def test_real_local_update_handshake_never_stops_busy_box_and_gracefully_closes_idle_box(pair,app):
    from time import monotonic
    from PySide6.QtTest import QTest
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    from ui import FileJob
    source,target,index=pair;(target/'data/workbox.db').unlink()
    service=DesktopService(target/'data/workbox.db');controller=FloatingController(service,tray_enabled=False)
    assert controller.start();controller.monitor_timer.stop();path,token=update.make_job(source,target,index=index)
    messages=[];handler=controller._ipc_message
    def record(socket):
        messages.append('received');return handler(socket)
    controller._ipc_message=record
    def send():
        # Production uses a separate updater process; exercise the actual local pipe that way.
        import subprocess,sys
        script="""
import json,sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from portable_update_ui import quiesce
app=QCoreApplication([])
try:
    quiesce(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]);error=''
except Exception as e:error=str(e)
print('IPC_RESULT='+json.dumps(dict(error=error)),flush=True)
"""
        child=subprocess.Popen([sys.executable,'-c',script,str(target),str(path),token],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        from PySide6.QtCore import QEventLoop
        loop=QEventLoop();timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:loop.quit() if child.poll() is not None else None)
        timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timer.start();timeout.start(12000);loop.exec();timer.stop();timeout.stop()
        if child.poll() is None:child.terminate();child.wait(3);pytest.fail('isolated IPC child timed out')
        out,err=child.communicate();assert child.returncode==0,(out,err)
        from types import SimpleNamespace
        return SimpleNamespace(error=json.loads(next(line.split('=',1)[1] for line in out.splitlines() if line.startswith('IPC_RESULT=')))['error'])
    try:
        controller.choosing_intake=True;busy=send();assert '正在处理材料' in busy.error and not controller.closed and controller.owns_lock,(busy.error,messages,controller.server.serverName())
        controller.choosing_intake=False;idle=send();app.processEvents()
        assert not idle.error and controller.closed and not controller.owns_lock
    finally:controller.choosing_intake=False;controller.shutdown();controller.dispose_ui()


def test_closing_update_window_before_queued_start_never_starts_update(app,monkeypatch):
    calls=[];monkeypatch.setattr(interface,'apply_job',lambda *a,**kw:calls.append(True))
    dialog=interface.UpdateProgressDialog('unused-plan','unused-token',no_launch=True)
    dialog.reject();app.processEvents()
    assert dialog.worker is None and not calls
    dialog.deleteLater()
