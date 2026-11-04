from pathlib import Path
import time
import pytest


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    a=QApplication.instance() or QApplication([]);a.setQuitOnLastWindowClosed(False);return a


def setup(tmp_path,count=2):
    from desktop_service import DesktopService
    from scripts.verify_batch_archive import archives
    from zip_intake_ui import ZipIntakeDialog
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    files=archives(tmp_path,count);return s,files,ZipIntakeDialog(s,files)


def settle(app):
    for _ in range(8):app.processEvents()


def close(app,d):
    d.reject();end=time.monotonic()+10
    while d.scan and d.scan.isRunning() and time.monotonic()<end:app.processEvents();time.sleep(.01)
    assert not d.scan or not d.scan.isRunning();d.deleteLater();settle(app)


def test_one_parent_folder_real_mixed_archives_and_restart(app,tmp_path):
    from scripts.verify_batch_archive import verify
    assert all(verify(tmp_path/'batch').values())


def test_skip_restore_and_cancel_do_not_create_directories_or_rules(app,tmp_path):
    s,files,d=setup(tmp_path);before=s.classifications.profiles(include_archives=True)
    dest=tmp_path/'材料库/不应创建';d.batch_locations.root.setText(str(dest))
    for item in d.raw_controls:item['skip_button'].click();item['name'].setText('')
    d.confirm();assert d.operation is None and '全部跳过' in d.error.text()
    item=d.raw_controls[1];item['skip_button'].click();item['name'].setText('只收录第二份')
    assert len(d.raw_rows())==1
    close(app,d);assert not s.production.rows() and not dest.exists() and s.classifications.profiles(include_archives=True)==before
    assert all(Path(p).is_file() for p in files)


def test_same_subfolder_names_are_separated_without_overwriting(app,tmp_path):
    s,files,d=setup(tmp_path);dest=tmp_path/'材料库/共同父目录';d.batch_locations.root.setText(str(dest))
    for item in d.raw_controls:item['save_location'].folder.setText('相同文件夹');item['save_location'].base.setText('运行材料')
    d.confirm();assert d.operation,d.error.text();d.operation();close(app,d)
    paths=[Path(r['original_zips'][0]['path']) for r in s.production.rows()]
    assert len({p.parent for p in paths})==2 and all(p.parent.parent==dest for p in paths)
    assert {p.read_bytes() for p in paths}=={Path(p).read_bytes() for p in files}


def test_batch_overwrite_new_and_old_paths_keep_history(app,tmp_path):
    s,files,d=setup(tmp_path);oldroot=tmp_path/'材料库/旧上级';d.batch_locations.root.setText(str(oldroot));d.confirm();d.operation();close(app,d)
    oldpaths=[Path(r['original_zips'][0]['path']) for r in s.production.rows()];oldbytes={str(p):p.read_bytes() for p in oldpaths}
    from zip_intake_ui import ZipIntakeDialog
    d=ZipIntakeDialog(s,files);d.show();settle(app);assert Path(d.batch_locations.root.text())==oldroot and d.all_replace.isVisible()
    d.batch_locations.root.setText(str(tmp_path/'材料库/新上级'));d.all_replace.click()
    assert all(i['action'].currentData()=='overwrite' for i in d.raw_controls)
    i=d.raw_controls[1];i['action'].setCurrentIndex(i['action'].findData('new'))
    d.confirm();assert d.operation,d.error.text();d.operation();close(app,d)
    assert len(s.production.rows())==3 and all(Path(p).read_bytes()==b for p,b in oldbytes.items())
    with s.db.connect() as c:parents={Path(r[0]).parent for r in c.execute('SELECT directory FROM task_storage_preferences')}
    assert tmp_path/'材料库/新上级' in parents


def test_many_files_scroll_and_mode_switch_preserve_shared_parent(app,tmp_path):
    s,files,d=setup(tmp_path,12);d.show();settle(app)
    assert d.batch_locations.scroll.verticalScrollBar().maximum()>0 and d.save_button.isVisible()
    assert d.height()<=d.screen().availableGeometry().height()-30
    chosen=tmp_path/'材料库/批量上级';d.batch_locations.root.setText(str(chosen));d.extract_radio.setChecked(True)
    end=time.monotonic()+20
    while d.form is None and not d.error.text() and time.monotonic()<end:app.processEvents();time.sleep(.01)
    assert d.form is not None,d.error.text();assert Path(d.form.save_location.root.text())==chosen
    d.form.save_location.root.setText(str(chosen/'另一个目录'));d.raw_radio.setChecked(True)
    assert Path(d.batch_locations.root.text())==chosen/'另一个目录'
    close(app,d);assert not s.production.rows() and not chosen.exists()
