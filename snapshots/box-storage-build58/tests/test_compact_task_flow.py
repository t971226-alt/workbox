from pathlib import Path
from unittest.mock import patch
import pytest
from PySide6.QtCore import Qt,QPoint
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog,QPushButton,QStyleOptionViewItem,QStyle
from PySide6.QtTest import QTest
from desktop_service import DesktopService
from production_ui import ProductionPage
from task_requirements_ui import TaskMaterialsDialog
from theme import install_theme,set_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);install_theme();return a

@pytest.fixture
def page(app,tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    for i in range(4):
        kinds=['软件说明书.docx'] if i<2 else ['软件说明书.docx','软件说明书.pdf']
        result=s.production.create_batch('测试批次'+str(i),['界面验证软件'+str(i)],kinds)
        for kind in kinds:
            p=tmp_path/(str(i)+kind);p.write_bytes((str(i)+kind).encode());s.production.receive(result['project_ids'][0],kind,p)
    p=ProductionPage(s);p.enable_workspace();p.resize(720,600);p.show()
    for _ in range(4):app.processEvents()
    yield p
    p.close();p.deleteLater();app.processEvents()

def settle(app):
    for _ in range(4):app.processEvents()

def test_category_switch_keeps_other_categories_and_filters_in_one_toolbar(page,app):
    w=page.task_workspace;counts=dict(w.rules.counts);assert len(counts)==2
    names=list(counts)
    for name in [names[0],names[1],names[0],None]:
        w.choose_category(name);settle(app)
        assert dict(w.rules.counts)==counts and w.rules.total==4
        assert len(w.rows)==(counts[name] if name else 4)
        assert abs(w.filter_button.y()-w.rules.y())<=2 and not w.summary.isVisible()
    w.filter_menu.popup(w.filter_button.mapToGlobal(w.filter_button.rect().bottomLeft()));settle(app)
    assert w.state.isVisible() and w.period.isVisible() and w.category.isVisible()
    w.filter_menu.close();settle(app)
    for name in names:
        b=next(b for b in w.rules.findChildren(QPushButton) if b.property('categoryName')==name);QTest.mouseClick(b,Qt.LeftButton);settle(app)
        assert dict(w.rules.counts)==counts and w.category.currentData()==name
        b=next(b for b in w.rules.findChildren(QPushButton) if b.property('categoryName')==name);QTest.mouseClick(b,Qt.LeftButton);settle(app);assert w.category.currentData() is None and len(w.rows)==4

def test_completed_latest_dates_keep_other_rule_choices(page,app):
    for row in page.store.rows():page.store.review_many([m['id'] for m in row['materials']])
    with page.service.db.connect() as c:
        c.execute("UPDATE material_reviews SET created_at='2026-10-29T10:00:00Z' WHERE version_id IN (SELECT id FROM material_versions WHERE project_id IN (1,2))")
        c.execute("UPDATE material_reviews SET created_at='2026-10-30T10:00:00Z' WHERE version_id IN (SELECT id FROM material_versions WHERE project_id IN (3,4))")
    w=page.task_workspace;w.tabs.setCurrentIndex(1);settle(app);counts=dict(w.rules.counts);assert set(counts.values())=={2} and len(counts)==2 and w.rules.total==2
    for name in counts:
        w.choose_category(name);settle(app);assert dict(w.rules.counts)==counts and len(w.rows)==2

def test_select_all_partial_shift_range_and_filtered_scope(page,app):
    w=page.task_workspace;w.select_all_check.click();assert len(w.selected_versions)==4
    w.table.item(1,0).setCheckState(Qt.Unchecked);assert w.select_all_check.checkState()==Qt.PartiallyChecked
    w.select_all_check.click();assert len(w.selected_versions)==4
    w.select_all_check.click();assert not w.selected_versions
    for i,modifiers in [(0,Qt.NoModifier),(2,Qt.ShiftModifier)]:
        rect=w.table.visualRect(w.table.model().index(i,0));QTest.mouseClick(w.table.viewport(),Qt.LeftButton,modifiers,pos=QPoint(rect.left()+10,rect.center().y()))
    assert len(w.selected_versions)==3
    w.choose_category(next(iter(w.rules.counts)));assert not w.selected_versions
    w.table.setFocus();QTest.keyClick(w.table,Qt.Key_A,Qt.ControlModifier);assert len(w.selected_versions)==len(w.rows)==2
    assert all(r['id'] in w.selected_versions for r in w.rows)

def test_compact_detail_real_bulk_review_submission_and_saved_file(page,app):
    row=page.store.rows()[-1];d=TaskMaterialsDialog(page,row);d.show();settle(app)
    try:
        assert d.width()<=700 and d.height()<=455 and d.table.rowCount()==2
        assert all(d.table.rowHeight(i)<=40 for i in range(2)) and d.table.isColumnHidden(0)
        d.all_check.click();assert len(d.checked)==2 and d.pass_button.isEnabled() and not d.submit_button.isEnabled()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
            d.pass_button.click();assert queued.called;result=queued.call_args.args[1]()
        assert result['reviewed_count']==2;page.refresh();settle(app);assert not d.checked
        d.all_check.click();assert d.submit_button.isEnabled() and not d.pass_button.isEnabled()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
            d.submit_button.click();queued.call_args.args[1]()
        page.refresh();assert page.store.rows()[-1]['submitted_count']==2
        current=d.materials[0]
        from saved_locations import saved_file_location
        expected=saved_file_location(page.service,row['id'],kind=current['kind'])
        with patch('task_requirements_ui.QDesktopServices.openUrl',return_value=True) as opened:
            d.open_file(current['kind'],current['id']);assert Path(opened.call_args.args[0].toLocalFile())==expected
        assert '_WB'+f"{row['id']:06d}" in d.location.text()
        assert '不属于软件名称' in d.identity.toolTip()
        for mode in ('dark','light'):set_theme(mode);settle(app);assert d.table.rowCount()==2
    finally:set_theme('light');d.close();d.deleteLater();settle(app)

def test_main_multiselect_can_review_and_submit_current_versions(page):
    w=page.task_workspace;w.choose_category('单手册 Word');w.set_all_checked(True);assert w.review_button.isEnabled() and not w.submit_button.isEnabled()
    page.resize(470,600);QApplication.processEvents()
    assert page.rect().contains(w.submit_button.mapTo(page,w.submit_button.rect().bottomRight()))
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
        w.review_button.click();assert queued.called;assert queued.call_args.args[1]()['reviewed_count']==2
    page.refresh();assert not w.selected_versions;w.set_all_checked(True);assert w.submit_button.isEnabled() and not w.review_button.isEnabled()
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
        w.submit_button.click();assert queued.called;assert queued.call_args.args[1]()['submitted_count']==2

def test_detail_rejects_stale_selection_and_old_file_version(page,app,tmp_path):
    row=page.store.rows()[-1];d=TaskMaterialsDialog(page,row);d.all_check.click();old=d.materials[0]
    p=tmp_path/'new.docx';p.write_bytes(b'new version');page.store.receive(row['id'],old['kind'],p)
    try:
        with patch.object(page,'request') as queued:d.review_selected();assert not queued.called
        assert '更新' in d.message.text()
        with patch('task_requirements_ui.QDesktopServices.openUrl') as opened:d.open_file(old['kind'],old['id']);assert not opened.called
    finally:d.close();d.deleteLater();settle(app)

def test_detail_individual_checkbox_multiselect_and_collect_mode(page,app):
    row=page.store.rows()[-1];d=TaskMaterialsDialog(page,row);d.show();settle(app)
    try:
        for i in range(2):
            index=d.table.model().index(i,1);option=QStyleOptionViewItem();d.table.initViewItemOption(option);d.table.itemDelegateForIndex(index).initStyleOption(option,index);option.rect=d.table.visualRect(index)
            check=d.table.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator,option,d.table);assert not check.isEmpty()
            QTest.mouseClick(d.table.viewport(),Qt.LeftButton,pos=check.center())
        assert len(d.checked)==2 and d.all_check.checkState()==Qt.Checked
        d.all_check.click();assert not d.checked
        page.store.set_batch_mode(row['batch_id'],'collect');page.refresh();settle(app)
        assert not d.all_check.isVisible() and not d.pass_button.isVisible() and '无需核对或提交' in d.picked.text()
        assert not (d.table.item(0,1).flags()&Qt.ItemIsUserCheckable)
    finally:d.close();d.deleteLater();settle(app)

def test_bulk_review_is_atomic_for_stale_corrupt_and_submitted_materials(page,tmp_path):
    s=page.service;rows=page.store.rows();a,b=rows[-1]['materials'];valid=a['id'];old=b['id']
    source=tmp_path/'replacement.pdf';source.write_bytes(b'new pdf');page.store.receive(rows[-1]['id'],b['kind'],source)
    with pytest.raises(ValueError):page.store.review_many([valid,old])
    assert page.store.rows()[-1]['reviewed_count']==0
    row=page.store.rows()[-1];ids=[m['id'] for m in row['materials']];page.store.review_many(ids);page.store.submit(ids)
    untouched=page.store.rows()[0]['materials'][0]['id']
    with pytest.raises(ValueError):page.store.review_many([untouched,ids[0]])
    assert page.store.rows()[0]['reviewed_count']==0
    page.store.set_batch_mode(rows[0]['batch_id'],'collect')
    with pytest.raises(ValueError):page.store.review_many([untouched])
    page.store.set_batch_mode(rows[0]['batch_id'],'review')
    corrupt=page.store.rows()[1];from saved_locations import saved_file_location
    saved_file_location(s,corrupt['id'],kind=corrupt['materials'][0]['kind']).write_bytes(b'changed published copy')
    with pytest.raises(ValueError):page.store.review_many([untouched,corrupt['materials'][0]['id']])
    assert page.store.rows()[0]['reviewed_count']==0
