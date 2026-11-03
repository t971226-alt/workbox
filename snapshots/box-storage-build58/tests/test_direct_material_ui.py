from pathlib import Path
import pytest
from PySide6.QtCore import Qt,QTimer,QEventLoop
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from direct_material_ui import DirectMaterialDialog
from desktop_controller import FloatingController
from theme import install_theme,set_theme


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料保存',complete_setup=True)
    install_theme(s);yield s;install_theme()


def file(tmp_path,name):
    p=tmp_path/name;p.write_bytes(b'isolated word');return p


def test_preview_validation_class_changes_and_cancel_leave_no_records(service,tmp_path,app):
    p=file(tmp_path,'说明书.docx');plan=service.receive_interactive([str(p)])['material_preview']
    d=DirectMaterialDialog(service,plan);d.show();app.processEvents()
    QTest.mouseClick(d.confirm_button,Qt.LeftButton);assert d.result()!=QDialog.Accepted and '名称' in d.error.text()
    d.table.item(0,2).setText('手工填写的软件')
    combo=d.class_controls[0];combo.setCurrentIndex(combo.findData('桌面14件'))
    assert d.kind_controls[0].findData('软件说明书.docx')>=0
    QTest.mouseClick(d.confirm_button,Qt.LeftButton)
    assert d.result()==QDialog.Accepted and len(d.rows[0]['kinds'])==14
    assert not service.production.rows() and not service.list_projects()
    d.deleteLater()
    d=DirectMaterialDialog(service,plan);d.show();QTest.mouseClick(d.cancel_button,Qt.LeftButton)
    assert d.result()==QDialog.Rejected and not service.production.rows() and p.exists();d.deleteLater()


def test_dialog_fits_light_dark_and_custom_same_name_union(service,tmp_path,app):
    paths=[file(tmp_path,'甲附件.txt'),file(tmp_path,'乙附件.pdf')]
    plan=service.receive_interactive([str(p) for p in paths])['material_preview']
    for mode in ('light','dark'):
        set_theme(mode,service);d=DirectMaterialDialog(service,plan);d.resize(760,500);d.show();app.processEvents()
        for control in (d.confirm_button,d.cancel_button,d.table,d.mode):assert d.rect().contains(control.mapTo(d,control.rect().bottomRight()))
        for i in range(2):d.table.item(i,2).setText('同一软件');d.class_controls[i].setCurrentIndex(d.class_controls[i].findData('自定义'))
        values=d.values();assert all(len(r['kinds'])==2 for r in values)
        d.reject();d.deleteLater()


def test_existing_disabled_class_uses_original_snapshot_in_mixed_preview(service,tmp_path,app):
    from requirement_intake import proposal
    profile=service.classifications.save('旧档案',['原报告.docx'],{'原报告.docx':['原报告']})
    service.production.import_plan([dict(proposal('旧系统',batch='原批'),package='旧档案',kinds=['原报告.docx'])],token='old')
    a=file(tmp_path,'旧系统_原报告.docx');service.receive_path(a);service.classifications.remove(profile)
    b=file(tmp_path,'新系统.docx');plan=service.receive_interactive([str(a),str(b)])['material_preview']
    d=DirectMaterialDialog(service,plan);row=next(i for i,e in enumerate(plan['entries']) if e['name']=='旧系统')
    assert not d.class_controls[row].isEnabled() and d.kind_controls[row].currentData()=='原报告.docx'
    assert d.values()[row]['kinds']==['原报告.docx']
    d.reject();d.deleteLater()


def test_intake_result_focuses_new_batch_in_current_workbench(service,app):
    from compact_ui import CompactPanel
    service.production.create_batch('前面的任务',[f'旧软件{i}' for i in range(15)],['软件说明书.docx'])
    new=service.production.create_batch('刚收录的批次',['新软件'],['软件说明书.docx'])
    p=CompactPanel(service);p.show()
    for _ in range(4):app.processEvents()
    p.open_production_batches([new['batch_id']])
    for _ in range(6):app.processEvents()
    ledger=p.production_page.task_workspace
    assert ledger.isVisible() and [r['id'] for r in ledger.rows]==new['project_ids']
    assert '临时' in ledger.scope_label.text()
    p.conversation_page.shutdown();p.document_pane.shutdown();p.hide();p.deleteLater()


@pytest.mark.parametrize('confirm',[False,True])
def test_real_controller_shows_preview_before_writes_and_finishes_master(service,tmp_path,app,confirm,monkeypatch):
    from direct_material_intake import DirectMaterialIntake
    monkeypatch.setattr(DirectMaterialIntake, 'can_auto_receive', staticmethod(lambda plan: False))
    paths=[file(tmp_path,f'直接拖入系统{i}.docx') for i in range(3)]
    c=FloatingController(service,tray_enabled=False);assert c.start();c.monitor_timer.stop()
    loop=QEventLoop();timer=QTimer();seen=[];errors=[]
    def advance():
        try:
            dialog=QApplication.activeModalWidget()
            if isinstance(dialog,DirectMaterialDialog) and not seen:
                assert not service.production.rows() and not service.list_projects();seen.append(True)
                dialog.mode.setCurrentIndex(dialog.mode.findData('collect'))
                QTest.mouseClick(dialog.confirm_button if confirm else dialog.cancel_button,Qt.LeftButton)
            elif seen and not c.busy:loop.quit()
        except Exception as e:errors.append(e);loop.quit()
    timer.timeout.connect(advance);timer.start(10)
    timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timeout.start(20000)
    try:
        c.receive({'kind':'files','paths':[str(p) for p in paths]});loop.exec()
        assert not errors and seen and not c.busy
        rows=service.production.rows();assert len(rows)==(3 if confirm else 0)
        if confirm:
            assert all(r['production_state']=='已收纳' for r in rows)
            assert len(c.panel.production_page.rows)==3 and c.panel.production_page.isVisible()
        assert all(p.exists() for p in paths)
    finally:
        timer.stop();timeout.stop()
        modal=QApplication.activeModalWidget()
        if modal:modal.reject()
        c.shutdown();c.dispose_ui()


def test_clear_five_material_drop_saves_without_extra_popup(service, tmp_path, app):
    folder=tmp_path/'auto-five';folder.mkdir()
    paths=[]
    for suffix in ['.docx','.pdf','.txt','_代码 (Python).docx','_代码 (Python).pdf']:
        p=folder/('自动收件系统'+suffix);p.write_bytes(b'isolated file');paths.append(p)
    service.set_setting('default_processing_mode', 'collect')
    c=FloatingController(service,tray_enabled=False);assert c.start();c.monitor_timer.stop()
    loop=QEventLoop();poll=QTimer();poll.setInterval(10);dialogs=[]
    def observe():
        modal=QApplication.activeModalWidget()
        from intake_link_ui import RecognitionSummaryDialog
        if isinstance(modal,RecognitionSummaryDialog):
            dialogs.append(True);modal.reject()
        elif isinstance(modal,DirectMaterialDialog):
            dialogs.append(True);modal.reject()
        if not c.busy:loop.quit()
    poll.timeout.connect(observe);poll.start()
    timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timeout.start(15000)
    try:
        c.receive({'kind':'files','paths':list(map(str,paths))});loop.exec()
        assert not c.busy and not dialogs
        rows=service.production.rows()
        assert len(rows)==1 and rows[0]['package_name']=='五件材料'
        assert rows[0]['collection_count']==5 and rows[0]['production_state']=='已收纳'
        assert all(p.exists() for p in paths)
    finally:
        poll.stop();timeout.stop();c.shutdown();c.dispose_ui()
