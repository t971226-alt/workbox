from pathlib import Path
from zipfile import ZipFile
import pytest
from desktop_service import DesktopService
from material_templates import PRESETS
from requirement_intake import proposal


@pytest.fixture
def service(tmp_path):return DesktopService(tmp_path/'data/workbox.db')


def word(tmp_path,name,content=b'isolated material'):
    p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);return p


def preview(s,paths):return s.receive_interactive([str(p) for p in paths])['material_preview']


def commit(s,plan,rows=None,mode='collect',**kwargs):
    return s.confirm_direct_materials(plan,rows or plan['entries'],plan['batch'],mode,**kwargs)


def test_six_words_preview_does_not_write_then_one_batch_master_and_flat_folder(service,tmp_path):
    files=[word(tmp_path,f'设备软件{i}.docx') for i in range(6)]
    plan=preview(service,files)
    assert not service.list_projects() and not service.production.rows()
    assert {e['package'] for e in plan['entries']}=={'单手册 Word'}
    assert not list(service.db.path.parent.rglob('材料版本'))
    result=commit(service,plan);rows=service.production.rows()
    assert len(rows)==6 and len(result['batch_ids'])==1 and result['copied_count']==6
    assert all(r['production_state']=='已收纳' and r['reviewed_count']==r['submitted_count']==0 for r in rows)
    folders={r['collection_folder'] for r in rows};assert len(folders)==1
    folder=Path(folders.pop());assert folder.name=='设备软件0-6件' and len(list(folder.glob('*.docx')))==6
    assert all(p.read_bytes()==b'isolated material' for p in files)
    commit(service,plan);assert len(service.production.rows())==6


def test_generic_word_requires_name_and_explicit_binding_receives_renamed_software(service,tmp_path):
    p=word(tmp_path,'说明书.docx');plan=preview(service,[p]);assert not plan['entries'][0]['name']
    with pytest.raises(ValueError,match='软件名称'):commit(service,plan)
    assert not service.list_projects()
    rows=[dict(plan['entries'][0],name='人工核对后的系统')]
    commit(service,plan,rows,mode='review');row=service.production.rows()[0]
    assert row['name']=='人工核对后的系统' and row['production_state']=='待检查' and row['collection_count']==1


@pytest.mark.parametrize('package',['五件材料','桌面14件','14件＋双视频'])
def test_software_folders_infer_complete_packages_and_pdf_dependency(service,tmp_path,package):
    root=tmp_path/'批次';files=[word(root/'资产管理系统',kind) for kind in PRESETS[package]]
    plan=preview(service,[root]);assert {e['name'] for e in plan['entries']}=={'资产管理系统'}
    assert {e['package'] for e in plan['entries']}=={package}
    result=commit(service,plan,mode='review');row=service.production.rows()[0]
    assert result['copied_count']==len(files) and row['collection_state']=='已收齐'
    assert not any(m.get('dependency_invalid') for m in row['materials'])


def test_custom_aliases_and_disabled_rule_are_respected(service,tmp_path):
    profile=service.classifications.save('验收文档',['验收报告.docx','签字页.pdf'],{'验收报告.docx':['验收单']},'collect')
    files=[word(tmp_path,'项目甲_验收单.docx'),word(tmp_path,'项目甲_签字页.pdf')]
    plan=preview(service,files);assert {e['name'] for e in plan['entries']}=={'项目甲'}
    assert {e['package'] for e in plan['entries']}=={'验收文档'}
    service.classifications.remove(profile)
    with pytest.raises(ValueError,match='规则'):commit(service,plan)
    assert not service.production.rows()


def test_preview_rejects_changed_source_before_metadata_and_can_omit_files(service,tmp_path):
    a=word(tmp_path,'软件甲.docx');b=word(tmp_path,'软件乙.docx');plan=preview(service,[a,b])
    a.write_bytes(b'changed after preview')
    with pytest.raises(ValueError,match='发生变化'):commit(service,plan)
    assert not service.list_projects()
    plan=preview(service,[a,b]);result=commit(service,plan,[plan['entries'][1]])
    assert result['copied_count']==1 and service.production.rows()[0]['name']=='软件乙'


def test_duplicate_roles_and_different_packages_never_overwrite_silently(service,tmp_path):
    files=[word(tmp_path,'one.docx'),word(tmp_path,'two.docx')];plan=preview(service,files)
    rows=[dict(e,name='同一个系统') for e in plan['entries']]
    with pytest.raises(ValueError,match='同类型'):commit(service,plan,rows)
    assert not service.production.rows()


def test_mixed_existing_and_new_waits_for_duplicate_choice_before_any_write(service,tmp_path):
    old=service.production.import_plan([proposal('已有系统',batch='旧批',requirement='单手册 Word')],token='old')
    a=word(tmp_path,'已有系统.docx',b'old');service.receive_path(a)
    original=service.production.rows()[0];material=original['materials'][0]
    service.production.review(material['id'],True);service.production.submit([material['id']])
    a.write_bytes(b'new bytes');b=word(tmp_path,'全新系统.docx')
    plan=preview(service,[a,b]);pending=commit(service,plan)
    assert len(pending['material_conflicts'])==1 and len(service.production.rows())==1
    assert service.production.rows()[0]['production_state']=='已提交'
    group=pending['material_conflicts'][0]
    result=service.resolve_duplicate_intake(pending['material_plan'],{group['key']:dict(action='overwrite',project_id=original['id'])})
    rows=service.production.rows();assert len(rows)==2 and result['copied_count']==2
    assert rows[0]['production_state']=='待检查' and rows[1]['production_state']=='已收纳'
    assert Path(material['path']).read_bytes()==b'old'
    service.resolve_duplicate_intake(pending['material_plan'],{group['key']:dict(action='overwrite',project_id=original['id'])})
    assert len(service.production.rows())==2 and rows[0]['materials'][0]['version_number']==2


def test_safe_zip_without_names_uses_folder_and_never_runs_members(service,tmp_path):
    archive=tmp_path/'档案软件.zip'
    with ZipFile(archive,'w') as z:z.writestr('软件说明书.docx',b'word')
    plan=preview(service,[archive]);assert plan['entries'][0]['name']=='档案软件'
    assert not service.list_projects()
    commit(service,plan);assert service.production.rows()[0]['collection_count']==1 and archive.exists()
    bad=tmp_path/'不安全.zip'
    with ZipFile(bad,'w') as z:z.writestr('../escape.docx',b'no')
    with pytest.raises(ValueError):preview(service,[bad])
    assert not (service.db.path.parent/'escape.docx').exists()


def test_retry_failed_explicit_binding_keeps_one_task_and_completes(service,tmp_path,monkeypatch):
    p=word(tmp_path,'说明书.docx');plan=preview(service,[p]);rows=[dict(plan['entries'][0],name='核对系统')]
    receive=service.production.receive
    monkeypatch.setattr(service.production,'receive',lambda *a,**k:(_ for _ in ()).throw(OSError('disk unavailable')))
    result=commit(service,plan,rows);assert result['intake_errors'] and len(service.production.rows())==1
    monkeypatch.setattr(service.production,'receive',receive)
    result=service.production.retry_import_files(plan['token'])
    assert not result['intake_errors'] and result['copied_count']==1 and len(service.production.rows())==1


def test_mixed_old_snapshot_survives_profile_edit_and_duplicate_new_is_idempotent(service,tmp_path):
    profile=service.classifications.save('验收档案',['旧报告.docx'],{'旧报告.docx':['验收单']})
    result=service.production.import_plan([dict(proposal('旧系统',batch='原批'),package='验收档案',kinds=['旧报告.docx'])],token='original')
    a=word(tmp_path,'旧系统_验收单.docx',b'old');service.receive_path(a)
    service.classifications.save('验收档案',['新报告.docx'],{},'review',profile)
    a.write_bytes(b'remake');b=word(tmp_path,'新系统.docx');plan=preview(service,[a,b])
    old_entry=next(e for e in plan['entries'] if e['name']=='旧系统')
    assert old_entry['kinds']==['旧报告.docx'] and old_entry['kind']=='旧报告.docx'
    pending=commit(service,plan);group=pending['material_conflicts'][0]
    decisions={group['key']:dict(action='new',project_id=result['project_ids'][0])}
    service.resolve_duplicate_intake(pending['material_plan'],decisions)
    service.resolve_duplicate_intake(pending['material_plan'],decisions)
    rows=service.production.rows();assert len(rows)==3 and rows[0]['materials'][0]['version_number']==1
    assert rows[1]['name']=='旧系统' and rows[1]['materials'][0]['kind']=='旧报告.docx'


def test_stale_duplicate_decision_cannot_create_new_software_or_overwrite(service,tmp_path):
    service.production.import_plan([proposal('旧系统',batch='原批',requirement='单手册 Word')],token='original')
    a=word(tmp_path,'旧系统.docx');service.receive_path(a);a.write_bytes(b'new revision');b=word(tmp_path,'新系统.docx')
    plan=preview(service,[a,b]);pending=commit(service,plan);group=pending['material_conflicts'][0]
    service.production.review(service.production.rows()[0]['materials'][0]['id'],True)
    with pytest.raises(ValueError,match='任务.*变化'):
        service.resolve_duplicate_intake(pending['material_plan'],{group['key']:dict(action='overwrite',project_id=group['candidates'][0]['id'])})
    assert len(service.production.rows())==1


def test_previous_legacy_receipt_can_be_registered_without_destroying_original_record(service,tmp_path):
    source=word(tmp_path,'历史收件系统.docx',b'preserved')
    service.receive_file(source)
    legacy=service.list_projects()[0];assert not service.production.rows()
    plan=preview(service,[source]);commit(service,plan)
    assert len(service.list_projects())==2 and len(service.production.rows())==1
    assert service.get_project(legacy['id'])['name']=='历史收件系统'
    assert source.read_bytes()==b'preserved'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows named streams')
def test_office_streams_survive_full_material_collection(service, tmp_path):
    source = word(tmp_path, '附加信息兼容系统.docx', b'office document content')
    streams = {'WPSSyncID': b'wps-id', 'user.library-file-id': b'file-id', 'Zone.Identifier': b'[ZoneTransfer]\r\nZoneId=3\r\n'}
    for name, data in streams.items():
        Path(str(source) + ':' + name).write_bytes(data)
    plan = preview(service, [source])
    result = commit(service, plan)
    assert result['copied_count'] == 1 and result['pending_count'] == 0
    row = service.production.rows()[0]
    target = Path(row['collection_folder']) / source.name
    assert target.read_bytes() == source.read_bytes()
    for name, data in streams.items():
        assert Path(str(target) + ':' + name).read_bytes() == data
        assert Path(str(source) + ':' + name).read_bytes() == data
    assert commit(service, plan)['copied_count'] == 1
    assert len(service.production.rows()) == 1


def test_retry_failure_count_tracks_remaining_sources(service, tmp_path, monkeypatch):
    files = [word(tmp_path, '重试兼容系统.docx')]
    plan = preview(service, files)
    real_receive = service.production.receive
    def fail(*args, **kwargs):
        raise OSError('isolated copy failure')
    monkeypatch.setattr(service.production, 'receive', fail)
    first = commit(service, plan)
    assert first['pending_count'] == 1 and first['copied_count'] == 0
    monkeypatch.setattr(service.production, 'receive', real_receive)
    result = service.production.retry_import_files(first['import_token'])
    assert result['pending_count'] == 0 and result['copied_count'] == 1
    assert not result['intake_errors'] and len(service.production.rows()) == 1
