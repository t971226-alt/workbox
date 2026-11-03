from pathlib import Path
import pytest
from desktop_service import DesktopService
from requirement_intake import proposal


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path/'data/workbox.db')


def plan(s,name='自定义软件',batch='第一批',package='验收报告'):
    rows=s.classifications.resolve([proposal(name,batch=batch,requirement=package)])
    return s.production.import_plan(rows,token=batch)


def test_custom_presets_keep_task_requirements_aliases_and_folder_after_edit(service,tmp_path):
    profile=service.classifications.save('验收报告',['报告.docx'],{'报告.docx':['验收单']},'collect')
    result=plan(service)
    pid=result['project_ids'][0];bid=result['batch_ids'][0]
    assert service.production.rows()[0]['processing_mode']=='collect'
    source=tmp_path/'自定义软件_验收单.docx';source.write_bytes(b'approved report')
    service.receive_path(source)
    before=service.production.rows()[0];assert before['production_state']=='已收纳'
    directory=before['collection_folder'];assert '验收报告' in directory
    service.classifications.save('新验收报告',['报告.docx','报告.pdf'],{'报告.docx':['新验收单']},'review',profile)
    after=service.production.rows()[0]
    assert after['collection_folder']==directory and after['required_count']==1 and after['package_name']=='验收报告'
    with service.db.connect() as c:
        assert service.production.match_kind(c,pid,'自定义软件_验收单.docx')=='报告.docx'
        assert service.production.match_kind(c,pid,'自定义软件_新验收单.docx')=='报告.docx'
    source.write_bytes(b'updated existing report');service.receive_path(source)
    assert service.production.rows()[0]['collection_folder']==directory
    assert len(service.production.history(pid,'报告.docx'))==2
    plan(service,name='新软件',batch='第二批',package='新验收报告')
    assert service.production.rows()[-1]['required_count']==2 and service.production.rows()[-1]['processing_mode']=='review'


def test_storage_mode_does_not_invent_review_or_submission_and_can_switch_back(service,tmp_path):
    bid=service.production.create_batch('第一批',['管理系统'],['软件说明书.docx'])['batch_id']
    source=tmp_path/'管理系统.docx';source.write_bytes(b'first');service.receive_path(source)
    row=service.production.rows()[0];vid=row['materials'][0]['id']
    service.production.set_batch_mode(bid,'collect')
    row=service.production.rows()[0]
    assert row['production_state']=='已收纳' and row['reviewed_count']==row['submitted_count']==0
    with pytest.raises(ValueError,match='统计整理已完成材料'):service.production.review(vid,True)
    with pytest.raises(ValueError,match='统计整理已完成材料'):service.production.submit([vid])
    day=service.production.local_date(row['collected_at'])
    assert service.production.storage_statistics(day)==1
    service.production.set_batch_mode(bid,'review');service.production.review(vid,True);service.production.submit([vid])
    assert service.production.rows()[0]['production_state']=='已提交'
    service.production.set_batch_mode(bid,'collect');service.production.set_batch_mode(bid,'review')
    assert service.production.rows()[0]['submitted_count']==1
    source.write_bytes(b'new version');service.receive_path(source)
    assert service.production.rows()[0]['reviewed_count']==service.production.rows()[0]['submitted_count']==0
    assert service.production.history(row['id'],'软件说明书.docx')[-1]['submissions']


def test_partial_storage_and_default_for_future_batch(service,tmp_path):
    service.set_setting('default_processing_mode','collect')
    result=service.production.create_batch('第一批',['甲系统'],['软件说明书.docx','登记申请信息.txt'])
    path=tmp_path/'甲系统.docx';path.write_bytes(b'word');service.receive_path(path)
    row=service.production.rows()[0];assert row['production_state']=='部分收纳' and row['collection_count']==1
    path=tmp_path/'甲系统_登记申请信息.txt';path.write_text('info');service.receive_path(path)
    assert service.production.rows()[0]['production_state']=='已收纳'
    assert service.get_project(result['project_ids'][0])['status']!='已交付'
    assert not service.get_project(result['project_ids'][0])['delivered_at']
    service.production.set_batch_mode(result['batch_id'],'review')
    assert service.get_setting('default_processing_mode')=='collect'


def test_delete_unused_and_disable_used_profiles_are_persistent(service):
    a=service.classifications.save('验收报告',['报告.docx']);plan(service)
    assert '停用' in service.classifications.remove(a)
    assert service.production.rows()[0]['required_count']==1
    b=service.classifications.save('备用',['备用.txt']);assert service.classifications.remove(b)=='已删除'
    again=DesktopService(service.db.path)
    assert '验收报告' not in again.classifications.packages() and '备用' not in again.classifications.packages()
    with pytest.raises(ValueError):service.classifications.save('全部',['a.txt'])
    with pytest.raises(ValueError):service.classifications.save('坏路径',['../a.txt'])
    with pytest.raises(ValueError):service.classifications.save('重复关键词',['a.docx','b.docx'],{'a.docx':['同一'],'b.docx':['同一']})


def test_cancel_mode_validation_and_sorting_preserve_records(service):
    initial=service.classifications.profiles();a,b=initial[:2]
    service.classifications.move(b['id'],-1)
    assert service.classifications.profiles()[0]['id']==b['id']
    result=service.production.create_batch('一批',['软件'],['软件说明书.docx'])
    before=service.production.rows()
    with pytest.raises(ValueError):service.production.set_batch_mode(result['batch_id'],'bad')
    assert service.production.rows()==before


class Registry:
    HKEY_CURRENT_USER='user';KEY_SET_VALUE=2;REG_SZ=1
    def __init__(self):self.values={'Unrelated':'keep'}
    def OpenKey(self,*args):return self
    def CreateKeyEx(self,*args):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def QueryValueEx(self,key,name):
        if name not in self.values:raise FileNotFoundError()
        return self.values[name],1
    def SetValueEx(self,key,name,zero,kind,value):self.values[name]=value
    def DeleteValue(self,key,name):
        if name not in self.values:raise FileNotFoundError()
        del self.values[name]


def test_startup_quotes_portable_path_and_changes_only_its_own_value(tmp_path):
    import startup
    folder=tmp_path/'BOX with spaces';folder.mkdir();exe=folder/'WorkBox.exe';exe.write_bytes(b'fixture not executed')
    reg=Registry();assert not startup.enabled(reg,exe)
    startup.set_enabled(True,reg,exe)
    assert startup.enabled(reg,exe) and reg.values[startup.VALUE_NAME]=='"'+str(exe.resolve())+'" --startup'
    assert reg.values['Unrelated']=='keep'
    startup.set_enabled(False,reg,exe);startup.set_enabled(False,reg,exe)
    assert not startup.enabled(reg,exe) and reg.values=={'Unrelated':'keep'}


def test_storage_queries_are_read_only_and_do_not_ask_for_review(service,tmp_path):
    from workspace_queries import parse_query,answer
    from datetime import date
    bid=service.production.create_batch('收纳批',['收纳系统'],['软件说明书.docx'])['batch_id']
    path=tmp_path/'收纳系统.docx';path.write_bytes(b'archive');service.receive_path(path)
    service.production.set_batch_mode(bid,'collect')
    row=service.production.rows()[0];today=date.fromisoformat(service.production.local_date(row['collected_at']))
    before=service.production.rows()
    spec=parse_query('今天收纳了多少？',today=today)
    assert spec['state']=='已收纳' and spec['name']==''
    assert answer(service.production,spec)['count']==1
    assert answer(service.production,parse_query('哪些软件还没检查？',today=today))['count']==0
    assert service.production.rows()==before


def test_automatic_tasks_keep_their_snapshot_after_template_edit(service,tmp_path):
    profile=next(p for p in service.classifications.profiles() if p['name']=='单手册 Word')
    service.production.import_plan([proposal('自动软件',batch='第一批')],token='auto')
    path=tmp_path/'自动软件.docx';path.write_bytes(b'first');service.receive_path(path)
    assert service.production.rows()[0]['required_count']==1
    service.classifications.save('单手册 Word',['软件说明书.docx','软件说明书.pdf'],profile_id=profile['id'])
    path.write_bytes(b'revision');service.receive_path(path)
    assert service.production.rows()[0]['required_count']==1
    service.classifications.remove(profile['id'])
    service.production.import_plan([proposal('后续软件',batch='第二批')],token='later')
    path=tmp_path/'后续软件.docx';path.write_bytes(b'later');service.receive_path(path)
    assert service.production.rows()[-1]['package_name']=='自定义'


def test_edited_word_class_can_store_both_formats_without_filename_collision(service,tmp_path):
    profile=next(p for p in service.classifications.profiles() if p['name']=='单手册 Word')
    service.classifications.save('单手册 Word',['软件说明书.docx','软件说明书.pdf'],profile_id=profile['id'])
    plan(service,name='双格式系统',package='单手册 Word')
    for suffix in ('docx','pdf'):
        path=tmp_path/('双格式系统_软件说明书.'+suffix);path.write_bytes(suffix.encode());service.receive_path(path)
    row=service.production.rows()[0]
    assert row['collection_count']==row['required_count']==2
    folder=Path(row['collection_folder'])
    assert len(list(folder.glob('*.docx')))==len(list(folder.glob('*.pdf')))==1
    assert next(folder.glob('*.docx')).read_bytes()==b'docx'
    assert next(folder.glob('*.pdf')).read_bytes()==b'pdf'
