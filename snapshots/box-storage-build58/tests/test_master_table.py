import csv
from pathlib import Path

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QInputDialog

from desktop_service import DesktopService
from requirement_intake import proposal
from compact_ui import CompactPanel
from theme import install_theme


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def batch(service, names, label):
    return service.production.import_plan([proposal(n, batch=label, requirement='单手册 Word') for n in names], token=label)


def receive(service, name, tmp_path):
    path = tmp_path / (name + '.docx')
    path.write_bytes(name.encode())
    return service.receive_path(path)


def test_one_master_keeps_all_batches_and_reopening_updates_same_rows(service, tmp_path):
    first = batch(service, ['甲软件', '乙软件'], '第一批')
    second = batch(service, ['丙软件'], '第二批')
    ids = first['project_ids'] + second['project_ids']
    assert [r['id'] for r in service.production.rows()] == ids
    receive(service, '乙软件', tmp_path)
    reopened = DesktopService(service.db.path)
    rows = reopened.production.rows()
    assert [r['id'] for r in rows] == ids
    assert [r['collection_state'] for r in rows] == ['未收录', '已收齐', '未收录']
    target = tmp_path / '总表.csv'
    reopened.production.export_master(target)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 3 and exported[1]['软件名称'] == '乙软件'
    assert exported[1]['收录结果'] == '已收齐'
    assert exported[0]['已收录'] == '0' and exported[0]['已检查'] == '0'
    assert all(r['建表月份'] == r['建表日期'][:7] for r in exported)


def test_folder_name_uses_first_software_and_batch_size_not_arrival_order(service, tmp_path):
    batch(service, ['首个软件', '第二软件', '第三软件'], '目录批次')
    receive(service, '第三软件', tmp_path)
    receive(service, '首个软件', tmp_path)
    rows = service.production.rows()
    folder = Path(rows[2]['collection_folder'])
    assert folder.name == '首个软件-3件' and folder.parent.name == '单Word'
    assert rows[0]['collection_folder'] == str(folder)
    assert len(list(folder.glob('*.docx'))) == 2


def test_repeat_folder_collision_uses_sequence_and_keeps_previous_batch(service, tmp_path):
    first = batch(service, ['同名软件'], '旧批次')
    source = tmp_path / '同名软件.docx'
    source.write_bytes(b'old')
    service.workspaces.import_file(source, first['project_ids'][0])
    second = batch(service, ['同名软件'], '新批次')
    source.write_bytes(b'new')
    service.workspaces.import_file(source, second['project_ids'][0])
    rows = service.production.rows()
    assert Path(rows[0]['collection_folder']).name == '同名软件-1件'
    assert Path(rows[1]['collection_folder']).name == '同名软件-1件（2）'
    assert (Path(rows[0]['collection_folder']) / source.name).read_bytes() == b'old'
    assert len(rows) == 2


def test_day_month_statistics_use_each_event_date_and_latest_valid_state(service, tmp_path):
    first = batch(service, ['已做软件'], '已做')
    second = batch(service, ['后续软件'], '后续')
    receive(service, '已做软件', tmp_path)
    task = service.production.rows(first['batch_ids'][0])[0]
    vid = task['materials'][0]['id']
    service.production.review(vid, True)
    service.production.submit([vid])
    with service.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-09-30T15:59:00Z' WHERE id=?", (first['project_ids'][0],))
        c.execute("UPDATE projects SET created_at='2026-10-01T00:00:00Z' WHERE id=?", (second['project_ids'][0],))
        c.execute("UPDATE collected_materials SET collected_at='2026-09-30T16:01:00Z'")
        c.execute("UPDATE material_reviews SET created_at='2026-10-01T16:01:00Z'")
        c.execute("UPDATE material_submissions SET created_at='2026-10-02T16:01:00Z'")
    assert service.production.statistics('2026-09-30') == dict(added=1, collected=0, checked=0, submitted=0)
    assert service.production.statistics('2026-10-01') == dict(added=1, collected=1, checked=0, submitted=0)
    assert service.production.statistics('2026-10-02')['checked'] == 1
    assert service.production.statistics('2026-10-03')['submitted'] == 1
    assert service.production.statistics('2026-10-29', True) == dict(added=1, collected=1, checked=1, submitted=1)
    assert service.production.statistics('2026-11-01', True) == dict(added=0, collected=0, checked=0, submitted=0)
    source = tmp_path / '已做软件.docx'
    source.write_bytes(b'changed')
    service.receive_path(source)
    assert service.production.statistics('2026-10-01')['collected'] == 0
    assert service.production.statistics('2026-10-29', True)['checked'] == 0
    assert service.production.statistics('2026-10-29', True)['submitted'] == 0


def test_old_folder_migration_resumes_after_partial_failure(service, tmp_path, monkeypatch):
    import material_collection
    result = batch(service, ['迁移软件', '后续软件'], '迁移')
    for name in ['迁移软件', '后续软件']:
        receive(service, name, tmp_path)
    rows = service.production.rows()
    folder = Path(rows[0]['collection_folder'])
    legacy = folder.parent / '旧格式_B000001'
    assert folder.resolve().is_relative_to(tmp_path.resolve()) and legacy.resolve().is_relative_to(tmp_path.resolve())
    folder.rename(legacy)
    with service.db.connect() as c:
        c.execute('DELETE FROM collection_batch_folders')
        for row in rows:
            c.execute('UPDATE collected_materials SET path=? WHERE project_id=?', (str(legacy / (row['name'] + '.docx')), row['id']))
    assert service.production.collection_migration_needed()
    original = material_collection.publish
    def interrupt(c, production, pid, *args):
        if pid == result['project_ids'][1]:
            raise OSError('模拟中断')
        return original(c, production, pid, *args)
    monkeypatch.setattr(material_collection, 'publish', interrupt)
    with pytest.raises(OSError):
        service.production.refresh_collections()
    assert service.production.collection_migration_needed()
    monkeypatch.setattr(material_collection, 'publish', original)
    service.production.refresh_collections()
    assert not service.production.collection_migration_needed()
    assert all(Path(r['collection_folder']).name == '迁移软件-2件' and r['collection_state'] == '已收齐' for r in service.production.rows())
    assert all((tmp_path / (r['name'] + '.docx')).is_file() for r in rows)


def test_ui_opens_one_master_from_any_conversation_and_changes_name(service, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    a = batch(service, ['第一软件'], '第一批')
    b = batch(service, ['第二软件'], '第二批')
    install_theme(service)
    panel = CompactPanel(service)
    panel.show()
    try:
        page = panel.production_page
        panel.open_production_batches(a['batch_ids'])
        assert page.batch.currentData() is None and page.table.rowCount() == 2
        page.search.setText('第一')
        panel.open_production_batches(b['batch_ids'])
        assert page.table.rowCount() == 2 and page.table.currentRow() == 1
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('改后的软件', True))
        page.rename_button.click()
        assert service.production.rows()[1]['name'] == '改后的软件'
        page.statistics_date.setDate(QDate.currentDate())
        page.statistics_mode.setCurrentIndex(1)
        assert '新增 2' in page.statistics_label.text()
        page.statistics_date.setDate(QDate(2025, 1, 1))
        assert '新增 0' in page.statistics_label.text()
        assert page.table.rowCount() == 2
    finally:
        panel.conversation_page.shutdown()
        panel.document_pane.shutdown()
        panel.close()
        install_theme()
