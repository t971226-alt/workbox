from pathlib import Path
import pytest

@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a

def test_name_template_full_intake_all_types_and_extra_materials(app,tmp_path):
    from scripts.verify_name_templates import verify
    assert all(verify(tmp_path/'templates').values())

def test_all_registered_suffixes_match_by_their_own_type():
    from file_families import TYPES,infer
    from name_patterns import software_name
    assert software_name(Path('材料系统-2-S.xlsx'),'材料系统-2-S')=='材料系统-2-S'
    assert software_name(Path('材料系统-2-S_统计.xlsx'),'材料系统-2-S_统计')=='材料系统-2-S'
    assert software_name(Path('材料系统-2-S_代码 (JavaScript+HTML+CSS).docx'),'材料系统-2-S',role_known=True)=='材料系统-2-S'
    for family,(_,canonical,extensions) in TYPES.items():
        kinds=['同名'+canonical,'代码'+canonical];patterns={kinds[0]:{'mode':'same'},kinds[1]:{'mode':'marker','marker':'代码'}};families={k:family for k in kinds}
        for ext in extensions:
            assert infer('测试软件'+ext,kinds,families,project_name='测试软件',patterns=patterns)==kinds[0]
            assert infer('测试软件_源代码（JavaScript+HTML+CSS）'+ext,kinds,families,project_name='测试软件',patterns=patterns)==kinds[1]
            assert infer('其他软件_代码'+ext,kinds,families,project_name='测试软件',patterns=patterns) is None
            assert infer('测试软件_未知材料'+ext,kinds,families,project_name='测试软件',patterns=patterns) is None

def test_literal_placeholders_save_as_patterns_and_labels_are_independent(app,tmp_path):
    from desktop_service import DesktopService
    from quick_classification_ui import QuickClassificationDialog
    from file_families import infer
    s=DesktopService(tmp_path/'data/workbox.db');q=QuickClassificationDialog(s)
    q.editor.name.setText('我的两份文件');q.editor.table.item(0,0).setText('软件名称');q.editor.table.cellWidget(0,3).setText('软件名称')
    q.editor.add('.docx');q.editor.table.item(1,0).setText('软件名称代码');q.save();assert q.profile_id,q.error.text()
    p=next(p for p in s.classifications.profiles(True) if p['name']=='我的两份文件')
    assert infer('电子元器件成品性能测试管理系统_代码 (JavaScript+HTML+CSS).docx',p['kinds'],p['family_map'],project_name='电子元器件成品性能测试管理系统',patterns=p['name_patterns'])=='软件名称代码.docx'
    q.close();q.deleteLater();app.processEvents()

def test_unsaved_rule_cancellation_and_manual_ambiguity(app,tmp_path):
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    p=tmp_path/'测试系统.docx';p.write_bytes(b'doc');q=tmp_path/'测试系统_代码.docx';q.write_bytes(b'code')
    plan=DirectMaterialIntake(s).prepare([str(p),str(q)],include_known=True);profiles=s.classifications.profiles(True)
    d=MaterialIntakeDialog(s,plan);d.reject();assert s.classifications.profiles(True)==profiles and not s.production.rows()
    d=MaterialIntakeDialog(s,plan);d.form.save_rule.setChecked(False)
    from intake_onboarding import confirm_first_receipts
    confirm_first_receipts(d.form)
    d.confirm();assert d.operation,d.error.text();d.operation()
    assert s.classifications.profiles(True)==profiles and s.production.rows()[0]['collection_count']==2 and len(s.production.rows()[0]['name_patterns'])==2
    d.close();d.deleteLater();app.processEvents()

def test_template_snapshot_does_not_follow_rule_edits(app,tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    rule=s.classifications.save('模板',['同名.docx'],mode='collect',family_map={'同名.docx':'word'},name_patterns={'同名.docx':{'mode':'same'}})
    pid=s.production.create_batch('批次',['测试系统'],['同名.docx'],package='模板')['project_ids'][0]
    s.classifications.save('模板改名',['同名.docx'],mode='collect',profile_id=rule,family_map={},name_patterns={'同名.docx':{'mode':'manual'}})
    p=tmp_path/'测试系统.doc';p.write_bytes(b'old word')
    with s.db.connect() as c:assert s.production.match_kind(c,pid,p.name)=='同名.docx'
    s.production.receive(pid,'同名.docx',p);assert s.production.rows()[0]['collection_count']==1

def test_duplicate_download_suffix_is_one_slot_and_archives_reenter_template(app,tmp_path):
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    from zipfile import ZipFile
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    p=tmp_path/'测试系统.docx';p.write_bytes(b'old');q=tmp_path/'测试系统 (1).docx';q.write_bytes(b'new')
    plan=DirectMaterialIntake(s).prepare([str(p),str(q)],include_known=True)
    assert len(plan['entries'][0]['suggested_draft']['kinds'])==1
    s.classifications.save('原包模板',['同名压缩包.zip'],mode='collect',family_map={'同名压缩包.zip':'archive'},name_patterns={'同名压缩包.zip':{'mode':'same'}})
    z=tmp_path/'归档系统.zip'
    with ZipFile(z,'w') as f:f.writestr('不解压.txt',b'raw')
    result=s.receive_interactive([str(z)],zip_mode='ask',linked=True)
    assert 'material_preview' in result and len(result['material_preview']['entries'])==1
    d=MaterialIntakeDialog(s,result['material_preview']);d.confirm();assert d.operation,d.error.text();d.operation()
    assert s.production.rows()[0]['collection_count']==1
    d.close();d.deleteLater();app.processEvents()

def test_extensionless_unknown_is_visible_and_does_not_block_known_file(app,tmp_path):
    from PySide6.QtCore import Qt
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    p=tmp_path/'材料系统.docx';p.write_bytes(b'word');q=tmp_path/'README';q.write_bytes(b'unknown')
    plan=DirectMaterialIntake(s).prepare([str(p),str(q)],include_known=True,keep_archives=True);assert len(plan['entries'])==2
    d=MaterialIntakeDialog(s,plan)
    i=next(i for i,e in enumerate(plan['entries']) if e['path']==str(q));assert d.form.table.item(i,0).checkState()==Qt.Unchecked
    d.confirm();assert d.operation,d.error.text();d.operation()
    assert len(s.production.rows())==1 and q.read_bytes()==b'unknown'
    d.close();d.deleteLater();app.processEvents()

def test_managed_code_copy_still_requires_replace_or_new_choice(app,tmp_path):
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'saved',complete_setup=True)
    p=tmp_path/'测试系统.docx';p.write_bytes(b'word');q=tmp_path/'测试系统_代码.docx';q.write_bytes(b'code')
    plan=DirectMaterialIntake(s).prepare([str(p),str(q)],include_known=True);d=MaterialIntakeDialog(s,plan)
    from intake_onboarding import confirm_first_receipts
    confirm_first_receipts(d.form)
    d.confirm();assert d.operation,d.error.text();d.operation()
    with s.db.connect() as c:saved=Path(c.execute("SELECT path FROM collected_materials WHERE kind='代码 Word.docx'").fetchone()[0])
    saved.write_bytes(b'changed managed code')
    pending=s.receive_interactive([str(saved)],linked=True)
    assert pending.get('material_conflicts') and len(pending['material_conflicts'])==1
    d.close();d.deleteLater();app.processEvents()
