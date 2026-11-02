"""Exercise folder grouping, type composition and real copied bytes in isolation."""
from pathlib import Path
from zipfile import ZipFile
import time

def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog
    from PySide6.QtCore import Qt
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog,ZipIntakeDialog
    from intake_folders import type_draft,records
    from theme import install_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    def settle(condition=lambda:True):
        end=time.monotonic()+20
        while time.monotonic()<end:
            for _ in range(4):app.processEvents()
            if condition():return
            time.sleep(.01)
        raise AssertionError('Grouped intake did not finish')
    def close(d):d.reject();d.deleteLater();settle()
    def service(folder):
        s=DesktopService(folder/'data/workbox.db');s.workspaces.configure_materials(folder/'saved',complete_setup=True);install_theme(s);return s
    def file(folder,name,suffix):
        p=folder/(name+suffix);p.parent.mkdir(parents=True,exist_ok=True)
        if suffix=='.zip':
            with ZipFile(p,'w') as z:z.writestr('不可自动解压.txt',b'keep original archive')
        else:p.write_bytes((name+suffix).encode())
        return p
    s=service(root/'batch');rules=[]
    for name,counts in [('Word资料',{'word':1}),('交付文档',{'word':2,'pdf':2,'text':1}),('运行包与说明',{'word':1,'archive':1})]:
        draft=type_draft(name,counts);s.classifications.save(name,draft['kinds'],mode='collect',family_map=draft['family_map'],name_patterns=draft['name_patterns']);rules.append(draft)
    sets=[('文档收纳系统',['.doc']),('完整交付系统',['.docx','_代码.docx','.pdf','_代码.pdf','.txt']),('运行交付系统',['.docx','.zip'])]
    paths=[]
    for name,suffixes in sets:
        for suffix in suffixes:paths.append(file(root/'batch/incoming'/name,name,suffix))
    plan=DirectMaterialIntake(s).prepare([str(p) for p in paths],include_known=True,keep_archives=True)
    d=MaterialIntakeDialog(s,plan);d.show();settle();view=d.form.grouped_view;assert len(view.groups)==3 and not d.form.table.isVisible()
    for g in view.groups:
        name=d.form.table.item(g['indices'][0],2).text();index=next(i for i,(n,_) in enumerate(sets) if n==name);view.assign(g,rules[index])
        panel=view.folders;panel.open_editor();panel.folder.setText(rules[index]['name']);panel.choice.setCurrentIndex(panel.choice.findData(rules[index]['name']));panel.apply.setChecked(False);panel.save.click();assert not panel.error.isVisible(),panel.error.text()
        g['destination']=next(key for key,r in panel.items.items() if r['name']==rules[index]['name']);g['location_dirty']=True
    settle();view.refresh();assert all(d.form.chosen_kind(i) for i in range(len(paths)))
    partial=next(g for g in view.groups if len(g['indices'])>1);i=partial['indices'][0];view.file_checked(i,False);settle();view.refresh();assert partial['check'].checkState()==Qt.PartiallyChecked and view.all.checkState()==Qt.PartiallyChecked
    partial['check'].click();settle();assert all(view.checked(i) for i in partial['indices'])
    view.grab().save(str(root/'批量确认-按软件分组.png'))
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();result=d.operation();assert result['copied_count']==8
    rows=s.production.rows();assert len({Path(r['collection_folder']).parent for r in rows})==3
    with s.db.connect() as c:saved=[Path(r[0]) for r in c.execute('SELECT path FROM collected_materials')]
    assert {p.read_bytes() for p in saved}=={p.read_bytes() for p in paths}
    assert len(records(s,root/'batch/saved'))==3;old_locations={r['id']:r['collection_folder'] for r in rows};close(d)
    # A second intake retains each established task's exact destination.
    p=paths[0];p.write_bytes(b'new source version');plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True,keep_archives=True);d=MaterialIntakeDialog(s,plan);settle();v=d.form.grouped_view
    v.folders.root.setText(str(root/'other-root'));assert str(Path(v.output(v.groups[0],0)['root'])/v.output(v.groups[0],0)['folder'])==old_locations[rows[0]['id']];close(d)
    # Build a new folder + standalone type requirements without saving a global preset.
    s2=service(root/'types');name='自选材料系统';a=file(root/'types/incoming',name,'.doc');b=file(root/'types/incoming',name,'.zip')
    plan=DirectMaterialIntake(s2).prepare([str(a),str(b)],include_known=True,keep_archives=True);d=MaterialIntakeDialog(s2,plan);d.show();settle();v=d.form.grouped_view;before=s2.classifications.profiles(True)
    panel=v.folders;panel.open_editor();panel.folder.setText('运行交付');panel.add_type('word');panel.add_type('archive');panel.remember.setChecked(False);panel.save.click();settle();assert not panel.error.isVisible(),panel.error.text()
    assert all(d.form.chosen_kind(i) for i in range(2));d.grab().save(str(root/'新建保存文件夹-自选类型.png'))
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();close(d)
    assert s2.classifications.profiles(True)==before
    with s2.db.connect() as c:saved=[Path(r[0]) for r in c.execute('SELECT path FROM collected_materials')]
    assert {p.suffix for p in saved}=={'.doc','.zip'} and all(p.parent==root/'types/saved/运行交付'/name for p in saved)
    # New folder editing is local until the intake itself is confirmed.
    other=file(root/'types/incoming','取消收纳系统','.pdf');plan=DirectMaterialIntake(s2).prepare([str(other)],include_known=True,keep_archives=True);d=MaterialIntakeDialog(s2,plan);panel=d.form.grouped_view.folders;panel.open_editor();panel.folder.setText('取消文件夹');panel.add_type('pdf');panel.save.click();close(d)
    assert not (root/'types/saved/取消文件夹').exists() and all(r['name']!='取消文件夹' for r in records(s2,root/'types/saved'))
    # An archive alone can establish a two-type collection without unpacking it.
    s3=service(root/'archive');a=file(root/'archive/incoming','先收原包系统','.zip');d=ZipIntakeDialog(s3,[str(a)]);d.show();settle();panel=d.raw_folders;panel.open_editor();panel.folder.setText('程序与说明');panel.add_type('archive');panel.add_type('word');panel.save.click();settle(lambda:d.form is not None or bool(d.error.text()));assert d.form is not None,d.error.text();assert not d.raw_radio.isChecked()
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();row=s3.production.rows()[0];assert row['required_count']==2 and row['collection_count']==1
    with s3.db.connect() as c:p=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0]);assert p.read_bytes()==a.read_bytes()
    close(d)
    # Not remembering an archive-only type rule must not create a global preset.
    s4=service(root/'archive-local');a=file(root/'archive-local/incoming','仅本次原包系统','.zip');before=s4.classifications.profiles(include_archives=True)
    d=ZipIntakeDialog(s4,[str(a)]);d.show();settle();panel=d.raw_folders;panel.open_editor();panel.folder.setText('仅本次归档');panel.add_type('archive');panel.remember.setChecked(False);panel.save.click();settle(lambda:d.form is not None or bool(d.error.text()));assert d.form is not None,d.error.text()
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();assert s4.classifications.profiles(include_archives=True)==before
    assert s4.production.rows()[0]['collection_count']==s4.production.rows()[0]['required_count']==1;close(d)
    # Two Word slots can be filled on different days without a phantom third slot.
    s5=service(root/'later-word');draft=type_draft('两份Word',{'word':2});s5.classifications.save(draft['name'],draft['kinds'],mode='collect',family_map=draft['family_map'],name_patterns=draft['name_patterns'])
    a=file(root/'later-word/incoming','分次收纳系统','.docx');plan=DirectMaterialIntake(s5).prepare([str(a)],include_known=True,keep_archives=True);d=MaterialIntakeDialog(s5,plan);view=d.form.grouped_view;view.assign(view.groups[0],draft);d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();close(d)
    a=file(root/'later-word/incoming','分次收纳系统','_代码.doc');plan=DirectMaterialIntake(s5).prepare([str(a)],include_known=True,keep_archives=True);assert not plan['entries'][0].get('extra_kinds');d=MaterialIntakeDialog(s5,plan);d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();close(d)
    assert len(s5.production.rows())==1 and s5.production.rows()[0]['required_count']==s5.production.rows()[0]['collection_count']==2
    other='复用两份文档系统';a=file(root/'later-word/incoming',other,'.docx');b=file(root/'later-word/incoming',other,'_代码.docx');plan=DirectMaterialIntake(s5).prepare([str(a),str(b)],include_known=True,keep_archives=True)
    assert all(e['package']==draft['name'] for e in plan['entries']);d=MaterialIntakeDialog(s5,plan);d.confirm();assert d.result()==QDialog.Accepted,d.error.text();d.operation();close(d)
    assert s5.production.rows()[-1]['collection_count']==s5.production.rows()[-1]['required_count']==2
    return dict(grouped_confirmation_has_one_material_list_per_software=True,
                different_material_lists_save_under_separate_parent_folders=True,
                standalone_types_and_real_extensions_keep_original_bytes=True,
                folder_name_and_material_list_are_independent=True,
                unremembered_type_rules_remain_task_and_folder_snapshots=True,
                cancelled_folder_setup_creates_no_directory_or_preset=True,
                existing_task_paths_do_not_follow_new_root_or_rule_settings=True,
                archive_with_word_requirement_stays_raw_and_can_be_completed_later=True,
                repeated_type_slots_bind_filenames_without_growing_requirements=True)
