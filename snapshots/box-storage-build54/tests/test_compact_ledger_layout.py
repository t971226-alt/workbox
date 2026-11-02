import pytest
from PySide6.QtWidgets import QApplication
from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

@pytest.fixture
def panel(app,tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    s.production.create_batch('独立批次',['独立软件系统'],['软件说明书.docx'])
    s.production.create_batch('组合批次',['组合软件甲系统','组合软件乙系统'],['软件说明书.docx'])
    install_theme(s);p=CompactPanel(s);p.production_page.show_legacy_workspace();p.resize(870,640);p.show();app.processEvents()
    yield p
    p.conversation_page.shutdown();p.document_pane.shutdown();p.close();p.deleteLater();install_theme()

def test_single_batch_has_one_row_and_group_keeps_heading(panel,app):
    page=panel.production_page;g=page.grouped;single=page.rows[0];multi=page.rows[1]
    assert g.batch_buttons[single['batch_id']] is g.task_buttons[single['id']]
    assert g.batch_buttons[multi['batch_id']] is not g.task_buttons[multi['id']]
    assert g.task_buttons[single['id']].drawing_font().pixelSize()==11
    assert g.task_buttons[single['id']].parentWidget().height()==28
    assert '独立批次' in g.task_buttons[single['id']].toolTip()
    g.task_buttons[single['id']].click();app.processEvents();assert single['id'] in g.open_tasks
    assert (single['id'],'软件说明书.docx','review') in g.file_controls
    g.task_buttons[single['id']].click();assert single['id'] not in g.open_tasks

def test_filter_does_not_erase_original_group_identity(panel,app):
    page=panel.production_page;row=page.rows[1];page.search.setText(row['name']);app.processEvents()
    g=page.grouped;assert len(page.rows)==1
    assert g.batch_buttons[row['batch_id']] is not g.task_buttons[row['id']]
    g.batch_buttons[row['batch_id']].click();app.processEvents()
    assert row['batch_id'] in g.closed_batches and row['id'] not in g.task_buttons
    g.batch_buttons[row['batch_id']].click();assert row['id'] in g.task_buttons

def test_toolbar_shares_row_and_more_opens_correct_batch(panel,app):
    page=panel.production_page;row=page.rows[0];g=page.grouped
    assert abs(page.search.y()-page.stats_scope.y())<5
    assert page.stats_scope_label.isHidden()
    assert page.overview.height()<=60
    frame=g.task_buttons[row['id']].parentWidget()
    more=next(b for b in frame.findChildren(type(g.task_buttons[row['id']])) if b.menu())
    more.menu().actions()[0].trigger();app.processEvents()
    assert page.active_batch==row['batch_id'] and page.stats_scope.currentData()=='batch'


def test_bulk_selection_scopes_partial_and_collapsed_batches(panel,app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    page=panel.production_page;g=page.grouped
    before=page.store.rows();single=page.rows[0];group=page.rows[1:];bid=group[0]['batch_id']
    QTest.mouseClick(g.task_checks[group[0]['id']],Qt.LeftButton)
    assert [r['id'] for r in page.checked()]==[group[0]['id']]
    assert g.all_check.property('mixed') and g.batch_checks[bid].property('mixed')
    QTest.mouseClick(g.batch_checks[bid],Qt.LeftButton)
    assert {r['id'] for r in page.checked()}=={r['id'] for r in group}
    assert g.batch_checks[bid].isChecked() and not g.task_checks[single['id']].isChecked()
    QTest.mouseClick(g.batch_checks[bid],Qt.LeftButton);assert not page.checked()
    g.batch_buttons[bid].click();app.processEvents()
    QTest.mouseClick(g.all_check,Qt.LeftButton)
    assert len(page.checked())==3 and g.all_check.isChecked()
    page.clear_button.click();assert not page.checked() and not g.all_check.property('mixed')
    page.search.setText(group[0]['name']);app.processEvents()
    assert len(page.rows)==1
    QTest.mouseClick(g.all_check,Qt.LeftButton)
    assert [r['id'] for r in page.checked()]==[group[0]['id']]
    QTest.mouseClick(g.all_check,Qt.LeftButton);assert not page.checked()
    assert page.store.rows()==before
