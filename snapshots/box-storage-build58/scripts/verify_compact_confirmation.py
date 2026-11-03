"""Exercise the reduced confirmation and independent rule-manager round trip."""
from pathlib import Path


def verify(root):
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QDialog
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    from management_ui import RuleManagementDialog
    from theme import install_theme,set_theme
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);install_theme()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    def settle():
        for _ in range(40):app.processEvents()
    name='全源人才聚合研判招聘系统'
    source=root/(name+'.docx');code=root/(name+'_代码.doc');source.write_bytes(b'manual');code.write_bytes(b'code')
    d=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(source),str(code)],include_known=True,keep_archives=True));d.show();settle()
    g=d.form.grouped_view.groups[0]
    assert d.width()==500 and d.height()<480,(d.size(),g["card"].size(),g["card"].minimumSizeHint())
    assert not g['detail'].isVisible() and not g['profile_combo'].isVisible()
    assert not g['expand'].isCheckable() and g['expand'].property('expandableGreen')
    assert d.cancel_button.variant==d.new_rule_button.variant==d.save_button.variant=='primary'
    target=root/'saved'/'双文档';target.mkdir()
    def create_rule(manager):
        manager.editor.name.setText('双文档收纳');assert manager.editor.table.rowCount()==2
        manager.form.keyword(0,'');manager.form.keyword(1,'代码')
        with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(target)):manager.choose_folder()
        manager.save();assert not manager.dirty,manager.status.text()
        return QDialog.Accepted
    with patch.object(RuleManagementDialog,'exec',create_rule):d.new_rule_button.click()
    settle();assert all(d.form.chosen_kind(i) for i in range(2))
    assert g['rule_label'].text()=='收纳规则：双文档收纳\n包含：Word ×2'
    g['software'].setText(name+'新版');g['software'].editingFinished.emit();settle()
    assert g['route'].storage(name)['folder']==name+'新版'
    g['software'].setText(name);g['software'].editingFinished.emit();settle()
    assert g['route'].storage(name)['folder']==name
    assert not s.production.rows() and not list(target.iterdir())
    for mode in ('light','dark'):
        set_theme(mode);settle();d.grab().save(str(root/('confirmation-'+mode+'.png')))
        g['configure'].setChecked(True);settle();assert d.width()==500 and d.height()<520
        d.grab().save(str(root/('confirmation-edit-'+mode+'.png')));g['configure'].setChecked(False)
    set_theme('light');settle();d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==2
    assert len(s.production.rows())==1
    assert {p.read_bytes() for p in (target/name).iterdir()}=={b'manual',b'code'}
    assert source.read_bytes()==b'manual' and code.read_bytes()==b'code'
    d.reject();d.deleteLater();settle()
    # A completely new shape cannot silently create an inline rule or lose files.
    unknown=root/'新格式系统.custom56';unknown.write_bytes(b'unknown')
    d=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(unknown)],include_known=True,keep_archives=True));d.show();settle()
    before=s.classifications.profiles(True);d.confirm();assert not d.operation and d.error.text()
    d.reject();d.deleteLater();settle();assert s.classifications.profiles(True)==before and unknown.read_bytes()==b'unknown' and len(s.production.rows())==1
    # Creating an archive-only rule returns to original-archive intake, rather
    # than trying to import it through a document-only transaction.
    from zipfile import ZipFile
    from zip_intake_ui import ZipIntakeDialog
    from intake_folders import type_draft
    import time
    archive=root/'可运行测试系统.zip'
    with ZipFile(archive,'w') as z:z.writestr('软件说明书.docx',b'packed document')
    target=root/'saved/程序原包';target.mkdir()
    d=ZipIntakeDialog(s,[str(archive)],start_mode='extract');d.show()
    end=time.monotonic()+20
    while d.form is None and time.monotonic()<end:settle();time.sleep(.01)
    assert d.form is not None,d.error.text();settle()
    def create_archive(manager):
        manager.editor.apply_suggestion(type_draft('程序原包规则',{'archive':1}))
        with patch('management_ui.QFileDialog.getExistingDirectory',return_value=str(target)):manager.choose_folder()
        manager.save();assert not manager.dirty,manager.status.text()
        return QDialog.Accepted
    with patch.object(RuleManagementDialog,'exec',create_archive):d.new_rule_button.click()
    settle();assert d.raw_radio.isChecked() and d.archive_draft()['name']=='程序原包规则'
    d.confirm();assert d.operation,d.error.text();d.operation()
    assert (target/archive.stem/archive.name).read_bytes()==archive.read_bytes()
    row=next(r for r in s.production.rows() if r['name']==archive.stem);assert row['original_zips'] and not row['materials']
    d.reject();d.deleteLater();settle()
    return dict(compact_confirmation_uses_readonly_rule_summary_and_no_inline_file_list=True,
                new_rule_manager_returns_and_rematches_type_family_files=True,
                same_fill_confirmation_actions_are_right_aligned=True,
                unmatched_confirmation_and_cancellation_do_not_copy_or_discard_files=True,
                new_archive_only_rule_returns_to_original_archive_storage=True)
