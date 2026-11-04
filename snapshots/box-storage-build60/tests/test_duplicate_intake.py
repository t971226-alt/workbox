from pathlib import Path
from zipfile import ZipFile
import time

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest

from desktop_service import DesktopService
from requirement_intake import proposal
from material_templates import PRESETS
from duplicate_intake_ui import DuplicateMaterialDialog
from desktop_controller import FloatingController


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def seeded(service, tmp_path, name='重复系统', package='单手册 Word', batch='已确认的批次'):
    result = service.production.import_plan([proposal(name, batch=batch, requirement=package)], token=batch)
    pid = result['project_ids'][0]
    paths = []
    for kind in PRESETS[package]:
        path = tmp_path / (name + '_' + kind)
        path.write_bytes(('original:' + kind).encode())
        service.receive_path(path)
        paths.append(path)
    task = service.production.rows(result['batch_ids'][0])[0]
    for material in task['materials']:
        service.production.review(material['id'], True)
    service.production.submit([m['id'] for m in task['materials']])
    return service.production.rows(result['batch_ids'][0])[0], paths


def decide(result, action, pid=None):
    return {g['key']: dict(action=action, project_id=pid or g['candidates'][0]['id']) for g in result['material_conflicts']}


def test_duplicate_prompts_before_mutating_and_new_preserves_submitted_task(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    paths[0].write_bytes(b'new content')
    pending = service.receive_interactive(paths)
    assert len(pending['material_conflicts']) == 1
    assert service.db.counts()['projects'] == 1
    assert service.production.rows()[0]['production_state'] == '已提交'
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    assert report['copied'] == 1 and report['failed'] == 0
    rows = service.production.rows()
    assert len(rows) == 2 and rows[0]['id'] == old['id'] and rows[0]['production_state'] == '已提交'
    assert rows[1]['production_state'] == '待检查' and rows[1]['reviewed_count'] == rows[1]['submitted_count'] == 0
    assert rows[1]['materials'][0]['id'] != old['materials'][0]['id']
    assert Path(rows[0]['materials'][0]['path']).read_bytes().startswith(b'original:')
    assert rows[0]['collection_folder'] != rows[1]['collection_folder']
    assert Path(rows[1]['collection_folder']).name == '重复系统-1件（2）'
    # A retry with the same decision token reuses the newly created round.
    service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    assert len(service.production.rows()) == 2


def test_overwrite_uses_same_row_new_version_and_keeps_old_record(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    paths[0].write_bytes(b'changed')
    pending = service.receive_interactive(paths)
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'overwrite'))
    row = service.production.rows()[0]
    assert report['failed'] == 0 and len(service.production.rows()) == 1
    assert row['id'] == old['id'] and row['materials'][0]['version_number'] == 2
    assert row['production_state'] == '待检查' and row['submitted_count'] == row['reviewed_count'] == 0
    assert Path(old['materials'][0]['path']).read_bytes().startswith(b'original:')
    with service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM material_submission_items').fetchone()[0] == 1


def test_identical_file_asks_then_replace_preserves_review(service,tmp_path):
    old,paths=seeded(service,tmp_path)
    result=service.receive_interactive(paths)
    assert result.get('material_conflicts')
    result=service.resolve_duplicate_intake(result['material_plan'],decide(result,'overwrite'))
    assert result['duplicate']
    row=service.production.rows()[0]
    assert len(service.production.rows())==1 and row['production_state']=='已提交'
    assert row['materials'][0]['id']==old['materials'][0]['id']


def test_multiple_files_same_software_get_one_decision_for_whole_folder(service, tmp_path):
    old, paths = seeded(service, tmp_path, package='五件材料')
    folder = tmp_path / '重新制作的五件'
    folder.mkdir()
    for path in paths:
        (folder / path.name).write_bytes(('new:' + path.name).encode())
    pending = service.receive_interactive([folder])
    assert len(pending['material_conflicts']) == 1 and len(pending['material_conflicts'][0]['files']) == 5
    assert len(service.production.rows()) == 1
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    assert report['copied'] == 5 and report['failed'] == 0
    rows = service.production.rows()
    assert rows[0]['production_state'] == '已提交'
    assert rows[1]['collection_state'] == '已收齐' and rows[1]['received_count'] == 5


def test_material_zip_gets_one_decision_before_replacing_any_material(service, tmp_path):
    old, paths = seeded(service, tmp_path, package='五件材料')
    zipped = tmp_path / '重复系统_五件材料.zip'
    with ZipFile(zipped, 'w') as archive:
        for path in reversed(paths):
            archive.writestr(path.name, b'new zip content')
    pending = service.receive_interactive([zipped])
    assert len(pending['material_conflicts']) == 1
    assert service.production.rows()[0]['production_state'] == '已提交'
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    assert report['copied'] == 5 and report['failed'] == 0
    assert len(service.production.rows()) == 2 and zipped.is_file()
    assert service.production.rows()[0]['production_state'] == '已提交'
    assert service.production.rows()[1]['collection_state'] == '已收齐'


def test_new_names_share_one_batch_for_a_single_multi_file_drop(service, tmp_path):
    old_a, a = seeded(service, tmp_path, name='系统A', batch='A批')
    old_b, b = seeded(service, tmp_path, name='系统B', batch='B批')
    a[0].write_bytes(b'new A')
    b[0].write_bytes(b'new B')
    pending = service.receive_interactive([a[0], b[0]])
    assert len(pending['material_conflicts']) == 2
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    assert len(report['batch_ids']) == 1 and report['copied'] == 2
    rows = service.production.rows()
    assert len(rows) == 4 and rows[2]['batch_id'] == rows[3]['batch_id']
    assert rows[2]['collection_folder'] == rows[3]['collection_folder']
    assert Path(rows[2]['collection_folder']).name == '系统A-2件'


def test_new_material_role_does_not_prompt_during_partial_delivery(service, tmp_path):
    result = service.production.import_plan([proposal('补交系统', batch='补交', requirement='五件材料')], token='partial')
    for kind in ['软件说明书.docx', '软件说明书.pdf']:
        path = tmp_path / ('补交系统_' + kind)
        path.write_bytes(kind.encode())
        received = service.receive_interactive([path])
        assert not received.get('material_conflicts')
    assert service.production.rows()[0]['collection_count'] == 2


def test_choose_existing_unfinished_round_instead_of_creating_third(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    extra = service.production.import_plan([proposal('重复系统', batch='新的待办', requirement='单手册 Word')], token='unfinished')
    pending = service.receive_interactive(paths)
    assert len(pending['material_conflicts'][0]['candidates']) == 2
    report = service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'overwrite', extra['project_ids'][0]))
    assert report['failed'] == 0 and len(service.production.rows()) == 2
    assert service.production.rows()[0]['production_state'] == '已提交'
    assert service.production.rows()[1]['collection_state'] == '已收齐'


def test_changed_task_while_dialog_open_requires_fresh_choice(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    paths[0].write_bytes(b'pending revision')
    pending = service.receive_interactive(paths)
    paths[0].write_bytes(b'externally changed')
    service.production.receive(old['id'], old['materials'][0]['kind'], paths[0])
    result=service.resolve_duplicate_intake(pending['material_plan'],decide(pending,'new'))
    assert result['material_conflicts'] and len(service.production.rows())==1


def test_mixed_new_and_overwrite_apply_to_only_selected_tasks(service, tmp_path):
    old_a, a = seeded(service, tmp_path, name='系统A', batch='A批')
    old_b, b = seeded(service, tmp_path, name='系统B', batch='B批')
    a[0].write_bytes(b'new A')
    b[0].write_bytes(b'new B')
    pending = service.receive_interactive([a[0], b[0]])
    choices = decide(pending, 'overwrite')
    choices['系统a']['action'] = 'new'
    service.resolve_duplicate_intake(pending['material_plan'], choices)
    rows = service.production.rows()
    assert len(rows) == 3
    assert rows[0]['production_state'] == '已提交'
    assert rows[1]['id'] == old_b['id'] and rows[1]['production_state'] == '待检查'
    assert rows[2]['name'] == '系统A' and rows[2]['production_state'] == '待检查'


def test_explicit_material_import_with_generic_filename_can_start_new_round(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    path = tmp_path / '生成结果.docx'
    path.write_bytes(b'new')
    pending = service.receive_interactive([path], project_id=old['id'], material_kind='软件说明书.docx')
    service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new'))
    rows = service.production.rows()
    assert len(rows) == 2 and rows[1]['name'] == old['name']
    assert rows[0]['production_state'] == '已提交' and rows[1]['collection_state'] == '已收齐'


def test_invalid_decision_creates_nothing(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    paths[0].write_bytes(b'pending revision')
    pending = service.receive_interactive(paths)
    with pytest.raises(ValueError):
        service.resolve_duplicate_intake(pending['material_plan'], decide(pending, 'new', 99999))
    assert len(service.production.rows()) == 1 and service.production.rows()[0]['production_state'] == '已提交'


def test_generic_zip_keeps_original_archive_flow(service, tmp_path):
    old, paths = seeded(service, tmp_path)
    zipped = tmp_path / '重复系统.zip'
    with ZipFile(zipped, 'w') as archive:
        archive.writestr('readme.txt', 'generic source archive')
    result = service.receive_interactive([zipped])
    assert not result.get('material_conflicts') and result['watching']
    assert zipped.is_file()
    row = service.production.rows()[0]
    assert row['materials'][0]['id'] == old['materials'][0]['id']


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.mark.parametrize('identical',[False,True])
@pytest.mark.parametrize('action', ['cancel', 'new', 'overwrite'])
def test_real_drop_dialog_and_buttons_control_result(service, tmp_path, qapp, action,identical):
    old, paths = seeded(service, tmp_path)
    if not identical:paths[0].write_bytes(b'changed')
    controller = FloatingController(service, tray_enabled=False)
    timer = QTimer()
    timer.setInterval(10)
    seen = []
    def answer():
        dialog = QApplication.activeModalWidget()
        from direct_material_ui import DirectMaterialDialog
        if isinstance(dialog,DirectMaterialDialog):dialog.confirm_button.click()
        elif isinstance(dialog, DuplicateMaterialDialog):
            seen.append(dialog.windowTitle())
            timer.stop()
            assert dialog.tasks.currentData() == old['id']
            assert service.production.rows()[0]['production_state'] == '已提交'
            if action == 'cancel':
                dialog.cancel_button.click()
            elif action == 'new':
                dialog.new_button.click()
            else:
                dialog.overwrite_button.click()
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        timer.timeout.connect(answer)
        timer.start()
        controller.receive({'kind': 'files', 'paths': [str(paths[0])]})
        deadline = time.monotonic() + 15
        while controller.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not controller.busy and seen == ['已存在同名文件']
        rows = service.production.rows()
        assert len(rows) == (2 if action == 'new' else 1)
        assert rows[0]['production_state'] == ('待检查' if action == 'overwrite' and not identical else '已提交')
        if action == 'new':
            assert rows[1]['production_state'] == '待检查'
            assert controller.panel.production_page.table.rowCount() == 2
    finally:
        timer.stop()
        controller.shutdown()
        controller.dispose_ui()
