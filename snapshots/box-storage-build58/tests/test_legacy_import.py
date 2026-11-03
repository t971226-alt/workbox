from pathlib import Path
import json
import sqlite3
import pytest
from desktop_service import DesktopService
from legacy_import import LegacyImporter,digest
from requirement_intake import proposal

@pytest.fixture
def setup(tmp_path):
    old=DesktopService(tmp_path/'旧BOX/data/workbox.db')
    new=DesktopService(tmp_path/'新BOX/data/workbox.db')
    return old,new,LegacyImporter(new)

def seed(service,root,name='软件甲',batch='一批'):
    result=service.production.import_plan([proposal(name,batch=batch,requirement='单手册 Word')],token=batch)
    path=root/(name+'.docx');path.write_bytes(b'original material');service.receive_path(path)
    return result,service.production.rows()[0],path

def test_full_import_keeps_reviews_submission_time_files_and_current_records(setup,tmp_path):
    old,new,imp=setup
    _,task,path=seed(old,tmp_path);vid=task['materials'][0]['id']
    old.production.review(vid,True,'人工检查');old.production.submit([vid],note='已外部提交')
    new.production.import_plan([proposal('新版软件',batch='一批',requirement='单手册 Word')],token='current')
    before=new.production.rows();old_bytes=old.db.path.read_bytes();file_bytes=path.read_bytes()
    plan=imp.prepare(old.db.path)
    assert len(new.production.rows())==1 and not plan['missing'] and plan['batch_collisions']==1
    report=imp.import_plan(plan)
    rows=new.production.rows();assert len(rows)==2 and rows[0]==before[0]
    imported=next(r for r in rows if r['name']=='软件甲')
    assert imported['production_state']=='已提交' and imported['reviewed_count']==imported['submitted_count']==1
    assert Path(imported['materials'][0]['path']).read_bytes()==file_bytes
    assert not Path(imported['materials'][0]['path']).is_relative_to(old.db.path.parent)
    assert old.db.path.read_bytes()==old_bytes and path.read_bytes()==file_bytes
    assert report['complete'] and Path(report['backup']).is_file() and Path(report['original']).is_file()
    imp.import_plan(plan);assert len(new.production.rows())==2
    assert imp.prepare(old.db.path)['already_imported']
    with new.db.connect() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()

def test_legacy_only_files_become_inventory_without_fake_checks_or_delivery(setup,tmp_path):
    old,new,imp=setup
    source=tmp_path/'旧系统.docx';source.write_bytes(b'legacy');old.receive_file(source)
    pid=old.list_projects()[0]['id']
    with old.db.connect() as c:c.execute("UPDATE projects SET status='已交付',delivered_at='2024-01-02T00:00:00Z' WHERE id=?",(pid,))
    assert not old.production.rows()
    report=imp.import_plan(imp.prepare(old.db.path));row=new.production.rows()[0]
    assert row['collection_state']=='已收齐' and row['reviewed_count']==row['submitted_count']==0
    assert new.get_project(row['id'])['status']=='已交付'
    assert Path(row['materials'][0]['path']).read_bytes()==b'legacy'
    assert report['projects']==1 and new.production.rows()[0]['processing_mode']=='collect'

def test_missing_material_is_explicit_partial_and_cancel_preview_does_not_write(setup,tmp_path):
    old,new,imp=setup;_,task,_=seed(old,tmp_path)
    with old.db.connect() as c:
        paths={r[0] for table in ('work_files','material_versions','collected_materials') for r in c.execute('SELECT path FROM '+table)}
    for p in paths:
        if Path(p).exists():Path(p).unlink()
    plan=imp.prepare(old.db.path);assert plan['missing']
    with pytest.raises(ValueError,match='缺失'):imp.import_plan(plan)
    assert not new.production.rows() and not imp.reports()
    report=imp.import_plan(plan,allow_partial=True)
    assert not report['complete'] and report['missing'] and len(new.production.rows())==1

def test_current_or_non_box_database_is_rejected_without_writes(setup,tmp_path):
    old,new,imp=setup
    with pytest.raises(ValueError,match='当前'):imp.prepare(new.db.path)
    bad=tmp_path/'other.db'
    with sqlite3.connect(bad) as c:c.execute('CREATE TABLE unrelated(id INTEGER)')
    with pytest.raises(ValueError,match='BOX'):imp.prepare(bad)
    assert not new.production.rows()

def test_discovery_is_bounded_read_only_and_excludes_current(setup,tmp_path):
    old,new,imp=setup;found=imp.discover([tmp_path])
    assert [r['path'] for r in found]==[str(old.db.path)]

def test_migrated_chats_and_json_project_ids_follow_new_ids(setup,tmp_path):
    old,new,imp=setup;cid=old.intake_chat.create();tid=old.intake_chat.add(cid,'旧软件',[proposal('旧软件',batch='旧批',requirement='单手册 Word')]);old.intake_chat.create_tasks(tid)
    new.production.create_batch('新版批次',['占用编号'],['软件说明书.docx'])
    imp.import_plan(imp.prepare(old.db.path))
    with new.db.connect() as c:
        row=c.execute('SELECT * FROM intake_turns').fetchone();result=json.loads(row['result_json'])
        old_project=next(r for r in new.production.rows() if r['name']=='旧软件')
        assert result['project_ids']==[old_project['id']] and old_project['id']!=1

def test_source_changes_stop_before_merging_and_current_backup_exists(setup,tmp_path):
    old,new,imp=setup;_,task,_=seed(old,tmp_path)
    plan=imp.prepare(old.db.path);Path(task['materials'][0]['path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='变化|校验'):imp.import_plan(plan)
    assert not new.production.rows() and not imp.reports()

def test_custom_class_collision_renames_old_rule_without_modifying_current(setup,tmp_path):
    old,new,imp=setup
    old.classifications.save('验收',['旧报告.docx']);new.classifications.save('验收',['新报告.pdf'])
    old.production.import_plan([dict(proposal('旧软件',batch='旧批'),package='验收',kinds=['旧报告.docx'])],token='custom')
    imp.import_plan(imp.prepare(old.db.path))
    assert new.classifications.packages()['验收']==['新报告.pdf']
    imported=new.production.rows()[0];assert imported['package_name']!='验收' and imported['materials'][0]['kind']=='旧报告.docx'

def test_old_folder_moved_is_recovered_and_workspace_references_are_local(setup,tmp_path):
    import shutil
    old,new,imp=setup;_,task,_=seed(old,tmp_path)
    original_root=old.db.path.parent.parent;relocated=tmp_path/'搬过来的BOX'
    shutil.copytree(original_root,relocated)
    assert original_root.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(original_root)
    plan=imp.prepare(relocated/'data/workbox.db');assert not plan['missing'] and plan['aliases']
    report=imp.import_plan(plan);row=new.production.rows()[0]
    assert Path(row['materials'][0]['path']).read_bytes()==b'original material'
    with new.db.connect() as c:
        workspace=c.execute('SELECT * FROM project_workspaces WHERE project_id=?',(row['id'],)).fetchone()
        assert Path(workspace['path']).is_dir() and Path(workspace['path']).is_relative_to(new.db.path.parent)
    assert report['complete']

def test_version_inputs_reviews_and_archived_state_are_preserved(setup,tmp_path):
    old,new,imp=setup
    result=old.production.import_plan([proposal('设计系统',batch='旧批',requirement='五件材料')],token='five')
    for kind in ('软件说明书.docx','软件说明书.pdf'):
        p=tmp_path/('设计系统_'+kind);p.write_bytes(kind.encode());old.receive_path(p)
    before=old.production.rows()[0];old.production.review(before['materials'][0]['id'],True,'原检查')
    imp.import_plan(imp.prepare(old.db.path));after=new.production.rows()[0]
    assert after['reviewed_count']==1 and not next(m for m in after['materials'] if m['kind']=='软件说明书.pdf')['dependency_invalid']
    assert after['legacy_status']==old.get_project(before['id'])['status']

def test_current_inventory_adopts_without_duplicate_projects(setup,tmp_path):
    _,service,imp=setup
    p=tmp_path/'本机旧材料.docx';p.write_bytes(b'legacy-local');service.receive_file(p)
    pid=service.list_projects()[0]['id']
    with service.db.connect() as c:
        c.execute("UPDATE projects SET status='已交付',delivered_at='2024-01-02T00:00:00Z' WHERE id=?",(pid,))
        before=dict(c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone())
    plan=imp.prepare_current();assert not service.production.rows()
    report=imp.import_plan(plan);row=service.production.rows()[0]
    assert row['id']==pid and row['collection_state']=='已收齐' and row['reviewed_count']==row['submitted_count']==0
    assert report['current_inventory'] and Path(report['backup']).is_file()
    with service.db.connect() as c:assert dict(c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone())==before
    assert imp.import_plan(plan)==report and len(service.list_projects())==1
    with pytest.raises(ValueError,match='无需重复'):imp.prepare_current()


def test_current_inventory_missing_can_recover_from_moved_material(setup,tmp_path):
    _,service,imp=setup;p=tmp_path/'移位软件.docx';p.write_bytes(b'moved');service.receive_file(p)
    with service.db.connect() as c:paths=[r[0] for r in c.execute('SELECT path FROM work_files')]
    moved=tmp_path/'补充';moved.mkdir();(moved/'移位软件.docx').write_bytes(b'moved')
    for path in paths:Path(path).unlink()
    plan=imp.prepare_current();assert plan['missing']
    with pytest.raises(ValueError,match='缺失'):imp.import_plan(plan)
    plan=imp.prepare_current(material_root=moved);assert not plan['missing']
    assert imp.import_plan(plan)['complete'] and service.production.rows()[0]['collection_state']=='已收齐'
