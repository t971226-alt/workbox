"""Storage-first workflow verification, using isolated data and real Qt widgets."""
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
import csv


def verify(root):
    from PySide6.QtWidgets import QApplication,QPushButton
    from PySide6.QtCore import Qt
    from desktop_service import DesktopService
    from production_ui import ProductionPage,MaterialDialog
    from task_requirements_ui import TaskMaterialsDialog
    from master_ledger_ui import MasterLedgerDialog,values,HEADERS
    from classification_ui import ClassificationDialog
    from task_query import status,unfinished,query,receipt_time
    from saved_locations import collection_location
    from theme import install_theme,set_theme
    from workspace_queries import parse_query,answer
    from intake_link import import_receipt
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);install_theme()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    def create(name,kinds):return s.production.create_batch(name+'批次',[name],kinds)['project_ids'][0]
    def row(pid):return next(r for r in s.production.rows() if r['id']==pid)
    def source(name,data):p=root/name;p.write_bytes(data);return p
    flags={}
    pid=create('收纳验证系统',['软件说明书.docx','软件说明书.pdf'])
    a=source('收纳验证系统.docx',b'word-v1');b=source('收纳验证系统.pdf',b'pdf-v1')
    s.production.receive(pid,'软件说明书.docx',a)
    assert status(row(pid))=='待补材料' and row(pid)['collection_count']==1
    s.production.receive(pid,'软件说明书.pdf',b)
    assert status(row(pid))=='已收齐' and row(pid)['reviewed_count']==0
    flags['receipt_completes_without_review_or_submission']=True
    # Historical check failure never makes valid stored files incomplete.
    with s.db.connect() as c:
        vid=row(pid)['materials'][0]['id']
        c.execute("UPDATE batch_processing_modes SET mode='review' WHERE batch_id=?",(row(pid)['batch_id'],))
        c.execute('INSERT INTO material_reviews(version_id,passed,note) VALUES (?,0,?)',(vid,'旧核对记录，必须保留'))
    assert status(row(pid))=='已收齐' and s.production.history(pid,'软件说明书.docx')[0]['reviews'][0]['note']=='旧核对记录，必须保留'
    a.write_bytes(b'word-v2');s.production.receive(pid,'软件说明书.docx',a)
    assert row(pid)['collection_count']==2 and status(row(pid))=='已收齐'
    history=s.production.history(pid,'软件说明书.docx');assert len(history)==2 and Path(history[-1]['path']).read_bytes()==b'word-v1'
    flags['old_records_preserved_and_dependencies_do_not_gate_storage']=True
    before=row(pid)['materials'][0]['id'];r=s.receive_interactive([str(a)],project_id=pid,material_kind='软件说明书.docx')
    assert not r.get('material_conflicts') and row(pid)['materials'][0]['id']==before and len(s.production.history(pid,'软件说明书.docx'))==2
    flags['identical_intact_file_does_not_add_version']=True
    a.write_bytes(b'word-v3');pending=s.receive_interactive([str(a)],project_id=pid,material_kind='软件说明书.docx');g=pending['material_conflicts'][0]
    s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='skip',project_id=pid)})
    assert row(pid)['materials'][0]['id']==before
    pending=s.receive_interactive([str(a)],project_id=pid,material_kind='软件说明书.docx');g=pending['material_conflicts'][0]
    s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=pid)})
    assert row(pid)['collection_count']==2 and len(s.production.history(pid,'软件说明书.docx'))==3
    flags['skip_and_replace_preserve_counts_and_history']=True
    a.write_bytes(b'new-task');pending=s.receive_interactive([str(a)],project_id=pid,material_kind='软件说明书.docx');g=pending['material_conflicts'][0]
    s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='new',project_id=pid)})
    assert len(s.production.rows())==2 and status(s.production.rows()[-1])=='待补材料'
    flags['same_name_new_task_is_separate']=True
    published=collection_location(s,pid,'软件说明书.pdf');published.write_bytes(b'changed-external')
    assert status(row(pid))=='有异常' and unfinished(row(pid)) and row(pid)['collection_count']==1
    s.production.receive(pid,'软件说明书.pdf',b)
    assert status(row(pid))=='已收齐'
    flags['tamper_is_not_counted_as_saved_and_reimport_repairs']=True
    raw=source('原包验证系统.zip',b'')
    with ZipFile(raw,'w') as z:z.writestr('软件说明书.docx',b'archive member')
    raw_result=s.receive_interactive([str(raw)],zip_mode='original');rawid=raw_result['project_ids'][0]
    assert row(rawid)['collection_count']==0 and status(row(rawid))=='原包已收录'
    flags['raw_zip_never_counts_unrecognized_members']=True
    # Partially received tasks are searchable by receipt date, including carryover.
    with s.db.connect() as c:
        c.execute("UPDATE material_versions SET created_at='2026-10-29T08:00:00Z' WHERE project_id=?",(pid,))
        c.execute("UPDATE original_zip_receipts SET created_at='2026-10-30T08:00:00Z'")
        c.execute("UPDATE material_versions SET created_at='2026-10-31T08:00:00Z' WHERE project_id NOT IN (?,?)",(pid,rawid))
    results,recent=query(s.production.rows(),s.production.local_date,view='completed')
    assert recent=='2026-10-31' and results and all(status(r)=='待补材料' for r in results)
    assert row(rawid)['id'] not in {r['id'] for r in query(s.production.rows(),s.production.local_date,view='unfinished')[0]}
    flags['latest_receipt_search_includes_incomplete_and_carryover']=True
    spec=parse_query('显示全部');assert len(answer(s.production,spec)['matched_project_ids'])==len(s.production.rows())
    spec=parse_query('今天收录了多少');assert spec['metric']=='received' and '检查通过' not in answer(s.production,spec)['response']
    flags['conversation_uses_storage_semantics']=True
    export=root/'收录总表.csv';s.production.export_master(export)
    with export.open(encoding='utf-8-sig') as f:
        records=list(csv.DictReader(f));assert len(records)==3 and '已检查' not in records[0] and '收纳状态' in records[0]
    flags['master_export_has_storage_columns']=True
    page=ProductionPage(s);page.enable_workspace();page.resize(760,600);page.show()
    for _ in range(4):app.processEvents()
    w=page.task_workspace;assert w.tabs.tabText(0).startswith('待整理') and w.tabs.tabText(1)=='收录查询'
    w.select_all_check.click();assert len(w.selected_versions)==len(w.rows) and not w.review_button.isVisible() and not w.submit_button.isVisible()
    w.tabs.setCurrentIndex(1)
    for _ in range(4):app.processEvents()
    assert len(w.rows)==1 and w.rows[0]['id']!=pid
    w.export_to(root/'当前筛选.csv')
    with (root/'当前筛选.csv').open(encoding='utf-8-sig') as f:records=list(csv.DictReader(f));assert len(records)==1 and '提交时间' not in records[0]
    flags['workbench_storage_tabs_selection_and_filtered_export']=True
    d=TaskMaterialsDialog(page,row(pid));d.show();app.processEvents()
    assert not d.pass_button.isVisible() and not d.submit_button.isVisible() and '核对通过' not in d.progress.text()
    assert all(d.table.item(i,2).text()=='已收录' for i in range(d.table.rowCount()))
    detail=MaterialDialog(page,row(pid),row(pid)['materials'][0]);detail.show();app.processEvents()
    assert detail.height()<=300 and not detail.history_table.isVisible() and not detail.pass_button.isVisible()
    assert Path(detail.location.text())==collection_location(s,pid,'软件说明书.docx') and '待检查' not in detail.current.text()
    detail.history_toggle.click();app.processEvents();assert detail.history_table.isVisible() and detail.history_table.rowCount()==3
    with patch('production_ui.QDesktopServices.openUrl',return_value=True) as opened:
        detail.open_version(2,0);assert Path(opened.call_args.args[0].toLocalFile())==Path(s.production.history(pid,'软件说明书.docx')[2]['path'])
    with patch('saved_locations.open_location') as located:
        detail.open_saved_location();assert located.call_args.args[0]==collection_location(s,pid,'软件说明书.docx')
    flags['compact_material_detail_and_explicit_history_target']=True
    ledger=MasterLedgerDialog(s);ledger.show();app.processEvents()
    assert '收纳状态' in HEADERS and '待检查' not in values(row(pid)) and '已收齐' in values(row(pid))
    rule=ClassificationDialog(s);rule.show();app.processEvents();assert not rule.mode.isVisible() and rule.mode.currentData()=='collect'
    flags['ledger_and_rules_have_one_storage_workflow']=True
    preview=root/'材料详情-收纳.png';detail.history_toggle.click();app.processEvents();detail.grab().save(str(preview));d.grab().save(str(root/'材料清单-收纳.png'));page.grab().save(str(root/'收录查询.png'))
    set_theme('dark');app.processEvents();detail.grab().save(str(root/'材料详情-深色.png'));set_theme('light')
    for widget in [rule,ledger,detail,d,page]:widget.close();widget.deleteLater()
    app.processEvents()
    # Restart reads the same task IDs and archived history; no material migration.
    restarted=DesktopService(s.db.path);assert len(restarted.production.rows())==3 and len(restarted.production.history(pid,'软件说明书.docx'))==3
    flags['restart_preserves_ids_paths_and_history']=True
    return flags


def verify_portable(report_path):
    import sys,json,hashlib,traceback
    from tempfile import mkdtemp
    report_path=Path(report_path).resolve();report_path.parent.mkdir(parents=True,exist_ok=True)
    root=Path(mkdtemp(prefix='BOX-storage-check-',dir=report_path.parent))
    report=dict(passed=False,frozen=bool(getattr(sys,'frozen',False)),workspace=str(root))
    if report['frozen']:report['executable_sha256']=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    try:
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        report.update(verify(root/'workflow'))
        from scripts.verify_intake_transactions import verify as transactions
        from scripts.verify_bulk_overwrite import verify as bulk
        report.update(transactions(root/'transactions'));report.update(bulk(root/'bulk'))
        from scripts.verify_zip_compact_flow import verify as zip_flow
        report.update(zip_flow(root/'zip-flow'))
        from scripts.verify_storage_management import verify as storage_management
        report.update(storage_management(root/'storage-management'))
        from scripts.verify_dialog_density import verify as dialog_density
        report.update(dialog_density(root/'dialog-density'))
        from scripts.verify_office_drop import verify as office_drop
        report.update(office_drop(root/'office-drop'))
        from scripts.verify_rule_intake import verify as rule_intake
        report.update(rule_intake(root/'rule-intake'))
        from scripts.verify_family_intake import verify as family_intake
        report.update(family_intake(root/'family-intake'))
        from scripts.verify_batch_archive import verify as batch_archive
        report.update(batch_archive(root/'batch-archive'))
        from scripts.verify_name_templates import verify as name_templates
        report.update(name_templates(root/'name-templates'))
        from scripts.verify_grouped_intake import verify as grouped_intake
        report.update(grouped_intake(root/'grouped-intake'))
        from scripts.verify_daily_receipts import verify as daily_receipts
        report.update(daily_receipts(root/'daily-receipts'))
        from scripts.verify_working_names import verify as working_names
        report.update(working_names(root/'working-names'))
        from scripts.verify_context_details import verify as context_details
        report.update(context_details(root/'context-details'))
        from scripts.verify_orb_activation import verify as orb_activation
        report.update(orb_activation(root/'orb-activation'))
        from scripts.verify_orb_docking import verify as orb_docking
        report.update(orb_docking(root/'orb-docking'))
        from scripts.verify_metal_branding import verify as metal_branding
        report.update(metal_branding(root/'metal-branding'))
        from scripts.verify_independent_startup import verify as independent_startup
        report.update(independent_startup(root/'independent-startup'))
        from scripts.verify_folder_naming import verify as folder_naming
        report.update(folder_naming(root/'folder-naming'))
        from scripts.verify_keyword_rules import verify as keyword_rules
        report.update(keyword_rules(root/'keyword-rules'))
        from scripts.verify_simple_rules import verify as simple_rules
        report.update(simple_rules(root/'simple-rules'))
        from scripts.verify_folder_routes import verify as folder_routes
        report.update(folder_routes(root/'folder-routes'))
        report['passed']=True
    except BaseException:report['error']=traceback.format_exc()
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1
