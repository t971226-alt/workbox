from pathlib import Path
import pytest
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QApplication,QDialog
from desktop_service import DesktopService
from direct_material_ui import DirectMaterialDialog
from intake_versions_ui import IntakeVersionsDialog
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]);a.setStyle('Fusion');a.setQuitOnLastWindowClosed(False);return a

def file(root,relative,text='original'):
    p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8');return p

def setup(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'收录',complete_setup=True)
    root=tmp_path/'文档提交用'
    file(root,'甲批/甲系统.docx');file(root,'甲批/乙系统.docx');file(root,'甲批/修订版/甲系统.docx','revision')
    file(root,'乙批/丙系统.docx');file(root,'乙批/丁系统.docx')
    return s,root,s.receive_interactive([str(root)])['material_preview']

def test_top_folders_create_separate_batches_and_revision_stays_with_parent(tmp_path):
    s,root,plan=setup(tmp_path)
    assert plan['split_batches'] and set(plan['batch_labels'])=={'甲批','乙批'}
    assert {e['batch_label'] for e in plan['entries'] if '修订版' in e['path']}=={'甲批'}
    selected=[e for e in plan['entries'] if not ('甲系统' in e['name'] and '修订版' not in e['path'])]
    result=s.confirm_direct_materials(plan,selected,plan['batch'],'collect')
    assert len(result['batch_ids'])==2 and result['copied_count']==4
    rows=s.production.rows();assert len(rows)==4 and len({r['collection_folder'] for r in rows})==2
    assert {r['production_state'] for r in rows}=={'已收纳'}
    s.confirm_direct_materials(plan,selected,plan['batch'],'collect');assert len(s.production.rows())==4
    assert len(list(root.rglob('*.docx')))==5

def test_direct_files_and_revision_subfolder_are_one_batch(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');root=tmp_path/'甲批'
    file(root,'甲系统.docx');file(root,'修订版/甲系统.docx','revision')
    plan=s.receive_interactive([str(root)])['material_preview'];assert not plan['split_batches']
    assert len({e['batch_key'] for e in plan['entries']})==1

def test_same_name_in_different_folders_is_two_tasks_not_one_revision_group(tmp_path,app):
    s=DesktopService(tmp_path/'data/workbox.db');root=tmp_path/'总目录'
    file(root,'甲批/共同系统.docx');file(root,'乙批/共同系统.docx','second batch')
    plan=s.receive_interactive([str(root)])['material_preview'];install_theme(s);d=DirectMaterialDialog(s,plan)
    try:
        assert not d.version_groups() and len(d.values())==2
        r=s.confirm_direct_materials(plan,d.values(),plan['batch'],'collect');assert len(r['batch_ids'])==2
        assert len(s.production.rows())==2
    finally:d.close();d.deleteLater();install_theme()

def test_pending_versions_do_not_block_other_materials(tmp_path,app):
    s,root,plan=setup(tmp_path);install_theme(s);d=DirectMaterialDialog(s,plan)
    try:
        assert len(d.version_groups())==1 and len(d.values())==4
        for indices in d.version_groups():
            for i in indices:d.table.item(i,0).setCheckState(Qt.Unchecked)
        assert '1 组版本暂不收录' in d.summary.text()
        d.confirm_button.click();assert d.result()==QDialog.Accepted
        r=s.confirm_direct_materials(plan,d.rows,d.batch.text(),'collect')
        assert r['copied_count']==3 and len(s.production.rows())==3
        assert not any(r['name']=='甲系统' for r in s.production.rows())
        assert len(list(root.rglob('*.docx')))==5
    finally:d.close();d.deleteLater();install_theme()

def test_choose_revision_in_real_dialog_and_cancel_keeps_selection(tmp_path,app):
    s,root,plan=setup(tmp_path);install_theme(s);d=DirectMaterialDialog(s,plan);d.show();app.processEvents()
    try:
        def choose():
            modal=QApplication.activeModalWidget();assert isinstance(modal,IntakeVersionsDialog)
            combo=modal.choices[0][1]
            i=next(i for i in range(combo.count()) if '修订版' in combo.itemText(i));combo.setCurrentIndex(i)
            modal.apply_button.click()
        QTimer.singleShot(0,choose);d.versions_button.click()
        values=d.values();assert len(values)==4 and any('修订版' in e['path'] for e in values)
        before=[e['path'] for e in values]
        QTimer.singleShot(0,lambda:QApplication.activeModalWidget().reject());d.versions_button.click()
        assert before==[e['path'] for e in d.values()]
        s.confirm_direct_materials(plan,d.values(),plan['batch'],'collect')
        row=next(r for r in s.production.rows() if r['name']=='甲系统')
        assert Path(row['materials'][0]['path']).read_text(encoding='utf-8')=='revision'
    finally:d.close();d.deleteLater();install_theme()

def test_existing_task_with_incoming_revisions_still_previews_before_overwrite(tmp_path):
    s,root,plan=setup(tmp_path)
    s.production.create_batch('已有批',['甲系统'],['软件说明书.docx'])
    source=root/'甲批'
    first=s.receive_interactive([str(source)])['material_preview']
    assert any(e.get('existing') for e in first['entries'] if e['name']=='甲系统')
    assert len(s.production.rows())==1

def test_repeat_multi_batch_new_round_gets_available_labels(tmp_path):
    s,root,plan=setup(tmp_path)
    selected=[e for e in plan['entries'] if '修订版' not in e['path']]
    s.confirm_direct_materials(plan,selected,plan['batch'],'collect')
    again=s.receive_interactive([str(root)])['material_preview']
    assert set(again['batch_labels'])=={'甲批 (2)','乙批 (2)'}
    selected=[e for e in again['entries'] if '修订版' not in e['path']]
    pending=s.confirm_direct_materials(again,selected,again['batch'],'collect')
    choices={g['key']:dict(action='new',project_id=g['candidates'][0]['id']) for g in pending['material_conflicts']}
    result=s.resolve_duplicate_intake(pending['material_plan'],choices)
    assert len(result['batch_ids'])==2 and len(s.production.rows())==8

def test_batch_rename_updates_only_its_own_rows(tmp_path,app):
    s,root,plan=setup(tmp_path);install_theme(s);d=DirectMaterialDialog(s,plan)
    try:
        i=next(i for i,e in enumerate(plan['entries']) if e['batch_label']=='甲批')
        d.table.item(i,5).setText('上午材料')
        assert {d.table.item(i,5).text() for i,e in enumerate(plan['entries']) if e['batch_label']=='甲批'}=={'上午材料'}
        assert {d.table.item(i,5).text() for i,e in enumerate(plan['entries']) if e['batch_label']=='乙批'}=={'乙批'}
        s.confirm_direct_materials(plan,d.values(),d.batch.text(),'collect')
        assert {b['label'] for b in s.production.batches()}=={'上午材料','乙批'}
    finally:d.close();d.deleteLater();install_theme()

def test_default_uses_modification_time_instead_of_revision_folder_name(tmp_path,app):
    import os
    s,root,_=setup(tmp_path)
    older=root/'甲批/修订版/甲系统.docx';newer=root/'甲批/甲系统.docx'
    os.utime(older,ns=(1_600_000_000_000_000_000,1_600_000_000_000_000_000))
    os.utime(newer,ns=(1_700_000_000_000_000_000,1_700_000_000_000_000_000))
    plan=s.receive_interactive([str(root)])['material_preview'];install_theme(s);d=DirectMaterialDialog(s,plan)
    try:
        assert str(newer) in [r['path'] for r in d.values()] and str(older) not in [r['path'] for r in d.values()]
        assert '1 组已选最近修改版本' in d.summary.text()
        def choose_old():
            modal=QApplication.activeModalWidget();combo=modal.choices[0][1]
            assert combo.itemData(1)==next(i for i,e in enumerate(plan['entries']) if e['path']==str(newer))
            combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i) is not None and plan['entries'][combo.itemData(i)]['path']==str(older)))
            modal.apply_button.click()
        QTimer.singleShot(0,choose_old);d.versions_button.click()
        assert str(older) in [r['path'] for r in d.values()]
        def verify_manual_choice():
            modal=QApplication.activeModalWidget();combo=modal.choices[0][1]
            assert plan['entries'][combo.currentData()]['path']==str(older)
            modal.reject()
        QTimer.singleShot(0,verify_manual_choice);d.versions_button.click()
        assert str(older) in [r['path'] for r in d.values()]
        s.confirm_direct_materials(plan,d.values(),plan['batch'],'collect')
        assert next(r for r in s.production.rows() if r['name']=='甲系统')['collection_state']=='已收齐'
        assert newer.read_text(encoding='utf-8')=='original' and older.read_text(encoding='utf-8')=='revision'
    finally:d.close();d.deleteLater();install_theme()

def test_equal_timestamps_have_stable_choice_and_changed_source_still_rejected(tmp_path,app):
    import os
    from direct_material_intake import ranked_versions
    s,root,_=setup(tmp_path)
    for p in root.rglob('*.docx'):os.utime(p,ns=(1_700_000_000_000_000_000,1_700_000_000_000_000_000))
    plan=s.receive_interactive([str(root)])['material_preview'];install_theme(s);d=DirectMaterialDialog(s,plan)
    try:
        indices=d.version_groups()[0]
        assert ranked_versions(plan['entries'],indices)==ranked_versions(plan['entries'],list(reversed(indices)))
        selected=next(e for e in d.values() if e['name']=='甲系统')
        Path(selected['path']).write_bytes(b'changed after preview')
        with pytest.raises(ValueError,match='发生变化'):s.confirm_direct_materials(plan,d.values(),plan['batch'],'collect')
        assert not s.production.rows()
    finally:d.close();d.deleteLater();install_theme()
