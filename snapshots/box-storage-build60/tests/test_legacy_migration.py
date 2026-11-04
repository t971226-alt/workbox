from pathlib import Path
from threading import Event
import pytest
from desktop_service import DesktopService
from legacy_import import LegacyImporter
from native_files import ZipLease
from test_legacy_import import seed


@pytest.fixture
def case(tmp_path):
    old=DesktopService(tmp_path/'old/data/workbox.db');new=DesktopService(tmp_path/'new/data/workbox.db')
    _,task,original=seed(old,tmp_path)
    extra=old.db.path.parent/'其他文件.txt';extra.write_text('保留其他文件',encoding='utf-8')
    imp=LegacyImporter(new);plan=imp.prepare(old.db.path)
    assert plan['material_paths']
    return old,new,imp,plan,original,extra


def test_move_only_registered_materials_after_commit_and_keeps_database(case):
    old,new,imp,plan,original,extra=case
    report=imp.import_plan(plan,transfer_mode='move')
    assert report['cleanup_complete'] and report['moved_files']==len(plan['material_paths'])
    assert all(not Path(p).exists() for p in plan['material_paths'])
    assert original.exists() and extra.exists() and old.db.path.exists()
    assert all(Path(r['target']).is_file() for r in report['transfer_items'])
    assert new.production.rows()[0]['collection_state']=='已收齐'
    assert imp.import_plan(plan)==report and len(new.production.rows())==1
    assert imp.resume_migration(report['run_id'])['cleanup_complete']


def test_copy_default_and_later_explicit_migration_do_not_duplicate(case):
    old,new,imp,plan,original,extra=case
    report=imp.import_plan(plan)
    assert report['transfer_mode']=='copy' and all(Path(p).exists() for p in plan['material_paths'])
    report=imp.resume_migration(report['run_id'])
    assert report['cleanup_complete'] and len(new.production.rows())==1


def test_merge_failure_never_removes_originals(case,monkeypatch):
    _,new,imp,plan,_,_=case
    def fail(*args):raise RuntimeError('隔离写入失败')
    monkeypatch.setattr(imp,'_merge',fail)
    with pytest.raises(RuntimeError):imp.import_plan(plan,transfer_mode='move')
    assert all(Path(p).exists() for p in plan['material_paths']) and not new.production.rows()


@pytest.mark.parametrize('change',['source','target','database'])
def test_changed_source_target_or_database_preserves_original(case,monkeypatch,change):
    old,new,imp,plan,_,_=case;merge=imp._merge;first=plan['material_paths'][0]
    def changed(*args):
        merge(*args);report=args[3]
        if change=='source':Path(first).write_bytes(b'new contents')
        elif change=='target':Path(next(r['target'] for r in report['transfer_items'] if r['source']==first)).write_bytes(b'bad copy')
        else:
            with old.db.connect() as c:c.execute("UPDATE projects SET name='changed'")
    monkeypatch.setattr(imp,'_merge',changed)
    report=imp.import_plan(plan,transfer_mode='move')
    assert Path(first).exists() and report['cleanup_pending'] and new.production.rows()
    if change=='database':assert all(Path(p).exists() for p in plan['material_paths'])


def test_locked_file_is_retained_and_resume_only_cleans_remaining(case):
    _,new,imp,plan,_,_=case;first=plan['material_paths'][0]
    with ZipLease(first,movable=False):report=imp.import_plan(plan,transfer_mode='move')
    assert report['cleanup_pending']==1 and Path(first).exists()
    report=imp.resume_migration(report['run_id'])
    assert report['cleanup_complete'] and not Path(first).exists() and len(new.production.rows())==1


def test_cancel_during_cleanup_is_a_successful_import_and_can_resume(case):
    _,new,imp,plan,_,_=case;event=Event()
    # Progress during copying uses byte counts; cancellation starts only in cleanup.
    def progress(done,total):
        if total==len(plan['material_paths']) and done==1:event.set()
    report=imp.import_plan(plan,transfer_mode='move',cancel_event=event,progress=progress)
    assert new.production.rows() and report['cleanup_pending'] and report['moved_files']==1
    report=imp.resume_migration(report['run_id'])
    assert report['cleanup_complete'] and len(new.production.rows())==1


def test_missing_move_is_blocked_even_with_partial_permission(case):
    old,new,imp,_,_,_=case
    with old.db.connect() as c:paths={r[0] for t in ('work_files','material_versions','collected_materials') for r in c.execute('SELECT path FROM '+t)}
    for raw in paths:Path(raw).unlink(missing_ok=True)
    plan=imp.prepare(old.db.path);assert plan['missing']
    with pytest.raises(ValueError,match='齐全'):imp.import_plan(plan,allow_partial=True,transfer_mode='move')
    assert not new.production.rows()


def test_shared_current_material_is_not_removed(case,tmp_path):
    old,new,imp,plan,_,_=case;first=plan['material_paths'][0]
    p=tmp_path/'新版共享.docx';p.write_bytes(b'new');new.receive_file(p)
    with new.db.connect() as c:c.execute('UPDATE work_files SET path=?',(first,))
    report=imp.import_plan(plan,transfer_mode='move')
    assert Path(first).exists() and report['cleanup_pending']>=1


def test_old_app_running_blocks_move_before_merge(case):
    old,new,imp,plan,_,_=case;lock=Path(str(old.db.path)+'.session.lock');lock.write_text('isolated fixture')
    with pytest.raises(ValueError,match='退出旧 BOX'):imp.import_plan(plan,transfer_mode='move')
    assert all(Path(p).exists() for p in plan['material_paths']) and not new.production.rows()


def test_recovery_after_source_removed_before_progress_saved(case,monkeypatch):
    _,new,imp,plan,_,_=case
    remove=ZipLease.remove_after_verified_commit;once=[]
    def remove_then_interrupt(lease):
        remove(lease)
        if not once:once.append(True);raise RuntimeError('隔离中断：原件已移除，进度尚未登记')
    with monkeypatch.context() as patch:
        patch.setattr(ZipLease,'remove_after_verified_commit',remove_then_interrupt)
        report=imp.import_plan(plan,transfer_mode='move')
    assert report['cleanup_pending']==1 and all(not Path(p).exists() for p in plan['material_paths'])
    report=LegacyImporter(new).resume_migration(report['run_id'])
    assert report['cleanup_complete'] and len(new.production.rows())==1
