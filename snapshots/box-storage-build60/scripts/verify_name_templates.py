"""Use isolated real intake queues to verify reusable patterns across file types."""
from pathlib import Path
from zipfile import ZipFile


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    from theme import install_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True);install_theme(s)
    name='电子元器件成品性能测试管理系统';incoming=root/'incoming'/name;incoming.mkdir(parents=True)
    suffixes=['.docx','_代码 (JavaScript+HTML+CSS).docx','.pdf','_代码 (JavaScript+HTML+CSS).pdf','.xlsx','.pptx','.txt','.png','.mp4','.mp3','.zip','.exe','.db','.sql']
    files=[]
    for suffix in suffixes:
        p=incoming/(name+suffix)
        if p.suffix=='.zip':
            with ZipFile(p,'w') as z:z.writestr('不得自动解压.txt',b'raw archive')
        else:p.write_bytes(('bytes '+suffix).encode())
        files.append(str(p))
    plan=DirectMaterialIntake(s).prepare([str(incoming)],include_known=True,keep_archives=True)
    assert len(plan['entries'])==len(files) and {e['name'] for e in plan['entries']}=={name}
    d=MaterialIntakeDialog(s,plan);d.show()
    def settle():
        for _ in range(8):app.processEvents()
    def close(d):d.close();d.deleteLater();settle()
    settle();assert all(d.form.chosen_kind(i) for i in range(len(files)))
    draft=d.form.quick_editor.draft();assert len(draft['kinds'])==len(files)
    assert draft['name_patterns']['同名 Word.docx']=={'mode':'same'}
    assert draft['name_patterns']['代码 Word.docx']=={'mode':'marker','marker':'代码'}
    from intake_onboarding import confirm_first_receipts
    confirm_first_receipts(d.form,remember=True)
    d.grab().save(str(root/'全部类型自动匹配.png'));d.confirm();assert d.result()==QDialog.Accepted,d.error.text()
    result=d.operation();assert result['copied_count']==len(files),result;close(d)
    row=s.production.rows()[0];pid=row['id'];assert row['required_count']==row['collection_count']==len(files)
    with s.db.connect() as c:
        saved={r['kind']:Path(r['path']) for r in c.execute('SELECT * FROM collected_materials WHERE project_id=?',(pid,))}
    assert saved['同名 Word.docx'].name==name+'.docx' and saved['代码 Word.docx'].name==name+'_代码.docx'
    assert {p.read_bytes() for p in saved.values()}=={Path(p).read_bytes() for p in files}
    assert saved['同名 压缩包.zip'].read_bytes()==(incoming/(name+'.zip')).read_bytes()
    profiles=s.classifications.profiles(True)
    # The same complete rule is reused with a different software title and DOC.
    other='全源人才聚合研判招聘系统';next_dir=root/other;next_dir.mkdir()
    paths=[]
    for tail in ['.doc','_源代码（C++）.doc']:
        p=next_dir/(other+tail);p.write_bytes(tail.encode());paths.append(str(p))
    plan=DirectMaterialIntake(s).prepare(paths,include_known=True,keep_archives=True)
    assert len({e['package'] for e in plan['entries']})==1 and plan['entries'][0]['package']==draft['name']
    d=MaterialIntakeDialog(s,plan);d.confirm();assert d.operation,d.error.text();d.operation();close(d)
    assert s.classifications.profiles(True)==profiles
    second=next(r for r in s.production.rows() if r['name']==other);assert second['collection_count']==2 and second['required_count']==len(files)
    # A new shape gets a new rule and folder; the old task stays unchanged.
    extra=incoming/(name+'_统计.xlsx');extra.write_bytes(b'new statistics')
    plan=DirectMaterialIntake(s).prepare([str(extra)],include_known=True,keep_archives=True)
    assert plan['entries'][0]['extra_kinds']
    d=MaterialIntakeDialog(s,plan);d.show();settle();g=d.form.grouped_view.groups[0]
    assert g['needs_rule'] and g['new_rule_action'].isVisible() and not d.save_button.isEnabled()
    from unittest.mock import patch
    from management_ui import RuleManagementDialog
    target=root/'saved/补充统计资料';target.mkdir()
    def create_rule(manager):
        manager.editor.name.setText('补充统计资料')
        with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(target)):manager.choose_folder()
        manager.save();assert not manager.dirty,manager.status.text()
        return QDialog.Accepted
    with patch.object(RuleManagementDialog,'exec',create_rule):g['new_rule_action'].click()
    settle();assert not g['needs_rule'] and d.save_button.isEnabled()
    d.confirm();assert d.operation,d.error.text();d.operation();close(d)
    assert [p for p in s.classifications.profiles(True) if p['name']!='补充统计资料']==profiles
    updated=next(r for r in s.production.rows() if r['id']==pid);assert updated['collection_count']==updated['required_count']==len(files)
    added=next(r for r in s.production.rows() if r['package_name']=='补充统计资料');assert added['collection_count']==added['required_count']==1
    assert (target/name/(name+extra.suffix)).read_bytes()==b'new statistics'
    restarted=DesktopService(s.db.path);assert next(r for r in restarted.production.rows() if r['id']==pid)['name_patterns']==updated['name_patterns']
    from quick_classification_ui import QuickClassificationDialog
    from file_families import infer
    q=QuickClassificationDialog(s);q.editor.name.setText('手动输入占位词');q.editor.table.item(0,0).setText('软件名称')
    q.editor.add('.docx');q.editor.table.item(1,0).setText('软件名称代码');q.save();assert q.profile_id,q.error.text()
    typed=next(p for p in s.classifications.profiles(True) if p['id']==q.profile_id)
    assert infer(name+'_代码 (JavaScript+HTML+CSS).docx',typed['kinds'],typed['family_map'],project_name=name,patterns=typed['name_patterns'])=='软件名称代码.docx';close(q)
    return dict(name_templates_match_same_title_and_code_with_language_decorations=True,
                all_supported_file_groups_enter_one_confirmation_and_keep_bytes=True,
                saved_name_templates_reuse_other_software_and_compatible_extensions=True,
                unmatched_files_create_separate_rule_without_rewriting_existing_task=True,
                template_snapshots_and_independent_materials_survive_restart=True,
                typed_software_name_placeholders_become_reusable_conditions=True)
