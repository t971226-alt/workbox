"""Storage completion is independent of content review and recoverable on disk."""
from pathlib import Path
from unittest.mock import patch
import pytest
from desktop_service import DesktopService
from task_query import status,query,unfinished
from workspace_queries import parse_query,answer


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    pid=s.production.create_batch('隔离批次',['边界验证系统'],['软件说明书.docx','软件说明书.pdf'])['project_ids'][0]
    for ext in ['docx','pdf']:
        p=tmp_path/('边界验证系统.'+ext);p.write_bytes(ext.encode());s.production.receive(pid,'软件说明书.'+ext,p)
    return s,pid,tmp_path


def task(data):s,pid,_=data;return next(r for r in s.production.rows() if r['id']==pid)


@pytest.mark.parametrize('target',['published','archive'])
@pytest.mark.parametrize('damage',['missing','changed'])
def test_explicit_reimport_repairs_only_selected_file_and_keeps_history(data,target,damage):
    s,pid,root=data;row=task(data);m=row['materials'][0]
    from saved_locations import collection_location
    p=collection_location(s,pid,m['kind']) if target=='published' else Path(m['path'])
    if damage=='missing':p.unlink()
    else:p.write_bytes(b'external bytes')
    assert status(task(data))=='有异常' and task(data)['collection_count']==1
    # Receiving the other file must not silently restore this one.
    s.production.receive(pid,'软件说明书.pdf',root/'边界验证系统.pdf')
    assert status(task(data))=='有异常'
    s.production.receive(pid,'软件说明书.docx',root/'边界验证系统.docx')
    assert status(task(data))=='已收齐' and task(data)['collection_count']==2
    assert collection_location(s,pid,'软件说明书.docx').read_bytes()==b'docx'
    if target=='published' and damage=='changed':assert any(p.read_bytes()==b'external bytes' for p in (s.db.path.parent/'材料历史').rglob('*.docx'))


def test_failed_replacement_is_pending_even_with_intact_previous_material(data):
    s,pid,root=data;p=root/'边界验证系统.docx';p.write_bytes(b'new')
    plan=s.receive_interactive([str(p)],linked=True)['material_preview']
    pending=s.confirm_direct_materials(plan,plan['entries'],plan['batch'],'collect')
    with patch.object(s.production,'receive',side_effect=OSError('isolated full disk')):
        result=s.resolve_duplicate_intake(pending['material_plan'],{g['key']:dict(action='overwrite',project_id=pid) for g in pending['material_conflicts']})
    assert result['failed_count'] if 'failed_count' in result else result.get('pending_count') or result.get('failed')
    r=task(data);assert unfinished(r) and status(r) in ('有异常','待确认') and r['collection_count']==2


def test_requirement_change_retains_previous_files_and_recalculates_counts(data):
    s,pid,root=data;old=task(data);oldpaths=[Path(m['path']) for m in old['materials']]
    s.production.configure_task(pid,['软件说明书.docx','软件说明书.pdf','登记信息.txt'],reason='新增一个文件要求')
    r=task(data);assert status(r)=='待补材料' and r['required_count']==3 and r['collection_count']==2
    assert all(p.is_file() for p in oldpaths)


def test_delete_restore_keeps_material_versions(data):
    s,pid,_=data;before=task(data);s.production.delete_tasks([pid],confirmed=True)
    assert not s.production.rows();s.production.restore_tasks([pid]);after=task(data)
    assert status(after)=='已收齐' and [m['id'] for m in before['materials']]==[m['id'] for m in after['materials']]


def test_receipt_date_filters_and_conversation_match_same_scope(data):
    s,pid,_=data
    with s.db.connect() as c:c.execute("UPDATE material_versions SET created_at='2026-10-01T10:00:00Z'")
    spec=parse_query('2026-10-01收录了多少')
    assert spec and spec['metric']=='received' and answer(s.production,spec)['matched_project_ids']==[pid]
    rows,_=query(s.production.rows(),s.production.local_date,view='completed',period='custom',start='2026-10-01',end='2026-10-01')
    assert [r['id'] for r in rows]==[pid]
