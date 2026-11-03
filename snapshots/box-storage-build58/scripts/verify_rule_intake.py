"""Exercise approved compact rules and destructive-copy opt-in in isolation."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import time


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog,QMessageBox
    from desktop_service import DesktopService
    from theme import install_theme
    from zip_intake_ui import ZipIntakeDialog,MaterialIntakeDialog
    from quick_classification_ui import QuickClassificationDialog
    from direct_material_intake import DirectMaterialIntake
    from rule_chrome import FileBadge
    from intake_rule_options import archive_rules
    from production_ui import ProductionPage
    from task_query import status
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    service=DesktopService(root/'data/workbox.db');service.workspaces.configure_materials(root/'材料库',complete_setup=True);install_theme(service)
    def settle():
        for _ in range(10):app.processEvents()
    def close(d):d.close();d.deleteLater();settle()
    archive=root/'图标规则测试系统.zip'
    with ZipFile(archive,'w') as z:z.writestr('保留原件.txt',b'never unpack in raw mode')
    before=service.classifications.profiles(include_archives=True)
    dialog=ZipIntakeDialog(service,[str(archive)]);dialog.show();settle()
    assert dialog.width()==680 and dialog.height()<=540
    assert len(dialog.archive_picker.findChildren(FileBadge))>3 and dialog.archive_picker.current()['intake_mode']=='original'
    assert not archive_rules(service) and service.classifications.profiles(include_archives=True)==before
    dialog.grab().save(str(root/'压缩包-规则图标.png'));close(dialog)
    assert not service.production.rows() and not archive_rules(service)
    dialog=ZipIntakeDialog(service,[str(archive)]);dialog.show();settle();dialog.confirm();assert dialog.result()==QDialog.Accepted,dialog.error.text()
    assert not archive_rules(service);result=dialog.operation();close(dialog)
    row=service.production.rows()[0];pid=row['id'];saved=Path(row['original_zips'][0]['path'])
    assert saved.read_bytes()==archive.read_bytes() and saved.parent==root/'材料库/软件运行包/图标规则测试系统'
    assert len(archive_rules(service))==1 and set(archive_rules(service)[0]['extensions'])=={'.zip','.7z','.rar'}
    dialog=ZipIntakeDialog(service,[str(archive)]);assert dialog.archive_picker.current()['name']=='压缩包收纳';close(dialog)
    flags=dict(rule_icons_distinguish_alternatives_from_required_types=True,archive_rule_is_created_once_only_on_confirm=True,archive_rule_pins_exact_destination_without_migrating_history=True)
    quick=QuickClassificationDialog(service);quick.show();settle();assert quick.editor.table.item(0,0).text()==''
    quick.editor.name.setText('验收报告');assert quick.editor.kinds()==['验收报告.docx']
    assert quick.width()<=460 and quick.height()<=300;quick.grab().save(str(root/'新建规则-单份合并.png'))
    quick.save();assert quick.result()==QDialog.Accepted;close(quick)
    assert service.classifications.packages()['验收报告']==['验收报告.docx']
    flags['single_rule_name_is_reused_without_manual_default']=True
    # Unrecognized spreadsheet receives an inline suggested rule, with a real
    # final extension and no standalone classification dialog.
    office=root/'新手表格系统_统计表.xlsx';office.write_bytes(b'original spreadsheet bytes')
    plan=DirectMaterialIntake(service).prepare([str(office)],include_known=True)
    dialog=MaterialIntakeDialog(service,plan);dialog.show();settle();assert dialog.form.grouped_view.isVisible()
    from intake_onboarding import confirm_first_receipts
    confirm_first_receipts(dialog.form,remember=True)
    dialog.grab().save(str(root/'普通材料-内联规则.png'));dialog.confirm();assert dialog.result()==QDialog.Accepted,dialog.error.text()
    result=dialog.operation();assert result['copied_count']==1;close(dialog)
    office_row=next(r for r in service.production.rows() if r['name']=='新手表格系统')
    assert office_row['collection_count']==1 and office_row['materials'][0]['kind'].endswith('.xlsx')
    assert any(p['name']==office_row['package_name'] for p in service.classifications.profiles(True))
    flags['office_intake_creates_suggested_rule_in_the_same_confirmation']=True
    repeated=root/'另一个表格系统.xlsx';repeated.write_bytes(b'another spreadsheet')
    next_plan=DirectMaterialIntake(service).prepare([str(repeated)],include_known=True)
    assert next_plan['entries'][0]['package']==office_row['package_name']
    flags['repeated_office_type_reuses_rule_without_duplicate_profile']=True
    # Default deletion retains copies and allows complete restoration.
    service.production.delete_tasks([pid],confirmed=True);assert saved.is_file() and archive.is_file()
    service.production.restore_tasks([pid]);assert status(next(r for r in service.production.rows() if r['id']==pid))=='原包已收录'
    # SQL failure after staging restores every moved file, leaving active tasks.
    with patch.object(service.production,'_change_deleted_tasks',side_effect=OSError('isolated commit failure')):
        try:service.production.delete_tasks([pid],confirmed=True,delete_materials=True)
        except OSError:pass
        else:raise AssertionError('expected injected failure')
    assert saved.is_file() and any(r['id']==pid for r in service.production.rows())
    assert not list(saved.parent.glob('.box-delete-*'))
    result=service.production.delete_tasks([pid],confirmed=True,delete_materials=True)
    assert result['deleted_file_count']>=1 and not saved.exists() and archive.is_file()
    assert next(r for r in service.production.deleted_rows() if r['id']==pid)['materials_deleted']
    service.production.restore_tasks([pid]);assert not saved.exists() and status(next(r for r in service.production.rows() if r['id']==pid))=='有异常'
    flags['delete_files_is_explicit_preserves_sources_and_restore_discloses_missing_files']=True
    flags['task_file_deletion_rolls_back_before_commit']=True
    # The visible checkbox is opt-in; only its explicit state reaches the job.
    page=ProductionPage(service);queued=[];page.request=lambda title,op:queued.append(op)
    # Use the actual modal button click; QMessageBox.clickedButton is native.
    from PySide6.QtCore import QTimer
    def accept_delete():
        box=QApplication.activeModalWidget()
        if isinstance(box,QMessageBox):
            assert not box.checkBox().isChecked();box.checkBox().setChecked(True)
            assert '恢复任务不会找回' in box.informativeText()
            next(b for b in box.buttons() if b.text()=='删除任务').click()
    timer=QTimer();timer.timeout.connect(accept_delete);timer.start(10)
    try:page.delete_tasks([office_row])
    finally:timer.stop()
    assert len(queued)==1;queued[0]();assert office.is_file()
    page.close();page.deleteLater();settle()
    flags['delete_confirmation_checkbox_reaches_the_actual_operation']=True
    return flags
