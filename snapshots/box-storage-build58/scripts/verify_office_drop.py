"""Send real Qt drag/drop events over child controls and exercise intake jobs."""
from pathlib import Path
import time

def verify(root):
    import py7zr
    from PySide6.QtCore import QMimeData,QUrl,QPoint,QPointF,Qt,QTimer
    from PySide6.QtGui import QDragEnterEvent,QDropEvent
    from PySide6.QtWidgets import QApplication,QPlainTextEdit
    from desktop_service import DesktopService
    from desktop_controller import FloatingController
    from compact_ui import CompactPanel
    from theme import install_theme
    from zip_intake_ui import ZipIntakeDialog,MaterialIntakeDialog
    from direct_material_ui import DirectMaterialDialog
    from file_formats import OFFICE
    from direct_material_intake import DirectMaterialIntake
    root=Path(root);root.mkdir(parents=True,exist_ok=True);app=QApplication.instance();app.setQuitOnLastWindowClosed(False)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True);install_theme(s)
    archive=root/'办公资料系统.7z'
    with py7zr.SevenZipFile(archive,'w') as z:
        z.writestr(b'word original','软件说明书.doc');z.writestr(b'excel original','统计表.xlsx');z.writestr(b'slide original','演示文稿.pptx')
    def settle(condition=lambda:True):
        end=time.monotonic()+20
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if condition():app.processEvents();return
        raise AssertionError('File drop or intake job did not finish')
    def drop(target,path):
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(path))]);enter=QDragEnterEvent(QPoint(8,8),Qt.CopyAction|Qt.MoveAction,mime,Qt.LeftButton,Qt.NoModifier)
        QApplication.sendEvent(target,enter);assert enter.isAccepted()
        event=QDropEvent(QPointF(8,8),Qt.CopyAction|Qt.MoveAction,mime,Qt.LeftButton,Qt.NoModifier)
        QApplication.sendEvent(target,event);assert event.isAccepted() and event.dropAction()==Qt.CopyAction
    panel=CompactPanel(s);panel.show();settle();seen=[];panel.intake.connect(seen.append)
    panel.tabs.setCurrentWidget(panel.conversation_page);settle()
    for target in [panel,panel.conversation_page.composer.viewport(),panel.conversation_page.scroll.viewport()]:
        count=len(seen);drop(target,archive);assert len(seen)==count+1 and seen[-1]['paths']==[str(archive)]
    assert not panel.conversation_page.composer.toPlainText()
    late_editor=QPlainTextEdit(panel.conversation_page);late_editor.show();settle()
    count=len(seen);drop(late_editor.viewport(),archive);assert len(seen)==count+1
    late_editor.deleteLater();settle()
    panel.tabs.setCurrentWidget(panel.workspace_page);settle();table=panel.production_page.task_workspace.table
    count=len(seen);drop(table.viewport(),archive);assert len(seen)==count+1
    panel.document_pane.shutdown();panel.close();panel.deleteLater();app.processEvents()
    controller=FloatingController(s,tray_enabled=False);timer=QTimer();timer.setInterval(20);accepted=[];failures=[]
    def choose():
        dialog=QApplication.activeModalWidget()
        if not isinstance(dialog,(ZipIntakeDialog,DirectMaterialDialog)):return
        try:
            if isinstance(dialog,ZipIntakeDialog) and not isinstance(dialog,MaterialIntakeDialog):
                assert dialog.raw_radio.isChecked() and dialog.extract_radio.isEnabled();dialog.grab().save(str(root/'7z-保存原包.png'))
            else:dialog.grab().save(str(root/'办公文件-收录.png'))
            if isinstance(dialog,MaterialIntakeDialog):
                from intake_onboarding import confirm_first_receipts
                confirm_first_receipts(dialog.form)
            accepted.append(type(dialog).__name__);dialog.confirm()
        except BaseException as e:failures.append(str(e));dialog.reject()
    timer.timeout.connect(choose)
    try:
        assert controller.start();controller.monitor_timer.stop();controller.panel.show();controller.panel.tabs.setCurrentWidget(controller.panel.workspace_page);settle();timer.start()
        drop(controller.panel.production_page.task_workspace.table.viewport(),archive)
        settle(lambda:not controller.busy and len(s.production.rows())==1)
        assert not failures and accepted==['ZipIntakeDialog'],(failures,accepted)
        row=s.production.rows()[0];pid=row['id'];assert row['collection_count']==0 and Path(row['original_zips'][0]['path']).read_bytes()==archive.read_bytes()
        timer.stop();d=ZipIntakeDialog(s,[str(archive)],controller.panel,start_mode='extract');d.show();settle(lambda:d.form is not None or bool(d.error.text()))
        assert d.form is not None,d.error.text()
        from intake_onboarding import confirm_first_receipts
        confirm_first_receipts(d.form)
        d.grab().save(str(root/'7z-解压分类.png'));d.confirm();assert d.operation,d.error.text()
        result=d.operation();assert result['copied_count']==3;d.close();d.deleteLater();settle()
        row=s.production.rows()[0];assert row['id']==pid and row['collection_count']==3
        office=root/'普通表格系统_统计表.xlsm';office.write_bytes(b'office unchanged');timer.start()
        controller.panel.tabs.setCurrentWidget(controller.panel.conversation_page);settle();drop(controller.panel.conversation_page.composer.viewport(),office)
        settle(lambda:not controller.busy and len(s.production.rows())==2);assert not failures,(failures,accepted)
        row=next(r for r in s.production.rows() if r['name']=='普通表格系统');assert row['collection_count']==1
        # Type rules keep a canonical logical slot; the saved bytes and suffix
        # remain those of the actual source file.
        assert any(m['id'] and (row['family_map'].get(m['kind'])=='sheet' or m['kind'].endswith('.xlsm')) for m in row['materials'])
        with s.db.connect() as c:saved=Path(c.execute('SELECT path FROM collected_materials WHERE project_id=?',(row['id'],)).fetchone()[0])
        assert saved.suffix=='.xlsm' and saved.read_bytes()==office.read_bytes()
        assert not controller.panel.conversation_page.composer.toPlainText()
        assert all(DirectMaterialIntake.role_and_name(Path('后缀验证系统_功能测试表'+ext),s.classifications.profiles(True),[])[0]=='后缀验证系统' for ext in OFFICE)
        # A further native decoder is exercised inside the frozen program too.
        extra=root/'编码验证.7z'
        with py7zr.SevenZipFile(extra,'w',filters=[{'id':py7zr.FILTER_PPMD}]) as z:z.writestr(b'ppmd codec','软件说明书.docx')
        plan=DirectMaterialIntake(s).prepare([str(extra)],require_material_zip=True,include_known=True);assert plan['entries']
    finally:
        timer.stop();controller.shutdown();controller.dispose_ui();app.processEvents()
    return dict(workbench_list_and_editor_file_drops_are_forwarded_once=True,
        seven_zip_raw_and_extract_use_same_intake_dialog=True,office_formats_are_classified_without_conversion=True,
        seven_zip_native_codecs_are_bundled=True)
