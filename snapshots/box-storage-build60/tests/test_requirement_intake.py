from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from material_templates import PRESETS
from requirement_intake import (parse_text, package_hint, proposal, validate_rows, read_sheet,
    write_template, folder_proposals)


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


RAW = '''项目：建筑检测材料
第一批：只做说明书Word
1. 建筑检测管理系统
2. 工程质量分析软件

项目：设备监测材料
第二批：14件材料＋两个视频
1. 设备异常监测系统
2. 能耗分析系统，其中这个只做五件材料
'''


def test_parse_multiple_groups_batches_and_individual_exception():
    rows, notes = parse_text(RAW)
    assert len(rows) == 4
    assert [len(r['kinds']) for r in rows] == [1, 1, 16, 5]
    assert rows[3]['name'] == '能耗分析系统'
    assert rows[3]['commission'] == '设备监测材料'
    assert not notes


def test_unknown_requirements_and_prose_are_not_guessed():
    rows, notes = parse_text('项目：临时委托\n批次：单项\n软件名称：库存系统\n尽快做好发给我')
    assert len(rows) == 1
    assert rows[0]['kinds'] == [] and rows[0]['package'] == '待确认'
    assert notes
    assert package_hint('随便做几份') == ('待确认', [])


@pytest.mark.parametrize('label,count', [('单手册 Word', 1), ('五件材料', 5), ('桌面14件', 14), ('14件＋双视频', 16)])
def test_all_presets_preserve_format_identity(label, count):
    package, kinds = package_hint(label)
    assert package == label and len(kinds) == count
    if count >= 5:
        assert '软件说明书.docx' in kinds and '软件说明书.pdf' in kinds


def test_atomic_import_and_retry_do_not_duplicate_tasks(service):
    rows, _ = parse_text(RAW)
    result = service.production.import_plan(rows, token='same-request', source_text=RAW)
    again = service.production.import_plan(rows, token='same-request', source_text=RAW)
    assert again['project_ids'] == result['project_ids']
    assert service.db.counts()['projects'] == 4
    assert len(result['batch_ids']) == 2
    a, b = result['batch_ids']
    assert [r['required_count'] for r in service.production.rows(a)] == [1, 1]
    assert [r['required_count'] for r in service.production.rows(b)] == [16, 5]
    assert service.production.rows(b)[1]['commission'] == '设备监测材料'


def test_duplicate_names_rejected_in_batch_but_allowed_across_groups(service):
    rows = [proposal('同名系统', commission=c, batch='第一批', requirement='单手册') for c in ['甲委托', '乙委托']]
    result = service.production.import_plan(rows, token='groups')
    assert len(result['batch_ids']) == 2
    assert len(service.file_routing('D:/同名系统_软件说明书.docx')['candidates']) == 2
    with pytest.raises(ValueError, match='重复'):
        validate_rows([rows[0], rows[0]])


def test_existing_batch_conflict_rolls_back_all_proposed_batches(service):
    row = proposal('旧系统', batch='已存在', requirement='单手册')
    service.production.import_plan([row], token='old')
    new = [proposal('新系统', batch='新批次', requirement='五件材料'), row]
    with pytest.raises(ValueError, match='已存在'):
        service.production.import_plan(new, token='new')
    assert service.db.counts()['projects'] == 1
    assert len(service.list_batches()) == 1


def test_template_xlsx_and_csv_read_and_missing_requirements(tmp_path):
    for extension in ('xlsx', 'csv'):
        target = tmp_path / ('模板.' + extension)
        write_template(target)
        rows, _ = read_sheet(target)
        assert [len(r['kinds']) for r in rows] == [1, 16, 5]
    missing = tmp_path / '缺要求.csv'
    missing.write_text('批次,软件名称\n单项,库存系统\n', encoding='utf-8-sig')
    rows, _ = read_sheet(missing)
    assert rows[0]['package'] == '待确认'


def test_xlsx_formula_and_wrong_headers_rejected(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(['软件名称', '批次', '材料要求'])
    sheet.append(['=HYPERLINK("x")', '批次', '五件材料'])
    target = tmp_path / '公式.xlsx'
    book.save(target)
    book.close()
    with pytest.raises(ValueError, match='公式'):
        read_sheet(target)
    bad = tmp_path / '错误.csv'
    bad.write_text('不是名称,批次\na,b', encoding='utf-8-sig')
    with pytest.raises(ValueError, match='软件名称'):
        read_sheet(bad)


def test_folder_inventory_does_not_infer_requirement_or_copy_before_confirm(service, tmp_path):
    root = tmp_path / '来件'
    for name in ('甲管理系统', '乙管理系统'):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / (name + '.docx')).write_bytes(b'original')
    rows, notes = folder_proposals(root, default_batch='文件夹批次')
    assert len(rows) == 2 and all(not r['kinds'] for r in rows)
    assert service.db.counts()['projects'] == 0
    assert notes
    for row in rows:
        row.update(package='单手册 Word', kinds=list(PRESETS['单手册 Word']))
    result = service.production.import_plan(rows, token='folder')
    assert result['copied_count'] == 2 and not result['intake_errors']
    for row in service.production.rows(result['batch_ids'][0]):
        assert row['materials'][0]['state'] == '待检查'
    assert len(list(root.rglob('*.docx'))) == 2


def test_unknown_task_is_managed_and_cannot_auto_complete(service, tmp_path):
    result = service.production.import_plan([proposal('未知系统', batch='待补充')], token='unknown')
    pid = result['project_ids'][0]
    assert service.production.managed(pid)
    assert service.production.rows(result['batch_ids'][0])[0]['production_state'] == '要求待确认'
    source = tmp_path / '未知系统.zip'
    with ZipFile(source, 'w') as archive:
        for i in range(8):
            archive.writestr(str(i) + '.txt', '文件')
    service.import_zip(source)
    assert service.get_project(pid)['status'] != '已交付'
    with pytest.raises(ValueError, match='未齐'):
        service.production.submit_projects([pid], [])


def test_per_task_changes_do_not_change_sibling_or_template(service):
    b = service.production.create_batch('两项同批', ['甲系统', '乙系统'], PRESETS['单手册 Word'])
    a, c = b['project_ids']
    service.production.configure_task(a, PRESETS['14件＋双视频'], reason='追加完整交付')
    assert [r['required_count'] for r in service.production.rows(b['batch_id'])] == [16, 1]
    reopened = DesktopService(service.db.path)
    assert [r['required_count'] for r in reopened.production.rows(b['batch_id'])] == [16, 1]
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM requirement_changes WHERE project_id=?', (a,)).fetchone()[0] == 2


def test_word_changes_invalidate_pdf_without_erasing_history(service, tmp_path):
    b = service.production.create_batch('文档依赖', ['说明系统'], ['软件说明书.docx', '软件说明书.pdf'])
    pid = b['project_ids'][0]
    for name in ['软件说明书.docx', '软件说明书.pdf']:
        path = tmp_path / name
        path.write_bytes(b'first')
        service.production.receive(pid, name, path)
    materials = service.production.rows(b['batch_id'])[0]['materials']
    for m in materials:
        service.production.review(m['id'], True)
    service.production.submit([m['id'] for m in materials])
    word = tmp_path / '软件说明书.docx'
    word.write_bytes(b'new word')
    service.production.receive(pid, '软件说明书.docx', word)
    row = service.production.rows(b['batch_id'])[0]
    assert row['materials'][1]['state'] == '待更新'
    assert row['submitted_count'] == 0
    assert service.production.history(pid, '软件说明书.pdf')[0]['submissions']
    with pytest.raises(ValueError, match='Word已更新'):
        service.production.submit([materials[1]['id']])
    service.production.receive(pid, '软件说明书.pdf', tmp_path / '软件说明书.pdf')
    refreshed = service.production.rows(b['batch_id'])[0]['materials'][1]
    assert refreshed['state'] == '待检查' and refreshed['version_number'] == 2


def test_removed_requirement_retains_history_but_cannot_submit(service, tmp_path):
    b = service.production.create_batch('删减要求', ['任务系统'], ['说明书', '源代码'])
    pid = b['project_ids'][0]
    path = tmp_path / '代码.txt'
    path.write_text('code', encoding='utf-8')
    service.production.receive(pid, '源代码', path)
    version = service.production.rows(b['batch_id'])[0]['materials'][1]['id']
    service.production.configure_task(pid, ['说明书'], reason='本次取消代码要求')
    assert service.production.history(pid, '源代码')
    with pytest.raises(ValueError, match='不属于本次要求'):
        service.production.review(version, True)


def test_legacy_batch_migrates_without_changing_saved_versions(service, tmp_path):
    capture = service.capture_message('迁移系统', '迁移批次')
    pid = capture['project_ids'][0]
    with service.db.connect() as connection:
        connection.execute('INSERT INTO production_kinds VALUES (?,?,?)', (capture['batch_id'], '说明书', 0))
    reopened = DesktopService(service.db.path)
    assert reopened.production.task_kinds(pid) == ['说明书']
    reopened.production.configure_task(pid, [], reason='要求暂不明确')
    reopened = DesktopService(service.db.path)
    assert reopened.production.task_kinds(pid) == []


def test_batch_default_never_overwrites_individual_snapshots(service):
    b = service.production.create_batch('默认变更', ['甲系统', '乙系统'], PRESETS['单手册 Word'])
    service.production.configure_task(b['project_ids'][0], PRESETS['五件材料'], reason='单项需要五件')
    service.production.configure(b['batch_id'], PRESETS['14件＋双视频'])
    assert [r['required_count'] for r in service.production.rows(b['batch_id'])] == [5, 1]


def test_unspecified_video_count_and_pasted_table_remain_precise():
    assert package_hint('14件材料加视频') == ('待确认', [])
    assert package_hint('14件材料加一个视频') == ('待确认', [])
    rows, _ = parse_text('委托项目\t批次\t软件名称\t材料要求\n甲委托\t第一批\t库存系统\t五件材料')
    assert rows[0]['name'] == '库存系统' and len(rows[0]['kinds']) == 5


def test_failed_folder_file_can_resume_without_new_tasks_or_losing_success(service, tmp_path, monkeypatch):
    root = tmp_path / '重试系统'
    root.mkdir()
    (root / '软件说明书.docx').write_bytes(b'word')
    (root / '软件说明书.pdf').write_bytes(b'pdf')
    rows, _ = folder_proposals(root, default_batch='可恢复批次', default_requirement='五件材料')
    original = service.production.receive
    def fail_one(pid,kind,path,*args, **kwargs):
        if str(path).endswith('.pdf'):
            raise OSError('模拟文件被占用')
        return original(pid,kind,path, *args, **kwargs)
    monkeypatch.setattr(service.production, 'receive', fail_one)
    result = service.production.import_plan(rows, token='resumable')
    assert result['copied_count'] == 1 and result['intake_errors']
    assert service.production.pending_imports()[0]['pending_count'] == 1
    monkeypatch.setattr(service.production, 'receive', original)
    resumed = service.production.retry_import_files('resumable')
    assert resumed['copied_count'] == 2 and not resumed['intake_errors']
    assert service.db.counts()['projects'] == 1
    assert service.production.pending_imports() == []
    assert len(service.production.history(result['project_ids'][0], '软件说明书.docx')) == 1


def test_same_requirement_can_use_spelled_out_video_count():
    assert len(package_hint('十四件材料加两份90秒演示视频')[1]) == 16
    assert len(package_hint('14件+两个视屏')[1]) == 16


def test_manual_batches_can_share_label_in_different_commissions(service):
    a = service.production.create_batch('第一批', ['同名系统'], ['说明书'], commission='甲委托')
    b = service.production.create_batch('第一批', ['同名系统'], ['说明书'], commission='乙委托')
    assert a['batch_id'] != b['batch_id']
