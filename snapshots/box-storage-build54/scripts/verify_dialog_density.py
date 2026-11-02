"""Exercise real compact dialog layouts and progressive storage statistics."""
from pathlib import Path
import time

def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog,QPushButton
    from desktop_service import DesktopService
    from classification_ui import ClassificationDialog
    from quick_classification_ui import QuickClassificationDialog
    from direct_material_ui import DirectMaterialDialog
    from direct_material_intake import DirectMaterialIntake
    from production_ui import ProductionPage,NewProductionDialog,MaterialDialog
    from task_requirements_ui import TaskMaterialsDialog,RequirementsDialog
    from master_ledger_ui import VersionHistoryDialog
    from storage_management_ui import StorageManagementDialog
    from intake_link_ui import ClassificationRuleDialog
    from intake_ui import IntakeDialog
    from duplicate_intake_ui import DuplicateMaterialDialog
    from storage_management import describe
    from theme import set_theme,install_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance();s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    original=root/'紧凑示例系统_软件说明书.docx';original.write_bytes(b'dialog preview')
    plan=DirectMaterialIntake(s).prepare([str(original)],include_known=True);s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect');project=s.production.rows()[0]
    install_theme(s);page=ProductionPage(s);windows=[];sizes={}
    def settle(condition=lambda:True):
        end=time.monotonic()+15
        stable=0
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            stable=stable+1 if condition() else 0
            if stable>=3:app.processEvents();return
        raise AssertionError('Dialog did not finish')
    def show(d,label,limit):
        windows.append(d);d.show();settle();assert d.width()<=900 and d.height()<=limit,(type(d).__name__,d.size())
        assert d.property('compactPopup')
        sizes[label]=[d.width(),d.height()];d.grab().save(str(root/(label+'.png')));return d
    storage=show(StorageManagementDialog(s),'存储管理',360);assert storage.table.rowCount()==4
    settle(lambda:not storage.job.isRunning());assert storage.table.item(3,1).text()=='1';storage.grab().save(str(root/'存储管理.png'))
    rule=show(ClassificationDialog(s),'管理分类',570);rule.advanced_button.click();settle();assert rule.advanced_panel.isVisible();rule.advanced_button.click();settle()
    old_height=rule.height()
    for _ in range(8):rule.quick_add('说明.docx')
    settle(lambda:rule.materials.verticalScrollBar().maximum()>0 and rule.height()<=570)
    assert rule.materials.verticalScrollBar().maximum()>0 and rule.height()<=570,(rule.height(),rule.materials.height(),rule.materials.rowCount())
    rule.dirty=False
    show(QuickClassificationDialog(s),'新建分类',340)
    show(ClassificationRuleDialog(s),'选择分类',300)
    show(DirectMaterialDialog(s,plan),'收录文件',410)
    detail=show(TaskMaterialsDialog(page,project),'材料清单',360);assert detail.table.rowCount()==1
    material=show(MaterialDialog(page,project,project['materials'][0]),'材料详情',300)
    material.history_toggle.click();settle();assert material.history_table.isVisible() and material.height()<390
    material.history_toggle.click();settle();assert material.height()<=300
    show(VersionHistoryDialog(s,project['id'],'软件说明书.docx'),'版本记录',280)
    req=show(RequirementsDialog(page,project),'调整清单',440);assert not req.history.isVisible();req.history_toggle.click();settle();assert req.history.isVisible()
    show(NewProductionDialog(),'新建任务',510)
    show(IntakeDialog(page),'粘贴名称',540)
    duplicate=show(DuplicateMaterialDialog(dict(name=project['name'],files=[str(original)],candidates=[project]),allow_overwrite_all=True),'同名处理',400)
    assert not duplicate.comparison.isVisible();duplicate.details_button.click();settle();assert duplicate.comparison.isVisible()
    duplicate.grab().save(str(root/'同名处理-展开.png'))
    seen=[];describe(s,progress=seen.append);assert len(seen)==4
    set_theme('dark');settle();storage.grab().save(str(root/'存储管理-深色.png'));set_theme('light')
    for d in reversed(windows):d.close();d.deleteLater()
    page.close();page.deleteLater();app.processEvents()
    from library_reset import reset
    cleared=reset(s.db.path,root/'恢复备份');assert cleared['cleared_projects']==1 and not s.production.rows() and original.is_file()
    import json
    (root/'尺寸.json').write_text(json.dumps(sizes,ensure_ascii=False,indent=2),encoding='utf-8')
    return dict(dialogs_fit_actual_rows_without_empty_canvas=True,novice_details_expand_without_extra_dialogs=True,
        storage_statistics_load_progressively_and_can_close=True,dialog_actions_and_large_lists_remain_accessible=True,
        test_library_reset_preserves_rules_and_originals=True)
