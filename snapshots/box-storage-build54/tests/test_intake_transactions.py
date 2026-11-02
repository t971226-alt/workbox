"""Fault injection across the task / file / database / receipt boundaries."""
from pathlib import Path
from unittest.mock import patch
from threading import Event
from concurrent.futures import CancelledError
import pytest
from desktop_service import DesktopService
from tests.test_intake_recovery import package,intake,raw
import intake_history as history
import intake_link


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True);return s


def conflict(s,p):
    first=raw(s,p);pending=raw(s,p);g=pending['material_conflicts'][0]
    return first,pending,{g['key']:dict(action='new',project_id=first['project_ids'][0])}


def test_material_database_rollback_keeps_original_publication(service,tmp_path):
    p=tmp_path/'材料事务系统.docx';p.write_bytes(b'A');intake(service,p)
    with service.db.connect() as c:old=dict(c.execute('SELECT * FROM collected_materials').fetchone())
    p.write_bytes(b'B')
    with patch.object(service.production,'_sync',side_effect=OSError('failure after publish')):failed=intake(service,p)
    with service.db.connect() as c:now=dict(c.execute('SELECT * FROM collected_materials').fetchone())
    assert now==old and Path(now['path']).read_bytes()==b'A'
    assert service.production.rows()[0]['collection_count']==1
    restarted=DesktopService(service.db.path);done=restarted.production.retry_import_files(failed['import_token'])
    assert done['copied_count']==1 and len(restarted.production.rows())==1
    row=restarted.production.rows()[0];assert Path(row['materials'][0]['path']).read_bytes()==b'B' and row['collection_count']==1


def test_post_commit_cleanup_can_restart_and_does_not_remove_inputs(service,tmp_path):
    p=tmp_path/'延迟整理系统.docx';p.write_bytes(b'A');intake(service,p)
    with service.db.connect() as c:old=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0])
    p.write_bytes(b'B')
    with patch('collection_retirement.finalize',side_effect=OSError('cleanup interrupted')):intake(service,p)
    assert old.is_file()
    restarted=DesktopService(service.db.path)
    assert restarted.production.rows()[0]['collection_count']==1 and not old.exists()
    assert p.read_bytes()==b'B'


@pytest.mark.parametrize('boundary',['task_journal','receipt_journal','after_copy'])
def test_zip_failure_boundaries_are_restartable_without_duplicate_tasks(service,tmp_path,boundary):
    p=tmp_path/'原包事务系统.zip';package(p);first,pending,dec=conflict(service,p)
    save=history.save;copy=service._working_zip_copy
    def fail_save(store,r,**kwargs):
        if boundary=='task_journal' and r.get('raw_zip_intake') and not r.get('copied_count'):raise OSError('initial journal failure')
        if boundary=='receipt_journal' and r.get('copied_count'):raise OSError('receipt journal failure')
        return save(store,r,**kwargs)
    def copy_then_interrupt(*args,**kwargs):
        result=copy(*args,**kwargs)
        if boundary=='after_copy':raise OSError('interrupted after physical copy')
        return result
    with patch.object(history,'save',side_effect=fail_save),patch.object(service,'_working_zip_copy',side_effect=copy_then_interrupt):
        if boundary=='task_journal':
            with pytest.raises(OSError):service.resolve_duplicate_intake(pending['material_plan'],dec)
        else:failed=service.resolve_duplicate_intake(pending['material_plan'],dec)
    restarted=DesktopService(service.db.path)
    if boundary=='task_journal':
        assert len(restarted.production.rows())==1
        done=restarted.resolve_duplicate_intake(pending['material_plan'],dec)
    else:
        assert len(restarted.production.rows())==2
        with restarted.db.connect() as c:assert c.execute('SELECT count(*) FROM original_zip_receipts').fetchone()[0]==1
        done=restarted.production.retry_import_files(failed['import_token'])
    assert done['copied_count']==1 and done['pending_count']==0 and len(restarted.production.rows())==2
    with restarted.db.connect() as c:assert c.execute('SELECT count(*) FROM original_zip_receipts').fetchone()[0]==2
    assert restarted.resolve_duplicate_intake(pending['material_plan'],dec)['copied_count']==1


def test_batch_cancel_between_items_preserves_whole_plan_and_completion(service,tmp_path):
    a=tmp_path/'取消甲系统.zip';b=tmp_path/'取消乙系统.zip';package(a);package(b);event=Event();register=intake_link.register_original_zip
    def cancel_after_register(*args,**kwargs):
        r=register(*args,**kwargs);event.set();return r
    with patch.object(intake_link,'register_original_zip',side_effect=cancel_after_register):
        with pytest.raises(CancelledError):service.receive_interactive([str(a),str(b)],zip_mode='original',cancel_event=event)
    restarted=DesktopService(service.db.path);r=history.latest(restarted.production)
    assert r['copied_count']==1 and r['pending_count']==1 and len(r['files'])==2
    done=restarted.production.retry_import_files(r['import_token']);assert done['copied_count']==2 and len(restarted.production.rows())==2


def test_mixed_bad_zip_does_not_orphan_valid_new_task(service,tmp_path):
    a=tmp_path/'混合已有系统.zip';bad=tmp_path/'损坏.zip';package(a);bad.write_bytes(b'broken');first=raw(service,a)
    pending=service.receive_interactive([str(a),str(bad)],zip_mode='original');g=pending['material_conflicts'][0]
    r=service.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=first['project_ids'][0])})
    assert r['copied_count']==1 and r['pending_count']==1 and len(r['files'])==2
    assert len(service.production.rows())==2 and all(row['original_zips'] for row in service.production.rows())
    assert next(f for f in r['files'] if not f['project_id'])['error']


def test_legacy_split_commit_recovers_saved_receipt_not_superseded(service,tmp_path):
    p=tmp_path/'旧版本分步提交系统.zip';package(p);first,pending,dec=conflict(service,p)
    with patch.object(service,'_working_zip_copy',side_effect=OSError('disk full')):r=service.resolve_duplicate_intake(pending['material_plan'],dec)
    f=r['files'][0];service.workspaces.ensure_projects([f['project_id']]);ws=service.workspaces.get_workspace(f['project_id']);saved=service._working_zip_copy(p,ws['path'])
    # Build 23 committed this receipt separately from its still-failed intake record.
    intake_link.register_original_zip(service,dict(project_id=f['project_id'],working_zip_path=saved['path']),p)
    with service.db.connect() as c:c.execute('DELETE FROM raw_intake_journal WHERE token=?',(r['import_token'],))
    restarted=DesktopService(service.db.path);done=restarted.production.retry_import_files(r['import_token'])
    assert done['copied_count']==1 and done['files'][0]['recovered_receipt'] and done['superseded_count']==0


def test_copy_without_receipt_recovers_when_original_is_gone(service,tmp_path):
    p=tmp_path/'中断后原件移走系统.zip';package(p);first,pending,dec=conflict(service,p);copy=service._working_zip_copy
    def copy_then_fail(*args,**kwargs):copy(*args,**kwargs);raise OSError('process interrupted after copy')
    with patch.object(service,'_working_zip_copy',side_effect=copy_then_fail):r=service.resolve_duplicate_intake(pending['material_plan'],dec)
    p.unlink();restarted=DesktopService(service.db.path);done=restarted.production.retry_import_files(r['import_token'])
    assert done['copied_count']==1 and Path(done['files'][0]['saved_path']).is_file()


@pytest.mark.parametrize('boundary',['before_plan_commit','after_copy','before_receipt_commit','after_receipt_commit'])
def test_actual_process_exit_recovers_atomic_zip_state(service,tmp_path,boundary):
    import subprocess,sys,pickle
    p=tmp_path/'进程退出恢复系统.zip';package(p);first,pending,dec=conflict(service,p)
    payload=tmp_path/'owned-test-payload.bin';payload.write_bytes(pickle.dumps((pending['material_plan'],dec)))
    child=r'''
import sys,os,pickle
from pathlib import Path
from desktop_service import DesktopService
import intake_history,raw_intake_engine
s=DesktopService(sys.argv[1]);plan,dec=pickle.loads(Path(sys.argv[2]).read_bytes());boundary=sys.argv[3]
save=intake_history.save;copy=s._working_zip_copy;commit=raw_intake_engine._commit
def crashing_save(store,result,**kw):
 if boundary=='before_plan_commit' and not result.get('copied_count'):os._exit(73)
 if boundary=='before_receipt_commit' and result.get('copied_count'):os._exit(73)
 return save(store,result,**kw)
def crashing_copy(*a,**kw):
 r=copy(*a,**kw)
 if boundary=='after_copy':os._exit(73)
 return r
def crashing_commit(*a,**kw):
 r=commit(*a,**kw)
 if boundary=='after_receipt_commit':os._exit(73)
 return r
intake_history.save=crashing_save;s._working_zip_copy=crashing_copy;raw_intake_engine._commit=crashing_commit
s.resolve_duplicate_intake(plan,dec)
raise AssertionError('crash hook did not execute')
'''
    done=subprocess.run([sys.executable,'-c',child,str(service.db.path),str(payload),boundary],capture_output=True,timeout=30)
    assert done.returncode==73,done.stderr.decode(errors='replace')
    restarted=DesktopService(service.db.path);token='zip-'+pending['material_plan']['token']
    if boundary=='before_plan_commit':
        assert len(restarted.production.rows())==1 and history.load_raw(restarted.production,token) is None
        r=restarted.resolve_duplicate_intake(pending['material_plan'],dec)
    else:
        state=history.load_raw(restarted.production,token);assert len(restarted.production.rows())==2
        assert state['copied_count']==(1 if boundary=='after_receipt_commit' else 0)
        if boundary=='after_copy':p.unlink()
        r=restarted.production.retry_import_files(token)
    assert r['copied_count']==1 and r['pending_count']==0 and len(restarted.production.rows())==2
    with restarted.db.connect() as c:assert c.execute('SELECT count(*) FROM original_zip_receipts').fetchone()[0]==2


def test_retirement_does_not_remove_externally_changed_export(service,tmp_path):
    p=tmp_path/'保留外部编辑系统.docx';p.write_bytes(b'A');intake(service,p)
    with service.db.connect() as c:old=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0])
    p.write_bytes(b'B')
    with patch('collection_retirement.finalize',side_effect=OSError('deferred cleanup')):intake(service,p)
    old.write_bytes(b'external edit to keep')
    restarted=DesktopService(service.db.path);assert old.read_bytes()==b'external edit to keep' and restarted.production.rows()[0]['collection_count']==1


def test_unmatched_same_name_zip_versions_are_stopped_before_tasks(service,tmp_path):
    a=tmp_path/'one/同名首次系统.zip';b=tmp_path/'two/同名首次系统.zip';a.parent.mkdir();b.parent.mkdir();package(a,b'A');package(b,b'B')
    with pytest.raises(ValueError,match='同名软件的不同 ZIP'):service.receive_interactive([str(a),str(b)],zip_mode='original')
    assert not service.production.rows()


def test_actual_process_exit_after_material_commit_pins_completed_attempt(service,tmp_path):
    import subprocess,sys
    p=tmp_path/'普通材料提交后中断系统.docx';p.write_bytes(b'A');intake(service,p);p.write_bytes(b'B')
    child=r'''
import sys,os
from pathlib import Path
from desktop_service import DesktopService
s=DesktopService(sys.argv[1]);p=Path(sys.argv[2]);receive=s.production.receive
def exit_after_receive(*args,**kwargs):receive(*args,**kwargs);os._exit(73)
s.production.receive=exit_after_receive
plan=s.receive_interactive([str(p)],linked=True)['material_preview'];pending=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'review')
s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=g['candidates'][0]['id']) for g in pending['material_conflicts']})
'''
    done=subprocess.run([sys.executable,'-c',child,str(service.db.path),str(p)],capture_output=True,timeout=30)
    assert done.returncode==73,done.stderr.decode(errors='replace')
    restarted=DesktopService(service.db.path);receipt=history.latest(restarted.production)
    assert receipt['copied_count']==1 and not receipt['pending_count'] and not restarted.production.pending_imports()
    f=receipt['files'][0];assert f['state']=='complete' and Path(f['saved_path']).read_bytes()==b'B'
    assert len(restarted.production.rows())==1
