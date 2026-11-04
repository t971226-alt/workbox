import csv
from pathlib import Path
from threading import Event
import pytest
from desktop_service import DesktopService


@pytest.fixture
def prepared(tmp_path):
    service=DesktopService(tmp_path/'data/workbox.db')
    service.workspaces.configure_materials(tmp_path/'材料',complete_setup=True)
    result=service.production.create_batch('删除验证',['已完成软件','待做软件','保留软件'],['软件说明书.docx'])
    source=tmp_path/'已完成软件.docx';source.write_bytes(b'keep contents')
    service.production.receive(result['project_ids'][0],'软件说明书.docx',source)
    service.production.set_batch_mode(result['batch_id'],'review')
    vid=service.production.rows()[0]['materials'][0]['id'];service.production.review(vid,True);service.production.submit([vid])
    return service,result


def test_delete_removes_from_master_export_and_statistics_but_preserves_history(prepared,tmp_path):
    service,batch=prepared;p=service.production;before=p.rows();first,second,third=batch['project_ids']
    files={path:path.read_bytes() for row in before if row['materials'][0]['id'] for path in (Path(row['materials'][0]['path']),Path(row['collection_folder'])/row['materials'][0]['filename'])}
    def history():
        with service.db.connect() as c:
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ('material_versions','material_reviews','material_submissions','material_submission_items','collected_materials')}
    saved=history();day=p.local_date(before[0]['created_at'])
    assert p.delete_tasks([first,second],confirmed=True)=={'deleted_count':2}
    assert [r['id'] for r in p.rows()]==[third]
    assert p.statistics(day)['added']==1 and p.statistics(day)['submitted']==0
    assert {r['id'] for r in p.deleted_rows()}=={first,second}
    target=tmp_path/'软件材料总表.csv';p.export_master(target)
    with target.open(encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f));assert [r['软件名称'] for r in rows]==['保留软件']
    assert saved==history() and all(path.read_bytes()==data for path,data in files.items())
    assert p.delete_tasks([first,second],confirmed=True)=={'deleted_count':0}
    assert p.restore_tasks([first,second])=={'restored_count':2}
    assert p.rows()==before and not p.deleted_rows() and history()==saved
    assert p.restore_tasks([first])=={'restored_count':0}


def test_confirmation_invalid_and_cancelled_bulk_never_partially_delete(prepared):
    service,batch=prepared;p=service.production;pid=batch['project_ids'][0];before=p.rows()
    with pytest.raises(ValueError):p.delete_tasks([pid])
    with pytest.raises(ValueError):p.delete_tasks([pid,999999],confirmed=True)
    assert p.rows()==before and not p.deleted_rows()
    event=Event();event.set()
    with pytest.raises(Exception):p.delete_tasks([pid],confirmed=True,cancel_event=event)
    assert p.rows()==before and not p.deleted_rows()


def test_batch_can_change_mode_after_one_task_is_deleted(prepared):
    service,batch=prepared;p=service.production
    p.delete_tasks([batch['project_ids'][1]],confirmed=True)
    p.set_batch_mode(batch['batch_id'],'collect')
    assert all(r['processing_mode']=='collect' for r in p.rows())
    assert len(p.deleted_rows())==1
