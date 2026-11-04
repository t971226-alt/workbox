"""A novice-facing, cancellable legacy import assistant."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,
    QStackedWidget,QWidget,QPlainTextEdit,QCheckBox,QFileDialog,QProgressBar)
from workspace_chrome import chrome_button,colors
from theme import apply_to
from ui import FileJob
from legacy_import import LegacyImporter


class LegacyTransferDialog(QDialog):
    """An explicit copy/move choice before any legacy-data search starts."""
    def __init__(self,parent=None):
        super().__init__(parent);self.transfer_mode=None
        self.setWindowTitle('导入旧 BOX');self.setFixedWidth(500);apply_to(self)
        c=colors();self.setStyleSheet('QDialog { background:'+c['surface']+'; color:'+c['ink']+'; } QLabel { background:transparent; color:'+c['ink']+';font-size:13px; }')
        layout=QVBoxLayout(self);layout.setContentsMargins(22,20,22,18);layout.setSpacing(12)
        title=QLabel('旧材料要复制，还是迁移？');title.setStyleSheet('font-size:17px;font-weight:600;');layout.addWidget(title)
        self.copy_button=chrome_button('复制到 BOX（推荐）',variant='primary');self.copy_button.setDefault(True);layout.addWidget(self.copy_button)
        copy_note=QLabel('在 BOX 中保存一份，原位置的材料保留。');copy_note.setWordWrap(True);layout.addWidget(copy_note)
        self.move_button=chrome_button('迁移到 BOX',variant='secondary');layout.addWidget(self.move_button)
        move_note=QLabel('导入成功并校验后，仅移除旧记录中已登记、已转存的材料。\n旧数据库、其他文件和未能安全转存的原件保留。');move_note.setWordWrap(True);layout.addWidget(move_note)
        note=QLabel('两种方式都先核对，确认导入后自动备份。迁移前请退出旧 BOX。');note.setWordWrap(True);note.setStyleSheet('color:'+c['muted']+';font-size:12px;');layout.addWidget(note)
        self.cancel_button=chrome_button('取消');layout.addWidget(self.cancel_button,0,Qt.AlignRight)
        self.copy_button.clicked.connect(lambda:self.choose('copy'));self.move_button.clicked.connect(lambda:self.choose('move'));self.cancel_button.clicked.connect(self.reject)

    def choose(self,mode):
        self.transfer_mode=mode;self.accept()


class LegacyImportDialog(QDialog):
    def __init__(self,service,parent=None,*,auto_scan=True,transfer_mode='copy'):
        super().__init__(parent);self.service=service;self.importer=LegacyImporter(service)
        self.transfer_mode=transfer_mode
        self.worker=None;self.plan=None;self.report=None;self._close_after=False;self._kind=''
        self.setWindowTitle('导入旧 BOX');self.resize(760,560);self.setMinimumSize(660,480);apply_to(self)
        c=colors();self.setStyleSheet('QDialog,QWidget { background:'+c['surface']+'; color:'+c['ink']+'; } QLabel { font-size:13px; } QListWidget,QPlainTextEdit { border:1px solid '+c['border']+'; border-radius:7px; padding:7px; background:'+c['field']+'; color:'+c['ink']+'; font-size:12px; selection-background-color:'+c['hover']+'; selection-color:'+c['ink']+'; }')
        layout=QVBoxLayout(self);layout.setContentsMargins(16,14,16,14);layout.setSpacing(10)
        title=QLabel('把旧记录和材料一起带过来');title.setStyleSheet('font-size:18px;font-weight:600;');layout.addWidget(title)
        note=QLabel('找到旧数据 → 核对内容 → 自动备份并导入。新版已有数据不会覆盖。\n请先关闭旧 BOX，避免核对后材料又发生变化。');note.setWordWrap(True);layout.addWidget(note)
        mode_row=QHBoxLayout();self.mode_note=QLabel();self.mode_note.setWordWrap(True);mode_row.addWidget(self.mode_note,1)
        self.mode_button=chrome_button('更改方式');self.mode_button.clicked.connect(self.change_transfer_mode);mode_row.addWidget(self.mode_button);layout.addLayout(mode_row)
        tools=QHBoxLayout();self.scan_button=chrome_button('自动查找',icon='search');self.folder_button=chrome_button('选择旧版文件夹',icon='box');self.file_button=chrome_button('选择数据文件')
        tools.addWidget(self.scan_button);tools.addWidget(self.folder_button);tools.addWidget(self.file_button);tools.addStretch();layout.addLayout(tools)
        self.pages=QStackedWidget();layout.addWidget(self.pages,1)
        search=QWidget();a=QVBoxLayout(search);a.setContentsMargins(0,0,0,0)
        self.found=QListWidget();self.found.setAccessibleName('找到的旧BOX数据')
        self.found.setStyleSheet('QListWidget::item { padding:8px; border:none; } QListWidget::item:selected:active,QListWidget::item:selected:!active,QListWidget::item:hover,QListWidget::item:focus { background:'+c['hover']+'; color:'+c['ink']+'; border:none; }');a.addWidget(self.found)
        self.read_button=chrome_button('核对选中的旧数据',variant='primary');self.read_button.setEnabled(False);a.addWidget(self.read_button,0,Qt.AlignRight);self.pages.addWidget(search)
        review=QWidget();b=QVBoxLayout(review);b.setContentsMargins(0,0,0,0)
        self.preview_text=QPlainTextEdit();self.preview_text.setReadOnly(True);self.preview_text.setAccessibleName('旧BOX导入前核对结果');b.addWidget(self.preview_text)
        self.material_button=chrome_button('补充材料位置');b.addWidget(self.material_button,0,Qt.AlignLeft)
        self.partial=QCheckBox('我知道有材料缺失，先导入记录和现有材料');self.partial.hide();b.addWidget(self.partial)
        self.confirm_button=chrome_button('自动备份并导入',variant='primary');b.addWidget(self.confirm_button,0,Qt.AlignRight);self.pages.addWidget(review)
        done=QWidget();d=QVBoxLayout(done);d.setContentsMargins(0,0,0,0)
        self.report_text=QPlainTextEdit();self.report_text.setReadOnly(True);self.report_text.setAccessibleName('旧BOX导入结果');d.addWidget(self.report_text)
        self.backup_button=chrome_button('打开备份位置',icon='box');d.addWidget(self.backup_button,0,Qt.AlignLeft);self.pages.addWidget(done)
        self.resume_button=chrome_button('继续迁移原材料',variant='primary');self.resume_button.hide();self.resume_button.clicked.connect(self.resume_migration);d.addWidget(self.resume_button,0,Qt.AlignRight)
        self.status=QLabel('自动查找通常会检查桌面、下载和常见的 BOX 安装位置。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.setTextVisible(False);self.progress.setFixedHeight(5);self.progress.hide();layout.addWidget(self.progress)
        bottom=QHBoxLayout();self.history_button=chrome_button('以前的导入结果');bottom.addWidget(self.history_button);bottom.addStretch();self.close_button=chrome_button('关闭');bottom.addWidget(self.close_button);layout.addLayout(bottom)
        self.scan_button.clicked.connect(self.scan);self.folder_button.clicked.connect(self.choose_folder);self.file_button.clicked.connect(self.choose_file)
        self.read_button.clicked.connect(self.read_selected);self.found.itemDoubleClicked.connect(self.read_selected)
        self.found.itemSelectionChanged.connect(lambda:self.read_button.setEnabled(self.found.currentItem() is not None and self.worker is None))
        self.material_button.clicked.connect(self.choose_materials);self.partial.toggled.connect(self.update_confirm)
        self.confirm_button.clicked.connect(self.import_now);self.close_button.clicked.connect(self.stop_or_close)
        self.backup_button.clicked.connect(self.open_backup);self.history_button.clicked.connect(self.show_reports)
        self.scan_timer=QTimer(self);self.scan_timer.setSingleShot(True);self.scan_timer.timeout.connect(self.scan)
        self.update_mode_note()
        if auto_scan:self.scan_timer.start(0)

    def update_mode_note(self):
        self.mode_note.setText('材料处理：迁移到 BOX · 校验成功后移除原材料' if self.transfer_mode=='move' else '材料处理：复制到 BOX · 原材料保留')

    def change_transfer_mode(self):
        from dialog_lifecycle import transient_dialog
        with transient_dialog(LegacyTransferDialog(self)) as choice:
            if choice.exec()==QDialog.Accepted:self.transfer_mode=choice.transfer_mode;self.update_mode_note();self.update_confirm()

    def set_busy(self,busy):
        for button in (self.scan_button,self.folder_button,self.file_button,self.read_button,self.confirm_button,self.material_button,self.history_button):button.setEnabled(not busy)
        self.mode_button.setEnabled(not busy);self.resume_button.setEnabled(not busy)
        self.partial.setEnabled(not busy);self.progress.setVisible(busy);self.close_button.setText('停止操作' if busy else '关闭')
        if not busy:
            self.read_button.setEnabled(self.found.currentItem() is not None);self.update_confirm()

    def start(self,kind,operation,message):
        if self.worker or self._close_after:return
        self._kind=kind;self.status.setText(message);self.set_busy(True)
        self.worker=FileJob(operation,self);self.worker.finished.connect(self.completed);self.worker.start()

    def scan(self):
        self.pages.setCurrentIndex(0);self.found.clear()
        self.start('scan',lambda **kw:self.importer.discover(**kw),'正在自动查找旧 BOX 数据…')

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'选择包含旧 BOX 程序或 data 的文件夹')
        if path:self.prepare(path)

    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'选择旧版 workbox.db','','BOX 数据库 (*.db *.sqlite *.sqlite3)')
        if path:self.prepare(path)

    def read_selected(self,*_):
        item=self.found.currentItem()
        if item:self.prepare(item.data(Qt.UserRole))

    def prepare(self,path,material_root=None):
        self.mode_button.show();self.update_mode_note()
        self.plan=None;self.pages.setCurrentIndex(1);self.preview_text.setPlainText('正在核对所选旧数据…');self.partial.hide();self.material_button.hide()
        self.start('prepare',lambda **kw:(self.importer.prepare_current(material_root=material_root,**kw) if path=='__current__' else self.importer.prepare(path,material_root=material_root,**kw)),'正在读取旧数据并核对材料；尚未导入任何记录…')

    def choose_materials(self):
        if not self.plan:return
        folder=QFileDialog.getExistingDirectory(self,'选择原材料所在的文件夹（搬过位置也可以）')
        if folder:self.prepare('__current__' if self.plan.get('current_inventory') else self.plan['source'],folder)

    def update_confirm(self):
        enabled=bool(self.plan and (not self.plan.get('missing') or self.partial.isChecked())) and self.worker is None
        if self.plan and self.plan.get('missing') and self.transfer_mode=='move':enabled=False
        self.confirm_button.setEnabled(enabled);self.confirm_button.setText('导入现有内容（有缺失）' if self.plan and self.plan.get('missing') else '自动备份并导入')

    def import_now(self):
        if not self.plan:return
        plan=self.plan;partial=self.partial.isChecked()
        mode=self.transfer_mode
        self.start('import',lambda **kw:self.importer.import_plan(plan,allow_partial=partial,transfer_mode=mode,**kw),'正在备份、转存并校验材料；迁移只在导入提交成功后移除原件…')

    def resume_migration(self):
        if not self.report:return
        run_id=self.report['run_id']
        self.start('resume',lambda **kw:self.importer.resume_migration(run_id,**kw),'正在核对已转存的副本，并继续迁移尚未移除的原材料…')

    def completed(self):
        job=self.worker
        if job is None:return
        self.worker=None;job.deleteLater();self.set_busy(False)
        if job.error:self.status.setText('本次未合并记录：'+job.error+'\n已有数据和原件保留，可更正位置后重试。')
        elif job.cancelled:self.status.setText('已停止本次操作，未提交的记录没有合并。已准备的备份和副本保留。')
        elif self._kind=='scan':
            for r in job.result:
                label="当前 BOX 的旧记录（补入总表，不重复建项目）" if r.get("current_inventory") else r["path"]
                item=QListWidgetItem(f"{label}\n{r['projects']} 个软件项目");item.setData(Qt.UserRole,r['path']);self.found.addItem(item)
            if len(job.result)==1 and not self._close_after:self.found.setCurrentRow(0);self.prepare(job.result[0]['path'])
            else:self.status.setText('找到多份旧数据，请选中需要的那一份再核对。' if job.result else '没有自动找到。请点“选择旧版文件夹”，通常是放着 WorkBox.exe 和 data 的目录。')
        elif self._kind=='prepare':
            if job.result.get('already_imported'):self.show_report(job.result['report']);self.status.setText('这份旧数据已经导入过，没有重复添加。')
            else:
                self.plan=job.result;counts=self.plan['counts'];missing=self.plan['missing'];self.partial.setChecked(False);self.partial.setVisible(bool(missing));self.material_button.setVisible(bool(missing))
                text=f"来源：{self.plan['source']}\n\n旧数据：{counts.get('batches',0)} 个批次，{counts.get('projects',0)} 个软件项目\n材料：找到 {self.plan['file_count']} 个文件，约 {self.plan['bytes']/1024/1024:.1f} MB\n检查记录：{counts.get('material_reviews',0)} 条\n提交明细：{counts.get('material_submission_items',0)} 条\n会话：{counts.get('intake_conversations',0)} 条\n\n同名批次：{self.plan['batch_collisions']} 个，将自动改名保留。\n同名软件：{self.plan['project_duplicates']} 条，将作为独立旧记录保留，不覆盖新版。\n\n旧版已交付状态保留，不会自动勾成新版已检查或已提交。旧版后台监控不自动继续。"
                if self.plan.get('current_inventory'):
                    self.transfer_mode='copy';self.update_mode_note();self.mode_button.setEnabled(False)
                    text=f"来源：{self.plan['source']}\n\n待整理：{counts['projects']} 个软件项目，{counts['batches']} 个批次\n材料：找到 {self.plan['file_count']} 个文件\n\n为现有旧项目补建总表清单，不会再建一份项目。\n材料已在当前 BOX 内，只补表和复制整理，不移除正在使用的文件。\n原来的状态、时间和历史保留；不会自动勾选检查和提交。"
                if missing:text+=f'\n\n有 {len(missing)} 个文件尚未找到：\n'+'\n'.join(missing[:100])+'\n\n请点“补充材料位置”，让BOX再查找；也可以明确选择先导入现有内容。'
                text+='\n\n将转存的旧材料目录：\n'+'\n'.join(self.plan['directories'])
                if self.transfer_mode=='move':text+='\n\n迁移只移除本次已转存且校验成功的材料；旧数据库与其他文件保留。有缺失时请先补齐，或改选复制。'
                self.preview_text.setPlainText(text);self.pages.setCurrentIndex(1);self.update_confirm();self.status.setText('请核对后点击导入。程序会先备份，再复制材料，最后合并记录。')
        else:self.show_report(job.result);self.status.setText('导入完成。关闭此窗口即可在总表查看旧记录。');self.service_changed()
        if self._close_after and self.worker is None:super().reject()

    def service_changed(self):
        host=self.parentWidget()
        if host is not None and hasattr(host,'refresh'):
            host.refresh()
            if self.report and hasattr(host,'production_page'):host.production_page.show_all(self.report.get('batch_ids',[]))

    def show_report(self,report):
        self.report=report;self.pages.setCurrentIndex(2)
        self.mode_button.hide()
        moving=report.get('transfer_mode')=='move'
        self.mode_note.setText('材料处理：迁移到 BOX' if moving else '材料处理：复制到 BOX · 原材料保留')
        can_migrate=bool(report['complete'] and report.get('transfer_items') and not report.get('cleanup_complete') and self.transfer_mode=='move')
        self.resume_button.setVisible(bool(moving and report.get('cleanup_pending')) or can_migrate)
        self.resume_button.setText('继续迁移原材料' if moving else '迁移已复制的原材料')
        text=('完整导入完成' if report['complete'] else '部分导入完成，仍有材料缺失')+f"\n\n批次：{report['batches']}\n软件项目：{report['projects']}\n转存并校验文件：{report['files']}\n检查记录：{report['reviews']}\n提交明细：{report['submissions']}\n\n新版已有数据没有覆盖。\n备份位置：{Path(report['backup']).parent}"
        if moving and report.get('cleanup_pending'):text='原材料尚未全部移除，已导入的记录和副本保留。\n\n'+text
        if moving:
            text+=f"\n\n原位置已移除材料：{report.get('moved_files',0)} 个\n尚未移除的原材料：{report.get('cleanup_pending',0)} 个\n旧数据库、其他文件及未能安全移除的原件保留。"
            if report.get('cleanup_notice'):text+='\n'+report['cleanup_notice']
            text+='\n'+'\n'.join(r['source']+'：'+(r.get('reason') or '等待继续迁移') for r in report.get('transfer_items',[]) if r['state']!='removed')
        else:text+='\n\n原材料保留。'
        if report['missing']:text+='\n\n未找到的材料：\n'+'\n'.join(report['missing'])
        if report.get('legacy_states'):
            text+='\n\n旧版状态原样保留（不自动视为逐文件检查或提交）：\n'
            text+='\n'.join(r['name']+' · '+r['status']+(' · 已删除记录' if r['deleted'] else ' · 已归档' if r['archived'] else '')+' · '+r['batch'] for r in report['legacy_states'])
        self.report_text.setPlainText(text)

    def show_reports(self):
        reports=self.importer.reports()
        if not reports:self.status.setText('还没有导入过旧数据。');return
        self.show_report(reports[0]);self.status.setText('显示最近一次的导入结果。完整报告与原数据库备份保存在同一目录。')

    def open_backup(self):
        if self.report:QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.report['backup']).parent)))

    def stop_or_close(self):
        if self.worker:self.worker.cancel_event.set();self.status.setText('正在安全停止，请稍候…')
        else:self.close()

    def closeEvent(self,event):
        self.scan_timer.stop();self._close_after=True
        if self.worker:self._close_after=True;self.worker.cancel_event.set();event.ignore();self.status.setText('正在安全停止，完成后关闭…')
        else:event.accept()

    def reject(self):
        self.scan_timer.stop();self._close_after=True
        if self.worker:self._close_after=True;self.worker.cancel_event.set()
        else:super().reject()

    def shutdown(self):
        self.scan_timer.stop();self._close_after=True
        if self.worker:
            self.worker.cancel_event.set()
            if not self.worker.wait(15000):raise RuntimeError('旧版导入尚未停止，请稍后退出。')
