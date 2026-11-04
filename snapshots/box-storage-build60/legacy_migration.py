"""Remove only committed, verified legacy material files, with resumable receipts."""
from contextlib import closing
from pathlib import Path
from concurrent.futures import CancelledError
import json
import os
import sqlite3
import hashlib

from native_files import ZipLease, plain_path, path_is_within
from workspace_manager import _hash_stream


def check_move_source(importer, plan):
    source=plain_path(plan['source'])
    if os.name!='nt':raise ValueError('当前环境只支持复制；安全迁移需要 Windows。')
    if path_is_within(source,importer.db.path.parent):raise ValueError('这份数据已在当前 BOX 内，请选择复制整理，保留正在使用的文件。')
    if Path(str(source)+'.session.lock').exists():raise ValueError('请先退出旧 BOX，再迁移材料。')


def _save(importer, report):
    with importer.db.connect() as c:
        c.execute('UPDATE legacy_import_runs SET report=? WHERE id=?',(json.dumps(report,ensure_ascii=False),report['run_id']))
    run=importer.db.path.parent/'旧版导入'/'导入记录'/report['run_id']
    try:
        temp=run/'导入报告.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');temp.replace(run/'导入报告.json')
    except OSError:report['report_file_warning']='报告文件未写出，可从以前的导入结果查看。'


def _protected(importer, source, c):
    if path_is_within(source,importer.db.path.parent):return '文件在当前 BOX 数据目录内，保留原件。'
    # Protect current working roots, even if an unregistered file lives there.
    settings=importer.service.workspaces.settings()
    for key in ('work_root','archive_root'):
        if settings.get(key) and path_is_within(source,Path(settings[key])):return '文件位于当前 BOX 使用的工作或归档目录，保留原件。'
    existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('work_files','material_versions','collected_materials','zips'):
        if table not in existing:continue
        for r in c.execute('SELECT path FROM '+table):
            p=Path(r[0])
            if p==source or (p.exists() and source.exists() and p.samefile(source)):return '当前 BOX 的其他记录仍在使用此文件，保留原件。'
    for table in ('project_workspaces','project_delivery_archives'):
        if table not in existing:continue
        for r in c.execute('SELECT path FROM '+table):
            if path_is_within(source,Path(r[0])):return '当前 BOX 的其他记录仍在使用此目录，保留原件。'
    return ''


def _counts(report):
    items=report.get('transfer_items',[])
    report['moved_files']=sum(r['state']=='removed' for r in items)
    report['cleanup_pending']=sum(r['state']!='removed' for r in items)
    report['cleanup_complete']=report['cleanup_pending']==0


def finish_transfer(importer, report, *, cancel_event=None, progress=None):
    """A committed import remains successful if source removal is blocked or cancelled."""
    from legacy_import import digest,snapshot,read_db
    run=importer.db.path.parent/'旧版导入'/'导入记录'/report['run_id']
    try:
        check_move_source(importer,report)
        if not report['complete']:raise ValueError('材料不齐，保留全部原件。')
        _counts(report);_save(importer,report)  # Durable authorization/manifest before any removal.
        source=plain_path(report['source'])
        # A reserved SQLite lock prevents the old app adding references during cleanup.
        with closing(sqlite3.connect(source.as_uri()+'?mode=rw',uri=True,timeout=.2)) as old:
            old.execute('BEGIN IMMEDIATE')
            snapshot(source,run/'迁移前核对.db',cancel_event)
            original=plain_path(report['original'])
            if not path_is_within(original,run) or digest(original)!=report['source_fingerprint']:raise ValueError('旧数据库备份校验失败，保留原材料。')
            def content_hash(path):
                value=hashlib.sha256()
                with read_db(path) as c:
                    for line in c.iterdump():value.update(line.encode('utf-8'));value.update(b'\n')
                return value.hexdigest()
            # SQLite backup may change destination header counters on a retry.
            if content_hash(run/'迁移前核对.db')!=content_hash(original):raise ValueError('旧数据在核对后变化，已导入的内容保留，暂不移除原材料。')
            report.pop('cleanup_notice',None)
            for i,item in enumerate(report['transfer_items']):
                if item['state']=='removed':continue
                if cancel_event is not None and cancel_event.is_set():break
                try:
                    src=plain_path(item['source']);dst=plain_path(item['target'])
                    if src==source or str(src) in {str(source)+'-wal',str(source)+'-shm',str(source)+'.session.lock'}:raise ValueError('旧数据库和运行记录保留。')
                    if not path_is_within(dst,run) or src==dst:raise ValueError('转存位置核对失败，保留原件。')
                    with importer.db.connect() as c:
                        c.execute('BEGIN IMMEDIATE')
                        reason=_protected(importer,src,c)
                        if reason:raise ValueError(reason)
                        # The source handle denies writers/deleters, so removal cannot hit a replacement.
                        with ZipLease(dst,movable=False) as target:
                            target_hash,target_size=_hash_stream(target.stream,cancel_event=cancel_event)
                            if (target_hash,target_size)!=(item['sha256'],item['size']):raise ValueError('BOX 内副本校验失败，保留原件。')
                            if src.exists():
                                with ZipLease(src) as original:
                                    if (original.initial.st_size,original.initial.st_mtime_ns)!=(item['size'],item['mtime']):raise ValueError('原材料发生变化，保留原件。')
                                    sha,size=_hash_stream(original.stream,cancel_event=cancel_event)
                                    if (sha,size)!=(item['sha256'],item['size']):raise ValueError('原材料内容发生变化，保留原件。')
                                    if original.named_streams()!=target.named_streams():raise ValueError('文件来源标记未完整转存，保留原件。')
                                    if cancel_event is not None and cancel_event.is_set():break
                                    original.remove_after_verified_commit()
                            item.update(state='removed',reason='')
                        _counts(report)
                        c.execute('UPDATE legacy_import_runs SET report=? WHERE id=?',(json.dumps(report,ensure_ascii=False),report['run_id']))
                except CancelledError:break
                except (OSError,ValueError,RuntimeError) as error:item.update(state='retained',reason=str(error))
                _counts(report);_save(importer,report)
                if progress:progress(i+1,len(report['transfer_items']))
    except CancelledError:report['cleanup_notice']='已停止移除原材料；导入已完成，尚未移除的原件保留。'
    except Exception as error:report['cleanup_notice']='导入已完成，原材料移除尚未完成：'+str(error)
    _counts(report)
    if report['cleanup_pending'] and cancel_event is not None and cancel_event.is_set():report['cleanup_notice']='已停止移除原材料；尚未移除的原件保留。'
    try:_save(importer,report)
    except Exception:report['cleanup_notice']='导入已完成；清理进度未能全部保存，下次可重新核对。'
    return report
