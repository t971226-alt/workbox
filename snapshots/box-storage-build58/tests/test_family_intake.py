from pathlib import Path
from zipfile import ZipFile
import pytest


def test_family_receipt_and_output_names(tmp_path):
    from PySide6.QtWidgets import QApplication
    from scripts.verify_family_intake import verify
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    assert all(verify(tmp_path/'family-flow').values())


@pytest.mark.parametrize('suffix',['.doc','.wps','.odt','.rtf','.docx'])
def test_word_variants_fill_one_logical_slot(tmp_path,suffix):
    from desktop_service import DesktopService
    from zip_intake import confirm_materials
    from direct_material_intake import DirectMaterialIntake
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    s.classifications.save('说明书规则',['操作说明.docx'],mode='collect',family_map={'操作说明.docx':'word'})
    source=tmp_path/('类型系统_操作说明'+suffix);source.write_bytes(b'exact bytes')
    plan=DirectMaterialIntake(s).prepare([str(source)],include_known=True)
    e=plan['entries'][0];assert e['kind']=='操作说明.docx'
    result=confirm_materials(s,plan,plan['entries'],plan['batch']);assert result['copied_count']==1
    row=s.production.rows()[0];assert row['required_count']==row['collection_count']==1
    with s.db.connect() as c:path=Path(c.execute('SELECT path FROM collected_materials').fetchone()[0])
    assert path.suffix==suffix and path.read_bytes()==source.read_bytes()


def test_two_variants_of_same_material_select_one_and_count_once(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    app=QApplication.instance() or QApplication([])
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    s.classifications.save('说明材料',['操作说明.docx'],mode='collect',family_map={'操作说明.docx':'word'})
    files=[]
    for ext in ['.doc','.docx']:
        p=tmp_path/('同项系统_操作说明'+ext);p.write_bytes(ext.encode());files.append(str(p))
    plan=DirectMaterialIntake(s).prepare(files,include_known=True);d=MaterialIntakeDialog(s,plan);d.show();app.processEvents()
    assert sum(d.form.table.item(i,0).checkState()==Qt.Checked for i in range(2))==1
    d.confirm();assert d.operation,d.error.text();d.operation();assert s.production.rows()[0]['collection_count']==1
    d.close();d.deleteLater();app.processEvents()


def test_output_path_validation_and_collision_preserve_unrelated_files(tmp_path):
    from desktop_service import DesktopService
    from intake_storage import validate,bind
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    pid=s.production.create_batch('命名校验',['系统'],['报告.docx'])['project_ids'][0]
    for folder in ['../escape','CON','name.','a/b']:
        with pytest.raises(ValueError):validate(s,dict(root=str(tmp_path/'材料库'),folder=folder,base='系统'))
    target=tmp_path/'材料库/已有目录';target.mkdir();unrelated=target/'原文件.docx';unrelated.write_bytes(b'do not overwrite')
    with s.db.connect() as c:
        out=bind(s,c,pid,dict(root=str(target.parent),folder=target.name,base='系统'),{'报告.docx':'系统'})
    assert Path(out)!=target and unrelated.read_bytes()==b'do not overwrite'


@pytest.mark.parametrize('extensions',[['.docx','.pdf'],['.docx','.docx']])
def test_multimaterial_defaults_only_disambiguate_actual_filename_collisions(tmp_path,extensions):
    from PySide6.QtWidgets import QApplication
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    from zip_intake_ui import MaterialIntakeDialog
    app=QApplication.instance() or QApplication([])
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    kinds=[role+ext for role,ext in zip(['报告','说明'],extensions)]
    s.classifications.save('两份材料',kinds,mode='collect')
    files=[]
    for kind in kinds:
        p=tmp_path/('默认命名系统_'+kind);p.write_bytes(kind.encode());files.append(str(p))
    plan=DirectMaterialIntake(s).prepare(files,include_known=True);d=MaterialIntakeDialog(s,plan)
    rows=d.form.values();names=[r['storage']['base'] for r in rows]
    if len(set(extensions))==2:assert names==['默认命名系统']*2
    else:
        assert len(set(names))==2 and all(n.startswith('默认命名系统_') for n in names)
        d.form.save_location.base.setText('同名文件')
        with pytest.raises(ValueError,match='重名文件'):
            DirectMaterialIntake(s).validate(d.form.preview_plan(),d.form.values(),plan['batch'],'collect')
        d.form.save_location.base_dirty=False
    d.confirm();assert d.operation,d.error.text();d.operation()
    row=s.production.rows()[0];assert row['collection_count']==2
    assert {p.name for p in Path(row['collection_folder']).iterdir() if p.is_file()}=={n+ext for n,ext in zip(names,extensions)}
    assert s.production.file_candidates('默认命名系统_'+kinds[0],s.production.rows())
    d.close();d.deleteLater();app.processEvents()


def test_later_supplement_keeps_software_name_and_format_collision_copies_distinct(tmp_path):
    from desktop_service import DesktopService
    from intake_storage import bind
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    s.classifications.save('同类两份',['报告.docx','说明.docx'],mode='collect',family_map={'报告.docx':'word','说明.docx':'word'})
    pid=s.production.create_batch('补交',['软件名称'],['报告.docx','说明.docx'],package='同类两份')['project_ids'][0]
    with s.db.connect() as c:bind(s,c,pid,dict(root=str(tmp_path/'材料库'),folder='软件名称',base='软件名称'),{'报告.docx':'软件名称','说明.docx':'软件名称'})
    a=tmp_path/'报告.doc';a.write_bytes(b'report');b=tmp_path/'说明.docx';b.write_bytes(b'description')
    s.production.receive(pid,'报告.docx',a);s.production.receive(pid,'说明.docx',b)
    folder=tmp_path/'材料库/软件名称';assert (folder/'软件名称.doc').read_bytes()==b'report' and (folder/'软件名称.docx').read_bytes()==b'description'
    a=tmp_path/'报告.docx';a.write_bytes(b'report new docx');s.production.receive(pid,'报告.docx',a)
    with s.db.connect() as c:paths=[Path(r[0]) for r in c.execute('SELECT path FROM collected_materials WHERE project_id=?',(pid,))]
    assert len(set(paths))==2 and {p.read_bytes() for p in paths}=={b'report new docx',b'description'}
    assert s.production.rows()[0]['collection_count']==2
