from datetime import date
from threading import Event
from concurrent.futures import CancelledError

import pytest

from desktop_service import DesktopService
from requirement_intake import proposal
from workspace_queries import parse_query, answer


DAY = date(2026, 10, 29)


@pytest.mark.parametrize('raw', ['查询管理系统', '统计分析软件', '项目：甲方', '1. 库存系统', '库存系统\n客户系统'])
def test_software_intake_is_not_consumed_as_a_query(raw):
    assert parse_query(raw, DAY) is None


@pytest.mark.parametrize('raw,name', [('查找 统计分析系统', '统计分析系统'),
    ('查一下 建筑检测管理系统的提交情况', '建筑检测管理系统'),
    ('搜索 "检查管理软件"', '检查管理软件')])
def test_explicit_software_lookup_preserves_name_and_includes_unfinished(raw, name):
    spec = parse_query(raw, DAY)
    assert spec['intent'] == 'records' and spec['name'] == name
    assert spec['state'] == spec['metric'] == ''


@pytest.mark.parametrize('raw,state', [('哪些软件还没检查？', '待检查'), ('查一下已提交的软件', '已提交'),
    ('统计未提交的任务有多少', '未提交'), ('哪些材料还没收齐', '未收齐')])
def test_status_queries_do_not_search_for_leftover_grammar(raw, state):
    spec = parse_query(raw, DAY)
    assert spec['state'] == state and not spec['name']


def test_query_grammar_ending_in_software_is_not_mistaken_for_a_name():
    spec = parse_query('统计今天新增了多少个软件', DAY)
    assert spec['intent'] == 'statistics' and spec['metric'] == 'added' and spec['name'] == ''
    assert parse_query('显示已提交的软件', DAY)['state'] == '已提交'


@pytest.fixture
def service(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.production.import_plan([proposal(n, batch='第一批', requirement='单手册 Word')
        for n in ('统计分析系统', '已交付系统', '空白系统')], token='first')
    for name in ('统计分析系统', '已交付系统'):
        source = tmp_path / (name + '.docx')
        source.write_bytes(name.encode())
        service.receive_path(source)
    rows = service.production.rows()
    vid = rows[1]['materials'][0]['id']
    service.production.review(vid, True)
    service.production.submit([vid])
    with service.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-09-30T15:59:00Z'")
        c.execute("UPDATE collected_materials SET collected_at='2026-09-30T16:01:00Z'")
        c.execute("UPDATE material_reviews SET created_at='2026-10-01T16:01:00Z'")
        c.execute("UPDATE material_submissions SET created_at='2026-10-02T16:01:00Z'")
    return service


def ask(service, raw):
    return answer(service.production, parse_query(raw, DAY))


def test_results_and_statistics_use_each_actual_event_date(service):
    assert ask(service, '查询 2026-10-01 收齐了多少')['count'] == 2
    assert ask(service, '查询 2026-10-02 检查通过多少')['count'] == 1
    assert ask(service, '查询 2026-10-03 提交了多少')['count'] == 1
    assert ask(service, '本月提交了多少？')['count'] == 1
    assert ask(service, '今天收齐了多少？')['count'] == 0
    assert ask(service, '统计 2026年10月 新增多少')['count'] == 0
    assert ask(service, '昨天收齐了多少？')['spec']['day'] == '2026-10-28'
    assert ask(service, '上月新增了多少？')['count'] == 3
    assert parse_query('查询 2026-02-30 收齐了多少', DAY)['intent'] == 'help'


def test_queries_do_not_change_material_review_submission_or_names(service):
    before = service.production.rows()
    assert ask(service, '哪些软件还没检查？')['matched_project_ids'] == [before[0]['id']]
    assert ask(service, '查找 统计分析系统的提交情况')['count'] == 1
    assert ask(service, '哪些软件未提交')['count'] == 2
    assert ask(service, '查找 不存在系统')['count'] == 0
    assert ask(service, '显示全部')['count'] == 3
    for raw in ('删除全部软件', '帮我提交所有任务', '把所有任务标记已提交'):
        assert ask(service, raw)['spec']['intent'] == 'unsupported'
    assert service.production.rows() == before


def test_query_history_reopens_and_rerun_can_read_new_material_state(service, tmp_path):
    chat = service.intake_chat
    cid = chat.create()
    tid = chat.begin_query(cid, '哪些软件还没检查？', parse_query('哪些软件还没检查？', DAY))
    first = chat.run_query(tid)['query_result']
    assert first['count'] == 1 and first['as_of']
    reopened = DesktopService(service.db.path)
    assert reopened.intake_chat.turn(tid)['result'] == first
    vid = service.production.rows()[0]['materials'][0]['id']
    service.production.review(vid, True)
    chat.restart_query(tid, parse_query('哪些软件还没检查？', DAY))
    assert chat.run_query(tid)['query_result']['count'] == 0
    assert chat.count(cid) == 1 and len(service.production.rows()) == 3


def test_cancel_and_interrupted_queries_are_retryable_without_creating_tasks(service):
    chat = service.intake_chat
    cid = chat.create()
    spec = parse_query('显示全部', DAY)
    tid = chat.begin_query(cid, '显示全部', spec)
    event = Event()
    event.set()
    with pytest.raises(CancelledError):
        chat.run_query(tid, cancel_event=event)
    assert chat.turn(tid)['state'] == 'failed'
    chat.restart_query(tid, spec)
    chat.recover()
    assert chat.turn(tid)['state'] == 'failed'
    chat.restart_query(tid, spec)
    assert chat.run_query(tid)['query_result']['count'] == 3
