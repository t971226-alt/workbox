from pathlib import Path
import pytest
from material_naming import candidates
from direct_material_intake import DirectMaterialIntake
from production import infer_kind
from material_templates import PRESETS

def profiles():return [dict(name=n,kinds=k,aliases={}) for n,k in PRESETS.items() if k]

@pytest.mark.parametrize('filename,name,kind',[
    ('资产系统_功能测试表.docx','资产系统','功能测试表.docx'),
    ('资产系统功能测试表.docx','资产系统','功能测试表.docx'),
    ('资产系统—用户手册.docx','资产系统','软件说明书.docx'),
    ('资产系统用户手册.docx','资产系统','软件说明书.docx'),
    ('资产系统_软件设计文档.docx','资产系统','软件设计文档.docx'),
    ('资产系统_源代码（C++）.docx','资产系统','软件著作权代码段.docx'),
    ('数据库演示_资产系统.mp4','资产系统','数据库演示.mp4'),
    ('资产系统_软件说明书 (1).docx','资产系统','软件说明书.docx'),
])
def test_new_and_existing_material_recognition_agree(filename,name,kind):
    assert DirectMaterialIntake.role_and_name(Path(filename),profiles(),[])==(name,kind)
    assert infer_kind(filename,[k for kinds in PRESETS.values() for k in kinds])==kind

def test_disabled_report_is_not_reinterpreted_as_manual():
    assert DirectMaterialIntake.role_and_name(Path('资产系统_功能测试表.docx'),profiles()[:1],[])==('资产系统','功能测试表.docx')

def test_batch_folder_is_not_a_software_name():
    assert DirectMaterialIntake.role_and_name(Path('功能特点.docx'),profiles(),['批次'])[0]==''
    assert DirectMaterialIntake.role_and_name(Path('功能特点.docx'),profiles(),['资产系统','批次'])[0]=='资产系统'
    assert DirectMaterialIntake.role_and_name(Path('软件著作权代码段（C++）.docx'),profiles(),[])==('','软件著作权代码段.docx')

def test_alias_in_software_name_does_not_claim_its_material_role():
    assert candidates('验收单管理系统_签字页.pdf',['验收报告.pdf','签字页.pdf'],{'验收报告.pdf':['验收单']})==[('验收单管理系统','签字页.pdf')]

def test_legacy_extensionless_requirements_remain_recognizable():
    assert infer_kind('资产系统_说明书.txt',['说明书','源代码'])=='说明书'

def test_existing_project_name_is_matched_whole_not_by_prefix(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db')
    s.production.create_batch('旧批',['资产'],['软件说明书.docx'])
    f=tmp_path/'资产_管理系统_软件说明书.docx';f.write_bytes(b'test')
    plan=s.receive_interactive([str(f)])['material_preview']
    assert plan['entries'][0]['name']=='资产_管理系统' and not plan['entries'][0].get('existing')

def test_joined_names_route_to_existing_task(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db')
    s.production.create_batch('旧批',['资产系统'],['功能测试表.docx'])
    f=tmp_path/'资产系统功能测试表.docx';f.write_bytes(b'test')
    assert [r['name'] for r in s.file_routing(f)['candidates']]==['资产系统']

def test_incomplete_shared_package_requires_choice(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db');f=tmp_path/'资产系统_功能测试表.docx';f.write_bytes(b'test')
    plan=s.receive_interactive([str(f)])['material_preview']
    row=plan['entries'][0]
    assert row['name']=='资产系统' and row['kind']=='功能测试表.docx' and row['package']=='待确认'
    with pytest.raises(ValueError,match='多个可能'):s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect')
    assert not s.production.rows()

def test_identical_user_profiles_do_not_depend_on_list_order(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db')
    for name in ['甲验收','乙验收']:s.classifications.save(name,['验收报告.docx'])
    f=tmp_path/'资产系统_验收报告.docx';f.write_bytes(b'test')
    plan=s.receive_interactive([str(f)])['material_preview']
    assert plan['entries'][0]['package']=='待确认'
