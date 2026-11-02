from pathlib import Path
from unittest.mock import patch
import pytest


def test_compact_rule_and_delete_flow(tmp_path):
    from PySide6.QtWidgets import QApplication
    from scripts.verify_rule_intake import verify
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    assert all(verify(tmp_path/'rule-flow').values())


def test_deletion_protects_shared_copy_and_outside_source(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    result=s.production.create_batch('共享保护',['软件甲','软件乙'],['报告.docx']);first,second=result['project_ids']
    source=tmp_path/'原始报告.docx';source.write_bytes(b'original report')
    s.production.receive(first,'报告.docx',source)
    with s.db.connect() as c:
        current=c.execute('SELECT * FROM material_versions WHERE project_id=?',(first,)).fetchone();shared=Path(current['path'])
        c.execute('INSERT INTO material_versions(project_id,kind,path,filename,sha256,size) VALUES (?,?,?,?,?,?)',(second,'报告.docx',str(shared),shared.name,current['sha256'],current['size']))
    result=s.production.delete_tasks([first],confirmed=True,delete_materials=True)
    assert shared.is_file() and source.is_file() and result['preserved_shared_files']>=1


def test_archive_rules_are_excluded_from_exact_material_requirements(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db')
    pid=s.classifications.save('运行包',['运行包.zip'],mode='collect',intake_mode='original',extensions=['.zip','.7z'])
    assert all(p['id']!=pid for p in s.classifications.profiles(True))
    assert any(p['id']==pid for p in s.classifications.profiles(True,include_archives=True))
    assert '运行包' not in s.classifications.packages()


def test_archive_rule_mismatch_creates_no_task_or_rule(tmp_path):
    from desktop_service import DesktopService
    from zip_intake import confirm_raw
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    archive=tmp_path/'错误规则系统.zip';archive.write_bytes(b'irrelevant')
    with pytest.raises(ValueError,match='格式不符合'):
        confirm_raw(s,[dict(path=str(archive),name=archive.stem,project_id=None,action='new')],archive_rule=dict(name='7z专用',extensions=['.7z']))
    assert not s.production.rows() and not any(p['name']=='7z专用' for p in s.classifications.profiles(include_archives=True))


def test_delete_invalid_batch_restores_all_staged_files(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    pid=s.production.create_batch('删除回滚',['软件'],['报告.docx'])['project_ids'][0]
    source=tmp_path/'报告.docx';source.write_bytes(b'stable bytes');s.production.receive(pid,'报告.docx',source)
    with s.db.connect() as c:paths=[Path(r[0]) for r in c.execute('SELECT path FROM material_versions WHERE project_id=?',(pid,))]
    with pytest.raises(ValueError):s.production.delete_tasks([pid,999999],confirmed=True,delete_materials=True)
    assert all(p.is_file() for p in paths) and not s.production.deleted_rows()


def test_bare_office_filename_reuses_unique_format_rule_without_creating_duplicates(tmp_path):
    from desktop_service import DesktopService
    from direct_material_intake import DirectMaterialIntake
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    s.classifications.save('单份表格',['统计表.xlsx'],mode='collect')
    file=tmp_path/'另一个软件系统.xlsx';file.write_bytes(b'unchanged spreadsheet')
    plan=DirectMaterialIntake(s).prepare([str(file)],include_known=True)
    assert plan['entries'][0]['package']=='单份表格' and plan['entries'][0]['kind']=='统计表.xlsx'
    # Two different same-format roles are ambiguous and must stay unassigned.
    s.classifications.save('预算材料',['预算表.xlsx'],mode='collect')
    plan=DirectMaterialIntake(s).prepare([str(file)],include_known=True)
    assert plan['entries'][0]['package']=='自定义'


def test_returning_to_original_mode_restores_archive_rule(tmp_path):
    import time
    from zipfile import ZipFile
    from PySide6.QtWidgets import QApplication
    from desktop_service import DesktopService
    from zip_intake_ui import ZipIntakeDialog
    app=QApplication.instance() or QApplication([])
    s=DesktopService(tmp_path/'data/workbox.db');s.workspaces.configure_materials(tmp_path/'材料库',complete_setup=True)
    path=tmp_path/'切换模式系统.zip'
    with ZipFile(path,'w') as z:z.writestr('软件说明书.docx',b'word')
    d=ZipIntakeDialog(s,[str(path)]);d.show();app.processEvents()
    d.archive_picker.select('单手册 Word');assert d.extract_radio.isChecked()
    d.raw_radio.setChecked(True);assert d.archive_draft()['intake_mode']=='original'
    d.reject();deadline=time.monotonic()+15
    while d.scan and d.scan.isRunning() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert not d.scan or not d.scan.isRunning();d.close();d.deleteLater();app.processEvents()
