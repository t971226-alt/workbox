"""Exercise rule mismatches and new formats through the real confirmation UI."""
from pathlib import Path
from unittest.mock import patch
import json


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from zip_intake_ui import MaterialIntakeDialog
    from direct_material_intake import DirectMaterialIntake
    from management_ui import RuleManagementDialog
    from folder_management import bind
    from unmatched_intake import ACTION,MESSAGE,save
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    oldk=['手册.docx','代码.docx'];patterns={oldk[0]:dict(mode='same'),oldk[1]:dict(mode='keywords',terms=['{software}','代码'])}
    ident=s.classifications.save('原双文档',oldk,mode='collect',family_map={k:'word' for k in oldk},name_patterns=patterns)
    original=next(p for p in s.classifications.profiles(True) if p['id']==ident)
    oldfolder=root/'saved/原双文档';oldfolder.mkdir();bind(s,oldfolder,original)
    from requirement_intake import proposal
    name='全源人才聚合研判招聘系统'
    seeded=s.production.import_plan([dict(proposal(name,batch='原记录'),package=original['name'],kinds=oldk,
        family_map=original['family_map'],name_patterns=patterns)],token='old-seed')
    pid=seeded['project_ids'][0];manual=root/(name+'.docx');manual.write_bytes(b'manual')
    s.production.receive(pid,oldk[0],manual);oldversion=s.production.history(pid,oldk[0])[0]
    code=root/(name+'_源程序.doc');code.write_bytes(b'source code')
    sheet=root/'机器人课程课件图文排版编辑软件.xlsx';sheet.write_bytes(b'spreadsheet')
    sources={p:p.read_bytes() for p in (manual,code,sheet)}
    def settle():
        for _ in range(60):app.processEvents()
    d=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(p) for p in sources],include_known=True,keep_archives=True));d.show();settle()
    groups=d.form.grouped_view.groups;assert len(groups)==2
    for g in groups:
        assert g['new_rule_action'].isVisible() and g['new_rule_action'].text()==ACTION
        assert g['compact_notice'].text()==MESSAGE and not g['compact_path'].isVisible()
    assert not d.new_rule_button.isVisible()
    assert 'Word ×2' in groups[0]['rule_label'].text() and groups[0]['status'].text()=='1 / 2 份'
    assert '源程序' in groups[0]['unmatched_files'].text() and sheet.name in groups[1]['unmatched_files'].text()
    d.grab().save(str(root/'unmatched-confirmation.png'))
    d.confirm();assert not d.operation and MESSAGE in d.error.text()
    # Leaving the creation form must not change tasks, rules or source bytes.
    before=s.classifications.profiles(False,include_archives=True)
    with patch.object(RuleManagementDialog,'exec',return_value=QDialog.Rejected):groups[0]['new_rule_action'].click()
    assert s.classifications.profiles(False,include_archives=True)==before
    targets=[root/'saved/源程序交付',root/'saved/表格资料']
    for p in targets:p.mkdir()
    for index,g in enumerate(groups):
        def create(manager,index=index):
            manager.show();settle()
            assert manager.intake_context and manager.current is None and not manager.list.isVisible()
            assert manager.width()<=560,manager.size()
            assert manager.editor.table.rowCount()==(2 if index==0 else 1)
            if index==0:
                assert any('源程序' in p.get('terms',[]) for p in manager.editor.draft()['name_patterns'].values())
            manager.editor.name.setText('源程序交付' if index==0 else '表格资料')
            manager.save();assert '选择保存文件夹' in manager.status.text()
            with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(oldfolder)):manager.choose_folder()
            assert manager.pending_folder is None and '原绑定保持不变' in manager.status.text()
            with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(targets[index])):manager.choose_folder()
            manager.grab().save(str(root/('new-rule-'+str(index)+'.png')))
            manager.save();assert not manager.dirty,manager.status.text()
            return QDialog.Accepted
        with patch.object(RuleManagementDialog,'exec',create):g['new_rule_action'].click()
        settle();assert not g['needs_rule'] and not g['new_rule_action'].isVisible(),g['compact_notice'].text()
        if index==0:assert groups[1]['needs_rule'] and not d.save_button.isEnabled()
        assert Path(g['route'].storage(name)['root'])/g['route'].storage(name)['folder']==targets[index]/g['software'].text()
    assert len(s.production.rows())==1 and not any(targets[0].iterdir())
    assert s.classifications.profiles(False)[next(i for i,p in enumerate(s.classifications.profiles(False)) if p['id']==ident)]==original
    with s.db.connect() as c:
        oldbinding=json.loads(c.execute('SELECT rule_json FROM intake_folder_presets WHERE name=?',(oldfolder.name,)).fetchone()[0])
        assert oldbinding['id']==ident and oldbinding['name_patterns']==patterns
    d.grab().save(str(root/'resolved-confirmation.png'))
    assert d.save_button.isEnabled() and not d.error.text()
    d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==3,result
    assert len(s.production.rows())==3 and s.production.history(pid,oldk[0])==[oldversion]
    assert Path(oldversion['path']).read_bytes()==b'manual'
    assert {p.suffix for p in (targets[0]/name).iterdir()}=={'.docx','.doc'}
    assert all(p.read_bytes()==b for p,b in sources.items())
    d.reject();d.deleteLater();settle()
    # A binding failure rolls back the newly inserted rule as well.
    target=root/'saved/失败回滚';target.mkdir();before=s.classifications.profiles(False)
    with s.db.connect() as c:c.execute("CREATE TRIGGER fail_new_binding BEFORE INSERT ON intake_folder_presets BEGIN SELECT RAISE(ABORT,'isolated binding failure'); END")
    rule=dict(name='不能留下的规则',kinds=['文档.pdf'],family_map={'文档.pdf':'pdf'},name_patterns={'文档.pdf':dict(mode='type')})
    try:save(s,rule,target)
    except Exception as error:assert 'isolated binding failure' in str(error)
    else:raise AssertionError('Expected transaction failure')
    with s.db.connect() as c:
        c.execute('DROP TRIGGER fail_new_binding');assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert s.classifications.profiles(False)==before
    return dict(unmatched_keyword_and_new_format_share_contextual_creation=True,
        intake_rule_requires_real_folder_and_preserves_existing_binding=True,
        new_intake_rule_rematches_selected_software_without_changing_old_task=True,
        new_rule_and_folder_binding_commit_or_rollback_together=True)
