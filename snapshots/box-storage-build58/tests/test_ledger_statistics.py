from pathlib import Path
import pytest
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import QApplication
from desktop_service import DesktopService
from compact_ui import CompactPanel
from ledger_statistics import summaries,activity,state_key
from theme import install_theme,set_theme


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料',complete_setup=True)
    collect=s.production.create_batch('归档批次',['成品整理系统','等待补齐系统'],['软件说明书.docx'])['batch_id']
    review=s.production.create_batch('核对批次',['尚缺文件系统','资料核对系统','外部待报系统','已报送记录系统'],['软件说明书.docx'])['batch_id']
    for name in ('成品整理系统','资料核对系统','外部待报系统','已报送记录系统'):
        p=tmp_path/(name+'.docx');p.write_bytes(b'isolated material');s.receive_path(p)
    for r in s.production.rows():
        if r['name'] in ('成品整理系统','外部待报系统','已报送记录系统'):s.production.review(r['materials'][0]['id'],True)
        if r['name'] in ('成品整理系统','已报送记录系统'):s.production.submit([r['materials'][0]['id']])
    s.production.set_batch_mode(collect,'collect')
    return s,collect,review


@pytest.fixture
def panel(data,app):
    s,_,_=data;install_theme(s);p=CompactPanel(s);p.production_page.show_legacy_workspace();p.show();app.processEvents()
    yield p
    p.conversation_page.shutdown();p.document_pane.shutdown();p.close();p.deleteLater();install_theme()


def test_state_counts_partition_modes_without_inventing_checks(data):
    s,collect,review=data;rows=s.production.rows();counts=summaries(rows)
    assert counts['collect']==dict(total=2,collecting=1,organized=1)
    assert counts['review']==dict(collecting=1,checking=1,submitting=1,submitted=1)
    assert sum(counts['review'].values())==4
    assert s.production.rows(collect)[0]['reviewed_count']==s.production.rows(collect)[0]['submitted_count']==1
    assert summaries([],None)=={'review':dict(collecting=0,checking=0,submitting=0,submitted=0)}


def test_daily_monthly_activity_excludes_storage_history_from_reviews(data):
    s,_,_=data;rows=s.production.rows();day=s.production.local_date(rows[0]['created_at'])
    counts=activity(rows,day,False,s.production.local_date)
    assert counts['collect']==dict(added=2,collected=1)
    assert counts['review']==dict(added=4,collected=3,checked=2,submitted=1)
    assert activity(rows,day,True,s.production.local_date)==counts
    other=activity(rows,'2000-01-01',False,s.production.local_date)
    assert all(not value for group in other.values() for value in group.values())


def test_scope_is_explicit_and_mixed_modes_have_separate_panels(panel,data,app):
    s,collect,review=data;page=panel.production_page
    assert set(page.overview.counts)=={'collect','review'} and '全部批次' in page.stats_scope_label.text()
    page.grouped.select_batch(collect);page.stats_scope.setCurrentIndex(1);app.processEvents()
    assert page.overview.counts=={'collect':dict(total=2,collecting=1,organized=1)}
    assert '当前批次' in page.stats_scope_label.text() and not page.overview.panels['review'][0].isVisible()
    assert '检查' not in page.statistics_label.text() and '提交' not in page.statistics_label.text()
    page.grouped.select_batch(review);app.processEvents()
    assert set(page.overview.counts)=={'review'} and page.processing_switch.isChecked()


def test_card_filter_is_read_only_and_does_not_shrink_its_own_denominator(panel,data,app):
    s,collect,_=data;page=panel.production_page;before=s.production.rows()
    page.grouped.select_batch(collect);page.stats_scope.setCurrentIndex(1)
    page.overview.buttons[('collect','collecting')].click();app.processEvents()
    assert [r['name'] for r in page.rows]==['等待补齐系统']
    assert page.overview.counts['collect']['total']==2 and page.clear_stat_button.isVisible()
    page.clear_stat_button.click();app.processEvents();assert len(page.rows)==6 and s.production.rows()==before


def test_zero_card_keeps_batch_scope_and_clearing_restores_results(panel,data,app):
    s,_,review=data;page=panel.production_page;page.search.setText('已报送记录系统')
    page.grouped.select_batch(review);page.stats_scope.setCurrentIndex(1)
    page.overview.buttons[('review','checking')].click();app.processEvents()
    assert not page.rows and page.active_batch==review and page.processing_switch.isChecked()
    assert not page.processing_switch.isEnabled()
    assert page.overview.counts['review']['submitted']==1
    page.clear_stat_button.click();assert len(page.rows)==1 and len(s.production.rows())==6


def test_filtered_visible_batch_becomes_switch_target(panel,data,app):
    s,collect,review=data;page=panel.production_page
    page.grouped.select_batch(collect)
    page.overview.buttons[('review','submitted')].click();app.processEvents()
    assert page.rows and all(r['batch_id']==review for r in page.rows)
    assert page.active_batch==review and page.processing_switch.isChecked()


def test_switch_updates_mode_counts_but_keeps_existing_human_history(panel,data,app):
    s,collect,_=data;page=panel.production_page;page.grouped.select_batch(collect);page.stats_scope.setCurrentIndex(1)
    history=[(r['id'],r['reviewed_count'],r['submitted_count']) for r in s.production.rows()]
    page.processing_switch.click();app.processEvents()
    assert set(page.overview.counts)=={'review'} and page.overview.counts['review']['submitted']==1
    page.processing_switch.click();app.processEvents()
    assert set(page.overview.counts)=={'collect'} and page.overview.counts['collect']['organized']==1
    page.stats_scope.setCurrentIndex(0);app.processEvents();assert page.overview.height()<=110
    assert history==[(r['id'],r['reviewed_count'],r['submitted_count']) for r in s.production.rows()]


def test_period_picker_changes_activity_not_current_state_counts(panel,data,app):
    page=panel.production_page;before=page.overview.counts.copy()
    page.statistics_date.setDate(QDate(2000,1,1));app.processEvents()
    assert page.overview.counts==before and '新增 0' in page.statistics_label.text()
    detail_layout=page.legacy_container.layout()
    assert detail_layout.itemAt(detail_layout.count()-2).layout().indexOf(page.statistics_label)>=0
    assert page.layout().contentsMargins().top()==3


def test_mixed_queries_use_same_mode_semantics(data):
    from workspace_queries import parse_query,answer
    from datetime import date
    s,collect,_=data;day=s.production.local_date(s.production.rows()[0]['created_at'])
    result=answer(s.production,parse_query('统计今天的进度',date.fromisoformat(day)))
    assert '整理：' in result['response'] and '检查提交：' in result['response']
    found=answer(s.production,parse_query('显示已提交的软件',date.fromisoformat(day)))
    assert all(r['batch_id']!=collect for r in s.production.rows() if r['id'] in found['matched_project_ids'])


def test_native_statistics_labels_fit_light_dark_and_small_window(panel,data,app):
    page=panel.production_page
    for mode in ('light','dark'):
        set_theme(mode,panel.service)
        for width,height in [(870,640),(760,520)]:
            panel.resize(width,height);app.processEvents()
            assert panel.width()==width and panel.height()==height
            for control in (page.overview,page.stats_scope,page.receive_button,page.period_button):
                assert panel.rect().contains(control.mapTo(panel,control.rect().bottomRight()))
            for button in page.overview.buttons.values():
                if button.isVisible():
                    assert button.number.font().pixelSize()>=18 and button.label.font().pixelSize()>=10
                    assert button.number.width()>=button.number.fontMetrics().horizontalAdvance(button.number.text())
                    assert button.parentWidget().rect().contains(button.geometry().bottomRight())
                    for label in (button.number,button.label):
                        assert button.rect().contains(label.mapTo(button,label.rect().bottomRight()))
                        assert label.width()>=label.fontMetrics().horizontalAdvance(label.text())


def test_empty_ledger_follows_new_batch_default_without_creating_tasks(app,tmp_path):
    s=DesktopService(tmp_path/'empty/data/workbox.db');install_theme(s);p=CompactPanel(s);p.show();app.processEvents()
    try:
        page=p.production_page;page.show_legacy_workspace();assert set(page.overview.counts)=={'review'}
        page.processing_switch.click();app.processEvents()
        assert set(page.overview.counts)=={'collect'} and page.overview.counts['collect']['total']==0
        assert not s.production.rows()
        page.error.setText('材料未能收录，请重试');assert page.error.isVisible()
        page.error.setText('');assert page.error.isHidden()
    finally:p.conversation_page.shutdown();p.document_pane.shutdown();p.close();p.deleteLater();install_theme()


def test_large_counts_remain_readable_without_expanding_window(panel,data,app):
    page=panel.production_page;rows=page.overview_base_rows
    page.overview.update_rows(rows*120)
    panel.resize(760,520);app.processEvents()
    assert panel.width()==760 and panel.height()==520
    for button in page.overview.buttons.values():
        if button.isVisible():
            for label in (button.number,button.label):
                assert button.rect().contains(label.mapTo(button,label.rect().bottomRight()))
                assert label.width()>=label.fontMetrics().horizontalAdvance(label.text())
