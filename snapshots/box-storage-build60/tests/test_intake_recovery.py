from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from desktop_service import DesktopService
from intake_history import latest
from intake_link import import_receipt


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True);return s


def intake(s,p):
    plan=s.receive_interactive([str(p)],linked=True)['material_preview'];r=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
    if r.get('material_conflicts'):r=s.resolve_duplicate_intake(r['material_plan'],{g['key']:dict(action='overwrite',project_id=g['candidates'][0]['id']) for g in r['material_conflicts']})
    return r


def raw(s,p):return s.receive_interactive([str(p)],zip_mode='original')


def package(p,content=b'A'):
    with ZipFile(p,'w') as z:z.writestr('readme.txt',content)


def test_old_failed_intake_never_reverts_submitted_new_version(service,tmp_path):
    a=tmp_path/'old/队列系统.docx';b=tmp_path/'new/队列系统.docx';a.parent.mkdir();b.parent.mkdir();a.write_bytes(b'old')
    with patch.object(service.production,'receive',side_effect=OSError('disk unavailable')):old=intake(service,a)
    b.write_bytes(b'new');new=intake(service,b);vid=service.production.rows()[0]['materials'][0]['id'];service.production.set_batch_mode(service.production.rows()[0]['batch_id'],'review');service.production.review(vid,True);service.production.submit([vid])
    assert not any(r['token']==old['import_token'] for r in service.production.pending_imports())
    assert import_receipt(service.production,old['import_token'])['files'][0]['state']=='superseded'
    r=service.production.retry_import_files(old['import_token']);assert r['superseded_count']==1 and r['pending_count']==0
    row=service.production.rows()[0];assert row['materials'][0]['id']==vid and row['submitted_count']==1
    assert Path(row['materials'][0]['path']).read_bytes()==b'new'


@pytest.mark.parametrize('missing',[False,True])
def test_collection_copy_changes_block_checks_submission_and_statistics(service,tmp_path,missing):
    p=tmp_path/'分类校验系统.docx';p.write_bytes(b'approved');r=intake(service,p);vid=service.production.rows()[0]['materials'][0]['id'];service.production.set_batch_mode(service.production.rows()[0]['batch_id'],'review');service.production.review(vid,True)
    with service.db.connect() as c:target=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0])
    if missing:target.unlink()
    else:target.write_bytes(b'damaged copy')
    row=service.production.rows()[0];assert row['collection_count']==0 and not row['materials'][0]['collection_saved'] and row['production_state']=='有异常'
    assert service.production.scan()['errors']
    with pytest.raises(ValueError,match='分类副本'):service.production.submit([vid])
    with pytest.raises(ValueError,match='分类副本'):service.production.review(vid,True)
    assert not service.production.rows()[0]['submitted_count']


def test_raw_new_copy_failure_has_durable_retry_without_duplicate_task(service,tmp_path):
    p=tmp_path/'原包恢复系统.zip';package(p);first=raw(service,p);pending=raw(service,p);g=pending['material_conflicts'][0];dec={g['key']:dict(action='new',project_id=first['project_ids'][0])}
    with patch.object(service,'_working_zip_copy',side_effect=OSError('disk full')):failed=service.resolve_duplicate_intake(pending['material_plan'],dec)
    assert failed['copied_count']==0 and failed['pending_count']==1 and failed['files'][0]['state']=='failed'
    assert len(service.production.rows())==2 and any(r['token']==failed['import_token'] for r in service.production.pending_imports())
    restarted=DesktopService(service.db.path);assert latest(restarted.production)['import_token']==failed['import_token']
    done=restarted.production.retry_import_files(failed['import_token']);assert done['copied_count']==1 and done['pending_count']==0
    assert len(restarted.production.rows())==2 and Path(done['files'][0]['saved_path']).read_bytes()==p.read_bytes()
    assert service.resolve_duplicate_intake(pending['material_plan'],dec)['copied_count']==1


def test_first_raw_copy_failure_reuses_created_task(service,tmp_path):
    p=tmp_path/'首份原包恢复系统.zip';package(p)
    with patch.object(service,'_working_zip_copy',side_effect=OSError('disk full')):failed=raw(service,p)
    assert failed['pending_count']==1 and len(failed['created_project_ids'])==1
    done=service.production.retry_import_files(failed['import_token']);assert done['copied_count']==1 and len(service.production.rows())==1


def test_latest_raw_receipt_survives_restart_and_keeps_original_location(service,tmp_path):
    p=tmp_path/'先收普通系统.docx';p.write_bytes(b'normal');intake(service,p)
    z=tmp_path/'最后原包系统.zip';package(z);first=raw(service,z)
    restarted=DesktopService(service.db.path);last=latest(restarted.production)
    assert last['import_token']==first['import_token'] and last['raw_zip_intake']
    package(z,b'new');pending=raw(service,z);g=pending['material_conflicts'][0]
    service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=first['project_ids'][0])})
    old=import_receipt(service.production,first['import_token']);assert old['files'][0]['saved_path']==first['files'][0]['saved_path']
    assert Path(old['files'][0]['saved_path']).read_bytes()!=z.read_bytes()


def test_raw_retry_source_change_and_withdrawn_task_are_guarded(service,tmp_path):
    p=tmp_path/'原包重试守卫系统.zip';package(p);first=raw(service,p);pending=raw(service,p);g=pending['material_conflicts'][0]
    with patch.object(service,'_working_zip_copy',side_effect=OSError('disk full')):failed=service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    pid=failed['created_project_ids'][0];service.production.delete_tasks([pid],confirmed=True)
    assert import_receipt(service.production,failed['import_token'])['files'][0]['state']=='paused'
    service.production.retry_import_files(failed['import_token']);assert len(service.production.rows())==1
    service.production.restore_tasks([pid]);package(p,b'changed')
    r=service.production.retry_import_files(failed['import_token']);assert r['files'][0]['state']=='source_changed'
    from production import source_digest
    r=service.production.retry_import_files(failed['import_token'],project_id=pid,source_path=str(p),accepted_source=source_digest(p));assert r['copied_count']==1


def test_failed_update_of_baseline_can_still_retry(service,tmp_path):
    p=tmp_path/'合法重试系统.docx';p.write_bytes(b'A');intake(service,p)
    p.write_bytes(b'B')
    with patch.object(service.production,'receive',side_effect=OSError('disk full')):failed=intake(service,p)
    assert any(r['token']==failed['import_token'] for r in service.production.pending_imports())
    assert service.production.retry_import_files(failed['import_token'])['copied_count']==1
    assert Path(service.production.rows()[0]['materials'][0]['path']).read_bytes()==b'B'


def test_batch_raw_failure_has_one_retry_record_and_keeps_success(service,tmp_path):
    a=tmp_path/'批量甲系统.zip';b=tmp_path/'批量乙系统.zip';package(a);package(b)
    copy=service._working_zip_copy
    def copy_or_fail(source,*args,**kwargs):
        if source.name==a.name:raise OSError('disk full')
        return copy(source,*args,**kwargs)
    with patch.object(service,'_working_zip_copy',side_effect=copy_or_fail):r=service.receive_interactive([str(a),str(b)],zip_mode='original')
    assert r['copied_count']==1 and r['pending_count']==1
    pending=service.production.pending_imports();assert len(pending)==1 and pending[0]['token']==r['import_token']
    done=service.production.retry_import_files(r['import_token']);assert done['copied_count']==2 and done['pending_count']==0
    assert len(service.production.rows())==2


def test_raw_failed_receipt_ui_exposes_retry_and_keeps_failure_count(service,tmp_path):
    from PySide6.QtWidgets import QApplication,QWidget,QLabel
    from intake_link_ui import IntakeReceiptDialog
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    p=tmp_path/'失败回执界面系统.zip';package(p);first=raw(service,p);pending=raw(service,p);g=pending['material_conflicts'][0]
    with patch.object(service,'_working_zip_copy',side_effect=OSError('disk full')):r=service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    class Page(QWidget):
        def __init__(self):super().__init__();self.service=service;self.store=service.production;self.requests=[]
        def request(self,*args):self.requests.append(args)
    page=Page();d=IntakeReceiptDialog(page,r)
    assert d.table.rowCount()==1 and d.table.item(0,1).text()=='收录失败' and d.table.item(0,3).text()=='disk full'
    assert any('保存 0 份，未完成 1 份' in label.text() for label in d.findChildren(QLabel))
    d.retry_selected();assert page.requests
    done=page.requests[0][1]();assert done['copied_count']==1 and len(service.production.rows())==2
    d.close();page.close()
