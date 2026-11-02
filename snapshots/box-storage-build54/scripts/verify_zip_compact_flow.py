"""Exercise the actual ZIP dialog and copy paths inside the distributed build."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import time


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from PySide6.QtTest import QTest
    from desktop_service import DesktopService
    from zip_intake_ui import ZipIntakeDialog,OriginalZipDetailsDialog
    from quick_classification_ui import QuickClassificationDialog
    from task_query import status,unfinished
    from production_ui import ProductionPage
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    source=root/'压缩包归档管理系统.zip'
    with ZipFile(source,'w') as z:z.writestr('软件说明书.docx',b'word v1')
    def settle(condition=lambda:True):
        end=time.monotonic()+15
        while time.monotonic()<end:
            app.processEvents()
            if condition():return
            time.sleep(.01)
        raise AssertionError('ZIP dialog did not settle')
    def close(d):d.reject();settle(lambda:not d.scan or not d.scan.isRunning());d.deleteLater();app.processEvents()
    flags={}
    with patch('material_collection.unpack_material_zip',side_effect=AssertionError('raw ZIP must not be extracted')):
        d=ZipIntakeDialog(s,[str(source)]);d.show();settle();assert d.raw_radio.isChecked() and d.scan is None and not s.production.rows()
        assert d.width()<=700 and d.height()<=540
        d.grab().save(str(root/'ZIP-原包直接保存.png'));d.confirm();assert d.result()==QDialog.Accepted;result=d.operation();assert result['copied_count']==1
    close(d);row=s.production.rows()[0];pid=row['id'];saved=Path(row['original_zips'][0]['path'])
    assert saved.read_bytes()==source.read_bytes() and saved.is_relative_to(root/'材料库')
    assert row['collection_count']==0 and status(row)=='原包已收录' and not unfinished(row)
    assert not (s.db.path.parent/'材料收件缓存').exists()
    flags['zip_raw_saves_without_unpacking_or_rules']=True
    flags['zip_raw_is_complete_without_counting_members']=True
    d=ZipIntakeDialog(s,[str(saved)],start_mode='extract');d.show();settle(lambda:d.form is not None or bool(d.error.text()));assert d.form is not None,d.error.text()
    selector=d.form.category_selector;assert d.form.grouped_view.isVisible();selector.setCurrentIndex(selector.findData('__new__'));d.form.quick_editor.name.setText('说明书归档');settle()
    assert d.form.grouped_view.isVisible();d.grab().save(str(root/'ZIP-窗口内新建分类.png'));d.confirm();assert d.result()==QDialog.Accepted,d.error.text();result=d.operation();assert not result.get('material_conflicts');close(d)
    row=s.production.rows()[0];assert row['id']==pid and row['collection_count']==1 and status(row)=='已收齐' and saved.is_file()
    material_kind=row['materials'][0]['kind']
    assert any(p['name']=='说明书归档' for p in s.classifications.profiles(True))
    flags['zip_saved_original_can_be_classified_later']=True
    flags['zip_rule_created_inline_without_manager']=True
    with ZipFile(source,'w') as z:z.writestr('软件说明书.docx',b'word v2')
    d=ZipIntakeDialog(s,[str(source)],start_mode='extract');d.show();settle(lambda:d.form is not None or bool(d.error.text()));assert d.form is not None,d.error.text()
    d.confirm();assert d.conflicts.isVisible() and len(d.conflict_controls)==1
    d.replace_all();d.confirm();assert d.result()==QDialog.Accepted,d.error.text();result=d.operation();assert not result.get('material_conflicts');close(d)
    history=s.production.history(pid,material_kind)
    assert len(history)==2 and {Path(v['path']).read_bytes() for v in history}=={b'word v1',b'word v2'}
    assert s.production.rows()[0]['collection_count']==1
    flags['zip_duplicate_decision_stays_in_same_window']=True
    quick=QuickClassificationDialog(s);quick.show();settle();assert quick.width()<=520 and quick.height()<=360
    quick.editor.name.setText('紧凑新建分类');quick.grab().save(str(root/'新建分类-紧凑.png'));quick.save();assert quick.result()==QDialog.Accepted and quick.profile_id;quick.deleteLater();app.processEvents()
    flags['new_classification_has_compact_name_and_material_list']=True
    # Open the completed raw-only row through the workbench detail path.
    second=root/'仅保存原包系统.zip'
    with ZipFile(second,'w') as z:z.writestr('untouched.bin',b'raw')
    d=ZipIntakeDialog(s,[str(second)]);d.confirm();raw_result=d.operation();close(d)
    page=ProductionPage(s);page.enable_workspace();page.set_intake_result(raw_result);assert page.task_workspace.view=='completed';assert page.task_workspace.table.item(0,2).text()=='原包';assert page.task_workspace.table.item(0,5).text()=='—';row=s.production.rows()[-1];detail=OriginalZipDetailsDialog(page,row);detail.show();settle();assert detail.extract_button.isEnabled();detail.grab().save(str(root/'原包详情.png'))
    detail.close();detail.deleteLater();page.close();page.deleteLater();app.processEvents()
    flags['raw_detail_exposes_open_location_and_later_extract']=True
    return flags
