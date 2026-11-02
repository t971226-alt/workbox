from pathlib import Path
from zipfile import ZipFile
import pytest
from desktop_service import DesktopService
from intake_link import import_receipt
from production import source_digest


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True);return s


def intake(s,p):
    plan=s.receive_interactive([str(p)],linked=True)['material_preview']
    return s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')


def fail(s,p,monkeypatch):
    original=s.production.receive
    with monkeypatch.context() as m:
        m.setattr(s.production,'receive',lambda *a,**kw:(_ for _ in ()).throw(OSError('copy failed')))
        result=intake(s,p)
    return result


def test_changed_source_requires_specific_hash_approval(service,tmp_path,monkeypatch):
    p=tmp_path/'来源校验系统.docx';p.write_bytes(b'A');result=fail(service,p,monkeypatch);token=result['import_token'];pid=result['project_ids'][0]
    p.write_bytes(b'B');service.production.retry_import_files(token)
    f=import_receipt(service.production,token)['files'][0];assert f['state']=='source_changed' and not f['saved_path']
    approved=source_digest(p);p.write_bytes(b'C')
    service.production.retry_import_files(token,project_id=pid,source_path=str(p),accepted_source=approved)
    assert import_receipt(service.production,token)['files'][0]['state']=='source_changed'
    service.production.retry_import_files(token,project_id=pid,source_path=str(p),accepted_source=source_digest(p))
    f=import_receipt(service.production,token)['files'][0];assert f['state']=='complete' and Path(f['saved_path']).read_bytes()==b'C'


def test_withdrawn_task_pauses_and_restore_resumes(service,tmp_path,monkeypatch):
    p=tmp_path/'撤销重试系统.docx';p.write_bytes(b'A');r=fail(service,p,monkeypatch);pid=r['project_ids'][0];token=r['import_token']
    service.production.delete_tasks([pid],confirmed=True)
    assert not service.production.pending_imports()
    result=service.production.retry_import_files(token);assert result['paused_count']==1 and result['pending_count']==0
    assert import_receipt(service.production,token)['files'][0]['state']=='paused'
    service.production.restore_tasks([pid]);assert service.production.pending_imports()
    assert service.production.retry_import_files(token)['copied_count']==1


def test_historical_receipt_pins_immutable_version_and_legacy_unknown(service,tmp_path):
    p=tmp_path/'历史回执系统.docx';p.write_bytes(b'A');first=intake(service,p)
    f=import_receipt(service.production,first['import_token'])['files'][0];assert f['saved_version_id'] and f['saved_sha256']==source_digest(p)
    p.write_bytes(b'B');pending=intake(service,p);g=pending['material_conflicts'][0]
    service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=first['project_ids'][0])})
    assert Path(import_receipt(service.production,first['import_token'])['files'][0]['saved_path']).read_bytes()==b'A'
    with service.db.connect() as c:c.execute("UPDATE production_import_files SET saved_path='',saved_version_id=NULL WHERE token=?",(first['import_token'],))
    legacy=import_receipt(service.production,first['import_token'])['files'][0];assert not legacy['saved_path'] and '无法确认' in legacy['error']


@pytest.mark.parametrize('action',['new','overwrite'])
@pytest.mark.parametrize('identical',[True,False])
def test_raw_zip_duplicates_require_choice_keep_history_and_replay(service,tmp_path,action,identical):
    p=tmp_path/'原包冲突系统.zip'
    with ZipFile(p,'w') as z:z.writestr('readme.txt',b'A')
    first=service.receive_interactive([str(p)],zip_mode='original');before=Path(first['working_zip_path']).read_bytes();pid=first['project_ids'][0]
    if not identical:
        with ZipFile(p,'w') as z:z.writestr('readme.txt',b'B')
    pending=service.receive_interactive([str(p)],zip_mode='original');assert pending['material_conflicts'] and len(service.production.rows())==1
    g=pending['material_conflicts'][0];choices={g['key']:dict(action=action,project_id=pid)}
    result=service.resolve_duplicate_intake(pending['material_plan'],choices)
    assert result['raw_zip_intake'] and len(service.production.rows())==(2 if action=='new' else 1)
    assert Path(first['working_zip_path']).read_bytes()==before
    assert all(Path(f['saved_path']).read_bytes()==p.read_bytes() for f in result['files'])
    assert service.resolve_duplicate_intake(pending['material_plan'],choices)==result
    assert not (service.db.path.parent/'材料收件缓存').exists()


def test_raw_zip_confirmation_rejects_source_and_task_changes(service,tmp_path):
    p=tmp_path/'原包校验系统.zip'
    with ZipFile(p,'w') as z:z.writestr('readme.txt',b'A')
    service.receive_interactive([str(p)],zip_mode='original');pending=service.receive_interactive([str(p)],zip_mode='original');g=pending['material_conflicts'][0]
    with ZipFile(p,'w') as z:z.writestr('readme.txt',b'B')
    with pytest.raises(ValueError,match='来源已变化'):service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=g['candidates'][0]['id'])})
    assert len(service.production.rows())==1


def test_mixed_retry_does_not_let_withdrawn_task_block_active_one(service,tmp_path,monkeypatch):
    paths=[]
    for name in ['暂停甲系统','继续乙系统']:
        p=tmp_path/(name+'.docx');p.write_bytes(b'A');paths.append(str(p))
    plan=service.receive_interactive(paths,linked=True)['material_preview']
    with monkeypatch.context() as m:
        m.setattr(service.production,'receive',lambda *a,**kw:(_ for _ in ()).throw(OSError('copy failed')))
        r=service.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
    service.production.delete_tasks([r['project_ids'][0]],confirmed=True)
    retried=service.production.retry_import_files(r['import_token'])
    assert retried['copied_count']==1 and retried['paused_count']==1 and retried['pending_count']==0
    assert [f['state'] for f in import_receipt(service.production,r['import_token'])['files']]==['paused','complete']


def test_raw_batch_conflicts_are_all_preflighted_before_writes(service,tmp_path):
    paths=[]
    for name in ['原包甲系统','原包乙系统']:
        p=tmp_path/(name+'.zip')
        with ZipFile(p,'w') as z:z.writestr('readme.txt',b'A')
        service.receive_interactive([str(p)],zip_mode='original');paths.append(str(p))
    pending=service.receive_interactive(paths,zip_mode='original');assert len(pending['material_conflicts'])==2
    assert len(service.production.rows())==2
    choices={g['key']:dict(action='new',project_id=g['candidates'][0]['id']) for g in pending['material_conflicts']}
    result=service.resolve_duplicate_intake(pending['material_plan'],choices)
    assert len(result['files'])==2 and len(service.production.rows())==4


def test_changed_source_dialog_cancellation_keeps_retry_pending(service,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QWidget,QMessageBox
    from PySide6.QtCore import QTimer
    from intake_link_ui import IntakeReceiptDialog
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    p=tmp_path/'确认来源系统.docx';p.write_bytes(b'A');r=fail(service,p,monkeypatch);p.write_bytes(b'B')
    class Page(QWidget):
        def __init__(self):super().__init__();self.service=service;self.store=service.production;self.requests=[]
        def request(self,*args):self.requests.append(args)
    page=Page();d=IntakeReceiptDialog(page,r);seen=[]
    def cancel():
        box=QApplication.activeModalWidget();assert isinstance(box,QMessageBox);seen.append(box.windowTitle());next(b for b in box.buttons() if b.text()=='取消').click()
    QTimer.singleShot(30,cancel);d.retry_selected()
    assert seen==['核对重试来源'] and not page.requests
    assert import_receipt(service.production,r['import_token'])['files'][0]['state']=='failed'
    d.close();page.close()


def test_broken_duplicate_zip_cannot_create_empty_new_task(service,tmp_path):
    p=tmp_path/'损坏重复系统.zip'
    with ZipFile(p,'w') as z:z.writestr('readme.txt',b'A')
    first=service.receive_interactive([str(p)],zip_mode='original');p.write_bytes(b'broken')
    pending=service.receive_interactive([str(p)],zip_mode='original');g=pending['material_conflicts'][0]
    with pytest.raises((ValueError,OSError)):
        service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    assert len(service.production.rows())==1
