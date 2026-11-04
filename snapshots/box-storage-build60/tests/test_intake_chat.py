import json
from pathlib import Path
from threading import Event
from concurrent.futures import CancelledError

import pytest

from desktop_service import DesktopService
from requirement_intake import proposal


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def turn(service, *, batch='第一批', requirement='五件材料'):
    cid = service.intake_chat.create()
    rows = [proposal('库存系统', commission='甲委托', batch=batch, requirement=requirement)]
    tid = service.intake_chat.add(cid, '原始软件需求\n保留完整原文', rows)
    return cid, tid


def test_preview_is_persistent_and_creates_no_tasks(service):
    cid, tid = turn(service)
    assert service.db.counts()['projects'] == 0
    reopened = DesktopService(service.db.path)
    row = reopened.intake_chat.turns(cid)[0]
    assert row['id'] == tid and row['state'] == 'needs-review'
    assert row['source_text'] == '原始软件需求\n保留完整原文'
    assert len(row['rows'][0]['kinds']) == 5


def test_draft_history_and_defaults_survive_reopen(service):
    cid = service.intake_chat.create('第一段')
    service.intake_chat.save_draft(cid, '还没整理的需求\n第二行', dict(default_commission='委托', default_requirement='单手册 Word'))
    second = service.intake_chat.create('第二段')
    service.intake_chat.select(cid)
    row = DesktopService(service.db.path).intake_chat.active()
    assert row['draft_text'] == '还没整理的需求\n第二行'
    assert json.loads(row['defaults_json'])['default_requirement'] == '单手册 Word'
    assert {r['id'] for r in service.intake_chat.conversations()} == {cid, second}


def test_confirm_reuses_same_import_token_and_is_idempotent(service):
    cid, tid = turn(service)
    result = service.intake_chat.create_tasks(tid)
    again = service.intake_chat.create_tasks(tid)
    assert result['project_ids'] == again['project_ids']
    assert result['conversation_turn_id'] == tid
    assert service.db.counts()['projects'] == 1
    assert service.intake_chat.turn(tid)['state'] == 'committed'
    with pytest.raises(ValueError):
        service.intake_chat.edit(tid, [proposal('改名', batch='其他')])


def test_conflict_can_be_edited_and_retried_without_losing_original(service):
    _, one = turn(service)
    service.intake_chat.create_tasks(one)
    _, two = turn(service)
    with pytest.raises(ValueError, match='已存在'):
        service.intake_chat.create_tasks(two)
    assert service.intake_chat.turn(two)['state'] == 'failed'
    rows = service.intake_chat.turn(two)['rows']
    rows[0]['batch'] = '第二批'
    service.intake_chat.edit(two, rows)
    service.intake_chat.create_tasks(two)
    assert service.db.counts()['projects'] == 2
    assert service.intake_chat.turn(two)['source_text'].startswith('原始软件需求')


def test_cancelled_preview_stays_in_history_without_creating_tasks(service):
    cid, tid = turn(service)
    service.intake_chat.discard(tid)
    assert service.intake_chat.turns(cid)[0]['state'] == 'discarded'
    with pytest.raises(ValueError, match='取消'):
        service.intake_chat.create_tasks(tid)
    assert service.db.counts()['projects'] == 0


def test_recover_interrupted_creation_and_saved_metadata(service):
    _, pending = turn(service, batch='中断')
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_turns SET state='creating' WHERE id=?", (pending,))
    service.intake_chat.recover()
    assert service.intake_chat.turn(pending)['state'] == 'failed'
    _, saved = turn(service, batch='已建立')
    row = service.intake_chat.turn(saved)
    result = service.production.import_plan(row['rows'], token=row['token'])
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_turns SET state='creating' WHERE id=?", (saved,))
    service.intake_chat.recover()
    assert service.intake_chat.turn(saved)['state'] == 'committed'
    assert service.intake_chat.turn(saved)['result']['project_ids'] == result['project_ids']
    service.intake_chat.create_tasks(saved)
    assert service.db.counts()['projects'] == 1


def test_cancellation_before_start_does_not_create_tasks(service):
    _, tid = turn(service)
    event = Event()
    event.set()
    with pytest.raises(CancelledError):
        service.intake_chat.create_tasks(tid, cancel_event=event)
    assert service.intake_chat.turn(tid)['state'] == 'needs-review'
    assert service.db.counts()['projects'] == 0


def test_unknown_requirements_are_preserved_in_created_task(service):
    _, tid = turn(service, requirement='待确认')
    result = service.intake_chat.create_tasks(tid)
    assert service.production.rows(result['batch_ids'][0])[0]['production_state'] == '要求待确认'


def test_editing_preserves_folder_source_file_association(service, tmp_path):
    cid, tid = turn(service)
    rows = service.intake_chat.turn(tid)['rows']
    path = tmp_path / '软件说明书.docx'
    path.write_bytes(b'local source')
    rows[0]['source_files'] = [str(path)]
    service.intake_chat.edit(tid, rows)
    assert service.db.counts()['projects'] == 0
    result = service.intake_chat.create_tasks(tid)
    assert result['copied_count'] == 1
    assert path.read_bytes() == b'local source'


def test_history_paginates_without_deleting_older_turns(service):
    cid = service.intake_chat.create()
    for i in range(23):
        service.intake_chat.add(cid, str(i), [proposal('系统', batch=str(i))])
    assert service.intake_chat.count(cid) == 23
    assert len(service.intake_chat.turns(cid)) == 20
    assert service.intake_chat.turns(cid, 40)[0]['source_text'] == '0'


def test_file_preview_does_not_erase_unsent_text_draft(service):
    cid = service.intake_chat.create()
    service.intake_chat.save_draft(cid, '尚未发出的另一批需求', {})
    service.intake_chat.add(cid, '需求表.xlsx', [proposal('表格系统', batch='表格')], source_kind='sheet')
    assert service.intake_chat.select(cid)['draft_text'] == '尚未发出的另一批需求'


def test_failure_after_metadata_commit_keeps_existing_tasks_and_link(service, monkeypatch):
    _, tid = turn(service)
    original = service.production.import_plan
    def fail_after_save(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('模拟后续文件处理中断')
    monkeypatch.setattr(service.production, 'import_plan', fail_after_save)
    with pytest.raises(OSError):
        service.intake_chat.create_tasks(tid)
    assert service.intake_chat.turn(tid)['state'] == 'committed'
    assert service.intake_chat.turn(tid)['result']['project_ids']
    service.intake_chat.create_tasks(tid)
    assert service.db.counts()['projects'] == 1
