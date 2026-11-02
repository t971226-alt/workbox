from pathlib import Path
from zipfile import ZipFile
from threading import Event
from concurrent.futures import CancelledError
import pytest,py7zr
from desktop_service import DesktopService
from direct_material_intake import DirectMaterialIntake
from file_formats import OFFICE,LABELS,badge
from zip_intake import confirm_raw
from archive_support import member_name,validate_stream
from storage_management import staging_root,finish_plan

@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    return s,tmp_path

def pack(root,files=None,password=None,filters=None):
    p=root/'办公资料系统.7z'
    with py7zr.SevenZipFile(p,'w',password=password,filters=filters) as z:
        for name,content in (files or {'软件说明书.doc':b'word','统计表.xlsx':b'sheet','演示文稿.pptx':b'slides','界面截图.png':b'image'}).items():z.writestr(content,name)
    return p

def test_all_office_suffixes_retain_format_and_recognize_name(data):
    s,_=data
    for ext in sorted(OFFICE):
        name,kind=DirectMaterialIntake.role_and_name(Path('办公资料系统_功能测试表'+ext.upper()),s.classifications.profiles(True),[])
        assert name=='办公资料系统' and kind=='功能测试表'+ext,(ext,name,kind)
        assert ext in LABELS and badge(ext)

@pytest.mark.parametrize('ext',['.doc','.docm','.wps','.xlsx','.xls','.xlsm','.xlsb','.csv','.tsv','.et','.ppt','.pptx','.pptm','.dps','.pdf','.rtf','.odt','.ods','.odp','.png','.jpeg','.svg','.md','.json'])
def test_office_drop_can_be_saved_without_conversion(data,ext):
    s,root=data;p=root/('办公资料系统_功能测试表'+ext);p.write_bytes(b'format unchanged')
    plan=DirectMaterialIntake(s).prepare([str(p)],include_known=True);e=plan['entries'][0]
    assert e['name']=='办公资料系统' and Path(e['kind']).suffix==ext
    result=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect');assert result['copied_count']==1
    row=s.production.rows()[0];assert row['collection_count']==1 and row['materials'][0]['kind'].endswith(ext) and p.read_bytes()==b'format unchanged'

def test_seven_zip_raw_keeps_original_and_does_not_extract(data):
    s,root=data;p=pack(root);before=p.read_bytes();result=confirm_raw(s,[dict(path=str(p),name='办公资料系统',target=None)])
    row=s.production.rows()[0];assert result['copied_count']==1 and row['production_state']=='原包已收录' and row['collection_count']==0
    saved=Path(row['original_zips'][0]['path']);assert saved.suffix=='.7z' and saved.read_bytes()==before and p.read_bytes()==before
    assert not staging_root(s).exists()

@pytest.mark.parametrize('codec',[py7zr.FILTER_LZMA2,py7zr.FILTER_BZIP2,py7zr.FILTER_DEFLATE,py7zr.FILTER_PPMD])
def test_seven_zip_extracts_office_formats_and_saves_all(data,codec):
    s,root=data;p=pack(root,filters=[{'id':codec}]);plan=DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True)
    assert len(plan['entries'])==4 and all(Path(e['path']).is_relative_to(staging_root(s)) for e in plan['entries'])
    result=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect');assert result['copied_count']==4
    rows=s.production.rows();assert len(rows)==1 and rows[0]['collection_count']==4

def test_encrypted_seven_zip_allows_raw_but_extraction_has_explanation(data):
    s,root=data;p=pack(root,password='local-test')
    assert confirm_raw(s,[dict(path=str(p),name='加密原件',target=None)])['copied_count']==1
    with pytest.raises(ValueError,match='加密'):DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True)
    assert len(s.production.rows())==1

def test_cancelled_preview_releases_seven_zip_cache(data):
    s,root=data;p=pack(root);plan=DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True)
    paths=[Path(e['path']) for e in plan['entries']];finish_plan(s,plan,discard=True)
    assert p.is_file() and all(not f.exists() for f in paths)

@pytest.mark.parametrize('name',['../out.doc','C:/out.doc','x/../../out.doc','x:stream.doc','CON.txt','x./a.doc','a\\..\\x.doc'])
def test_archive_member_paths_are_bounded(name):
    with pytest.raises(ValueError):member_name(name)

def test_corrupt_seven_zip_creates_no_task(data):
    s,root=data;p=root/'损坏.7z';p.write_bytes(b'7z\xbc\xaf\x27\x1c'+b'\0'*9)
    with pytest.raises(ValueError):confirm_raw(s,[dict(path=str(p),name='损坏',target=None)])
    assert not s.production.rows()

def test_zip_containing_only_excel_and_slides_is_recognized(data):
    s,root=data;p=root/'办公资料系统.zip'
    with ZipFile(p,'w') as z:z.writestr('统计表.xls',b'x');z.writestr('演示文稿.ppt',b'p')
    plan=DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True)
    assert len(plan['entries'])==2

def test_seven_zip_cancel_creates_no_task_or_partial_unpacked_dir(data):
    s,root=data;p=pack(root);event=Event();event.set()
    with pytest.raises(CancelledError):DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True,cancel_event=event)
    assert not s.production.rows() and not list(staging_root(s).glob('.7z-reading-*'))

def test_same_name_with_different_case_in_seven_zip_is_rejected(data):
    s,root=data;p=pack(root,{'报告.docx':b'a','报告.DOCX':b'b'})
    with pytest.raises(ValueError,match='重复'):DirectMaterialIntake(s).prepare([str(p)],require_material_zip=True,include_known=True)
    assert not s.production.rows()

def test_truncated_seven_zip_is_rejected_before_creating_task(data):
    s,root=data;p=pack(root);p.write_bytes(p.read_bytes()[:-4])
    with pytest.raises(ValueError):confirm_raw(s,[dict(path=str(p),name='不完整原包',target=None)])
    assert not s.production.rows()

def test_doc_does_not_silently_fulfill_docx_requirement(data):
    s,root=data;pid=s.production.create_batch('不同格式',['同名办公系统'],['软件说明书.docx'])['project_ids'][0]
    source=root/'同名办公系统_软件说明书.doc';source.write_bytes(b'old doc format')
    plan=DirectMaterialIntake(s).prepare([str(source)],include_known=True)
    with pytest.raises(ValueError,match='材料'):s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect')
    assert s.production.rows()[0]['collection_count']==0 and source.is_file()

def test_bare_canonical_names_keep_existing_material_roles(data):
    s,_=data
    for ext,kind in [('.docx','软件说明书.docx'),('.pdf','软件说明书.pdf'),('.txt','登记申请信息.txt'),('.doc','软件说明书.doc')]:
        assert DirectMaterialIntake.role_and_name(Path('兼容验证系统'+ext),s.classifications.profiles(True),[])==('兼容验证系统',kind)
