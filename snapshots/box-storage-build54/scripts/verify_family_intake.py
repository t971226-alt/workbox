"""Real family-slot receipt, independent naming and matching UI in isolation."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from theme import install_theme,set_theme
    from zip_intake_ui import ZipIntakeDialog,MaterialIntakeDialog
    from quick_classification_ui import QuickClassificationDialog
    from direct_material_intake import DirectMaterialIntake
    from file_families import task_families
    from task_query import status
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance();s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True);install_theme(s)
    def settle():
        for _ in range(8):app.processEvents()
    def close(d):d.close();d.deleteLater();settle()
    # Old explicit suffix remains strict; a newly saved type rule accepts DOC
    # in its Word slot while the archived and published copies retain .doc.
    s.classifications.save('旧规则',['验收报告.docx'],mode='collect')
    q=QuickClassificationDialog(s);q.editor.name.setText('验收材料');q.editor.table.item(0,0).setText('验收报告');q.editor.table.cellWidget(0,3).setText('软件名称_验收报告');q.save();assert q.result()==QDialog.Accepted;close(q)
    profile=next(p for p in s.classifications.profiles(True) if p['name']=='验收材料');assert profile['family_map']=={'验收报告.docx':'word'}
    source=root/'类型兼容系统_验收报告.doc';source.write_bytes(b'actual old Word format bytes')
    plan=DirectMaterialIntake(s).prepare([str(source)],include_known=True)
    assert plan['entries'][0]['package']=='验收材料' and plan['entries'][0]['kind']=='验收报告.docx'
    d=MaterialIntakeDialog(s,plan);d.show();settle();assert not d.form.rule_picker.isVisible()
    assert d.form.save_location.folder.text()=='类型兼容系统' and d.form.save_location.base.text()=='类型兼容系统'
    d.form.save_location.folder.setText('交付材料');d.form.save_location.base.setText('运行说明');d.grab().save(str(root/'按类型收录与独立命名.png'))
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();assert d.operation()['copied_count']==1;close(d)
    row=s.production.rows()[0];pid=row['id'];assert row['required_count']==row['collection_count']==1
    assert row['family_map']=={'验收报告.docx':'word'} and Path(row['collection_folder'])==root/'材料库/验收材料/交付材料'
    saved=root/'材料库/验收材料/交付材料/运行说明.doc';assert saved.read_bytes()==source.read_bytes()
    assert Path(s.production.history(pid,'验收报告.docx')[0]['path']).suffix=='.doc'
    # Rename the reusable rule and narrow it: the established task snapshot and
    # user-chosen output location must not follow those future rule edits.
    s.classifications.save('后续规则',['验收报告.docx'],mode='collect',profile_id=profile['id'],family_map={})
    assert s.production.rows()[0]['family_map']=={'验收报告.docx':'word'} and saved.is_file()
    newer=root/'类型兼容系统_验收报告.docx';newer.write_bytes(b'newer Word bytes')
    s.production.receive(pid,'验收报告.docx',newer)
    assert (root/'材料库/验收材料/交付材料/运行说明.docx').read_bytes()==newer.read_bytes()
    assert len(s.production.history(pid,'验收报告.docx'))==2 and s.production.rows()[0]['collection_count']==1
    assert Path(s.production.history(pid,'验收报告.docx')[-1]['path']).read_bytes()==source.read_bytes()
    old=s.production.create_batch('旧任务',['严格格式系统'],['验收报告.docx'],package='旧规则')['project_ids'][0]
    try:s.production.receive(old,'验收报告.docx',source)
    except ValueError:pass
    else:raise AssertionError('legacy DOCX requirement accepted DOC')
    assert s.production.task_kinds(old)==['验收报告.docx']
    flags=dict(file_family_accepts_variants_without_conversion_or_extra_counts=True,existing_rule_and_task_snapshots_remain_strict_or_frozen=True,rule_rename_does_not_move_named_outputs=True)
    # A compressed original has an independent folder and defaults to the
    # software title as its saved filename; manual names survive title edits.
    archive=root/'压缩包命名系统.zip'
    with ZipFile(archive,'w') as z:z.writestr('unread.txt',b'raw archive')
    d=ZipIntakeDialog(s,[str(archive)]);d.show();settle();item=d.raw_controls[0];out=item['save_location']
    assert out.folder.text()==out.base.text()=='压缩包命名系统'
    item['name'].setText('默认名称系统');assert out.folder.text()==out.base.text()=='默认名称系统'
    out.folder.setText('运行包目录');out.base.setText('运行包副本');item['name'].setText('最终软件名称')
    assert out.folder.text()=='运行包目录' and out.base.text()=='运行包副本'
    d.archive_selector.setCurrentIndex(d.archive_selector.findData('__new__'));d.archive_editor.name.setText('可运行压缩包')
    assert d.archive_editor.format.count()==1 and not d.archive_selection.isVisible()
    d.grab().save(str(root/'压缩包-新规则与独立保存.png'));d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();close(d)
    raw=next(r for r in s.production.rows() if r['name']=='最终软件名称');raw_path=Path(raw['original_zips'][0]['path'])
    assert raw_path==root/'材料库/软件运行包/运行包目录/运行包副本.zip' and raw_path.read_bytes()==archive.read_bytes()
    assert '可运行压缩包' not in str(raw_path) and status(raw)=='原包已收录'
    restarted=DesktopService(s.db.path);assert Path(next(r for r in restarted.production.rows() if r['id']==raw['id'])['original_zips'][0]['path'])==raw_path
    flags['software_title_defaults_and_manual_name_overrides_are_independent']=True
    flags['raw_archive_custom_output_survives_restart']=True
    # New-rule type controls are short and use the same native visual system.
    q=QuickClassificationDialog(s);q.show();settle();assert q.editor.format.count()<=10
    assert not q.editor.toggle.isVisible() and not q.editor.brief.isVisible()
    q.editor.name.setText('新规则');q.grab().save(str(root/'新建规则-精简类型.png'))
    set_theme('dark',persist=False);settle();q.grab().save(str(root/'新建规则-深色.png'));set_theme('light',persist=False);close(q)
    flags['compact_type_controls_and_light_dark_styles_are_consistent']=True
    return flags
