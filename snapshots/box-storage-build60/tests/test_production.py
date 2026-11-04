import csv
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def batch(service, name='第一批', names=None, kinds=None):
    return service.production.create_batch(name, names or ['库存系统'], kinds or ['说明书', '源代码'])


def receive(service, tmp_path, pid, kind, content='内容', filename=None):
    source = tmp_path / '来源' / (filename or f'库存系统_{kind}.txt')
    source.parent.mkdir(exist_ok=True)
    source.write_text(content, encoding='utf-8')
    service.production.receive(pid, kind, source)
    bid = service.get_project(pid)['batch_id']
    return next(m for r in service.production.rows(bid) if r['id'] == pid for m in r['materials'] if m['kind'] == kind)


def ready(service, tmp_path, pid):
    for kind in ('说明书', '源代码'):
        m = receive(service, tmp_path, pid, kind)
        service.production.review(m['id'], True)
    return [m['id'] for r in service.production.rows(service.get_project(pid)['batch_id']) if r['id'] == pid for m in r['materials']]


def test_repeat_names_create_independent_tasks_and_directories(service, tmp_path):
    a = batch(service)
    pa = a['project_ids'][0]
    vids = ready(service, tmp_path, pa)
    service.production.submit_projects([pa], vids)
    b = batch(service, '第二批')
    pb = b['project_ids'][0]
    assert pa != pb
    assert service.workspaces.get_workspace(pa)['path'] != service.workspaces.get_workspace(pb)['path']
    assert service.production.rows(a['batch_id'])[0]['production_state'] == '已提交'
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '材料未齐'
    choices = service.file_routing(tmp_path / '库存系统_操作手册.docx')
    assert len(choices['candidates']) == 2
    with pytest.raises(ValueError, match='批次名称已存在'):
        batch(service)
    assert len(service.list_projects()) == 2


def test_content_duplicate_preserves_review_submission_and_history(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    vids = ready(service, tmp_path, pid)
    service.production.submit(vids)
    m = receive(service, tmp_path, pid, '说明书')
    assert m['state'] == '已提交'
    assert m['version_number'] == 1
    assert service.get_project(pid)['status'] == '已交付'
    assert service.production.submit(vids)['submitted_count'] == 0
    assert len(service.production.history(pid, '说明书')[0]['submissions']) == 1


def test_new_version_resets_only_changed_material_and_keeps_archived_old_copy(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    vids = ready(service, tmp_path, pid)
    service.production.submit(vids)
    changed = receive(service, tmp_path, pid, '说明书', '重新制作')
    row = service.production.rows(b['batch_id'])[0]
    assert changed['state'] == '待检查'
    assert changed['version_number'] == 2
    assert row['materials'][1]['state'] == '已提交'
    assert row['production_state'] == '部分提交'
    history = service.production.history(pid, '说明书')
    assert Path(history[-1]['path']).read_text(encoding='utf-8') == '内容'
    assert history[-1]['submissions']
    with pytest.raises(ValueError, match='已更新'):
        service.production.review(vids[0], True)
    with pytest.raises(ValueError, match='变化'):
        service.production.submit_projects([pid], vids)


def test_batch_submission_is_atomic_and_requires_review(service, tmp_path):
    b = batch(service, names=['库存系统', '客户系统'])
    a, c = b['project_ids']
    ids = ready(service, tmp_path, a)
    other = receive(service, tmp_path, c, '说明书')
    with pytest.raises(ValueError, match='未齐'):
        service.production.submit_projects([a, c], ids + [other['id']])
    with pytest.raises(ValueError, match='尚未检查通过'):
        service.production.submit(ids + [other['id']])
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '待提交'
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM material_submissions').fetchone()[0] == 0


def test_partial_submission_review_failure_retry_and_void(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    m = receive(service, tmp_path, pid, '说明书')
    service.production.review(m['id'], False, '需要补图')
    assert service.production.rows(b['batch_id'])[0]['materials'][0]['state'] == '需修改'
    with pytest.raises(ValueError):
        service.production.submit([m['id']])
    service.production.review(m['id'], True, '已检查')
    result = service.production.submit([m['id']])
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '部分提交'
    service.production.void_submission(result['submission_id'], '误勾')
    current = service.production.rows(b['batch_id'])[0]['materials'][0]
    assert current['state'] == '检查通过'
    assert service.production.history(pid, '说明书')[0]['submissions'][0]['void_reason'] == '误勾'
    service.production.submit([m['id']])
    assert len(service.production.history(pid, '说明书')[0]['submissions']) == 2


def test_modified_archive_cannot_be_reviewed_or_submitted(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    m = receive(service, tmp_path, pid, '说明书')
    service.production.review(m['id'], True)
    Path(m['path']).write_text('被修改', encoding='utf-8')
    with pytest.raises(ValueError, match='归档已变化'):
        service.production.submit([m['id']])
    with pytest.raises(ValueError, match='归档已变化'):
        service.production.review(m['id'], True)


def test_zip_count_cannot_complete_checklist_or_bypass_review(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    source = tmp_path / '库存系统.zip'
    with ZipFile(source, 'w') as archive:
        for i in range(8):
            archive.writestr(f'{i}.txt', '材料')
    result = service.import_zip(source)
    assert service.get_project(pid)['status'] != '已交付'
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '材料未齐'
    with pytest.raises(ValueError, match='材料清单'):
        service.confirm_delivery(pid, result['zip']['id'], confirmed=True)


def test_inferred_type_and_explicit_binding_are_versioned(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    source = tmp_path / '库存系统_说明书.txt'
    source.write_text('一', encoding='utf-8')
    service.workspaces.import_file(source, pid)
    assert service.production.rows(b['batch_id'])[0]['materials'][0]['state'] == '待检查'
    source = tmp_path / 'generic.txt'
    source.write_text('二', encoding='utf-8')
    saved = service.workspaces.import_file(source, pid)
    service.production.bind(saved['file']['id'], '源代码')
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '待检查'


def test_reuse_same_bytes_new_round_does_not_inherit_confirmation(service, tmp_path):
    a = batch(service)
    pid = a['project_ids'][0]
    ids = ready(service, tmp_path, pid)
    service.production.submit(ids)
    old = service.production.rows(a['batch_id'])[0]['materials'][0]
    b = batch(service, '第二轮')
    service.production.receive(b['project_ids'][0], '说明书', old['path'])
    new = service.production.rows(b['batch_id'])[0]['materials'][0]
    assert new['state'] == '待检查'
    assert new['sha256'] == old['sha256']
    assert new['path'] != old['path']


def test_persistence_configuration_export_and_old_data(service, tmp_path):
    old = service.capture_message('原有系统', '旧批次')
    service.production.configure(old['batch_id'], ['操作手册'])
    m = receive(service, tmp_path, old['project_ids'][0], '操作手册')
    service.production.review(m['id'], True, '=HYPERLINK("bad")')
    with pytest.raises(ValueError, match='已有材料版本'):
        service.production.configure(old['batch_id'], ['其他'])
    reopened = DesktopService(service.db.path)
    assert reopened.production.rows(old['batch_id'])[0]['production_state'] == '待提交'
    target = tmp_path / '表.csv'
    reopened.production.export(old['batch_id'], target)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        data = list(csv.reader(stream))
    assert data[1][8].startswith("'=")
    assert reopened.db.counts()['projects'] == 1


def test_cancellation_keeps_review_and_submission_unwritten(service, tmp_path):
    b = batch(service)
    m = receive(service, tmp_path, b['project_ids'][0], '说明书')
    event = Event()
    event.set()
    from concurrent.futures import CancelledError
    with pytest.raises(CancelledError):
        service.production.review(m['id'], True, cancel_event=event)
    assert service.production.rows(b['batch_id'])[0]['materials'][0]['state'] == '待检查'


def test_archived_task_cannot_review_or_submit(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    ids = ready(service, tmp_path, pid)
    service.bulk_update_projects([pid], archived=True)
    with pytest.raises(ValueError, match='恢复归档'):
        service.production.submit(ids)
    assert service.production.rows(b['batch_id']) == []


def test_regenerated_work_file_waits_stable_and_blocks_stale_confirmation(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    ids = ready(service, tmp_path, pid)
    service.production.submit(ids)
    with service.db.connect() as connection:
        work = connection.execute('''SELECT f.path FROM work_files f JOIN material_versions v
            ON f.id=v.work_file_id WHERE v.id=?''', (ids[0],)).fetchone()[0]
    Path(work).write_text('重新生成的说明书', encoding='utf-8')
    with pytest.raises(ValueError, match='重新生成'):
        service.production.submit(ids)
    assert service.production.scan()['changed'] == 0
    service.production._observed = {k: v - 20 for k, v in service.production._observed.items()}
    assert service.production.scan()['changed'] == 1
    row = service.production.rows(b['batch_id'])[0]
    assert row['materials'][0]['state'] == '待检查'
    assert row['materials'][1]['state'] == '已提交'
    assert service.production.scan()['changed'] == 0


def test_void_one_material_does_not_void_other_batch_items(service, tmp_path):
    b = batch(service)
    pid = b['project_ids'][0]
    ids = ready(service, tmp_path, pid)
    result = service.production.submit(ids)
    service.production.void_submission(result['submission_id'], '只撤销说明书', version_ids=[ids[0]])
    states = [m['state'] for m in service.production.rows(b['batch_id'])[0]['materials']]
    assert states == ['检查通过', '已提交']


def test_custom_type_routes_and_typed_zip_is_material(service, tmp_path):
    b = batch(service, kinds=['验收记录', '源代码'])
    pid = b['project_ids'][0]
    source = tmp_path / '库存系统_验收记录.txt'
    source.write_text('验收内容', encoding='utf-8')
    service.receive_path(source)
    assert service.production.rows(b['batch_id'])[0]['materials'][0]['state'] == '待检查'
    zipped = tmp_path / '库存系统_源代码.zip'
    with ZipFile(zipped, 'w') as archive:
        archive.writestr('main.py', 'print(1)')
    service.receive_path(zipped)
    assert service.production.rows(b['batch_id'])[0]['materials'][1]['state'] == '待检查'
    assert len(service.list_projects()) == 1


def test_submission_failure_rolls_back_whole_batch(service, tmp_path):
    b = batch(service)
    ids = ready(service, tmp_path, b['project_ids'][0])
    with service.db.connect() as connection:
        connection.execute(f'''CREATE TRIGGER fail_second_item BEFORE INSERT ON material_submission_items
            WHEN NEW.version_id={ids[1]} BEGIN SELECT RAISE(ABORT,'injected failure'); END''')
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        service.production.submit(ids)
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM material_submissions').fetchone()[0] == 0
    assert service.production.rows(b['batch_id'])[0]['production_state'] == '待提交'
