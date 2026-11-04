from datetime import date
from pathlib import Path
from unittest.mock import patch
import csv
import pytest
from PySide6.QtCore import Qt,QPoint,QDate
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QMessageBox
from desktop_service import DesktopService
from production_ui import ProductionPage
from task_query import query,status,unfinished,completion_time


@pytest.fixture(scope='module')
def app():
    from theme import install_theme
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);install_theme();return a


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    ids=[]
    for i,name in enumerate(['跨天缺件系统','已核对待提交系统','已完成提交系统','整理完成系统','同名称系统','同名称系统']):
        result=s.production.create_batch('批次'+str(i),[name],['软件说明书.docx','登记申请信息.txt'] if i==0 else ['软件说明书.docx']);pid=result['project_ids'][0];ids.append(pid)
        if i==3:s.production.set_batch_mode(result['batch_id'],'collect')
        if i in (0,1,2,3):
            p=tmp_path/f'{i}.docx';p.write_bytes(('sample'+str(i)).encode());s.production.receive(pid,'软件说明书.docx',p)
        if i in (1,2):
            row=next(r for r in s.production.rows() if r['id']==pid);s.production.review(row['materials'][0]['id'],True)
            if i==2:s.production.submit([row['materials'][0]['id']])
    with s.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-10-28T23:00:00Z'")
        c.execute("UPDATE material_reviews SET created_at='2026-10-30T15:00:00Z'")
        c.execute("UPDATE material_submissions SET created_at='2026-10-30T15:30:00Z'")
        c.execute("UPDATE collected_materials SET collected_at='2026-10-29T15:00:00Z' WHERE project_id=?",(ids[3],))
    s.test_ids=ids;return s


def rows_by_id(s):return {r['id']:r for r in s.production.rows()}


def test_carryover_never_disappears_at_midnight_and_completed_storage_is_distinct(data):
    s=data;before=rows_by_id(s);first=s.test_ids[0]
    for day in [date(2026,10,31),date(2026,11,1)]:
        result,_=query(list(before.values()),s.production.local_date,day=day)
        assert {r['id'] for r in result}==set(s.test_ids)-{s.test_ids[2],s.test_ids[3]}
        assert first in {r['id'] for r in result}
    assert status(before[s.test_ids[3]])=='已收纳' and before[s.test_ids[3]]['submitted_count']==0
    assert rows_by_id(s)==before


def test_latest_completion_day_and_custom_date_range_follow_current_filtered_results(data):
    s=data;all_rows=s.production.rows();local=s.production.local_date
    result,recent=query(all_rows,local,view='completed')
    assert recent=='2026-10-30' and {r['id'] for r in result}=={s.test_ids[1],s.test_ids[2]}
    result,recent=query(all_rows,local,view='completed',text='整理完成')
    assert recent=='2026-10-29' and [r['id'] for r in result]==[s.test_ids[3]]
    result,_=query(all_rows,local,view='completed',period='custom',start='2026-10-29',end='2026-10-30')
    assert len(result)==3
    result,_=query(all_rows,local,view='completed',period='today',day=date(2026,10,31));assert result==[]


def test_invalid_or_changed_versions_return_to_unfinished_without_fake_completion(data,tmp_path):
    s=data;pid=s.test_ids[2];row=rows_by_id(s)[pid]
    from saved_locations import saved_file_location
    target=saved_file_location(s,pid,kind='软件说明书.docx');target.write_bytes(b'changed externally')
    row=rows_by_id(s)[pid];assert unfinished(row) and not completion_time(row) and status(row)=='副本异常'
    p=tmp_path/'update.docx';p.write_bytes(b'new current version');s.production.receive(pid,'软件说明书.docx',p)
    row=rows_by_id(s)[pid];assert row['submitted_count']==0 and not completion_time(row) and status(row)=='待核对'
    assert any(v['submissions'] for v in s.production.history(pid,'软件说明书.docx'))


def test_active_query_excludes_deleted_tasks_and_preserves_same_names(data):
    s=data;result,_=query(s.production.rows(),s.production.local_date,text='同名称');assert len(result)==2
    s.production.delete_tasks([s.test_ids[4]],confirmed=True)
    result,_=query(s.production.rows(),s.production.local_date,text='同名称');assert [r['id'] for r in result]==[s.test_ids[5]]
    s.production.restore_tasks([s.test_ids[4]])
    assert len(query(s.production.rows(),s.production.local_date,text='同名称')[0])==2


@pytest.fixture
def page(data,app):
    p=ProductionPage(data)
    p.enable_workspace()
    p.resize(740,670)
    p.show()
    app.processEvents()
    yield p
    if hasattr(p,'master_ledger_dialog'):p.master_ledger_dialog.close()
    p.close();p.deleteLater();app.processEvents()


def test_workspace_filters_date_resets_and_real_file_tooltips(page,data,app,tmp_path):
    p=page;w=p.task_workspace;assert w.isVisible() and not p.legacy_container.isVisible()
    assert w.table.rowCount()==4 and w.rules.add_button.isVisible()
    i=next(i for i,r in enumerate(w.rows) if r['id']==data.test_ids[0]);assert '登记申请信息.txt' in w.table.item(i,3).toolTip()
    w.search.setText('同名称');assert w.table.rowCount()==2
    w.search.setText(f'WB-{data.test_ids[4]:06d}');assert w.table.rowCount()==1
    w.reset();w.tabs.setCurrentIndex(1);assert w.table.rowCount()==2 and '2026-10-30' in w.period.currentText()
    w.search.setText('整理完成');assert w.table.rowCount()==1 and '2026-10-29' in w.period.currentText()
    output=tmp_path/'筛选.csv';w.export_to(output);exported=list(csv.reader(output.open(encoding='utf-8-sig')));assert len(exported)==2 and exported[1][0]==f'WB-{data.test_ids[3]:06d}'
    w.reset();w.tabs.setCurrentIndex(0);assert w.period.currentData()=='all' and len(w.rows)==4
    page.error.setText('材料未能收录，请重试');assert w.error.isVisible() and '重试' in w.error.text();page.error.setText('');assert not w.error.isVisible()


def test_checkbox_survives_refresh_but_clears_for_changed_versions(page,data,app,tmp_path):
    w=page.task_workspace;i=next(i for i,r in enumerate(w.rows) if r['id']==data.test_ids[1]);w.table.scrollToItem(w.table.item(i,0));app.processEvents();rect=w.table.visualRect(w.table.model().index(i,0))
    QTest.mouseClick(w.table.viewport(),Qt.LeftButton,pos=QPoint(rect.left()+10,rect.center().y()));assert data.test_ids[1] in w.selected_versions
    w.refresh();assert data.test_ids[1] in w.selected_versions
    p=tmp_path/'new.docx';p.write_bytes(b'updated');data.production.receive(data.test_ids[1],'软件说明书.docx',p);w.refresh();assert data.test_ids[1] not in w.selected_versions


def test_rule_creation_entry_reuses_real_catalog_and_does_not_change_existing_tasks(page,data,app):
    from classification_ui import ClassificationDialog
    before=data.production.rows()
    def create(d):
        assert d.current is None
        d.catalog.save('用户自建验收规则',['报告.pdf','演示.mp4']);return QDialog.Rejected
    with patch.object(ClassificationDialog,'exec',create):page.task_workspace.rules.add_button.click()
    w=page.task_workspace;assert w.category.findData('用户自建验收规则')>=0
    w.choose_category('用户自建验收规则');assert w.table.rowCount()==0 and data.production.rows()==before


def test_submission_uses_pinned_versions_and_revalidates_before_queue(page,data,tmp_path):
    w=page.task_workspace;pid=data.test_ids[1];i=next(i for i,r in enumerate(w.rows) if r['id']==pid);w.table.item(i,0).setCheckState(Qt.Checked)
    source=tmp_path/'new.docx';source.write_bytes(b'changed after checked');data.production.receive(pid,'软件说明书.docx',source)
    with patch.object(page,'request') as queued:w.submit_selected();assert not queued.called
    assert '变化' in w.error.text() and not w.selected_versions


def test_review_ready_submission_and_saved_directory_use_correct_task(page,data,app):
    from saved_locations import collection_location
    w=page.task_workspace;pid=data.test_ids[1];i=next(i for i,r in enumerate(w.rows) if r['id']==pid);w.table.item(i,0).setCheckState(Qt.Checked)
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
        w.submit_selected();assert queued.called;operation=queued.call_args.args[1];operation()
    w.refresh();assert pid not in {r['id'] for r in w.rows}
    with patch('saved_locations.open_location') as opened:w.open_saved(pid);assert opened.call_args.args[0]==collection_location(data,pid)


def test_intake_focus_can_show_collected_completion_until_reset(page,data):
    w=page.task_workspace;w.search.setText('没有匹配的名称');w.focus_projects([data.test_ids[3]])
    assert [r['id'] for r in w.rows]==[data.test_ids[3]] and '临时' in w.scope_label.text()
    w.reset();assert data.test_ids[3] not in {r['id'] for r in w.rows}


def test_legacy_detail_view_and_conversation_query_remain_reachable(page,data,app):
    w=page.task_workspace;page.show_legacy_workspace();assert page.legacy_container.isVisible() and not w.isVisible()
    page.legacy_back.click();assert w.isVisible()
    page.apply_query(dict(intent='records',name='已完成提交',state='已提交',metric='submitted',date_field='completed_submission_at',day='',monthly=False))
    assert w.view=='completed' and [r['id'] for r in w.rows]==[data.test_ids[2]]


def test_hidden_query_cannot_change_legacy_batch_mode_selection(page,data,app):
    pid=data.test_ids[1];batch=rows_by_id(data)[pid]['batch_id'];page.show_legacy_workspace();page.grouped.select_batch(batch)
    page.change_processing_mode(False);app.processEvents()
    assert page.active_batch==batch
    assert rows_by_id(data)[pid]['processing_mode']=='collect'
    assert rows_by_id(data)[data.test_ids[0]]['processing_mode']=='review'


def test_conversation_results_match_advertised_scope_and_replace_stale_filters(page,data):
    from workspace_queries import parse_query,answer
    w=page.task_workspace;before=data.production.rows()
    for text in ('显示全部','收齐了多少？','哪些软件未提交','查找 已完成提交系统'):
        w.search.setText('没有这个名称');w.choose_category('五件材料')
        w.state.setCurrentIndex(1)
        spec=parse_query(text);expected=answer(data.production,spec)
        page.apply_query(spec)
        assert {r['id'] for r in w.rows}==set(expected['matched_project_ids'])
        assert not w.search.text() and w.category.currentData() is None and w.state.currentData() is None
        assert '查询结果' in w.caption.text()
    page.show_all();assert w.external_spec is None and len(w.rows)==4
    collected_batch=rows_by_id(data)[data.test_ids[3]]['batch_id']
    page.show_all([collected_batch]);assert [r['id'] for r in w.rows]==[data.test_ids[3]]
    assert data.production.rows()==before


def test_current_menu_can_reopen_receipt_and_resume_a_failed_intake(page,data,tmp_path):
    from intake_link_ui import IntakeReceiptDialog
    source=tmp_path/'菜单重试验证软件.docx';source.write_bytes(b'recover from menu')
    plan=data.receive_interactive([str(source)],linked=True)['material_preview']
    with patch.object(data.production,'receive',side_effect=OSError('temporary write failure')):
        result=data.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
    assert result['pending_count']==1
    page.refresh();page.set_intake_result(result);w=page.task_workspace;w.sync_intake_actions()
    assert page.retry_action in w.more_menu.actions() and page.retry_action.isVisible() and page.retry_action.isEnabled()
    assert page.receipt_action in w.more_menu.actions() and page.receipt_action.isEnabled()
    with patch.object(IntakeReceiptDialog,'exec',return_value=QDialog.Rejected) as opened:
        page.receipt_action.trigger();assert opened.called
    with patch.object(page,'request') as queued:
        page.retry_action.trigger();assert queued.called;done=queued.call_args.args[1]()
    assert done['pending_count']==0 and done['copied_count']==1
    page.refresh();w.sync_intake_actions();assert not page.retry_action.isVisible()
    assert len([r for r in data.production.rows() if r['name']=='菜单重试验证软件'])==1
