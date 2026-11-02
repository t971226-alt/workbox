"""Build verification in an isolated temporary workspace; no customer data is read."""
import json
import hashlib
import sys
import traceback
from pathlib import Path
from tempfile import mkdtemp


def verify(report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(mkdtemp(prefix='BOX-portable-check-', dir=report_path.parent))
    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'workspace': str(root), 'passed': False}
    if report['frozen']:
        with open(sys.executable, 'rb') as stream:
            report['executable_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    controller = None
    try:
        from PySide6.QtWidgets import QApplication, QDialog, QRadioButton
        from PySide6.QtCore import QTimer, QEventLoop, Qt
        from duplicate_intake_ui import DuplicateMaterialDialog
        from direct_material_ui import DirectMaterialDialog
        from intake_link_ui import RecognitionSummaryDialog,ClassificationRuleDialog,IntakeReceiptDialog
        from desktop_service import DesktopService
        from desktop_controller import FloatingController
        from onboarding_ui import FirstRunDialog
        from theme import install_theme, set_theme
        from requirement_intake import proposal
        app = QApplication.instance() or QApplication([])
        app.setStyle('Fusion')
        app.setQuitOnLastWindowClosed(False)
        # Exercise the actual frozen copy path with Office/file-library streams.
        from workspace_manager import copy_verified, _path
        metadata_source = root / '元数据兼容.docx'
        metadata_source.write_bytes(b'isolated office material')
        office_streams = {'WPSSyncID': b'wps-meta', 'user.library-file-id': b'library-id',
                          'Zone.Identifier': b'[ZoneTransfer]\r\nZoneId=3\r\n'}
        for stream_name, payload in office_streams.items():
            Path(str(metadata_source) + ':' + stream_name).write_bytes(payload)
        metadata_target = root / '收录副本' / metadata_source.name
        copy_verified(metadata_source, metadata_target)
        assert metadata_target.read_bytes() == metadata_source.read_bytes()
        for stream_name, payload in office_streams.items():
            assert Path(str(metadata_target) + ':' + stream_name).read_bytes() == payload
            assert Path(str(metadata_source) + ':' + stream_name).read_bytes() == payload
        assert _path('"' + str(metadata_target) + '"') == metadata_target
        report['office_stream_copy_and_quoted_paths'] = True
        from native_files import _windows_path
        deep_target=root/('a'*72)/('b'*72)/('c'*72)/'深层材料.docx'
        assert len(str(deep_target))>260
        deep_receipt=copy_verified(metadata_source,deep_target)
        assert deep_receipt['path']==str(deep_target)
        assert Path(_windows_path(deep_target)).read_bytes()==metadata_source.read_bytes()
        for stream_name,payload in office_streams.items():
            assert Path(_windows_path(deep_target)+':'+stream_name).read_bytes()==payload
        report['deep_material_copy_preserves_plain_paths_and_streams']=True
        from direct_material_intake import DirectMaterialIntake
        classification_service = DesktopService(root / 'five-material-check/data/workbox.db')
        classification_files = []
        for ending in ['.docx', '.pdf', '.txt', '_代码 (Python).docx', '_代码 (Python).pdf']:
            sample = root / ('五件材料自动识别系统' + ending)
            sample.write_bytes(('isolated ' + ending).encode())
            classification_files.append(str(sample))
        classification_plan = classification_service.receive_interactive(classification_files)['material_preview']
        assert DirectMaterialIntake.can_auto_receive(classification_plan)
        classified = classification_service.confirm_direct_materials(classification_plan, classification_plan['entries'], classification_plan['batch'], classification_plan['mode'])
        assert classified['copied_count'] == 5
        classification_row = classification_service.production.rows()[0]
        assert classification_row['package_name'] == '五件材料' and classification_row['collection_count'] == 5
        assert '五件材料' in Path(classification_row['collection_folder']).parts
        report['bare_title_five_material_classification'] = True
        service = DesktopService(root / 'data' / 'workbox.db')
        install_theme(service)
        set_theme('light', service)
        wizard = FirstRunDialog(service)
        assert wizard.work_root.text()==service.workspaces.settings()['work_root']
        assert not Path(wizard.work_root.text()).is_relative_to(service.db.path.parent)
        wizard.show()
        app.processEvents()
        if not wizard.grab().save(str(report_path.with_suffix('.setup.png'))):
            raise RuntimeError('无法保存首次设置预览')
        assert not wizard.findChildren(QRadioButton) and not hasattr(wizard, 'next_button')
        assert not hasattr(wizard, 'archive_root')
        wizard.finish_button.click()
        assert wizard.result() == QDialog.Accepted
        assert not service.workspaces.setup_required()
        assert service.workspaces.settings()['auto_deliver_zip'] is False
        assert not Path(service.workspaces.settings()['archive_root']).exists()
        report['single_material_directory'] = True
        report['setup_directory_only'] = True
        report['fresh_default_material_directory_saves_without_editing']=True
        controller = FloatingController(service, tray_enabled=False)
        if not controller.start():
            raise RuntimeError('无法启动桌面控制器')
        controller.monitor_timer.stop()
        panel = controller.panel
        panel.resize(870, 640)
        controller.show_panel()
        # The orb feedback shares the workspace's neutral controls in both themes.
        controller.bubble.show_message('松开：分类收录材料，保留原件',controller.orb)
        app.processEvents()
        controller.bubble.grab().save(str(report_path.with_suffix('.feedback-light.png')))
        assert controller.bubble.close_button.accessibleName()=='收起提示'
        assert not controller.bubble.cancel.isVisible()
        controller.bubble.close_button.click();assert not controller.bubble.isVisible()
        set_theme('dark',service)
        controller.bubble.show_message('正在分类收录材料，原文件会保留。',controller.orb,busy=True)
        app.processEvents();assert controller.bubble.cancel.isVisible() and not controller.bubble.timer.isActive()
        controller.bubble.grab().save(str(report_path.with_suffix('.feedback-dark.png')))
        controller.bubble.close_button.click();set_theme('light',service)
        report['polished_orb_feedback']=True
        page = panel.conversation_page
        app.processEvents()
        assert not hasattr(page, 'new_button')
        assert page.history.itemText(0) == '会话记录'
        assert panel.size_grip.isVisible() and panel.windowFlags() & Qt.WindowMaximizeButtonHint
        controller.orb.grab().save(str(report_path.with_suffix('.orb.png')))
        orb_menu = controller.orb.context_menu()
        assert [a.text() for a in orb_menu.actions() if not a.isSeparator()] == ['打开 BOX', '收录材料', '打开文件保存位置', '隐藏悬浮球', '退出 BOX']
        from unittest.mock import patch
        with patch('desktop_controller.QDesktopServices.openUrl',return_value=True) as opened:
            next(a for a in orb_menu.actions() if a.text()=='打开文件保存位置').trigger()
            assert Path(opened.call_args.args[0].toLocalFile())==Path(service.workspaces.settings()['work_root']).resolve()
        report['orb_opens_current_saved_material_root']=True
        orb_menu.popup(controller.orb.mapToGlobal(controller.orb.rect().bottomLeft()))
        app.processEvents()
        orb_menu.grab().save(str(report_path.with_suffix('.orb-menu.png')))
        orb_menu.close()
        orb_menu.deleteLater()
        report['simplified_orb_menu'] = True
        report['resizable_window'] = True
        panel.grab().save(str(report_path.with_suffix('.empty.png')))
        assert page.chat_body.isVisible() and panel.production_page.isVisible()
        assert panel.tabs.currentWidget() is panel.workspace_page
        ledger = panel.production_page
        assert ledger.task_workspace.isVisible() and not ledger.legacy_container.isVisible()
        panel.grab().save(str(report_path.with_suffix('.task-workspace-default.png')))
        # The previous detailed editor remains a real, accessible secondary view.
        ledger.show_legacy_workspace();app.processEvents()
        assert not ledger.state_filter.isVisible() and not ledger.statistics_date.isVisible()
        ledger.filter_menu.popup(ledger.filter_button.mapToGlobal(ledger.filter_button.rect().bottomLeft()))
        app.processEvents()
        ledger.filter_menu.grab().save(str(report_path.with_suffix('.filters.png')))
        ledger.filter_menu.close()
        panel.more_button.menu().popup(panel.more_button.mapToGlobal(panel.more_button.rect().bottomLeft()))
        app.processEvents()
        panel.more_button.menu().grab().save(str(report_path.with_suffix('.menu.png')))
        panel.more_button.menu().close()
        def calendar_preview():
            popup = QApplication.activePopupWidget()
            if popup:
                popup.grab().save(str(report_path.with_suffix('.calendar.png')))
                popup.close()
        QTimer.singleShot(0, calendar_preview)
        ledger.period_button.click()
        report['compact_workspace'] = [panel.width(), panel.height()]
        names = [f'验证软件{i}系统' for i in range(1, 7)]
        tid = page.store.add(page.ensure_conversation(), '\n'.join(names), [proposal(n, batch='六份Word验证') for n in names])
        result = page.store.create_tasks(tid)
        incoming = root / '成品'
        incoming.mkdir()
        for name in names:
            path = incoming / (name + '.docx')
            path.write_bytes(b'Portable verification fixture; not a customer document.')
            service.receive_path(path)
        extra_tid = page.store.add(page.ensure_conversation(), '后续验证系统', [proposal('后续验证系统', batch='之后加入的批次')])
        extra = page.store.create_tasks(extra_tid)
        page.refresh_turns()
        controller.refresh()
        for _ in range(6):
            app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.chat.png')))
        panel.open_production_batches(extra['batch_ids'])
        for _ in range(6):
            app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.table.png')))
        rows = service.production.rows(result['batch_ids'][0])
        assert len(rows) == 6 and all(r['collection_state'] == '已收齐' for r in rows)
        assert all(r['reviewed_count'] == r['submitted_count'] == 0 for r in rows)
        folders = {r['collection_folder'] for r in rows}
        assert len(folders) == 1 and len(list(Path(next(iter(folders))).glob('*.docx'))) == 6
        assert Path(next(iter(folders))).name == names[0] + '-6件'
        assert panel.production_page.table.rowCount() == 7 and panel.production_page.batch.currentData() is None
        assert panel.production_page.table.item(0, 4).text() == '已收齐'
        assert panel.production_page.table.item(6, 4).text() == '未收录'
        assert '新增 7' in panel.production_page.statistics_label.text()
        service.production.export_master(root / '软件材料总表.csv')
        def query(raw, count):
            page.composer.setPlainText(raw)
            page.send()
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(20)
            timer.timeout.connect(lambda: loop.quit() if not controller.busy else None)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            timer.start()
            timeout.start(10000)
            loop.exec()
            timer.stop()
            timeout.stop()
            assert not controller.busy
            turn = page.store.turns(page.current_id)[-1]
            assert turn['source_kind'] == 'query' and turn['state'] == 'committed', turn
            assert turn['result']['count'] == count, turn['result']
            assert panel.production_page.table.rowCount() == count
            assert panel.production_page.query_spec == turn['result']['spec']
            return turn
        query('今天收齐了多少？', 6)
        app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.query.png')))
        query('查找 验证软件1系统的提交情况', 1)
        query('哪些软件还没检查？', 6)
        query('显示全部', 7)
        panel.production_page.clear_query_button.click()
        assert panel.production_page.query_spec is None and panel.production_page.table.rowCount() == 7
        set_theme('dark', service)
        app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.dark.png')))
        panel.resize(780, 650)
        for _ in range(5):
            app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.narrow.png')))
        assert panel.width() == 780
        assert page.chat_body.isVisible() and panel.production_page.isVisible()
        panel.resize(870, 640)
        set_theme('light', service)
        # Exercise the actual frozen UI's new / overwrite / cancel decisions.
        old = rows[0]
        old_version = old['materials'][0]
        service.production.review(old_version['id'], True)
        service.production.submit([old_version['id']])
        first_path = incoming / (names[0] + '.docx')
        choices = []
        for action in ('new', 'overwrite', 'cancel'):
            first_path.write_bytes(Path(old_version['path']).read_bytes() if action=='new' else ('duplicate choice fixture: '+action).encode())
            if action == 'overwrite':
                set_theme('dark', service)
            elif action == 'cancel':
                set_theme('light', service)
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(10)
            timeout = QTimer()
            timeout.setSingleShot(True)
            errors, seen = [], []
            def advance():
                dialog = QApplication.activeModalWidget()
                if isinstance(dialog,DirectMaterialDialog):dialog.confirm_button.click()
                elif isinstance(dialog, DuplicateMaterialDialog) and not seen:
                    seen.append(action)
                    try:
                        if action == 'overwrite':
                            dialog.tasks.setCurrentIndex(dialog.tasks.findData(old['id']))
                        dialog.grab().save(str(report_path.with_suffix('.duplicate-' + action + '.png')))
                        {'new': dialog.new_button, 'overwrite': dialog.overwrite_button, 'cancel': dialog.cancel_button}[action].click()
                    except Exception:
                        errors.append(traceback.format_exc())
                        dialog.reject()
                elif not controller.busy:
                    loop.quit()
            def expired():
                errors.append('同名选择流程超时')
                modal = QApplication.activeModalWidget()
                if modal:
                    modal.reject()
                controller.cancel_jobs()
                loop.quit()
            timer.timeout.connect(advance)
            timeout.timeout.connect(expired)
            timer.start()
            timeout.start(20000)
            controller.receive({'kind': 'files', 'paths': [str(first_path)]})
            loop.exec()
            timer.stop()
            timeout.stop()
            if errors:
                raise RuntimeError('\n'.join(errors))
            assert seen == [action] and not controller.busy
            current = service.production.rows()
            assert len(current) == 8
            if action == 'new':
                assert current[0]['production_state'] == '已提交' and current[-1]['submitted_count'] == 0
            elif action == 'overwrite':
                assert current[0]['production_state'] == '待检查' and current[0]['materials'][0]['version_number'] == 2
            else:
                assert current[0]['materials'][0]['version_number'] == 2
                assert current[-1]['materials'][0]['version_number'] == 1
            choices.append(action)
        assert Path(old_version['path']).read_bytes().startswith(b'Portable verification fixture;')
        # Validate the new UI against an actual batch, without changing user data.
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox
        from classification_ui import ClassificationDialog
        ledger.show_all()
        bid=result['batch_ids'][0]
        ledger.grouped.select_batch(bid)
        ledger.grouped.open_tasks.add(old['id'])
        ledger.grouped.rebuild(True)
        for _ in range(6):app.processEvents()
        QTest.mouseClick(ledger.processing_switch,Qt.LeftButton)
        app.processEvents()
        assert all(r['processing_mode']=='collect' and r['production_state']=='已收纳' for r in service.production.rows(bid))
        assert not ledger.grouped.file_controls
        assert 'collect' in ledger.overview.counts and 'review' in ledger.overview.counts
        assert ledger.overview.counts['collect']['organized']==6
        panel.grab().save(str(report_path.with_suffix('.statistics-mixed.png')))
        ledger.stats_scope.setCurrentIndex(1);app.processEvents()
        assert set(ledger.overview.counts)=={'collect'} and '当前批次' in ledger.stats_scope_label.text()
        assert '检查' not in ledger.statistics_label.text() and '提交' not in ledger.statistics_label.text()
        panel.grab().save(str(report_path.with_suffix('.statistics-collect.png')))
        before_stat_count=len(ledger.rows)
        ledger.overview.buttons[('collect','organized')].click();app.processEvents()
        assert len(ledger.rows)==6 and ledger.overview.counts['collect']['total']==6
        ledger.clear_stat_button.click();app.processEvents();assert len(ledger.rows)==before_stat_count
        for _ in range(6):app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.storage.png')))
        QTest.mouseClick(ledger.processing_switch,Qt.LeftButton)
        app.processEvents()
        assert all(r['processing_mode']=='review' for r in service.production.rows(bid))
        assert ledger.grouped.file_controls
        assert set(ledger.overview.counts)=={'review'}
        panel.grab().save(str(report_path.with_suffix('.statistics-review.png')))
        ledger.stats_scope.setCurrentIndex(0);app.processEvents()
        report['mode_aware_statistics']=True;report['statistics_scope_filter']=True;report['native_statistics_text']=True
        for _ in range(6):app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.grouped.png')))
        page.composer.setPlainText('未发送草稿：软件名称仍然保留')
        QTest.mouseClick(page.collapse_button,Qt.LeftButton)
        for _ in range(4):app.processEvents()
        assert page.chat_body.isHidden() and page.expand_button.isVisible()
        assert service.get_setting('conversation_collapsed') is True
        panel.grab().save(str(report_path.with_suffix('.collapsed.png')))
        panel.resize(760,520)
        for _ in range(4):app.processEvents()
        for button in (ledger.category_button,):
            assert button.isVisible() and panel.rect().contains(button.mapTo(panel,button.rect().bottomRight()))
        panel.grab().save(str(report_path.with_suffix('.collapsed-narrow.png')))
        QTest.mouseClick(page.expand_button,Qt.LeftButton)
        for _ in range(4):app.processEvents()
        assert page.chat_body.isVisible() and page.composer.toPlainText().startswith('未发送草稿')
        assert not service.get_setting('conversation_collapsed')
        page.composer.clear();panel.resize(870,640)
        report.update(collapsible_conversation=True,inline_classification_controls=True)
        dialog=ClassificationDialog(service,panel);dialog.new();dialog.show()
        starter=next(r for r in dialog.records if r['name']=='单手册 Word')
        dialog.starter.setCurrentIndex(dialog.starter.findData(starter['id']))
        assert dialog.materials.rowCount()==1 and dialog.materials.isColumnHidden(1)
        dialog.name.setText('验收资料');dialog.materials.cellWidget(0,0).name_edit.setText('验收报告')
        dialog.mode.setCurrentIndex(dialog.mode.findData('collect'))
        app.processEvents();dialog.grab().save(str(report_path.with_suffix('.classification-new.png')))
        dialog.save_button.click()
        assert service.classifications.packages()['验收资料']==['验收报告.docx']
        dialog.advanced_button.click();dialog.materials.item(0,1).setText('验收单')
        confirmed=[]
        def confirm_rule():
            box=QApplication.activeModalWidget()
            if isinstance(box,QMessageBox):
                confirmed.append('新批次' in box.text() and '材料' in box.text())
                box.button(QMessageBox.Save).click()
        safety=QTimer(dialog);safety.setSingleShot(True)
        safety.timeout.connect(lambda: QApplication.activeModalWidget().reject() if isinstance(QApplication.activeModalWidget(),QMessageBox) else None)
        safety.start(5000);QTimer.singleShot(0,confirm_rule);dialog.save_button.click();safety.stop()
        assert confirmed==[True] and service.classifications.packages()['验收资料']==['验收报告.docx']
        app.processEvents();dialog.grab().save(str(report_path.with_suffix('.classifications.png')))
        dialog.close();dialog.deleteLater()
        report['guided_classification_creation']=True
        # Drop-only intake must preview before creating any name or copying material.
        from direct_material_ui import DirectMaterialDialog
        naming_service=DesktopService(root/'classification-check/data/workbox.db')
        naming_file=root/'分类核对系统功能测试表.docx';naming_file.write_bytes(b'isolated naming fixture')
        naming_plan=naming_service.receive_interactive([str(naming_file)])['material_preview']
        assert naming_plan['entries'][0]['name']=='分类核对系统'
        assert naming_plan['entries'][0]['kind']=='功能测试表.docx'
        assert naming_plan['entries'][0]['package']=='待确认'
        naming_dialog=DirectMaterialDialog(naming_service,naming_plan)
        naming_dialog.show();app.processEvents();naming_dialog.confirm_button.click()
        assert naming_dialog.result()!=QDialog.Accepted and '分类' in naming_dialog.error.text()
        naming_dialog.grab().save(str(report_path.with_suffix('.classification-choice.png')))
        choice=naming_dialog.class_controls[0]
        choice.setCurrentIndex(choice.findData('自定义'))
        editable=naming_dialog.kind_controls[0]
        assert editable.isEditable() and editable.lineEdit().text()==editable.currentText()
        assert editable.lineEdit().geometry().right()<=editable.width()-28
        naming_dialog.table.selectRow(0);app.processEvents()
        naming_dialog.grab().save(str(report_path.with_suffix('.editable-material-type.png')))
        report['editable_material_type_single_text']=True
        choice.setCurrentIndex(choice.findData('桌面14件'))
        assert naming_dialog.values()[0]['kind']=='功能测试表.docx'
        naming_dialog.confirm_button.click();assert naming_dialog.result()==QDialog.Accepted
        assert not naming_service.production.rows()
        naming_dialog.close();naming_dialog.deleteLater()
        report['classification_and_name_recognition']=True
        from intake_versions_ui import IntakeVersionsDialog
        grouped_root=root/'分批核对'
        for relative,body in [('甲批/批甲系统.docx',b'base'),('甲批/修订版/批甲系统.docx',b'revision'),('乙批/批乙系统.docx',b'other')]:
            fixture=grouped_root/relative;fixture.parent.mkdir(parents=True,exist_ok=True);fixture.write_bytes(body)
        grouped_plan=naming_service.receive_interactive([str(grouped_root)])['material_preview']
        grouped_dialog=DirectMaterialDialog(naming_service,grouped_plan);grouped_dialog.show();app.processEvents()
        assert grouped_plan['split_batches'] and len(grouped_dialog.values())==2
        version_errors=[]
        def pick_incoming_revision():
            modal=QApplication.activeModalWidget()
            try:
                assert isinstance(modal,IntakeVersionsDialog)
                modal.grab().save(str(report_path.with_suffix('.incoming-versions.png')))
                indices,combo=modal.choices[0]
                chosen=next(i for i in indices if '修订版' in grouped_plan['entries'][i]['path'])
                combo.setCurrentIndex(combo.findData(chosen));modal.apply_button.click()
            except BaseException as error:
                version_errors.append(error)
                if isinstance(modal,QDialog):modal.reject()
        QTimer.singleShot(0,pick_incoming_revision);grouped_dialog.versions_button.click()
        assert not version_errors and len(grouped_dialog.values())==2
        grouped_dialog.grab().save(str(report_path.with_suffix('.incoming-batches.png')))
        grouped_result=naming_service.confirm_direct_materials(grouped_plan,grouped_dialog.values(),grouped_dialog.batch.text(),'collect')
        assert len(grouped_result['batch_ids'])==2 and grouped_result['copied_count']==2
        assert len({r['collection_folder'] for r in naming_service.production.rows()})==2
        assert (grouped_root/'甲批/批甲系统.docx').read_bytes()==b'base'
        assert (grouped_root/'甲批/修订版/批甲系统.docx').read_bytes()==b'revision'
        grouped_dialog.close();grouped_dialog.deleteLater()
        report['folder_batches_and_incoming_versions']=True
        drop_names=[f'直接收纳软件{i}' for i in range(1,7)]
        drop_files=[]
        for name in drop_names:
            path=incoming/(name+'.docx');path.write_bytes(b'Drop-only isolated fixture');drop_files.append(str(path))
        before_count=len(service.production.rows());direct_seen=[];direct_errors=[]
        loop=QEventLoop();direct_timer=QTimer();direct_timer.setInterval(10)
        service.set_setting('default_processing_mode','collect')
        def confirm_direct():
            modal=QApplication.activeModalWidget()
            if isinstance(modal,RecognitionSummaryDialog):
                direct_errors.append('首次材料不应弹出识别摘要');modal.reject();loop.quit()
            elif isinstance(modal,DirectMaterialDialog):
                direct_errors.append('明确材料不应弹出确认窗口');modal.reject();loop.quit()
            elif not controller.busy:
                direct_seen.append(True);loop.quit()
        direct_timer.timeout.connect(confirm_direct)
        direct_timeout=QTimer();direct_timeout.setSingleShot(True);direct_timeout.timeout.connect(loop.quit)
        direct_timer.start();direct_timeout.start(20000)
        controller.receive({'kind':'files','paths':drop_files});loop.exec();direct_timer.stop();direct_timeout.stop()
        if direct_errors:raise RuntimeError('\n'.join(direct_errors))
        assert direct_seen and not controller.busy
        added=[r for r in service.production.rows() if r['name'] in drop_names]
        assert len(added)==6 and all(r['production_state']=='已收纳' and r['reviewed_count']==r['submitted_count']==0 for r in added)
        assert len({r['collection_folder'] for r in added})==1
        assert Path(added[0]['collection_folder']).name==drop_names[0]+'-6件'
        for _ in range(4):app.processEvents()
        panel.grab().save(str(report_path.with_suffix('.direct-master.png')))
        # Rejection must leave the original ledger unchanged.
        cancel_file=incoming/'说明书.docx';cancel_file.write_bytes(b'cancel fixture')
        cancel_seen=[];loop=QEventLoop();cancel_timer=QTimer();cancel_timer.setInterval(10)
        def cancel_direct():
            modal=QApplication.activeModalWidget()
            if isinstance(modal,DirectMaterialDialog) and not cancel_seen:cancel_seen.append(True);modal.cancel_button.click()
            elif cancel_seen and not controller.busy:loop.quit()
        cancel_timer.timeout.connect(cancel_direct);cancel_timer.start();direct_timeout.timeout.disconnect();direct_timeout.timeout.connect(loop.quit);direct_timeout.start(20000)
        controller.receive({'kind':'files','paths':[str(cancel_file)]});loop.exec();cancel_timer.stop();direct_timeout.stop()
        assert cancel_seen and not controller.busy and len(service.production.rows())==before_count+6 and cancel_file.exists()
        report.update(automatic_clear_material_drop=True,direct_drop_six_words=True,ambiguous_drop_cancel=True,identical_material_requires_choice=True)
        # ZIP choice is shown before any extraction; verify all three real UI paths.
        from zipfile import ZipFile
        from PySide6.QtWidgets import QMessageBox
        for zip_action in ('自动解压并分类','直接收录原包','取消'):
            title='ZIP验证'+{'自动解压并分类':'解压','直接收录原包':'原包','取消':'取消'}[zip_action]+'系统'
            zip_path=incoming/(title+'.zip')
            with ZipFile(zip_path,'w') as archive:
                for suffix in ('.docx','.pdf','.txt','_代码 (Python).docx','_代码 (Python).pdf'):
                    archive.writestr(title+'/'+title+suffix,b'isolated ZIP fixture')
            zip_before=zip_path.read_bytes();prior_ids={r['id'] for r in service.list_projects()}
            zip_seen=[];zip_errors=[];loop=QEventLoop();zip_timer=QTimer();zip_timer.setInterval(10)
            def choose_zip():
                modal=QApplication.activeModalWidget()
                if isinstance(modal,QMessageBox) and modal.windowTitle()=='ZIP 收录方式' and not zip_seen:
                    if {r['id'] for r in service.list_projects()}!=prior_ids:zip_errors.append('ZIP choice wrote records before selection')
                    zip_seen.append(True)
                    if zip_action=='自动解压并分类':modal.grab().save(str(report_path.with_suffix('.zip-choice.png')))
                    next(b for b in modal.buttons() if b.text()==zip_action).click()
                elif isinstance(modal,RecognitionSummaryDialog):modal.confirm_button.click()
                elif zip_seen and not controller.busy:loop.quit()
            zip_timer.timeout.connect(choose_zip);zip_timer.start()
            zip_timeout=QTimer();zip_timeout.setSingleShot(True);zip_timeout.timeout.connect(loop.quit);zip_timeout.start(20000)
            controller.receive({'kind':'files','paths':[str(zip_path)]});loop.exec();zip_timer.stop();zip_timeout.stop()
            assert zip_seen and not zip_errors and not controller.busy and zip_path.read_bytes()==zip_before
            zip_tasks=[r for r in service.production.rows() if r['name']==title]
            if zip_action=='自动解压并分类':
                assert len(zip_tasks)==1 and zip_tasks[0]['package_name']=='五件材料' and zip_tasks[0]['collection_count']==5
            else:
                assert len(zip_tasks)==(0 if zip_action=='取消' else 1)
                if zip_tasks:assert zip_tasks[0]['production_state']=='原包待核对' and zip_tasks[0]['collection_count']==0
                assert len({r['id'] for r in service.list_projects()}-prior_ids)==(0 if zip_action=='取消' else 1)
        report.update(zip_choice_before_processing=True,zip_extract_classification=True,zip_original_intake=True,zip_cancel_preserves_data=True)
        # Multi-software batches expose their hierarchy without changing fonts.
        hierarchy=panel.production_page.grouped
        multi=next(r for r in panel.production_page.rows if sum(x['batch_id']==r['batch_id'] for x in panel.production_page.rows)>1)
        heading=hierarchy.batch_buttons[multi['batch_id']]
        assert heading.text().startswith('批次 · ')
        assert heading.drawing_font().pixelSize()==13
        task=hierarchy.task_buttons[multi['id']]
        assert task.parentWidget().layout().contentsMargins().left()==31
        assert hierarchy.task_checks[multi['id']].parentWidget() is task.parentWidget()
        if multi['id'] in hierarchy.open_tasks:
            task.click();app.processEvents();task=hierarchy.task_buttons[multi['id']]
        assert task.drawing_font().pixelSize()==11 and task.symbol=='expand'
        task.click();app.processEvents()
        assert multi['id'] in hierarchy.open_tasks and hierarchy.task_buttons[multi['id']].symbol=='chevron'
        hierarchy.grab().save(str(report_path.with_suffix('.hierarchy.png')))
        hierarchy.task_buttons[multi['id']].click();app.processEvents()
        report['clear_batch_software_material_hierarchy']=True
        # Clipboard names choose their rule before creating a review card or task.
        chat=panel.conversation_page;prior_tasks=len(service.production.rows());raw_names='1. 规则粘贴验证甲系统\n2. 规则粘贴验证乙系统'
        previous_clipboard=QApplication.clipboard().text();QApplication.clipboard().setText(raw_names)
        paste_seen=[];loop=QEventLoop();paste_timer=QTimer();paste_timer.setInterval(10)
        def select_paste_rule():
            modal=QApplication.activeModalWidget()
            if isinstance(modal,ClassificationRuleDialog):
                assert len(service.production.rows())==prior_tasks
                paste_seen.append(True);modal.rules.setCurrentIndex(modal.rules.findData('五件材料'))
                modal.grab().save(str(report_path.with_suffix('.paste-rule.png')));modal.confirm_button.click()
            elif paste_seen:loop.quit()
        paste_timer.timeout.connect(select_paste_rule);paste_timer.start()
        paste_timeout=QTimer();paste_timeout.setSingleShot(True);paste_timeout.timeout.connect(loop.quit);paste_timeout.start(20000)
        chat.composer.paste();loop.exec();paste_timer.stop();paste_timeout.stop();QApplication.clipboard().setText(previous_clipboard)
        assert paste_seen and len(service.production.rows())==prior_tasks
        pasted=chat.store.turns(chat.current_id)[-1]
        assert len(pasted['rows'])==2 and all(r['package']=='五件材料' and len(r['kinds'])==5 for r in pasted['rows'])
        with service.db.connect() as c:last_import=json.loads(c.execute('SELECT result_json FROM production_imports ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()[0])
        panel.production_page.set_intake_result(last_import)
        assert last_import.get('import_token')
        receipt_dialog=IntakeReceiptDialog(panel.production_page,last_import)
        receipt_dialog.show();app.processEvents()
        assert receipt_dialog.table.rowCount()==5 and all(Path(f['saved_path']).is_file() for f in receipt_dialog.files)
        receipt_dialog.grab().save(str(report_path.with_suffix('.intake-receipt.png')));receipt_dialog.accept();receipt_dialog.deleteLater()
        panel.production_page.focus_intake_projects([added[0]['id']]);app.processEvents()
        assert added[0]['id'] in panel.production_page.grouped.highlighted_tasks
        report.update(paste_requires_classification_rule=True,intake_receipt_and_row_focus=True)
        # Exercise the shipped old-data wizard on isolated fixtures only.
        from legacy_import_ui import LegacyImportDialog,LegacyTransferDialog
        old=DesktopService(root/'旧BOX'/'data'/'workbox.db')
        legacy_file=incoming/'历史软件.docx';legacy_file.write_bytes(b'legacy import fixture')
        old.receive_file(legacy_file)
        before_legacy=len(service.production.rows())
        choice=LegacyTransferDialog(panel);choice.show();app.processEvents()
        assert choice.copy_button.isDefault()
        choice.grab().save(str(report_path.with_suffix('.legacy-choice.png')))
        choice.move_button.click();assert choice.transfer_mode=='move'
        migration=LegacyImportDialog(service,panel,auto_scan=False,transfer_mode=choice.transfer_mode);migration.show();choice.deleteLater()
        def wait_migration():
            loop=QEventLoop();poll=QTimer();poll.setInterval(20)
            poll.timeout.connect(lambda:loop.quit() if migration.worker is None else None)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
            poll.start();timeout.start(60000);loop.exec();poll.stop();timeout.stop()
            assert migration.worker is None,migration.status.text()
        migration.prepare(str(old.db.path));wait_migration()
        assert migration.plan and len(service.production.rows())==before_legacy,migration.status.text()
        migration.grab().save(str(report_path.with_suffix('.legacy-preview.png')))
        migration.confirm_button.click();wait_migration()
        assert migration.report and migration.report['complete'] and migration.report['cleanup_complete'],migration.status.text()
        assert all(not Path(r['source']).exists() for r in migration.report['transfer_items'])
        assert old.db.path.exists() and legacy_file.exists()
        report['legacy_move_choice']=True;report['legacy_material_migration']=True
        assert len(service.production.rows())==before_legacy+1
        restored=next(r for r in service.production.rows() if r['name']=='历史软件')
        assert restored['collection_state']=='已收齐' and restored['reviewed_count']==restored['submitted_count']==0
        assert Path(migration.report['backup']).is_file()
        assert migration.importer.import_plan(migration.plan)==migration.report
        migration.grab().save(str(report_path.with_suffix('.legacy-report.png')))
        migration.close();migration.shutdown();migration.deleteLater()
        report['legacy_import_wizard']=True;report['legacy_import_idempotent']=True
        from portable_update_ui import ChoiceDialog
        update_prompt=ChoiceDialog('发现已有 BOX，是否更新？','原位置：日常使用的 BOX\n确认后先备份总表和设置，再更新原位置，继续使用同一份总表。',[('update','备份、更新并打开原 BOX'),('open','暂不更新，打开原 BOX')],panel)
        update_prompt.show();app.processEvents();update_prompt.grab().save(str(report_path.with_suffix('.update-prompt.png')))
        assert update_prompt.buttons['update'].isDefault();update_prompt.reject();update_prompt.deleteLater()
        if report['frozen']:
            import shutil,subprocess,sqlite3
            from contextlib import closing
            from portable_update import pe_identity,read_release,make_job
            from portable_release import RELEASE_KEY
            current_build=Path(sys.executable).parent
            previous=report_path.parent.parent/'dist/total/WorkBox/WorkBox.exe'
            if not previous.exists() or not pe_identity(previous) or pe_identity(previous)>=RELEASE_KEY:raise RuntimeError('需要较旧的本地 BOX 程序作为隔离更新验证输入。')
            target=root/'隔离更新目标';target.mkdir();shutil.copy2(previous,target/'WorkBox.exe')
            (target/'_internal').mkdir();(target/'_internal/原附加文件.txt').write_text('保留',encoding='utf-8')
            (target/'data/材料').mkdir(parents=True);material=target/'data/材料/原材料.docx';material.write_bytes(b'preserve user material')
            with closing(sqlite3.connect(target/'data/workbox.db')) as c:
                c.execute('CREATE TABLE records(id INTEGER PRIMARY KEY,name TEXT)');c.execute("INSERT INTO records VALUES(1,'original record')");c.commit()
            plan_path,token=make_job(current_build,target,index=root/'更新位置登记.json')
            child=subprocess.Popen([sys.executable,'--apply-update',str(plan_path),'--update-token',token,'--update-no-launch'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            loop=QEventLoop();poll=QTimer();poll.setInterval(50);poll.timeout.connect(lambda:loop.quit() if child.poll() is not None else None)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);poll.start();timeout.start(90000);loop.exec();poll.stop();timeout.stop()
            if child.poll() is None:child.terminate();child.wait(5);raise RuntimeError('隔离更新助手超时，未完成本次验收。')
            assert child.returncode==0,child.returncode
            assert read_release(target,full=True).version==RELEASE_KEY
            assert material.read_bytes()==b'preserve user material'
            with closing(sqlite3.connect(target/'data/workbox.db')) as c:assert c.execute('SELECT name FROM records').fetchone()[0]=='original record'
            assert (target/'data/更新备份'/token/'更新前.db').is_file()
            assert (target/'data/更新备份'/token/'保留的附加文件/_internal/原附加文件.txt').read_text('utf-8')=='保留'
            assert not plan_path.parent.exists()
            report['portable_version_gate']=True;report['portable_update_helper']=True;report['portable_update_preserves_data']=True
        assert panel.startup_action.isCheckable()
        import startup
        if report['frozen']:assert startup.command(sys.executable).endswith('" --startup')
        feedback=service.production.create_batch('提交反馈验证',['提交反馈验证系统'],['软件说明书.docx'])
        feedback_pid=feedback['project_ids'][0];service.production.set_batch_mode(feedback['batch_id'],'review')
        feedback_file=incoming/'提交反馈验证系统.docx';feedback_file.write_bytes(b'isolated submission feedback')
        service.production.receive(feedback_pid,'软件说明书.docx',feedback_file);controller.refresh()
        ledger=panel.production_page;ledger.show_all();ledger.search.setText('提交反馈验证系统');app.processEvents()
        assert len(ledger.rows)==1
        ledger.table.item(0,0).setCheckState(Qt.Checked);feedback_errors=[]
        def go_review():
            box=QApplication.activeModalWidget()
            try:
                assert isinstance(box,QMessageBox) and '尚未检查通过' in box.text()
                box.grab().save(str(report_path.with_suffix('.submission-blocked.png')))
                next(b for b in box.buttons() if b.text()=='去检查').click()
            except BaseException as error:
                feedback_errors.append(error)
                if isinstance(box,QDialog):box.reject()
        QTimer.singleShot(0,go_review);ledger.submit_button.click();app.processEvents()
        assert not feedback_errors and feedback_pid in ledger.grouped.open_tasks
        assert service.production.rows(feedback['batch_id'])[0]['submitted_count']==0
        def wait_submission_feedback():
            loop=QEventLoop();poll=QTimer();poll.setInterval(10);poll.timeout.connect(lambda:loop.quit() if not controller.busy else None)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);poll.start();timeout.start(10000);loop.exec();poll.stop();timeout.stop();assert not controller.busy
        ledger.grouped.file_controls[(feedback_pid,'软件说明书.docx','review')].click();wait_submission_feedback()
        ledger.table.item(0,0).setCheckState(Qt.Checked)
        def confirm_submission():
            box=QApplication.activeModalWidget()
            try:
                assert isinstance(box,QMessageBox);box.button(QMessageBox.Yes).click()
            except BaseException as error:
                feedback_errors.append(error)
                if isinstance(box,QDialog):box.reject()
        QTimer.singleShot(0,confirm_submission);ledger.submit_button.click();wait_submission_feedback();app.processEvents()
        assert not feedback_errors and ledger.rows[0]['production_state']=='已提交'
        assert '已记录 1 份材料的提交' in ledger.error.text() and ledger.error.isVisible()
        assert ledger.grouped.task_buttons[feedback_pid].drawing_font().pixelSize()==11
        assert ledger.grouped.task_buttons[feedback_pid].parentWidget().height()==28
        assert ledger.grouped.batch_buttons[feedback['batch_id']] is ledger.grouped.task_buttons[feedback_pid]
        assert abs(ledger.search.y()-ledger.stats_scope.y())<5
        report['compact_readable_ledger']=True
        ledger.build_category_menu()
        assert ledger.category_button.isVisible() and ledger.remove_type_button.isHidden()
        assert len([a for a in ledger.category_menu.actions() if a.isCheckable()])==ledger.type_tabs.count()
        assert panel.more_button.height()==22 and panel.layout().contentsMargins().top()==3
        from workspace_chrome import icon_renderer
        assert icon_renderer('categories','#292929').isValid()
        ledger.category_menu.popup(ledger.category_button.mapToGlobal(ledger.category_button.rect().bottomLeft()));app.processEvents()
        ledger.category_menu.grab().save(str(report_path.with_suffix('.category-menu.png')));ledger.category_menu.hide()
        report['unified_category_toolbar']=True
        import saved_locations
        from PySide6.QtWidgets import QPushButton
        from production_ui import MaterialDialog
        saved_open=saved_locations.open_location;opened_locations=[]
        try:
            saved_locations.open_location=lambda path:opened_locations.append(Path(path))
            current=ledger.rows[0];task_button=ledger.grouped.task_buttons[current['id']]
            menu=next(b.menu() for b in task_button.parentWidget().findChildren(QPushButton) if b.menu())
            next(a for a in menu.actions() if a.text()=='打开保存目录').trigger()
            assert opened_locations[-1]==Path(current['collection_folder']).resolve()
            material=current['materials'][0]
            control=next(b for b in ledger.grouped.body.findChildren(QPushButton) if b.accessibleName()=='材料详情与版本：'+material['kind'] and b.isVisible())
            control.menu().actions()[0].trigger()
            assert opened_locations[-1].parent==Path(current['collection_folder']).resolve()
            assert opened_locations[-1]!=Path(material['path'])
            folder_dialog=MaterialDialog(ledger,current,material);folder_dialog.show();app.processEvents();folder_dialog.folder_button.click()
            assert opened_locations[-1].is_file()
            folder_dialog.grab().save(str(report_path.with_suffix('.saved-folder.png')));folder_dialog.close();folder_dialog.deleteLater()
            report['saved_folder_navigation']=True
        finally:saved_locations.open_location=saved_open
        from unified_storage import unify_storage,needs_unification,_rewrite_references
        storage=DesktopService(root/'storage-migration/data/workbox.db');storage_root=root/'统一材料目录'
        storage.workspaces.configure_materials(storage_root,complete_setup=True)
        batch=storage.production.create_batch('整理验证',['目录整理甲系统','目录整理乙系统'],['软件说明书.docx'])
        for i,pid in enumerate(batch['project_ids']):
            source=root/f'整理来源{i}.docx';source.write_bytes(f'preserved material {i}'.encode());storage.production.receive(pid,'软件说明书.docx',source)
        record=storage.production.rows()[0];version=record['materials'][0]['id'];storage.production.review(version,True);storage.production.submit([version])
        cache=Path(storage.workspaces.get_workspace(record['id'])['group_path']);old_work=storage_root/'原工作目录';cache.replace(old_work)
        current=storage_root/'单Word';old_export=storage_root/'分类收录/单Word';old_export.parent.mkdir();current.replace(old_export)
        with storage.db.connect() as c:_rewrite_references(c,[dict(source=str(cache),target=str(old_work)),dict(source=str(current),target=str(old_export))])
        consolidated=unify_storage(storage)
        assert consolidated['storage_unified'] and (Path(consolidated['storage_backup'])/'整理前.db').is_file()
        assert not needs_unification(storage) and {p.name for p in storage_root.iterdir()}=={'单Word'}
        assert storage.production.rows()[0]['submitted_count']==1 and storage.production.rows()[0]['materials'][0]['id']==version
        assert all(Path(r['collection_folder']).is_relative_to(storage_root/'单Word') for r in storage.production.rows())
        report['unified_material_directory']=True;report['storage_migration_backup_and_history']=True
        from compact_ui import CompactPanel
        bulk_panel=CompactPanel(storage);bulk_panel.resize(870,640);bulk_panel.show();app.processEvents()
        try:
            bulk=bulk_panel.production_page;group=bulk.grouped;bid=batch['batch_id']
            before_selection=storage.production.rows()
            group.task_checks[batch['project_ids'][0]].click()
            assert len(bulk.checked())==1 and group.all_check.property('mixed') and group.batch_checks[bid].property('mixed')
            group.batch_checks[bid].click();assert len(bulk.checked())==2
            group.batch_checks[bid].click();assert not bulk.checked()
            group.batch_buttons[bid].click();app.processEvents()
            group.all_check.click();assert len(bulk.checked())==2
            bulk.clear_button.click();assert not bulk.checked()
            bulk.search.setText('目录整理甲');app.processEvents()
            group.all_check.click();assert len(bulk.checked())==1 and bulk.checked()[0]['id']==batch['project_ids'][0]
            assert storage.production.rows()==before_selection
            bulk.search.clear();app.processEvents();group.batch_buttons[bid].click();app.processEvents()
            bulk_panel.grab().save(str(report_path.with_suffix('.multi-select.png')))
            report['scoped_bulk_selection']=True
        finally:
            bulk_panel.conversation_page.shutdown();bulk_panel.document_pane.shutdown();bulk_panel.close();bulk_panel.deleteLater()
        deletion_errors=[];deletion_pid=ledger.rows[0]['id'];deletion_version=ledger.rows[0]['materials'][0]['id']
        def choose_deletion(label):
            def click():
                box=QApplication.activeModalWidget()
                try:
                    assert isinstance(box,QMessageBox)
                    if label=='删除任务':box.grab().save(str(report_path.with_suffix('.delete-confirm.png')))
                    next(b for b in box.buttons() if b.text()==label).click()
                except BaseException as e:
                    deletion_errors.append(str(e))
                    if isinstance(box,QDialog):box.reject()
            QTimer.singleShot(0,click)
        ledger.grouped.task_checks[deletion_pid].click();choose_deletion('取消');ledger.delete_tasks_button.click()
        assert not deletion_errors and len(ledger.rows)==1
        choose_deletion('删除任务');ledger.delete_tasks_button.click();wait_submission_feedback();app.processEvents()
        assert not deletion_errors and not ledger.rows and ledger.empty_label.isVisible()
        assert deletion_pid not in {r['id'] for r in service.production.rows()}
        service.production.export_master(root/'删除后总表.csv')
        import csv
        with (root/'删除后总表.csv').open(encoding='utf-8-sig') as stream:
            assert str(deletion_pid) not in {r['任务编号'] for r in csv.DictReader(stream)}
        def restore_deleted():
            from PySide6.QtWidgets import QListWidget,QPushButton
            dialog=QApplication.activeModalWidget()
            try:
                assert isinstance(dialog,QDialog) and dialog.windowTitle()=='已删除任务'
                items=dialog.findChild(QListWidget)
                item=next(items.item(i) for i in range(items.count()) if items.item(i).data(Qt.UserRole)==deletion_pid)
                item.setCheckState(Qt.Checked);dialog.grab().save(str(report_path.with_suffix('.deleted-tasks.png')))
                dialog.findChild(QPushButton,'restoreDeletedTasks').click()
            except BaseException as e:
                deletion_errors.append(str(e))
                if isinstance(dialog,QDialog):dialog.reject()
        QTimer.singleShot(0,restore_deleted);ledger.deleted_tasks_action.trigger();wait_submission_feedback();app.processEvents()
        assert not deletion_errors and ledger.rows[0]['id']==deletion_pid
        assert ledger.rows[0]['materials'][0]['id']==deletion_version and ledger.rows[0]['submitted_count']==1
        report['task_deletion_and_restore']=True;report['deleted_tasks_removed_from_master_export']=True
        controller.bubble.hide();panel.grab().save(str(report_path.with_suffix('.submission-success.png')))
        report['submission_feedback_and_compact_text']=True
        assert ledger.layout().contentsMargins().top()==3
        assert ledger.statistics_label.mapTo(ledger,ledger.statistics_label.rect().topLeft()).y()>ledger.grouped.y()
        report['bottom_statistics_layout']=True
        if report['frozen']:
            import subprocess,time
            panel.hide()
            wake=subprocess.Popen([sys.executable,'--data-dir',str(root/'data'),'--panel'],cwd=Path(sys.executable).parent)
            loop=QEventLoop();poll=QTimer();poll.setInterval(20)
            poll.timeout.connect(lambda:loop.quit() if wake.poll() is not None and panel.isVisible() else None)
            timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
            poll.start();timeout.start(10000);loop.exec();poll.stop();timeout.stop()
            assert wake.poll()==0 and panel.isVisible(),'重复启动未唤起主窗口'
            report['frozen_second_launch_restores_panel']=True
        report.update(grouped_materials=True,storage_mode=True,editable_classifications=True,startup_setting=True)
        from scripts.verify_intake_integrity import verify as verify_integrity
        report.update(verify_integrity(root/'intake-integrity'))
        from scripts.verify_intake_recovery import verify as verify_recovery
        report.update(verify_recovery(root/'intake-recovery'))
        from scripts.verify_intake_transactions import verify as verify_transactions
        report.update(verify_transactions(root/'intake-transactions'))
        from scripts.verify_bulk_overwrite import verify as verify_bulk
        report.update(verify_bulk(root/'bulk-overwrite'))
        from scripts.verify_master_ledger import verify as verify_master
        report.update(verify_master(root/'master-ledger',report_path.with_suffix('.master-ledger.png')))
        from scripts.verify_task_workspace import verify as verify_workspace
        report.update(verify_workspace(root/'task-workspace',report_path.with_suffix('.task-workspace.png')))
        report.update(passed=True, total_rows=len(service.production.rows()), received=sum(r['collection_count'] for r in service.production.rows()), batch_folders=len(folders), folder_name=Path(next(iter(folders))).name,
                      duplicate_choices=choices, queries=4, split_workspace=True, review_count=sum(r['reviewed_count'] for r in service.production.rows()), submission_count=sum(r['submitted_count'] for r in service.production.rows()))
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if controller:
            controller.shutdown()
            controller.dispose_ui()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1
