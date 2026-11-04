from pathlib import Path
from zipfile import ZipFile
import pytest
from desktop_service import DesktopService
from direct_material_intake import DirectMaterialIntake


def make_files(root, name='自动归档管理系统'):
    files=[]
    for suffix in ['.docx', '.pdf', '.txt', '_代码 (JavaScript+HTML+CSS).docx', '_代码 (JavaScript+HTML+CSS).pdf']:
        p=root/(name+suffix);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('isolated '+suffix).encode())
        files.append(p)
    return files


def test_bare_titles_form_five_material_profile_and_publish_correct_category(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    files=make_files(tmp_path/'incoming')
    plan=s.receive_interactive(list(map(str,files)))['material_preview']
    assert {e['package'] for e in plan['entries']} == {'五件材料'}
    assert {e['kind'] for e in plan['entries']} == set(s.classifications.packages()['五件材料'])
    assert DirectMaterialIntake.can_auto_receive(plan)
    result=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
    assert result['copied_count']==5 and not result['intake_errors']
    row=s.production.rows()[0]
    assert row['package_name']=='五件材料' and row['collection_count']==5
    assert '五件材料' in Path(row['collection_folder']).parts
    assert all(f.exists() for f in files)


def test_complete_five_materials_inside_nested_zip_are_classified_without_fake_archive_task(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    files=make_files(tmp_path/'staging')
    folder=tmp_path/'incoming';folder.mkdir()
    with ZipFile(folder/'自动归档管理系统-5-S.zip','w') as z:
        for f in files:z.write(f,'自动归档管理系统/'+f.name)
    (folder/'未支持程序包.7z').write_bytes(b'isolated unsupported archive')
    plan=s.receive_interactive([str(folder)])['material_preview']
    assert len(plan['entries'])==5 and len(plan['skipped_archives'])==1
    assert DirectMaterialIntake.can_auto_receive(plan)
    result=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
    assert result['copied_count']==5 and len(result['skipped_archives'])==1
    assert [r['name'] for r in s.production.rows()]==['自动归档管理系统']


def test_multiple_versions_and_incomplete_ambiguous_sets_require_review(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    a=tmp_path/'incoming/版本系统.docx';a.parent.mkdir();a.write_bytes(b'a')
    b=a.parent/'修订版'/a.name;b.parent.mkdir();b.write_bytes(b'b')
    plan=s.receive_interactive([str(a.parent)])['material_preview']
    assert not DirectMaterialIntake.can_auto_receive(plan)
    pdf=tmp_path/'未确定系统.pdf';pdf.write_bytes(b'pdf')
    plan=s.receive_interactive([str(pdf)])['material_preview']
    assert not DirectMaterialIntake.can_auto_receive(plan)
    assert not s.list_projects()


def test_unsupported_archive_alone_does_not_create_a_task(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');p=tmp_path/'软件-4-S.7z';p.write_bytes(b'archive')
    with pytest.raises(ValueError,match='先解压'):
        s.receive_interactive([str(p)])
    assert not s.list_projects()


def test_single_word_is_clear_but_unknown_custom_material_is_not_automatic(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    p=tmp_path/'明确系统.docx';p.write_bytes(b'word')
    plan=s.receive_interactive([str(p)])['material_preview']
    assert DirectMaterialIntake.can_auto_receive(plan)
    q=tmp_path/'未知附件.png';q.write_bytes(b'png')
    plan=s.receive_interactive([str(q)])['material_preview']
    assert not DirectMaterialIntake.can_auto_receive(plan)


def test_known_folder_with_unsupported_archive_keeps_explicit_archive_warning(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db')
    folder=tmp_path/'incoming';files=make_files(folder)
    plan=s.receive_interactive(list(map(str,files)))['material_preview']
    s.confirm_direct_materials(plan,plan['entries'],plan['batch'],plan['mode'])
    (folder/'不能直接处理.7z').write_bytes(b'archive')
    plan=s.receive_interactive([str(folder)])['material_preview']
    assert len(plan['skipped_archives'])==1
    assert DirectMaterialIntake.can_auto_receive(plan)
    assert len(s.production.rows())==1
