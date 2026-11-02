"""Verify the batch parent-folder UI and actual archive receipts in isolation."""
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch


def archives(root,count=2):
    import py7zr
    files=[]
    for i in range(count):
        p=root/(('弱电智能化工程项目管理系统-4-S' if i==0 else '电子元器件成品性能测试管理系统-2-S' if i==1 else f'批量材料{i}系统')+('.7z' if i%2==0 else '.zip'))
        if p.suffix=='.7z':
            with py7zr.SevenZipFile(p,'w') as z:z.writestr(b'word bytes','软件说明书.docx')
        else:
            with ZipFile(p,'w') as z:z.writestr('软件说明书.docx',b'word bytes')
        files.append(str(p))
    return files


def verify(root):
    from PySide6.QtWidgets import QApplication,QDialog,QLineEdit
    from desktop_service import DesktopService
    from zip_intake_ui import ZipIntakeDialog
    from theme import install_theme,set_theme
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance()
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True);install_theme(s)
    files=archives(root);d=ZipIntakeDialog(s,files);d.show()
    def settle():
        for _ in range(8):app.processEvents()
    settle();assert d.batch_raw and not d.raw_scroll.isVisible()
    common=d.batch_locations.root;chosen=root/'材料库/可运行压缩包'
    with patch('zip_intake_ui.QFileDialog.getExistingDirectory',return_value=str(chosen)) as browser:
        d.batch_locations.browse_button.click();assert browser.call_count==1
    assert len(d.findChildren(QLineEdit,'batchSaveRoot'))==1 and not chosen.exists()
    for item in d.raw_controls:
        out=item['save_location'];assert out.root is common and out.folder.text()==out.base.text()==item['name'].text()
    # Independent edits survive both a new parent directory and a title edit.
    first=d.raw_controls[0];out=first['save_location'];out.folder.setText('弱电运行包');out.base.setText('弱电运行副本');first['name'].setText('弱电运行软件')
    common.setText(str(root/'材料库/另一个父目录'));common.setText(str(chosen))
    assert out.folder.text()=='弱电运行包' and out.base.text()=='弱电运行副本'
    settle();d.grab().save(str(root/'批量收录-统一上级目录.png'))
    set_theme('dark',persist=False);settle();d.grab().save(str(root/'批量收录-深色.png'));set_theme('light',persist=False)
    d.confirm();assert d.result()==QDialog.Accepted,d.error.text();result=d.operation();assert result['copied_count']==2
    rows=s.production.rows();assert len(rows)==2
    expected={str(chosen/'弱电运行包/弱电运行副本.7z'),str(chosen/Path(files[1]).stem/Path(files[1]).name)}
    actual={z['path'] for row in rows for z in row['original_zips']};assert actual==expected
    for source,saved in zip(files,[chosen/'弱电运行包/弱电运行副本.7z',chosen/Path(files[1]).stem/Path(files[1]).name]):assert saved.read_bytes()==Path(source).read_bytes()
    assert Path(s.workspaces.settings()['work_root'])==root/'材料库'
    d.close();d.deleteLater();settle()
    restarted=DesktopService(s.db.path);assert {z['path'] for row in restarted.production.rows() for z in row['original_zips']}==expected
    return dict(batch_archives_share_one_parent_and_keep_software_subfolders=True,batch_archive_names_and_real_extensions_survive_parent_changes=True,batch_archive_receipts_and_paths_survive_restart=True)
