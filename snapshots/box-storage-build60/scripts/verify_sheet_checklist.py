"""Verify main-sheet intake, cancellation, real matching and saved-folder routing."""
from pathlib import Path
from unittest.mock import patch


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from PySide6.QtCore import Qt
    from openpyxl import Workbook
    from desktop_service import DesktopService
    from production_ui import ProductionPage
    from sheet_checklist import load_checklist,compare
    from sheet_checklist_ui import SheetImportDialog,ChecklistOverview,ChecklistDetails
    from theme import install_theme,set_theme
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True);install_theme(s)
    pid=s.production.create_batch('本次',['甲软件系统'],['说明书.docx','代码.docx'])['project_ids'][0]
    source=root/'甲软件系统.docx';source.write_bytes(b'manual');s.production.receive(pid,'说明书.docx',source)
    duplicate=s.production.create_batch('重做',['同名软件系统'],['说明书.docx'])['project_ids'][0]
    s.production.create_batch('原批次',['同名软件系统'],['说明书.docx'])
    w=Workbook();sheet=w.active;sheet.append(['日期','软件名称']);sheet.append(['2026-11-03','甲软件系统']);sheet.append([None,'未收录软件系统']);sheet.append([None,'同名软件系统']);sheet.merge_cells('A2:A4');p=root/'名称清单.xlsx';w.save(p)
    page=ProductionPage(s);page.enable_workspace();page.resize(950,530);page.show();app.processEvents()
    assert page.task_workspace.sheet_import_button.isVisible() and page.task_workspace.sheet_import_button.text()=='导入表格'
    before=s.production.rows();original=p.read_bytes()
    with patch('sheet_checklist_ui.QFileDialog.getOpenFileName',return_value=(str(p),'')),patch.object(SheetImportDialog,'exec',return_value=QDialog.Rejected):
        page.task_workspace.sheet_import_button.click()
    assert not load_checklist(s.db.path.parent) and before==s.production.rows()
    d=SheetImportDialog(page,p);d.show();app.processEvents()
    assert d.name_column.currentData()==1 and d.date_column.currentData()==0
    assert [d.preview.item(i,1).text() for i in range(3)]==['材料不齐','尚未收录','同名待确认']
    d.reject();assert not load_checklist(s.db.path.parent) and before==s.production.rows()
    d.deleteLater();d=SheetImportDialog(page,p);d.commit();assert d.result()==QDialog.Accepted
    entries=load_checklist(s.db.path.parent);assert len(entries)==3 and all(e['date']=='2026-11-03' for e in entries)
    d2=SheetImportDialog(page,p);d2.commit();assert len(load_checklist(s.db.path.parent))==3
    overview=ChecklistOverview(page);overview.show();app.processEvents()
    assert len(overview.entries)==4 and all(t.columnCount()==2 for t in overview.tables)
    assert any(e['name']=='未收录软件系统' and e['state']=='尚未收录' for e in overview.entries)
    entry=compare([dict(name='甲软件系统',date='2026-11-03')],s.production.rows())[0]
    detail=ChecklistDetails(page,entry);detail.show();app.processEvents()
    assert detail.files.rowCount()==2 and detail.folder.isEnabled() and detail.target.is_dir()
    with patch('saved_locations.open_location') as opened:
        detail.folder.click();assert opened.call_args.args[0]==detail.target
    same=ChecklistDetails(page,compare([dict(name='同名软件系统',date='')],s.production.rows())[0]);same.show();app.processEvents()
    assert same.choice.isVisible() and not same.folder.isEnabled() and same.row is None
    same.choice.setCurrentIndex(same.choice.findData(duplicate));assert same.row['id']==duplicate
    missing=ChecklistDetails(page,compare([dict(name='未收录软件系统',date='')],s.production.rows())[0]);assert not missing.folder.isEnabled()
    assert before==s.production.rows() and p.read_bytes()==original and source.read_bytes()==b'manual'
    dated=root/'日期筛选.xlsx';w2=Workbook();sh=w2.active;sh.append(['日期','软件名称']);sh.append(['2026-11-02','前一天系统']);sh.append(['2026-11-03','当天系统']);sh.append(['2026-11-04','后一天系统']);w2.save(dated)
    filtered=SheetImportDialog(page,dated);filtered.show();app.processEvents()
    filtered.date_scope.setCurrentIndex(filtered.date_scope.findData('day'));filtered.date_start.setCurrentIndex(filtered.date_start.findData('2026-11-03'));assert [e['name'] for e in filtered.entries]==['当天系统']
    filtered.date_scope.setCurrentIndex(filtered.date_scope.findData('range'));filtered.date_start.setCurrentIndex(filtered.date_start.findData('2026-11-02'));filtered.date_end.setCurrentIndex(filtered.date_end.findData('2026-11-03'));assert len(filtered.entries)==2
    filtered.commit();assert {e['name'] for e in load_checklist(s.db.path.parent)}.issuperset({'前一天系统','当天系统'}) and '后一天系统' not in {e['name'] for e in load_checklist(s.db.path.parent)}
    assert before==s.production.rows();filtered.close();filtered.deleteLater()
    for mode in ('light','dark'):
        set_theme(mode);app.processEvents();overview.grab().save(str(root/('overview-'+mode+'.png')));detail.grab().save(str(root/('details-'+mode+'.png')));page.grab().save(str(root/('main-'+mode+'.png')))
    for widget in (missing,same,detail,overview,d,d2,page):widget.close();widget.deleteLater()
    app.processEvents()
    return dict(sheet_import_day_and_inclusive_range_filter_only_selected_records=True,main_sheet_import_is_visible_and_cancellation_read_only=True,
        sheet_merged_dates_duplicates_and_ambiguous_names_are_preserved=True,
        compact_two_column_checklist_and_details_folder_target_verified=True)


if __name__=='__main__':
    import sys,json
    print(json.dumps(verify(sys.argv[1]),ensure_ascii=False))
