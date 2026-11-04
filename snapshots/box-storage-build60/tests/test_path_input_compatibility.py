from pathlib import Path
import os,hashlib
import pytest
from desktop_service import DesktopService
from workspace_manager import _path


def test_quoted_and_environment_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('BOX_TEST_ROOT', str(tmp_path))
    assert _path('  "%BOX_TEST_ROOT%/materials"  ') == tmp_path / 'materials'


def test_material_root_cannot_contain_database(tmp_path):
    service = DesktopService(tmp_path / 'app' / 'data' / 'workbox.db')
    with pytest.raises(ValueError, match='数据目录'):
        service.workspaces.configure_materials(tmp_path / 'app')
    assert not (tmp_path / 'app' / 'data' / 'materials').exists()


def test_material_root_cannot_be_drive_root(tmp_path):
    service = DesktopService(tmp_path / 'app' / 'data' / 'workbox.db')
    with pytest.raises(ValueError, match='磁盘根目录'):
        service.workspaces.configure_materials(Path(tmp_path.anchor))


@pytest.mark.skipif(os.name!='nt',reason='Windows extended filesystem paths')
def test_deep_copy_keeps_plain_records_hash_streams_and_no_overwrite(tmp_path):
    from native_files import _windows_path
    from workspace_manager import copy_verified
    from legacy_import import digest
    source=tmp_path/'source.docx';source.write_bytes(b'deep path content')
    Path(str(source)+':WPSSyncID').write_bytes(b'office metadata')
    target=tmp_path/('a'*72)/('b'*72)/('c'*72)/'manual.docx'
    assert len(str(target))>260
    result=copy_verified(source,target);io=Path(_windows_path(target))
    assert result['path']==str(target) and not result['path'].startswith('\\\\?\\')
    assert io.read_bytes()==source.read_bytes() and digest(target)==hashlib.sha256(source.read_bytes()).hexdigest()
    assert Path(str(io)+':WPSSyncID').read_bytes()==b'office metadata'
    with pytest.raises(FileExistsError):copy_verified(source,target)
    assert io.read_bytes()==b'deep path content' and not list(io.parent.glob('*.partial'))
