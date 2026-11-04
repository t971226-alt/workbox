"""Real routing, binding, manual folders, cancellation and pinned receipt paths."""
from pathlib import Path


def verify(root):
    from PySide6.QtWidgets import QApplication
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog,ZipIntakeDialog
    from intake_folders import type_draft,records,commit
    from folder_route_ui import FolderRoute
    from zipfile import ZipFile
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance() or QApplication([])
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True)
    draft=type_draft('单份文档要求',{'word':1});s.classifications.save(draft['name'],draft['kinds'],mode='collect',family_map=draft['family_map'],name_patterns=draft['name_patterns'])
    profile=next(p for p in s.classifications.profiles(True) if p['name']==draft['name'])
    manual=root/'saved'/'手动新建的单手册';manual.mkdir();old=manual/'原有文件.txt';old.write_bytes(b'untouched')
    def settle():
        for _ in range(8):app.processEvents()
    def close(d):d.reject();d.deleteLater();settle()
    def incoming(name,suffix='.docx'):
        p=root/'incoming'/(name+suffix);p.parent.mkdir(exist_ok=True);p.write_bytes((name+suffix).encode());return p
    def dialog(p):
        d=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(p)],include_known=True,keep_archives=True));g=d.form.grouped_view.groups[0];d.form.grouped_view.assign(g,profile);settle();return d,g,g['route']
    p=incoming('实验室仪器现场安装工单管理系统');d,g,r=dialog(p)
    assert not records(s) and r.toggle.isChecked()
    from unittest.mock import patch
    parent=root/'another-parent';parent.mkdir();proposed=r.directory.text()
    with patch('folder_route_ui.QFileDialog.getExistingDirectory',return_value=str(parent)) as chooser:r.browse_button.click();assert chooser.call_count==1
    assert Path(r.root.text())==parent and r.directory.text()==proposed and not (parent/proposed).exists()
    assert r.choose_path(manual) and not records(s)
    assert not (manual/p.stem).exists() and old.read_bytes()==b'untouched'
    close(d);assert not records(s),'Cancelled preview persisted a binding'
    d,g,r=dialog(p);assert r.choose_path(manual);d.confirm();assert d.operation,d.error.text();result=d.operation();assert result['copied_count']==1
    assert (manual/p.stem/(p.stem+'.docx')).read_bytes()==p.read_bytes();assert old.read_bytes()==b'untouched';close(d)
    assert len(records(s))==1 and records(s)[0]['name']==manual.name
    p2=incoming('机器人课程课件图文排版编辑软件','.doc');d,g,r=dialog(p2);assert not r.unresolved and Path(r.storage(p2.stem)['root'])==manual
    d.confirm();assert d.operation,d.error.text();d.operation();close(d)
    assert (manual/p2.stem/(p2.stem+'.doc')).is_file()
    other=root/'elsewhere'/'另一收纳目录';other.mkdir(parents=True)
    rr=FolderRoute(s,profile,'测试系统');assert rr.choose_path(other);commit(s,dict(root=str(root),items=[rr.binding()]))
    p3=incoming('多目录选择系统');d,g,r=dialog(p3);assert r.unresolved
    d.confirm();assert not d.operation
    target=next(x for x in records(s) if x['name']==other.name);r.choice.setCurrentIndex(r.choice.findData(target['id']));settle();d.confirm();assert d.operation,d.error.text();d.operation();close(d)
    assert (other/p3.stem/(p3.stem+'.docx')).is_file()
    # Established tasks stay pinned even when their rule has several targets.
    d=MaterialIntakeDialog(s,DirectMaterialIntake(s).prepare([str(p)],include_known=True,keep_archives=True));g=d.form.grouped_view.groups[0];assert Path(g['route'].storage(p.stem)['directory'])==manual/p.stem;close(d)
    missing_rule=type_draft('缺失目录要求',{'pdf':1});lost=root/'saved'/'后来删除的目录';lost.mkdir();commit(s,dict(root=str(root/'saved'),items=[dict(name=lost.name,rule=missing_rule)]));lost.rmdir()
    rr=FolderRoute(s,missing_rule,'缺失目录系统');assert rr.unresolved
    try:rr.storage('缺失目录系统');raise AssertionError('Missing folder was silently recreated')
    except ValueError:pass
    rr=FolderRoute(s,missing_rule,'其他系统');assert not rr.choose_path(manual),'A directory was silently rebound to a different rule'
    # A software subdirectory is never a new collection-directory binding.
    assert not rr.choose_path(manual/p.stem)
    z=root/'原包收纳系统.zip'
    with ZipFile(z,'w') as stream:stream.writestr('不可自动解压.txt',b'raw')
    raw_dir=root/'saved'/'手动原包目录';raw_dir.mkdir()
    d=ZipIntakeDialog(s,[str(z)]);r=d.raw_controls[0]['route'];assert r.choose_path(raw_dir);d.confirm();assert d.operation,d.error.text();d.operation();close(d)
    assert (raw_dir/z.stem/(z.stem+'.zip')).read_bytes()==z.read_bytes()
    z2=root/'复用原包收纳系统.zip';z2.write_bytes(z.read_bytes());d=ZipIntakeDialog(s,[str(z2)]);assert Path(d.raw_controls[0]['route'].storage(z2.stem)['root'])==raw_dir;close(d)
    raw_profile=next(p for p in s.classifications.profiles(True,include_archives=True) if p.get('intake_mode')=='original')
    s.classifications.save('更名后的压缩包规则',raw_profile['kinds'],mode='collect',profile_id=raw_profile['id'],intake_mode='original',extensions=raw_profile['extensions'])
    d=ZipIntakeDialog(s,[str(z2)]);assert Path(d.raw_controls[0]['route'].storage(z2.stem)['root'])==raw_dir;close(d)
    s.classifications.save('更名后的单份文档规则',profile['kinds'],mode='collect',profile_id=profile['id'],family_map=profile['family_map'],name_patterns=profile['name_patterns'])
    choices=DirectMaterialIntake(s).profiles();assert not any(p['name']==profile['name'] for p in choices)
    s.classifications.save('更名后的单份文档规则',profile['kinds'],mode='collect',profile_id=profile['id'],active=False,family_map=profile['family_map'],name_patterns=profile['name_patterns'])
    assert not any(p.get('id')==profile['id'] or p['name']==profile['name'] for p in DirectMaterialIntake(s).profiles()),'A folder binding reactivated a disabled rule'
    removable=type_draft('删除规则测试',{'text':1});rid=s.classifications.save(removable['name'],removable['kinds'],mode='collect',family_map=removable['family_map'],name_patterns=removable['name_patterns'])
    shelf=root/'saved'/'删除规则目录';shelf.mkdir();commit(s,dict(root=str(shelf.parent),items=[dict(name=shelf.name,rule=removable)]));s.classifications.remove(rid)
    assert not any(p['name']==removable['name'] for p in DirectMaterialIntake(s).profiles()),'A deleted rule was revived from its folder snapshot'
    return dict(bound_rule_routes_materials_and_archives_to_existing_directories=True,
                unbound_manual_folders_bind_only_after_confirmed_receipt=True,
                multiple_rule_directories_require_explicit_choice=True,
                missing_or_conflicting_bindings_are_not_silently_recreated_or_rebound=True,
                software_subfolders_and_unselected_existing_contents_are_preserved=True,
                existing_task_paths_remain_pinned_after_binding_changes=True)
