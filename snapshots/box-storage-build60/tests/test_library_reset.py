from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from desktop_service import DesktopService
from direct_material_intake import DirectMaterialIntake
from zip_intake import confirm_raw
from library_reset import plan,reset,connection,digest
from native_files import ZipLease

@pytest.fixture
def data(tmp_path):
    s=DesktopService(tmp_path/'app/data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    original=tmp_path/'原始系统_软件说明书.docx';original.write_bytes(b'original bytes')
    p=DirectMaterialIntake(s).prepare([str(original)],include_known=True);result=s.confirm_direct_materials(p,p['entries'],p['batch'],'collect')
    raw=tmp_path/'测试原包.zip'
    with ZipFile(raw,'w') as z:z.writestr('file.txt',b'raw bytes')
    confirm_raw(s,[dict(path=str(raw),name='测试原包',target=None)])
    unknown=tmp_path/'材料库/我的其他文件.txt';unknown.write_bytes(b'not managed')
    return s,original,raw,unknown,tmp_path

def test_reset_clears_tasks_versions_receipts_and_managed_copies_preserving_settings_sources(data):
    s,original,raw,unknown,root=data;before=plan(s.db.path);assert before['counts']['projects']==2
    result=reset(s.db.path,root/'recovery');assert result['cleared_projects']==2 and result['deleted_files']>=3
    assert original.read_bytes()==b'original bytes' and raw.is_file() and unknown.read_bytes()==b'not managed'
    assert all(not Path(f['path']).exists() for f in before['files'])
    assert not s.production.rows() and not s.db.list_projects()
    with connection(s.db.path) as c:
        assert c.execute('SELECT count(*) FROM material_versions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM material_profiles').fetchone()[0]==len(before['config']['material_profiles'])
    assert (root/'recovery/workbox.db').is_file()
    restarted=DesktopService(s.db.path);p=DirectMaterialIntake(restarted).prepare([str(original)],include_known=True)
    assert restarted.confirm_direct_materials(p,p['entries'],p['batch'],'collect')['copied_count']==1

def test_reset_failure_restores_files_and_database(data):
    s,original,raw,unknown,root=data;before=plan(s.db.path);remove=ZipLease.remove_after_verified_commit;count=0
    def fail(lease):
        nonlocal count
        count+=1
        if count==2:raise OSError('simulated locked file')
        remove(lease)
    with patch.object(ZipLease,'remove_after_verified_commit',fail),pytest.raises(OSError):reset(s.db.path,root/'recovery')
    assert len(s.production.rows())==2
    assert all(Path(f['path']).is_file() and digest(f['path'])==f['sha256'] for f in before['files'])
    assert unknown.is_file() and original.is_file() and raw.is_file()

def test_reset_backup_cannot_be_inside_material_root(data):
    s,_,_,_,root=data
    with pytest.raises(ValueError,match='清理范围'):reset(s.db.path,root/'材料库/backup')
    assert len(s.production.rows())==2
