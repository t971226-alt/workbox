from pathlib import Path
from zipfile import ZipFile

import pytest
from desktop_service import DesktopService
from requirement_intake import proposal
from material_templates import PRESETS


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def test_six_names_six_words_one_batch_folder_and_original_table_updated(service, tmp_path):
    names = [f'软件{i}管理系统' for i in range(6)]
    result = service.production.import_plan([proposal(n, batch='六件Word批次') for n in names], token='six')
    assert all(r['collection_state'] == '未收录' for r in service.production.rows(result['batch_ids'][0]))
    for name in names:
        source = tmp_path / (name + '.docx')
        source.write_bytes(b'word content')
        service.receive_path(source)
        assert source.is_file()
    rows = service.production.rows(result['batch_ids'][0])
    assert len(rows) == 6 and all(r['collection_state'] == '已收齐' for r in rows)
    assert all(r['reviewed_count'] == r['submitted_count'] == 0 for r in rows)
    directories = {r['collection_folder'] for r in rows}
    assert len(directories) == 1
    folder = Path(next(iter(directories)))
    assert folder.parent.name == '单Word'
    assert len(list(folder.glob('*.docx'))) == 6
    assert service.db.counts()['projects'] == 6
    reopened = DesktopService(service.db.path)
    assert all(r['collection_state'] == '已收齐' for r in reopened.production.rows(result['batch_ids'][0]))


def test_five_and_sixteen_classified_by_roles_not_whole_batch_file_count(service, tmp_path):
    for index, preset in enumerate(['五件材料', '14件＋双视频']):
        name = f'项目{index}系统'
        result = service.production.import_plan([proposal(name, batch=name)], token=name)
        for kind in PRESETS[preset]:
            source = tmp_path / (name + '_' + kind)
            source.write_bytes(kind.encode())
            service.receive_path(source)
        row = service.production.rows(result['batch_ids'][0])[0]
        assert row['required_count'] == len(PRESETS[preset])
        assert row['collection_state'] == '已收齐'
        assert ('五件材料' if index == 0 else '14件＋视频') in row['collection_folder']


def test_partial_duplicate_and_changed_word_keep_old_versions(service, tmp_path):
    result = service.production.import_plan([proposal('甲系统', batch='批次'), proposal('乙系统', batch='批次')], token='partial')
    source = tmp_path / '甲系统.docx'
    source.write_bytes(b'first')
    service.receive_path(source)
    service.receive_path(source)
    row = service.production.rows(result['batch_ids'][0])[0]
    assert row['materials'][0]['version_number'] == 1
    assert service.production.rows(result['batch_ids'][0])[1]['collection_state'] == '未收录'
    source.write_bytes(b'second')
    service.receive_path(source)
    history = service.production.history(row['id'], '软件说明书.docx')
    assert len(history) == 2 and Path(history[-1]['path']).read_bytes() == b'first'
    assert list((service.db.path.parent / '材料历史').rglob('*.docx'))


def test_named_material_zip_is_unpacked_and_refills_existing_task(service, tmp_path):
    result = service.production.import_plan([proposal('材料系统', batch='批次')], token='zip')
    source = tmp_path / '材料系统_五件材料.zip'
    with ZipFile(source, 'w') as archive:
        for kind in PRESETS['五件材料']:
            archive.writestr('材料系统_' + kind, kind)
    report = service.receive_path(source)
    assert report['failed'] == 0 and report['copied'] == 5
    assert service.production.rows(result['batch_ids'][0])[0]['collection_state'] == '已收齐'
    assert source.is_file()


def test_dangerous_zip_has_no_external_write(service, tmp_path):
    service.production.import_plan([proposal('材料系统', batch='批次')], token='unsafe')
    source = tmp_path / '材料系统_五件材料.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('../escape.txt', 'bad')
    with pytest.raises(ValueError):
        service.receive_path(source)
    assert not (service.db.path.parent / 'escape.txt').exists()


def test_folder_drop_matches_original_six_rows_without_new_tasks(service, tmp_path):
    names = [f'批量软件{i}' for i in range(6)]
    result = service.production.import_plan([proposal(n, batch='文件夹批次') for n in names], token='folder')
    folder = tmp_path / '六份成品'
    folder.mkdir()
    for name in names:
        (folder / (name + '.docx')).write_bytes(name.encode())
    report = service.receive_path(folder)
    assert report['copied'] == 6 and report['failed'] == report['needs_choice'] == 0
    assert service.db.counts()['projects'] == 6
    assert all(r['collection_state'] == '已收齐' for r in service.production.rows(result['batch_ids'][0]))


def test_explicit_five_requirements_do_not_become_single_word(service, tmp_path):
    row = proposal('显式系统', batch='五件', requirement='五件材料')
    result = service.production.import_plan([row], token='explicit')
    source = tmp_path / '显式系统.docx'
    source.write_bytes(b'word')
    service.receive_path(source)
    task = service.production.rows(result['batch_ids'][0])[0]
    assert task['required_count'] == 5 and task['collection_state'] == '部分收录'
    assert '五件材料' in task['collection_folder']


def test_manual_requirements_override_previous_automatic_guess(service, tmp_path):
    result = service.production.import_plan([proposal('调整系统', batch='调整')], token='adjust')
    source = tmp_path / '调整系统.docx'
    source.write_bytes(b'word')
    service.receive_path(source)
    pid = result['project_ids'][0]
    service.production.configure_task(pid, PRESETS['14件＋双视频'], package='14件＋双视频', reason='确认本次完整要求')
    pdf = tmp_path / '调整系统_软件说明书.pdf'
    pdf.write_bytes(b'pdf')
    service.receive_path(pdf)
    task = service.production.rows(result['batch_ids'][0])[0]
    assert task['required_count'] == 16 and task['package_name'] == '14件＋双视频'
    assert task['collection_count'] == 2 and task['collection_state'] == '部分收录'


def test_edited_collection_copy_can_be_received_without_deleting_source(service, tmp_path):
    result = service.production.import_plan([proposal('编辑系统', batch='编辑')], token='edit')
    source = tmp_path / '编辑系统.docx'
    source.write_bytes(b'first')
    service.receive_path(source)
    task = service.production.rows(result['batch_ids'][0])[0]
    collected = Path(task['collection_folder']) / source.name
    collected.write_bytes(b'edited')
    service.receive_path(collected)
    task = service.production.rows(result['batch_ids'][0])[0]
    assert collected.read_bytes() == b'edited' and task['collection_state'] == '已收齐'
    assert task['materials'][0]['version_number'] == 2
    assert source.read_bytes() == b'first'
