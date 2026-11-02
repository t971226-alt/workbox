"""Exercise real rule persistence, first receipt, unknown keep/skip and copying."""
from pathlib import Path


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from quick_classification_ui import QuickClassificationDialog
    from file_families import infer
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    q=QuickClassificationDialog(s);q.editor.name.setText('说明书与代码')
    p=q.editor.simple_presentation;p.controls[0]['count'].setValue(2);app.processEvents()
    assert q.editor.table.rowCount()==2
    p.controls[0]['toggle'].setChecked(True);p.keyword(0,'');p.keyword(1,'代码');app.processEvents()
    draft=q.editor.draft();k0,k1=draft['kinds'];title='全源人才聚合研判招聘系统'
    assert draft['name_patterns'][k0]['mode']=='same'
    assert infer(title+'.doc',draft['kinds'],draft['family_map'],project_name=title,patterns=draft['name_patterns'])==k0
    assert infer(title+'_代码.docx',draft['kinds'],draft['family_map'],project_name=title,patterns=draft['name_patterns'])==k1
    assert infer(title+'_其他.docx',draft['kinds'],draft['family_map'],project_name=title,patterns=draft['name_patterns']) is None
    q.save();assert q.result()==QDialog.Accepted,q.error.text();q.close();q.deleteLater();app.processEvents()
    before=len(s.classifications.profiles(True));software='电子元器件成品性能测试管理系统'
    incoming=root/'incoming'/software;incoming.mkdir(parents=True)
    for name in (software+'_测试表.xlsx',software+'_报告.pdf',software+'_数据.custom'):(incoming/name).write_bytes(name.encode())
    plan=DirectMaterialIntake(s).prepare([str(incoming)],include_known=True,keep_archives=True)
    d=MaterialIntakeDialog(s,plan);d.show();app.processEvents();v=d.form.grouped_view
    g=next(g for g in v.groups if any(Path(plan['entries'][i]['path']).suffix=='.xlsx' for i in g['indices']))
    panel=g['onboarding'];assert panel.first and panel.remember.isChecked()
    panel.remember.setChecked(False)
    d.confirm();assert not d.operation
    assert panel.choices,'Unknown file must be visible'
    panel.folder.setText('表格与报告')
    for choice in panel.choices.values():choice.setCurrentIndex(choice.findData('keep'))
    panel.apply();app.processEvents();assert panel.confirmed,panel.error.text()
    assert not (root/'saved'/'表格与报告').exists(),'Preview created a directory before approval'
    d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==3,result
    assert len(s.classifications.profiles(True))==before,'Transient rule was remembered'
    out=root/'saved'/'表格与报告'/software
    assert len(list(out.iterdir()))==3 and any(f.suffix=='.custom' for f in out.iterdir())
    for f in out.iterdir():assert f.read_bytes() in [p.read_bytes() for p in incoming.iterdir()]
    d.close();d.deleteLater();app.processEvents()
    # Independent first receipt: skipped unknown never enters the saved preset.
    software2='实验室仪器现场安装工单管理系统';src=root/'incoming'/software2;src.mkdir()
    for suffix in ('.xlsx','.pdf','.custom'):(src/(software2+suffix)).write_bytes(suffix.encode())
    plan=DirectMaterialIntake(s).prepare([str(src)],include_known=True,keep_archives=True)
    d=MaterialIntakeDialog(s,plan);v=d.form.grouped_view;g=v.groups[0];panel=g['onboarding'];panel.editor.name.setText('表格和报告要求');panel.folder.setText('交付资料');panel.remember.setChecked(True)
    for choice in panel.choices.values():choice.setCurrentIndex(choice.findData('skip'))
    panel.apply();app.processEvents();assert panel.confirmed,panel.error.text()
    panel.folder.textEdited.emit('交付资料');assert not panel.confirmed
    d.confirm();assert not d.operation
    panel.apply();d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==2,result
    rule=next(p for p in s.classifications.profiles(True) if p['name']=='表格和报告要求');assert not any(k.endswith('.custom') for k in rule['kinds'])
    d.close();d.deleteLater();app.processEvents()
    return dict(simple_rule_type_counts_and_per_file_keywords_are_persisted=True,
                first_receipt_requires_explicit_arrangement_without_creating_files=True,
                unknown_originals_are_explicitly_kept_or_skipped=True,
                transient_receipts_do_not_save_rules_and_edits_require_reconfirmation=True,
                remembered_rules_exclude_unrecognized_extensions=True)
