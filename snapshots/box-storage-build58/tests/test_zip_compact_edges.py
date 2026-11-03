from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from desktop_service import DesktopService
from direct_material_intake import identity
from zip_intake import confirm_raw
from original_zip_intake import OriginalZipIntake
from task_query import status,unfinished


@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    p=tmp_path/'原包边界系统.zip'
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'v1')
    return s,p,tmp_path


def request(s,p,pid=None):
    expected=OriginalZipIntake(s).snapshot([s.production.rows()[0]]) if pid else None
    return [dict(path=str(p),name=p.stem,project_id=pid,action='overwrite' if pid else 'new',identity=identity(p),expected=expected)]


def test_library_orphan_copy_recovers_without_original_source(data):
    s,p,_=data
    with patch('raw_intake_engine._commit',side_effect=OSError('isolated commit failure')):failed=confirm_raw(s,request(s,p))
    assert failed['copied_count']==0 and failed['pending_count']==1
    assert list((Path(s.workspaces.settings()['work_root'])/'ZIP原包').rglob('*.zip'))
    p.unlink();restarted=DesktopService(s.db.path);done=restarted.production.retry_import_files(failed['import_token'])
    assert done['copied_count']==1 and status(restarted.production.rows()[0])=='原包已收录'


def test_stale_task_cannot_overwrite_newer_raw_receipt(data):
    s,p,root=data;confirm_raw(s,request(s,p));pid=s.production.rows()[0]['id'];old=request(s,p,pid)
    other=root/'new'/p.name;other.parent.mkdir()
    with ZipFile(other,'w') as z:z.writestr('软件说明书.docx',b'newer')
    confirm_raw(s,request(s,other,pid));before=len(s.production.rows()[0]['original_zips'])
    with pytest.raises(ValueError,match='变化'):confirm_raw(s,old)
    assert len(s.production.rows()[0]['original_zips'])==before


def test_missing_old_history_does_not_mark_newest_original_incomplete(data):
    s,p,_=data;confirm_raw(s,request(s,p));row=s.production.rows()[0];old=Path(row['original_zips'][0]['path'])
    with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'v2')
    confirm_raw(s,request(s,p,row['id']));old.unlink();row=s.production.rows()[0]
    assert status(row)=='原包已收录' and not unfinished(row)


def test_two_different_new_packages_for_same_name_do_not_create_partial_tasks(data):
    s,p,root=data;other=root/'second'/p.name;other.parent.mkdir()
    with ZipFile(other,'w') as z:z.writestr('x.txt',b'different')
    with pytest.raises(ValueError,match='同名'):confirm_raw(s,request(s,p)+request(s,other))
    assert not s.production.rows()


def test_skip_does_not_create_task_for_that_archive(data):
    s,p,root=data;other=root/'另一个软件.zip'
    with ZipFile(other,'w') as z:z.writestr('x.txt',b'other')
    rows=request(s,p)+request(s,other);rows[0]['action']='skip';done=confirm_raw(s,rows)
    assert done['copied_count']==1 and [r['name'] for r in s.production.rows()]==['另一个软件']


def test_empty_valid_zip_is_still_a_complete_original_receipt(data):
    s,p,_=data
    with ZipFile(p,'w'):pass
    done=confirm_raw(s,request(s,p));assert done['copied_count']==1 and status(s.production.rows()[0])=='原包已收录'
