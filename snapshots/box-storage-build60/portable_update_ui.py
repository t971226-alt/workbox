"""Novice local update guidance. No data service opens until version checks finish."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QProgressBar,QFileDialog,QListWidget,QListWidgetItem
from workspace_chrome import chrome_button,colors
from theme import apply_to,install_theme
from dialog_lifecycle import transient_dialog
from ui import FileJob
from portable_release import RELEASE_KEY,RELEASE_LABEL
from portable_update import (read_release,discover,read_index,write_json,index_path,make_job,
                             apply_job,load_job,data_version,cleanup_completed,
                             independent_instance,create_independent_instance)


def activate_existing(folder,*,sources=(),startup=False):
    folder=Path(folder);database=(folder/'data/workbox.db').resolve()
    name='WorkBox-'+hashlib.sha256(str(database).casefold().encode()).hexdigest()[:24]
    socket=QLocalSocket();socket.connectToServer(name)
    if not socket.waitForConnected(200):return False
    message=dict(command='activate',legacy_sources=[str(p) for p in sources],silent=startup)
    socket.write((json.dumps(message)+'\n').encode('utf-8'));socket.waitForBytesWritten(500)
    # Keep the connection alive until the receiving event loop handles activation.
    # Older versions do not acknowledge; the instance lock still prevents doubles.
    if not socket.bytesAvailable():socket.waitForReadyRead(1500)
    socket.disconnectFromServer();return True


def launch(folder,*,sources=(),startup=False):
    release=read_release(folder)
    if activate_existing(release.root,sources=sources,startup=startup):return
    args=[str(release.root/'WorkBox.exe')]
    if release.version>=(1,1,0,1):args.append('--startup' if startup else '--panel')
    for source in sources if release.version>=(1,1,0,1) else ():
        if Path(source).is_file() and Path(source)!=release.root/'data/workbox.db':args.extend(['--legacy-source',str(source)])
    subprocess.Popen(args,cwd=release.root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


class ChoiceDialog(QDialog):
    def __init__(self,title,body,options,parent=None):
        super().__init__(parent);self.choice=None;self.buttons={};self.setWindowTitle('BOX · 版本与数据');self.setFixedWidth(520);apply_to(self)
        c=colors();self.setStyleSheet('QDialog {background:'+c['surface']+';} QLabel {background:transparent;color:'+c['ink']+';font-size:12px;} QPushButton {font-size:12px;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(16,14,16,12);layout.setSpacing(8)
        heading=QLabel(title);heading.setWordWrap(True);heading.setStyleSheet('font-size:14px;font-weight:600;');layout.addWidget(heading)
        self.body=QLabel(body);self.body.setWordWrap(True);layout.addWidget(self.body)
        for i,(value,text) in enumerate(options):
            button=chrome_button(text,variant='primary' if i==0 else 'secondary');button.setDefault(i==0)
            button.clicked.connect(lambda checked=False,v=value:self.select(v));self.buttons[value]=button;layout.addWidget(button)
        cancel=chrome_button('取消');cancel.clicked.connect(self.reject);layout.addWidget(cancel)

    def select(self,value):self.choice=value;self.accept()


class LocationDialog(QDialog):
    def __init__(self,releases,parent=None):
        super().__init__(parent);self.selected=None;self.releases=releases;self.resize(650,380);self.setWindowTitle('选择日常使用的 BOX');apply_to(self)
        c=colors();layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18);layout.setSpacing(12)
        note=QLabel('发现多份 BOX，请选择日常使用的位置。其他目录的数据会保留，可在导入核对后合并。');note.setWordWrap(True);layout.addWidget(note)
        self.list=QListWidget();self.list.setStyleSheet('QListWidget {border:1px solid '+c['border']+';background:'+c['surface']+';color:'+c['ink']+';border-radius:8px;} QListWidget::item {padding:12px;} QListWidget::item:selected {background:'+c['hover']+';color:'+c['ink']+';}');layout.addWidget(self.list,1)
        for r in releases:self.list.addItem(QListWidgetItem(r.label+'\n'+str(r.root)))
        self.list.setCurrentRow(0);row=QHBoxLayout();cancel=chrome_button('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel);row.addStretch()
        choose=chrome_button('使用这个位置',variant='primary');choose.clicked.connect(self.confirm);row.addWidget(choose);layout.addLayout(row)

    def confirm(self):
        if self.list.currentRow()>=0:self.selected=self.releases[self.list.currentRow()];self.accept()


def choose(title,body,options):
    with transient_dialog(ChoiceDialog(title,body,options)) as dialog:
        return dialog.choice if dialog.exec()==QDialog.Accepted else None


def spawn_helper(path,token,*,restore=False):
    args=[sys.executable,'--apply-update',str(path),'--update-token',token]
    if restore:args.append('--restore-update')
    subprocess.Popen(args,cwd=Path(sys.executable).parent,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def preflight(folder,*,startup=False,index=None,roots=None,import_sources=None):
    """Return True only when this exact directory may initialize its own data."""
    install_theme();current=read_release(folder)
    if current.version!=RELEASE_KEY:raise ValueError('当前程序与发布版本不一致，请重新解压完整的 BOX 免安装包。')
    if independent_instance(current.root):
        if data_version(folder)>RELEASE_KEY:raise ValueError('本目录的数据由较新版本使用，请打开新版 BOX。')
        if import_sources is not None:import_sources.clear()
        return not activate_existing(current.root,startup=startup)
    cleanup_completed(index)
    value=read_index(index);pending=value.get('pending','')
    if pending and Path(pending).is_file():
        plan=json.loads(Path(pending).read_text('utf-8'))
        load_job(pending,plan['token'])
        answer=choose('上次更新尚未结束','先恢复可用的原程序，再重新开始更新。现有总表和材料保留。',[('restore','恢复原位置的 BOX')])
        if answer:spawn_helper(pending,plan['token'],restore=True)
        return False
    preferred=Path(value.get('preferred',''));has_preferred=bool(value.get('preferred') and preferred.exists())
    if has_preferred and independent_instance(preferred):has_preferred=False
    if has_preferred:
        # Never replace a user's missing/corrupt installation with an unnoticed blank ledger.
        target=read_release(preferred)
        if target.root==current.root:
            if data_version(folder)>RELEASE_KEY:raise ValueError('本目录的数据由较新版本使用，请打开新版 BOX。')
            if activate_existing(current.root,startup=startup):return False
            return True
        found=[target]+[r for r in discover(folder,index=index,roots=roots) if r.root!=target.root]
    else:found=discover(folder,index=index,roots=roots)
    if not found:
        if (current.root/'data/workbox.db').exists():
            if data_version(folder)>RELEASE_KEY:raise ValueError('此数据来自较新版本，不能用旧版打开。')
            return True
        answer=choose('开始使用 BOX','如果之前用过 BOX，请选择原文件夹，继续使用同一份总表。',[('new','第一次使用，开始设置'),('existing','已有 BOX，选择原文件夹')])
        if answer=='new':return True
        if answer!='existing':return False
        selected=QFileDialog.getExistingDirectory(None,'选择包含原 WorkBox.exe 和 data 的文件夹')
        if not selected:return False
        found=[read_release(selected)]
        if found[0].root==current.root:return True
    if (current.root/'data/workbox.db').exists() and not has_preferred and current.version>=max(r.version for r in found):found=[current]+found
    if len(found)>1 and (not has_preferred or found[0].root!=preferred):
        with transient_dialog(LocationDialog(found)) as dialog:
            if dialog.exec()!=QDialog.Accepted:return False
            target=dialog.selected
    else:target=found[0]
    sources=[r.root/'data/workbox.db' for r in found if r.root!=target.root and (r.root/'data/workbox.db').exists()]
    if (current.root/'data/workbox.db').exists():sources.append(current.root/'data/workbox.db')
    note=('\n\n另外找到数据目录，打开后可核对导入；不会自动合并。' if sources else '')
    if target.root==current.root:
        if data_version(folder)>RELEASE_KEY:raise ValueError('此数据由较新版本使用，请打开新版 BOX。')
        if import_sources is not None:import_sources.extend(p for p in sources if p not in import_sources and p!=current.root/'data/workbox.db')
        return True
    data=current.root/'data'
    fresh=not data.exists() or not any(data.iterdir())
    independent=[('independent','独立使用新版 · 从空总表开始')] if fresh else []
    if target.version==current.version and not fresh:
        launch(target.root,sources=sources,startup=startup);return False
    if target.version>=current.version:
        if fresh:
            answer=choose('选择如何使用 BOX',f'已有 BOX：{target.label}\n位置：{target.root}\n\n打开原 BOX：沿用原总表和材料。\n独立使用：在当前目录从空总表开始，不复制旧数据、设置或历史。',[('open','打开原 BOX')]+[('independent','独立使用当前版本 · 从空总表开始')])
            if answer=='independent':
                create_independent_instance(current.root)
                if import_sources is not None:import_sources.clear()
                return True
            if answer=='open':launch(target.root,sources=sources,startup=startup)
            return False
        answer=choose('已有更新版本的 BOX',f'正在打开的版本：{current.label}\n已有版本：{target.label}\n位置：{target.root}\n\n请打开新版，继续使用原来的总表和材料。'+note,[('open','打开新版 BOX')])
        if answer:launch(target.root,sources=sources,startup=startup)
        return False
    explanation='独立使用新版：从空总表开始，不复制旧数据、设置或历史。' if fresh else '当前目录已有数据，已保留；如需独立使用，请把完整新版放到一个新的空目录。'
    answer=choose('新版 BOX 怎么使用？',f'原 BOX：{target.root}\n{target.label} → {current.label}\n\n更新原 BOX：自动备份，替换程序，保留原总表和材料。\n'+explanation,[('update','更新原 BOX（推荐）')]+independent+[('open','暂不使用新版 · 打开原 BOX')])
    if answer=='independent':
        create_independent_instance(current.root)
        if import_sources is not None:import_sources.clear()
        return True
    if answer=='open':launch(target.root,sources=sources,startup=startup)
    elif answer=='update':
        path,token=make_job(current.root,target.root,index=index);plan=json.loads(path.read_text('utf-8'));plan['legacy_sources']=[str(p) for p in sources];write_json(path,plan);spawn_helper(path,token)
    return False


def quiesce(target,path,token):
    name='WorkBox-'+hashlib.sha256(str((target/'data/workbox.db').resolve()).casefold().encode()).hexdigest()[:24]
    socket=QLocalSocket();socket.connectToServer(name)
    if not socket.waitForConnected(300):return
    socket.write((json.dumps(dict(command='prepare-update',path=str(path),token=token))+'\n').encode('utf-8'));socket.waitForBytesWritten(500)
    if socket.bytesAvailable() or socket.waitForReadyRead(1500):
        response=bytes(socket.readAll())
        if b'busy' in response:raise ValueError('原 BOX 正在处理材料，请完成当前工作后重试。')
    socket.disconnectFromServer()


class UpdateProgressDialog(QDialog):
    def __init__(self,path,token,*,restore=False,no_launch=False):
        super().__init__();self.path=path;self.token=token;self.restore=restore;self.no_launch=no_launch;self.worker=None;self.result=None;self.exit_code=1;self.closing=False
        self.setWindowTitle('BOX · 更新');self.resize(530,250);apply_to(self)
        c=colors();self.setStyleSheet('QDialog {background:'+c['surface']+';} QLabel {background:transparent;color:'+c['ink']+';font-size:13px;} QProgressBar {border:none;border-radius:2px;background:'+c['field']+';} QProgressBar::chunk {background:'+c['muted']+';border-radius:2px;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(22,20,22,18);layout.setSpacing(14)
        self.title=QLabel('正在恢复 BOX' if restore else '正在更新原位置的 BOX');self.title.setStyleSheet('font-size:18px;font-weight:600;');layout.addWidget(self.title)
        self.note=QLabel('先备份和校验，再替换程序。请暂时保留下载的完整文件夹。');self.note.setWordWrap(True);layout.addWidget(self.note)
        self.bar=QProgressBar();self.bar.setRange(0,0);self.bar.setTextVisible(False);self.bar.setFixedHeight(5);layout.addWidget(self.bar)
        row=QHBoxLayout();self.retry=chrome_button('重试',variant='primary');self.retry.hide();self.retry.clicked.connect(self.start);row.addWidget(self.retry);row.addStretch()
        self.cancel=chrome_button('取消更新');self.cancel.clicked.connect(self.reject);row.addWidget(self.cancel);layout.addLayout(row)
        QTimer.singleShot(0,self.start)

    def start(self):
        if self.worker or self.closing:return
        self.retry.hide();self.bar.show();self.cancel.setText('取消更新');self.note.setText('正在恢复未完成的程序替换。总表和材料保持。' if self.restore else '正在校验程序、备份并更新。原来的材料目录保持。')
        self.worker=FileJob(lambda **kw:apply_job(self.path,self.token,restore_only=self.restore,quiesce=lambda target:quiesce(target,self.path,self.token),**kw),self)
        self.worker.finished.connect(self.finished_update);self.worker.start()

    def finished_update(self):
        worker=self.worker;self.worker=None;worker.deleteLater();self.bar.hide()
        if worker.error or worker.cancelled:
            self.note.setText(('已取消更新。' if worker.cancelled else worker.error)+'\n未完成的程序替换已尝试恢复；原数据保留。可重试或重新打开下载的 BOX。');self.retry.show();self.cancel.setText('关闭');return
        self.result=worker.result;self.exit_code=0
        if not self.no_launch:
            try:launch(self.result['target'],sources=getattr(self,'legacy_sources',()))
            except (OSError,ValueError):
                self.note.setText('更新已完成。请从原位置打开 BOX：\n'+self.result['target']);self.cancel.setText('关闭');return
        self.accept()

    def reject(self):
        if self.worker:self.worker.cancel_event.set();self.note.setText('正在安全停止并恢复程序，请稍候…')
        else:self.closing=True;super().reject()

    def closeEvent(self,event):
        if self.worker:self.reject();event.ignore()
        else:self.closing=True;event.accept()


def run_helper(path,token,*,restore=False,no_launch=False):
    plan=json.loads(Path(path).read_text('utf-8'));load_job(path,token);install_theme()
    with transient_dialog(UpdateProgressDialog(path,token,restore=restore,no_launch=no_launch)) as dialog:
        dialog.legacy_sources=plan.get('legacy_sources',[]);dialog.exec()
        return dialog.exit_code
