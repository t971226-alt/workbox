"""Verify compact category controls and novice material actions in the frozen app."""
from pathlib import Path

def verify(page,root,screenshot):
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QMessageBox,QStyleOptionViewItem,QStyle
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from theme import set_theme
    from task_requirements_ui import TaskMaterialsDialog
    from saved_locations import saved_file_location
    root=Path(root);root.mkdir(exist_ok=True);s=page.service;w=page.task_workspace
    def settle():
        for _ in range(5):QApplication.processEvents()
    page.show_all();counts=dict(w.rules.counts);assert len(counts)>=2
    for name in counts:
        w.choose_category(name);settle();assert dict(w.rules.counts)==counts
    w.choose_category(None);settle();w.select_all_check.click();assert len(w.selected_versions)==len(w.rows)
    w.table.item(0,0).setCheckState(Qt.Unchecked);assert w.select_all_check.checkState()==Qt.PartiallyChecked
    w.select_all_check.click();assert len(w.selected_versions)==len(w.rows);w.clear_checks()
    w.filter_menu.popup(w.filter_button.mapToGlobal(w.filter_button.rect().bottomLeft()));settle();assert w.state.isVisible() and w.period.isVisible();w.filter_menu.close()
    kinds=['软件说明书.docx','软件说明书.pdf','登记申请信息.txt','软件著作权代码段.docx','软件著作权代码段.pdf'];pid=s.production.create_batch('紧凑界面验证',['建筑工程施工现场安全风险预警系统'],kinds)['project_ids'][0]
    for kind in kinds:
        p=root/kind;p.write_bytes(('isolated '+kind).encode());s.production.receive(pid,kind,p)
    page.refresh();project=next(r for r in s.production.rows() if r['id']==pid);d=TaskMaterialsDialog(page,project);d.show();settle()
    try:
        assert d.width()<=700 and d.height()<=455 and d.table.rowCount()==5 and d.table.isColumnHidden(0)
        assert all(d.table.rowHeight(i)==38 for i in range(5))
        for i in range(2):
            index=d.table.model().index(i,1);option=QStyleOptionViewItem();d.table.initViewItemOption(option);d.table.itemDelegateForIndex(index).initStyleOption(option,index);option.rect=d.table.visualRect(index)
            indicator=d.table.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator,option,d.table);assert not indicator.isEmpty();QTest.mouseClick(d.table.viewport(),Qt.LeftButton,pos=indicator.center())
        assert len(d.checked)==2;assert d.grab().save(str(screenshot))
        set_theme('dark');settle();assert d.grab().save(str(Path(screenshot).with_suffix('.dark.png')));set_theme('light');settle()
        d.all_check.click();assert len(d.checked)==5 and d.pass_button.isEnabled()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
            d.pass_button.click();assert queued.called
            result=queued.call_args.args[1]();assert result['reviewed_count']==5
        page.refresh();settle();assert not d.checked;d.all_check.click();assert d.submit_button.isEnabled()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
            d.submit_button.click();assert queued.called;submitted=queued.call_args.args[1]();assert submitted['submitted_count']==5
        page.refresh();settle();m=d.materials[0];expected=saved_file_location(s,pid,kind=m['kind'])
        with patch('task_requirements_ui.QDesktopServices.openUrl',return_value=True) as opened:
            d.open_file(m['kind'],m['id']);assert Path(opened.call_args.args[0].toLocalFile())==expected
        assert f'_WB{pid:06d}' in d.location.text() and '不属于软件名称' in d.identity.toolTip()
    finally:set_theme('light');d.close();d.deleteLater();settle()
    from task_query import status
    page.show_all();ready=[r['id'] for r in w.rows if status(r)=='待核对'];assert ready
    for i,r in enumerate(w.rows):
        if r['id'] in ready:w.table.item(i,0).setCheckState(Qt.Checked)
    assert w.review_button.isEnabled()
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
        w.review_button.click();assert queued.called;reviewed=queued.call_args.args[1]();assert reviewed['reviewed_count']>=len(ready)
    page.refresh();settle()
    for i,r in enumerate(w.rows):
        if r['id'] in ready:w.table.item(i,0).setCheckState(Qt.Checked)
    assert w.submit_button.isEnabled()
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(page,'request') as queued:
        w.submit_button.click();assert queued.called;submitted=queued.call_args.args[1]();assert submitted['submitted_count']>=len(ready)
    return dict(compact_filter_toolbar_and_stable_categories=True,current_result_multiselect=True,novice_material_dialog_and_bulk_review=True,material_folder_ids_preserved_and_explained=True)
