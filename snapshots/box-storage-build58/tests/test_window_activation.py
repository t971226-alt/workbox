import subprocess,sys,time,json
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from desktop_controller import FloatingController
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

@pytest.mark.parametrize('fragmented,silent',[(False,False),(True,False),(False,True)])
def test_separate_process_wakes_hidden_window_and_receives_ack(app,tmp_path,fragmented,silent):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料',complete_setup=True);install_theme(s)
    c=FloatingController(s,tray_enabled=False);assert c.start();c.monitor_timer.stop();c.panel.hide()
    code='''import sys,time
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
a=QCoreApplication([]);s=QLocalSocket();s.connectToServer(sys.argv[1]);assert s.waitForConnected(2000)
raw=sys.argv[2].encode()
if sys.argv[3]=='True':
 s.write(raw[:10]);s.waitForBytesWritten(1000);time.sleep(.1);s.write(raw[10:])
else:s.write(raw)
s.waitForBytesWritten(1000)
assert s.bytesAvailable() or s.waitForReadyRead(3000)
assert b'activated' in bytes(s.readAll())
'''
    child=None
    try:
        child=subprocess.Popen([sys.executable,'-c',code,c.server_name,json.dumps(dict(command='activate',silent=silent))+'\n',str(fragmented)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        deadline=time.monotonic()+8
        while child.poll() is None and time.monotonic()<deadline:QTest.qWait(20)
        assert child.poll()==0,child.communicate(timeout=1)
        assert c.panel.isVisible()==(not silent)
        assert c.owns_lock
    finally:
        if child and child.poll() is None:child.terminate();child.wait(5)
        c.shutdown();c.dispose_ui();install_theme()
