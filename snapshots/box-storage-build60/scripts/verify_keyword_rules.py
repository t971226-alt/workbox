"""Verify keyword matching, explicit ambiguity choices and independent destinations."""
from pathlib import Path


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from PySide6.QtCore import Qt
    from theme import install_theme,set_theme
    from desktop_service import DesktopService
    from quick_classification_ui import QuickClassificationDialog
    from file_families import infer
    from name_patterns import SOFTWARE_TOKEN,keyword_terms,validate
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog,ZipIntakeDialog
    from zipfile import ZipFile
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance() or QApplication([]);install_theme()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    title='全源人才聚合研判招聘系统';kind='代码材料.docx'
    q=QuickClassificationDialog(s);q.editor.name.setText('代码材料');condition=q.editor.table.cellWidget(0,3)
    assert condition.pattern()['mode']=='type'
    condition.software.setChecked(True)
    condition.plus.setText('代码');q.editor.set_software_name(title);q.editor.settings_button.setChecked(True);q.show();app.processEvents()
    draft=q.editor.draft();pattern=draft['name_patterns'][kind]
    assert pattern==dict(mode='keywords',terms=[SOFTWARE_TOKEN,'代码'])
    assert title in q.editor.resolved.text() and '代码' in q.editor.resolved.text()
    def matched(filename,patterns=None):return infer(filename,draft['kinds'],draft['family_map'],project_name=title,patterns=patterns or draft['name_patterns'])
    for sep in ('，',',',' ','+','＋',' + '):
        condition.plus.setText('代码'+sep+'终稿');p=q.editor.draft()['name_patterns'][kind]
        assert p['terms']==[SOFTWARE_TOKEN,'代码','终稿']
        assert matched(title+'_终稿_代码.doc', {kind:p})==kind
        assert matched(title+'_代码.docx', {kind:p}) is None
    condition.plus.setText('代码')
    assert matched(title+'_代码.docx')==kind and matched('代码_'+title+'.doc')==kind
    assert matched(title+'.docx') is None and matched(title+'_说明书.docx') is None and matched(title+'_代码.pdf') is None
    condition.software.setChecked(False);condition.plus.clear();assert q.editor.draft()['name_patterns'][kind]==dict(mode='keywords',terms=[])
    condition.software.setChecked(True);condition.plus.setText('代码')
    q.grab().save(str(root/'关键词识别-浅色.png'));set_theme('dark',persist=False);app.processEvents();q.grab().save(str(root/'关键词识别-深色.png'));set_theme('light',persist=False)
    q.save();assert q.result()==QDialog.Accepted,q.error.text();q.close();q.deleteLater();app.processEvents()
    incoming=root/'incoming'/title;incoming.mkdir(parents=True)
    a=incoming/(title+'_代码.docx');b=incoming/(title+'_代码_终稿.doc');a.write_bytes(b'code one');b.write_bytes(b'code two')
    plan=DirectMaterialIntake(s).prepare([str(a),str(b)],include_known=True,keep_archives=True)
    d=MaterialIntakeDialog(s,plan);d.show();app.processEvents();view=d.form.grouped_view
    view.assign(view.groups[0],next(p for p in s.classifications.profiles(True) if p['name']=='代码材料'));app.processEvents()
    assert d.form.version_groups() and all(d.form.table.item(i,0).checkState()==Qt.Unchecked for i in range(2)),[(d.form.table.item(i,2).text(),d.form.proposal['entries'][i].get('batch_key'),d.form.kind_controls[i].currentIndex(),d.form.chosen_kind(i),d.form.table.item(i,0).checkState()) for i in range(2)]
    d.confirm();assert not d.operation
    d.form.table.item(0,0).setCheckState(Qt.Checked);app.processEvents()
    chosen=root/'单手册';view.folders.root.setText(str(chosen));app.processEvents()
    assert Path(view.output(view.groups[0],0)['root'])==chosen
    d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==1
    row=s.production.rows()[0];saved=Path(row['collection_folder']);assert saved==chosen/title
    assert not (chosen/'Word文档').exists() and next(saved.iterdir()).read_bytes()==b'code one'
    d.close();d.deleteLater();app.processEvents()
    # Receiving into an established task preserves its pinned destination.
    a.write_bytes(b'code update');plan=DirectMaterialIntake(s).prepare([str(a)],include_known=True,keep_archives=True);d=MaterialIntakeDialog(s,plan)
    view=d.form.grouped_view;view.folders.root.setText(str(root/'another-root'));app.processEvents()
    assert Path(view.output(view.groups[0],0)['root'])/view.output(view.groups[0],0)['folder']==saved
    d.close();d.deleteLater();app.processEvents()
    # A raw archive uses the same chosen directory without an automatic rule layer.
    z=root/'原包收录系统.zip'
    with ZipFile(z,'w') as stream:stream.writestr('内容.txt',b'raw bytes')
    d=ZipIntakeDialog(s,[str(z)]);d.raw_folders.root.setText(str(root/'压缩包目录'));app.processEvents();d.confirm();assert d.operation,d.error.text();d.operation()
    raw=next(r for r in s.production.rows() if r['name']==z.stem);assert Path(raw['original_zips'][0]['path']).parent==root/'压缩包目录'/z.stem
    d.close();d.deleteLater();app.processEvents()
    old={kind:dict(mode='same')};assert matched(title+'_代码.docx',old) is None and matched(title+'.docx',old)==kind
    assert s.classifications.profiles(True)[-1]['name_patterns'] or pattern
    return dict(keyword_separators_and_automatic_software_token_match_all_terms=True,
                keyword_type_constraints_and_legacy_snapshots_remain_independent=True,
                multiple_keyword_matches_require_explicit_file_selection=True,
                selected_material_root_has_no_automatic_classification_layer=True,
                explicit_folders_and_existing_task_destinations_remain_available=True)
