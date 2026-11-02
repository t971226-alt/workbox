import csv
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QAbstractItemView
from desktop_service import DesktopService
from master_ledger_ui import MasterLedgerDialog,VersionHistoryDialog
from requirement_intake import proposal
from saved_locations import saved_file_location


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


@pytest.fixture
def service(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    r=s.production.import_plan([proposal('总表甲系统',batch='第一批',requirement='单手册 Word'),proposal('总表乙系统',batch='第二批',requirement='五件材料'),proposal('总表甲系统',batch='第三批',requirement='单手册 Word')],token='tasks')
    p=tmp_path/'总表甲系统.docx';p.write_bytes(b'A');s.production.receive(r['project_ids'][0],'软件说明书.docx',p)
    s.test_ids=r['project_ids'];return s


def select(d,pid):
    for i in range(d.table.rowCount()):
        if d.table.item(i,0).data(Qt.UserRole)==pid:d.table.selectRow(i);d.show_details();return
    raise AssertionError('task not shown')


def test_overview_keeps_same_names_separate_and_filters_and_exports(service,tmp_path,app):
    d=MasterLedgerDialog(service);d.show();app.processEvents()
    try:
        assert d.table.rowCount()==3 and d.table.editTriggers()==QAbstractItemView.NoEditTriggers
        d.search.setText('总表甲');assert d.table.rowCount()==2
        d.search.setText(f'WB-{service.test_ids[0]:06d}');assert d.table.rowCount()==1
        out=tmp_path/'filtered.csv';d.export_to(out);rows=list(csv.reader(out.open(encoding='utf-8-sig')));assert len(rows)==2 and rows[1][0]==f'WB-{service.test_ids[0]:06d}'
        d.export_to(out,True);assert len(list(csv.reader(out.open(encoding='utf-8-sig'))))==4
        d.reset();d.batch.setCurrentIndex(d.batch.findData(service.production.rows()[1]['batch_id']));assert d.table.rowCount()==1 and d.selected()['id']==service.test_ids[1]
        d.reset();d.category.setCurrentIndex(d.category.findData('单手册 Word'));assert d.table.rowCount()==2
    finally:d.close()


def test_saved_file_action_selects_published_file_not_archive(service,app):
    d=MasterLedgerDialog(service);d.show();app.processEvents();select(d,service.test_ids[0])
    try:
        target=saved_file_location(service,service.test_ids[0],kind='软件说明书.docx')
        archive=Path(service.production.rows()[0]['materials'][0]['path']);assert target!=archive and target.is_file()
        with patch('saved_locations.open_location') as opened:
            d.locate_button.click();assert opened.call_args.args==(target,)
            d.directory_button.click();assert opened.call_args.args==(target.parent,)
        assert str(target) in d.path.toPlainText()
        select(d,service.test_ids[1]);assert not d.locate_button.isEnabled() and not d.directory_button.isEnabled()
    finally:d.close()


def test_live_refresh_deletion_and_missing_file_never_open_other_task(service,app):
    d=MasterLedgerDialog(service);d.show();app.processEvents();select(d,service.test_ids[0])
    try:
        target=saved_file_location(service,service.test_ids[0],kind='软件说明书.docx');target.unlink();d.refresh()
        assert '异常 1' in d.statistics.text() and not d.locate_button.isEnabled()
        service.production.delete_tasks([service.test_ids[0]],confirmed=True)
        with patch('saved_locations.open_location') as opened:d.open_saved();assert not opened.called
        assert d.table.rowCount()==2 and '重新选择' in d.error.text()
        service.production.restore_tasks([service.test_ids[0]]);d.refresh();assert d.table.rowCount()==3
    finally:d.close()


def test_raw_zip_details_and_location_keep_unverified_status(service,tmp_path,app):
    p=tmp_path/'总表原包系统.zip'
    with ZipFile(p,'w') as z:z.writestr('readme.txt',b'raw')
    r=service.receive_interactive([str(p)],zip_mode='original');pid=r['project_ids'][0];d=MasterLedgerDialog(service);d.show();app.processEvents();select(d,pid)
    try:
        assert '原包待核对' in d.detail_summary.text() and '包内材料未核对' in d.materials.item(0).text()
        with patch('saved_locations.open_location') as opened:d.locate_button.click();assert opened.call_args.args[0]==Path(r['working_zip_path']).resolve()
        assert not d.history_button.isEnabled()
    finally:d.close()


def test_version_history_is_readonly_and_opens_selected_archive(service,tmp_path,app):
    pid=service.test_ids[0];p=tmp_path/'new.docx';p.write_bytes(b'B');service.production.receive(pid,'软件说明书.docx',p)
    d=VersionHistoryDialog(service,pid,'软件说明书.docx');d.show();app.processEvents()
    try:
        assert d.table.rowCount()==2 and d.table.editTriggers()==QAbstractItemView.NoEditTriggers
        d.table.selectRow(1)
        with patch('saved_locations.open_location') as opened:d.open_location();assert opened.call_args.args[0].read_bytes()==b'A'
    finally:d.close()


def test_entry_button_reuses_one_window(service,app):
    from production_ui import ProductionPage
    p=ProductionPage(service);p.enable_workspace();p.show();p.master_ledger_button.click();first=p.master_ledger_dialog
    try:
        assert first.isVisible();first.close();p.master_ledger_button.click();assert p.master_ledger_dialog is first and first.isVisible()
    finally:first.close();p.close()


def test_compact_details_toggle_and_theme_preserve_selection(service,app):
    from theme import set_theme
    d=MasterLedgerDialog(service);d.show();app.processEvents();select(d,service.test_ids[0])
    try:
        selected=d.selected()['id'];chosen=d.materials.currentItem().data(Qt.UserRole)
        d.details_toggle.click();assert not d.details_panel.isVisible() and d.reopen_details.isVisible()
        d.reopen_details.click();assert d.details_panel.isVisible()
        set_theme('dark');app.processEvents();set_theme('light');app.processEvents()
        assert d.selected()['id']==selected and d.materials.currentItem().data(Qt.UserRole)==chosen
        assert d.locate_button.isEnabled() and d.table.rowCount()==3
    finally:set_theme('light');d.close()
