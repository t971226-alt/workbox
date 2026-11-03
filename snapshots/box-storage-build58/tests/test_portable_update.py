from pathlib import Path
from contextlib import closing
from threading import Event
from concurrent.futures import CancelledError
import json
import sqlite3
import pytest
import portable_update as update
from portable_release import RELEASE_KEY,APP_ID


def fixture_release(root,version,tag=''):
    root.mkdir(parents=True,exist_ok=True)
    (root/'WorkBox.exe').write_text(json.dumps(dict(version=version,tag=tag)))
    (root/'_internal').mkdir(exist_ok=True);(root/'_internal/runtime.dll').write_bytes(('runtime '+tag).encode())
    files={p.relative_to(root).as_posix():dict(size=p.stat().st_size,sha256=update.digest(p)) for p in root.rglob('*') if p.is_file() and p.relative_to(root).parts[0] in {'WorkBox.exe','_internal'}}
    update.write_json(root/update.MANIFEST,dict(schema=1,app_id=APP_ID,version='.'.join(map(str,version[:3])),build=version[3],files=files))


@pytest.fixture
def pair(tmp_path,monkeypatch):
    def identity(path):
        try:return tuple(json.loads(Path(path).read_text())['version'])
        except (OSError,ValueError,KeyError):return None
    monkeypatch.setattr(update,'pe_identity',identity)
    source=tmp_path/'下载的BOX';target=tmp_path/'日常BOX';index=tmp_path/'设置/portable-locations.json'
    fixture_release(source,RELEASE_KEY,'new');fixture_release(target,(1,0,0,0),'old')
    data=target/'data';data.mkdir();(data/'材料').mkdir();(data/'材料/原手册.docx').write_bytes(b'original user material')
    with closing(sqlite3.connect(data/'workbox.db')) as c:
        c.execute('CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT)');c.execute("INSERT INTO projects VALUES(1,'existing')")
        c.execute('CREATE TABLE workflow_settings(key TEXT PRIMARY KEY,value TEXT)')
        c.execute('INSERT INTO workflow_settings VALUES (?,?)',('work_root',json.dumps(str(data/'材料'))));c.commit()
    return source,target,index


def user_data(target):
    with closing(sqlite3.connect(target/'data/workbox.db')) as c:rows=list(c.iterdump())
    return rows,(target/'data/材料/原手册.docx').read_bytes()


def test_update_in_place_preserves_ledger_materials_and_takes_backup(pair):
    source,target,index=pair;before=user_data(target);path,token=update.make_job(source,target,index=index)
    report=update.apply_job(path,token)
    assert report['complete'] and update.read_release(target).version==RELEASE_KEY
    assert user_data(target)==before and Path(report['backup']).is_file()
    assert update.read_index(index)['preferred']==str(target) and not update.read_index(index)['pending']
    assert not path.parent.exists() and (source/'WorkBox.exe').is_file()


def test_previous_legacy_program_without_manifest_is_recognized(pair):
    source,target,index=pair;(target/update.MANIFEST).unlink()
    assert update.read_release(target).version==(1,0,0,0)
    path,token=update.make_job(source,target,index=index);assert update.apply_job(path,token)['complete']


def test_same_or_older_release_cannot_overwrite(pair):
    source,target,index=pair
    with pytest.raises(ValueError,match='较新'):update.make_job(target,source,index=index)
    with pytest.raises(ValueError,match='较新'):update.make_job(source,source,index=index)


@pytest.mark.parametrize('bad',['data/workbox.db','../workbox.db','_internal/../../data/workbox.db','C:/data/file','_internal/a:stream','_internal/./alias'])
def test_data_and_path_escape_in_manifest_are_rejected(pair,bad):
    source,target,index=pair;m=json.loads((source/update.MANIFEST).read_text());m['files'][bad]=dict(size=0,sha256='0'*64);update.write_json(source/update.MANIFEST,m)
    with pytest.raises(ValueError):update.make_job(source,target,index=index)
    assert update.read_release(target).version==(1,0,0,0)


def test_corrupt_source_is_rejected_before_any_replacement(pair):
    source,target,index=pair;(source/'_internal/runtime.dll').write_bytes(b'changed')
    with pytest.raises(ValueError,match='不完整'):update.make_job(source,target,index=index)
    assert update.read_release(target).version==(1,0,0,0)


def test_data_from_newer_version_is_protected(pair):
    source,target,index=pair;update.write_json(target/'data/BOX-Data-Version.json',dict(version='2.0.0',build=1))
    with pytest.raises(ValueError,match='降级'):update.make_job(source,target,index=index)


def test_failed_program_swap_restores_old_program_then_retry_succeeds(pair,monkeypatch):
    source,target,index=pair;before=user_data(target);old_exe=update.digest(target/'WorkBox.exe');path,token=update.make_job(source,target,index=index)
    replace=Path.replace
    def fail(self,dest):
        if self==path.parent/'payload/WorkBox.exe':raise OSError('isolated program replacement failure')
        return replace(self,dest)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'replace',fail)
        with pytest.raises(OSError):update.apply_job(path,token)
    assert update.digest(target/'WorkBox.exe')==old_exe and user_data(target)==before
    assert json.loads((path.parent/'state.json').read_text())['state']=='restored'
    assert update.apply_job(path,token)['complete'] and user_data(target)==before


def test_interrupted_swap_can_be_restored_without_the_download(pair):
    source,target,index=pair;path,token=update.make_job(source,target,index=index);job=path.parent
    (job/'rollback').mkdir();(target/'_internal').replace(job/'rollback/_internal')
    (target/'_internal').mkdir();(target/'_internal/partial.dll').write_bytes(b'partial')
    update.write_json(job/'state.json',dict(state='swapping',steps=[dict(name='_internal',old_exists=True)]))
    (source/'WorkBox.exe').unlink()
    assert update.apply_job(path,token,restore_only=True)['restored']
    assert (target/'_internal/runtime.dll').read_bytes()==b'runtime old' and not (target/'_internal/partial.dll').exists()
    assert not job.exists() and user_data(target)[1]==b'original user material'


def test_cancel_before_swap_keeps_original_and_retry_works(pair):
    source,target,index=pair;path,token=update.make_job(source,target,index=index);event=Event()
    def cancel(*args):event.set()
    with pytest.raises(CancelledError):update.apply_job(path,token,cancel_event=event,progress=cancel)
    assert update.read_release(target).version==(1,0,0,0)
    assert update.apply_job(path,token)['complete']


def test_running_box_blocks_update_without_ending_its_work(pair,monkeypatch):
    from PySide6.QtCore import QLockFile
    source,target,index=pair;path,token=update.make_job(source,target,index=index)
    lock=QLockFile(str(target/'data/workbox.db.session.lock'));assert lock.tryLock(0)
    monkeypatch.setattr(update.time,'sleep',lambda _:None)
    try:
        with pytest.raises(ValueError,match='完成当前工作'):update.apply_job(path,token)
        assert update.read_release(target).version==(1,0,0,0)
    finally:lock.unlock()


def test_unknown_runtime_additions_are_saved_instead_of_lost(pair):
    source,target,index=pair;(target/'_internal/用户备注.txt').write_text('keep',encoding='utf-8')
    path,token=update.make_job(source,target,index=index);update.apply_job(path,token)
    assert (target/'data/更新备份'/token/'保留的附加文件/_internal/用户备注.txt').read_text()=='keep'


def test_changed_original_after_confirmation_is_not_overwritten(pair):
    source,target,index=pair;path,token=update.make_job(source,target,index=index);(target/'WorkBox.exe').write_bytes(b'user changed file')
    with pytest.raises(ValueError,match='身份'):update.apply_job(path,token)
    assert (target/'WorkBox.exe').read_bytes()==b'user changed file'


def test_discovery_follows_saved_location_and_excludes_current(pair):
    source,target,index=pair;update.register(target,index)
    assert [r.root for r in update.discover(source,index=index,roots=[])]==[target]


def test_real_previous_box_windows_version_resource():
    path=Path(__file__).resolve().parents[1]/'dist/total/WorkBox/WorkBox.exe'
    if not path.exists():pytest.skip('No locally maintained previous portable executable')
    assert update.pe_identity(path) is not None


def test_completed_update_removes_temporary_old_program_on_next_start(pair,monkeypatch):
    source,target,index=pair;path,token=update.make_job(source,target,index=index)
    def locked(*args):raise PermissionError('isolated temporary file lock')
    with monkeypatch.context() as patch:
        patch.setattr(update,'_remove_job',locked)
        receipt=update.apply_job(path,token)
    assert receipt['complete'] and path.parent.exists() and update.read_index(index)['cleanups']==[str(path)]
    update.cleanup_completed(index)
    assert not path.parent.exists() and not update.read_index(index)['cleanups']
    assert update.read_release(target).version==RELEASE_KEY
