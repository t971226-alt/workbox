"""Exercise the deployed task workbench against isolated material/version records."""
from pathlib import Path


def verify(root,screenshot):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from PySide6.QtCore import Qt
    from unittest.mock import patch
    from desktop_service import DesktopService
    from production_ui import ProductionPage
    from task_query import query
    from theme import set_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True);s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    s.classifications.save('自定义验收',['验收报告.docx','登记信息.txt','演示.mp4'])
    records=[('环网配电开关控制设备状态监测平台',['软件说明书.docx','登记申请信息.txt'],'review'),('箱式配电开关控制设备联动管控软件',['软件说明书.docx'],'review'),('低压配电参数配置嵌入式管控系统',['软件说明书.docx'],'review'),('高低压成套开关设备状态监测管理系统',['软件说明书.docx'],'review'),('高压开关分合闸状态实时监测平台',['软件说明书.docx'],'review'),('机器人课程课件图文排版编辑软件',['验收报告.docx','登记信息.txt','演示.mp4'],'collect')]
    ids=[]
    for i,(name,kinds,mode) in enumerate(records):
        result=s.production.create_batch('第'+str(i+1)+'批',[name],kinds,package='自定义验收' if i==5 else None);pid=result['project_ids'][0];ids.append(pid)
        if mode=='collect':s.production.set_batch_mode(result['batch_id'],'collect')
        if i==1:continue
        for kind in (kinds[:1] if i==0 else kinds):
            p=root/(str(i)+'-'+kind);p.write_bytes(('isolated '+kind).encode());s.production.receive(pid,kind,p)
        if i in (3,4):
            row=next(r for r in s.production.rows() if r['id']==pid);s.production.review(row['materials'][0]['id'],True)
            if i==4:s.production.submit([row['materials'][0]['id']])
    # Distinct local dates prove that latest-day filtering is not a fixed calendar date.
    with s.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-10-28T23:00:00Z'")
        c.execute("UPDATE material_reviews SET created_at='2026-10-30T15:00:00Z'")
        c.execute("UPDATE collected_materials SET collected_at='2026-10-29T15:00:00Z' WHERE project_id=?",(ids[5],))
    page=ProductionPage(s);page.enable_workspace();page.resize(790,650);page.show();QApplication.processEvents();QApplication.processEvents();w=page.task_workspace
    try:
        assert w.isVisible() and not page.legacy_container.isVisible() and len(w.rows)==4
        assert w.rules.add_button.isVisible() and w.period.currentData()=='all'
        assert '登记申请信息.txt' in w.table.item(next(i for i,r in enumerate(w.rows) if r['id']==ids[0]),3).toolTip()
        assert w.grab().save(str(screenshot))
        w.tabs.setCurrentIndex(1);QApplication.processEvents();assert len(w.rows)==2 and '2026-10-30' in w.period.currentText()
        assert w.grab().save(str(Path(screenshot).with_suffix('.completed.png')))
        w.search.setText('机器人课程');assert len(w.rows)==1 and '2026-10-29' in w.period.currentText()
        out=root/'筛选结果.csv';w.export_to(out);assert '机器人课程' in out.read_text(encoding='utf-8-sig') and '高压开关分合闸' not in out.read_text(encoding='utf-8-sig')
        w.reset();w.tabs.setCurrentIndex(0);idx=next(i for i,r in enumerate(w.rows) if r['id']==ids[3]);w.table.item(idx,0).setCheckState(Qt.Checked);assert ids[3] in w.selected_versions
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
            w.submit_selected();assert queued.called;queued.call_args.args[1]()
        w.refresh();assert ids[3] not in {r['id'] for r in w.rows}
        page.show_legacy_workspace();assert page.legacy_container.isVisible();page.legacy_back.click();assert w.isVisible()
        set_theme('dark');QApplication.processEvents();assert w.grab().save(str(Path(screenshot).with_suffix('.dark.png')));set_theme('light');QApplication.processEvents()
        page.resize(560,650);QApplication.processEvents();assert w.table.isColumnHidden(1)
        assert len(query(s.production.rows(),s.production.local_date)[0])==3
        from workspace_queries import parse_query,answer
        before=s.production.rows()
        for text in ('显示全部','收齐了多少？','哪些软件未提交'):
            w.search.setText('不存在的旧筛选');w.choose_category('自定义验收');w.state.setCurrentIndex(1)
            spec=parse_query(text);expected=answer(s.production,spec)
            page.apply_query(spec)
            assert {r['id'] for r in w.rows}==set(expected['matched_project_ids'])
        assert s.production.rows()==before
        page.show_all();assert not w.external_spec and len(w.rows)==3
        source=root/'菜单重试软件.docx';source.write_bytes(b'menu resume')
        plan=s.receive_interactive([str(source)],linked=True)['material_preview']
        with patch.object(s.production,'receive',side_effect=OSError('temporary isolated failure')):
            failed=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
        assert failed['pending_count']==1
        page.refresh();page.set_intake_result(failed);w.sync_intake_actions()
        assert page.receipt_action in w.more_menu.actions() and page.receipt_action.isEnabled()
        assert page.retry_action in w.more_menu.actions() and page.retry_action.isEnabled() and page.retry_action.isVisible()
        with patch.object(page,'request') as queued:
            page.retry_action.trigger();assert queued.called;resumed=queued.call_args.args[1]()
        assert resumed['copied_count']==1 and resumed['pending_count']==0
        page.refresh();w.sync_intake_actions();assert not page.retry_action.isVisible()
        from scripts.verify_compact_task_flow import verify as verify_compact
        compact_result=verify_compact(page,root/'compact-check',Path(screenshot).with_suffix('.materials.png'))
    finally:set_theme('light');page.close();page.deleteLater();QApplication.processEvents()
    return dict(task_workspace_default_unfinished_carries_over=True,task_workspace_latest_completion_and_custom_rules=True,task_workspace_export_and_pinned_submission=True,task_workspace_compact_icons_and_detail_entry=True,task_workspace_conversation_matches_reported_results=True,task_workspace_receipt_and_retry_accessible=True,**compact_result)
