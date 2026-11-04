from pathlib import Path
import pytest
from PySide6.QtCore import Qt,QTimer,QEventLoop
from PySide6.QtWidgets import QApplication,QMessageBox
from desktop_service import DesktopService
from desktop_controller import FloatingController
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setStyle('Fusion');a.setQuitOnLastWindowClosed(False);return a

@pytest.fixture
def controller(app,tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料',complete_setup=True)
    result=s.production.create_batch('反馈验证',['反馈系统'],['软件说明书.docx']);pid=result['project_ids'][0]
    p=tmp_path/'反馈系统.docx';p.write_bytes(b'original');s.production.receive(pid,'软件说明书.docx',p)
    s.production.set_batch_mode(result['batch_id'],'review');install_theme(s)
    c=FloatingController(s,tray_enabled=False);assert c.start();c.monitor_timer.stop();c.show_panel();c.panel.production_page.show_legacy_workspace();app.processEvents()
    yield c
    c.shutdown();c.dispose_ui();install_theme()

def wait(c):
    loop=QEventLoop();poll=QTimer();poll.setInterval(10);poll.timeout.connect(lambda:loop.quit() if not c.busy else None)
    timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);poll.start();timeout.start(10000);loop.exec();poll.stop();timeout.stop();assert not c.busy

def select(page):page.table.item(0,0).setCheckState(Qt.Checked)

def test_pending_review_opens_visible_reason_and_goes_to_check(controller,app):
    c=controller;page=c.panel.production_page;select(page);observed=[]
    def respond():
        box=QApplication.activeModalWidget();observed.append(box.text());next(b for b in box.buttons() if b.text()=='去检查').click()
    QTimer.singleShot(0,respond);page.submit_button.click();app.processEvents()
    row=page.rows[0];assert observed and '尚未检查通过' in observed[0]
    assert row['id'] in page.grouped.open_tasks and row['submitted_count']==row['reviewed_count']==0
    assert '暂未记录提交' in page.error.text() and page.error.isVisible()
    assert page.grouped.task_buttons[row['id']].drawing_font().pixelSize()==11

def test_ready_submission_refreshes_counts_and_reports_success(controller,app,monkeypatch):
    c=controller;page=c.panel.production_page;row=page.rows[0];vid=row['materials'][0]['id']
    page.store.review(vid,True);c.refresh();select(page)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Yes)
    page.submit_button.click();wait(c);app.processEvents()
    assert page.rows[0]['production_state']=='已提交' and page.rows[0]['submitted_count']==1
    assert '已记录 1 份材料的提交' in page.error.text() and page.error.isVisible()
    assert not page.checked()
    select(page);monkeypatch.setattr(QMessageBox,'information',lambda *a,**k:QMessageBox.Ok)
    page.submit_button.click();assert '无需重复操作' in page.error.text()

def test_failed_file_validation_is_visible_and_does_not_mark_submitted(controller,app,monkeypatch):
    c=controller;page=c.panel.production_page;row=page.rows[0];m=row['materials'][0]
    page.store.review(m['id'],True);c.refresh();Path(m['path']).write_bytes(b'changed outside BOX');select(page)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.Yes)
    page.submit_button.click();wait(c);app.processEvents()
    assert '未能记录提交' in page.error.text() and page.error.isVisible()
    assert page.store.rows()[0]['submitted_count']==0


def test_delete_cancel_confirm_and_restore_update_master(controller,app):
    from PySide6.QtWidgets import QDialog,QListWidget
    c=controller;page=c.panel.production_page;row=page.rows[0];pid=row['id'];original=Path(row['materials'][0]['path']).read_bytes();errors=[]
    def choose(text):
        def click():
            box=QApplication.activeModalWidget()
            try:
                assert isinstance(box,QMessageBox)
                next(b for b in box.buttons() if b.text()==text).click()
            except BaseException as e:
                errors.append(e)
                if isinstance(box,QDialog):box.reject()
        QTimer.singleShot(0,click)
    select(page);choose('取消');page.delete_tasks_button.click();app.processEvents()
    assert not errors and len(page.rows)==1 and not page.store.deleted_rows()
    choose('删除任务');page.delete_tasks_button.click();wait(c);app.processEvents()
    assert not errors and not page.rows and not page.checked() and page.empty_label.isVisible()
    assert '总表与统计已更新' in page.error.text()
    def restore():
        dialog=QApplication.activeModalWidget()
        try:
            assert dialog.windowTitle()=='已删除任务'
            items=dialog.findChild(QListWidget);assert items.count()==1
            items.item(0).setCheckState(Qt.Checked)
            next(b for b in dialog.findChildren(QWidget) if b.objectName()=='restoreDeletedTasks').click()
        except BaseException as e:
            errors.append(e);dialog.reject()
    from PySide6.QtWidgets import QWidget
    QTimer.singleShot(0,restore);page.deleted_tasks_action.trigger();wait(c);app.processEvents()
    assert not errors and page.rows[0]['id']==pid and not page.store.deleted_rows()
    assert Path(page.rows[0]['materials'][0]['path']).read_bytes()==original


def test_partial_intake_failure_is_visible_on_first_notice_line(controller, app):
    c = controller
    result = dict(material_intake=True, batch_ids=[], project_ids=[], created_project_ids=[],
                  copied_count=10, pending_count=21,
                  intake_errors=[dict(path='isolated.docx', error='isolated failure')])
    c.enqueue('核对后收录材料', lambda **kw: result)
    wait(c)
    app.processEvents()
    assert '未完成 21 份' in c.panel.notice.text()
    assert '继续未完成导入' in c.panel.notice.text()

