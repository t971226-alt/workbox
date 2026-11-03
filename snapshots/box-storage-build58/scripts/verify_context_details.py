"""Exercise real provenance, selected versions and shared native menus in isolation."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import json


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from production_ui import ProductionPage,MaterialDialog
    from task_requirements_ui import TaskMaterialsDialog,RequirementsDialog
    from zip_intake_ui import OriginalZipDetailsDialog
    from intake_history import receive_original
    from saved_locations import original_file_location,saved_file_location
    from material_context import task_menu
    from theme import install_theme,set_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    source=root/'提交用'/'原始 示例系统.zip';source.parent.mkdir()
    with ZipFile(source,'w') as z:z.writestr('unchanged.txt',b'original one')
    receive_original(s,[str(source)]);row=s.production.rows()[0];pid=row['id'];receipt=row['original_zips'][0]
    assert original_file_location(s,pid,zip_id=receipt['id'])==source.resolve()
    saved=saved_file_location(s,pid,zip_id=receipt['id']);assert saved!=source.resolve() and saved.read_bytes()==source.read_bytes()
    install_theme(s)
    page=ProductionPage(s)
    page.enable_workspace()
    d=OriginalZipDetailsDialog(page,row);d.show()
    for _ in range(6):app.processEvents()
    assert d.width()<=560 and d.height()<290,(d.width(),d.height())
    assert not d.open_button.isVisible() and not d.folder_button.isVisible() and not d.extract_button.isVisible()
    menu=d.file_menu();actions={a.text():a for a in menu.actions()}
    with patch('saved_locations.open_location') as locate,patch('PySide6.QtGui.QDesktopServices.openUrl',return_value=True) as opened:
        actions['打开原始位置'].trigger();assert locate.call_args.args[0]==source.resolve() and not opened.called
        actions['打开保存位置'].trigger();assert locate.call_args.args[0]==saved and not opened.called
        d.open_file();assert Path(opened.call_args.args[0].toLocalFile())==saved
    # Provenance still works if BOX's copy is missing; source is never substituted.
    saved_bytes=saved.read_bytes();saved.unlink();assert original_file_location(s,pid,zip_id=receipt['id'])==source.resolve();saved.write_bytes(saved_bytes)
    source.rename(source.with_suffix('.moved'))
    with patch('saved_locations.open_location') as locate:
        d.open_original();assert not locate.called and '原始文件已移动或删除' in d.error.text()
    source.with_suffix('.moved').rename(source);d.report_error('')
    # Different historical raw versions must resolve their own original input.
    second=root/'另一来源.zip'
    with ZipFile(second,'w') as z:z.writestr('changed.txt',b'original two')
    from intake_link import register_original_zip
    from intake_history import save
    copy=saved.parent/'v2.zip';copy.write_bytes(second.read_bytes())
    result=register_original_zip(s,dict(project_id=pid,working_zip_path=str(copy)),str(second))
    with s.db.connect() as c:r=dict(c.execute('SELECT * FROM original_zip_receipts WHERE project_id=? AND path=?',(pid,str(copy))).fetchone())
    result['files'][0].update(receipt_id=r['id'],source_sha256=r['sha256']);save(s.production,result)
    row=next(r for r in s.production.rows() if r['id']==pid);d.close();d.deleteLater();d=OriginalZipDetailsDialog(page,row);d.show()
    for _ in range(5):app.processEvents()
    with patch('saved_locations.open_location') as locate:
        for i,r in enumerate(d.archives):
            d.table.selectRow(i);d.open_original();assert locate.call_args.args[0]==(second.resolve() if r['id']==result['files'][0]['receipt_id'] else source.resolve())
    # Unknown provenance must not pretend the saved copy is the original.
    with s.db.connect() as c:c.execute('DELETE FROM intake_history');c.execute('DELETE FROM production_imports')
    try:original_file_location(s,pid,zip_id=receipt['id'])
    except ValueError as e:assert '没有记录原始位置' in str(e)
    else:raise AssertionError('Unknown source must not fall back to saved archive')
    # Main task menu has one rule entry, shared source/saved actions and raw-only extraction.
    workspace=page.task_workspace
    menu=task_menu(workspace,row);captions=[a.text() for a in menu.actions()]
    assert '调整收纳规则' not in captions and '重新识别分类' not in captions and '打开原始位置' in captions and '解压并整理' in captions
    with patch.object(workspace,'open_original') as opened:
        next(a for a in menu.actions() if a.text()=='打开原始位置').trigger();opened.assert_called_once_with(pid)
    material_pid=s.production.create_batch('普通材料',['普通材料示例系统'],['说明书.docx','代码.docx'])['project_ids'][0]
    for name in ('说明书.docx','代码.docx'):
        p=root/name;p.write_bytes(name.encode());s.production.receive(material_pid,name,p)
    material_row=next(r for r in s.production.rows() if r['id']==material_pid)
    m=material_row['materials'][0];assert original_file_location(s,material_pid,version_id=m['id'])==(root/m['kind']).resolve()
    menu2=task_menu(workspace,material_row);assert '解压并整理' not in [a.text() for a in menu2.actions()]
    task=TaskMaterialsDialog(page,material_row);task.show()
    for _ in range(6):app.processEvents()
    assert task.width()<=560 and task.height()<340 and not task.folder_button.isVisible()
    menu3=task.file_menu(0)
    with patch('saved_locations.open_location') as locate:
        next(a for a in menu3.actions() if a.text()=='打开原始位置').trigger();assert locate.call_args.args[0]==(root/m['kind']).resolve()
    with patch('task_requirements_ui.QDesktopServices.openUrl',return_value=True) as opened:
        task.table.cellDoubleClicked.emit(0,1);assert not opened.called
    assert task.table.cellWidget(0,3).text()=='查看材料'
    with patch.object(task,'open_material') as show:
        task.table.cellWidget(0,3).click();show.assert_called_once_with(0,1)
    detail=MaterialDialog(page,material_row,m);detail.show()
    replacement=root/'另一个原始版本.docx';replacement.write_bytes(b'updated version')
    s.production.receive(material_pid,m['kind'],replacement);detail.refresh_storage()
    with patch('saved_locations.open_location') as locate:
        history_menu=detail.history_menu(1)
        next(a for a in history_menu.actions() if a.text()=='打开原始位置').trigger()
        assert locate.call_args.args[0]==(root/m['kind']).resolve()
        detail.open_original_location();assert locate.call_args.args[0]==replacement.resolve()
        history_menu.deleteLater()
    req=RequirementsDialog(page,material_row);assert not req.rematch.isChecked()
    with patch.object(RequirementsDialog,'exec',return_value=QDialog.Accepted),patch.object(page,'request') as requested:
        # The one rule dialog also exposes the existing reclassification operation.
        def accept_rules(dialog):
            dialog.required=[m['kind'] for m in material_row['materials']];dialog.reason.setText('验证统一规则入口');dialog.rematch.setChecked(True)
            return QDialog.Accepted
        with patch.object(RequirementsDialog,'exec',accept_rules):page.edit_task(material_row)
        assert requested.called;assert requested.call_args.args[1]()['classified_count']>=2
    for mode in ('light','dark'):
        set_theme(mode)
        for _ in range(5):app.processEvents()
        assert d.grab().save(str(root/('压缩包详情-'+mode+'.png')))
        assert task.grab().save(str(root/('材料详情-'+mode+'.png')))
    set_theme('light')
    for widget in (req,detail,task,d,page):widget.close();widget.deleteLater()
    for menu in (menu,menu2,menu3):menu.deleteLater()
    app.processEvents();install_theme()
    return dict(context_menu_unifies_task_actions_and_compact_details=True,
        original_and_saved_locations_use_selected_version_and_never_fallback=True)
