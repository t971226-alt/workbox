"""Read-only access to the published material location, not its internal archive."""
import os
import subprocess
import json
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def original_file_location(service,project_id,*,kind=None,zip_id=None,version_id=None):
    """Resolve recorded provenance for the selected version, never a saved-copy fallback."""
    source=None
    with service.db.connect() as c:
        service.db._assert_projects_editable(c,project_id)
        if zip_id is not None:
            receipt=c.execute('SELECT * FROM original_zip_receipts WHERE project_id=? AND id=?',(project_id,zip_id)).fetchone()
            if not receipt:raise ValueError('所选原包记录不存在，请刷新后重新选择。')
            for table in ('intake_history','production_imports'):
                for record in c.execute('SELECT result_json FROM '+table+' ORDER BY rowid DESC'):
                    try:payload=json.loads(record[0])
                    except (ValueError,TypeError):continue
                    for item in payload.get('files',[]):
                        if item.get('project_id')!=project_id or item.get('state')!='complete':continue
                        same_id=item.get('receipt_id')==zip_id
                        same_copy=(item.get('saved_path')==receipt['path'] and
                                   (item.get('saved_sha256') or item.get('source_sha256'))==receipt['sha256'])
                        if same_id or same_copy:
                            source=item.get('source_path');break
                    if source:break
                if source:break
        else:
            if version_id is None:
                version_id=c.execute('SELECT MAX(id) FROM material_versions WHERE project_id=? AND kind=?',(project_id,kind)).fetchone()[0]
            record=c.execute('''SELECT w.source_path FROM material_versions v JOIN work_files w ON w.id=v.work_file_id
                WHERE v.project_id=? AND v.id=?''',(project_id,version_id)).fetchone()
            if record:source=record[0]
        if not source:raise ValueError('这份材料没有记录原始位置，请到导入来源中查找。')
        path=Path(source)
        # Internal work files are not the user's original import location.
        from storage_management import INTERNAL,LEGACY_INTERNAL
        material_root=Path(service.workspaces.settings()['work_root'])
        managed=[service.db.path.parent,material_root/INTERNAL,material_root/LEGACY_INTERNAL]
        if any(path.resolve().is_relative_to(p.resolve()) for p in managed):
            raise ValueError('这份材料只记录了 BOX 内部位置，无法确定最初导入的位置。')
        if not path.exists():raise ValueError('原始文件已移动或删除：'+str(path))
        return path.resolve()


def collection_location(service,project_id,kind=None):
    with service.db.connect() as connection:
        if kind is not None:
            receipt=connection.execute('SELECT path,version_id FROM collected_materials WHERE project_id=? AND kind=?',(project_id,kind)).fetchone()
            current=service.production._current(connection,project_id,kind)
            if not receipt or not current['id'] or receipt['version_id']!=current['id']:
                raise ValueError('当前材料尚未完成收录，请先收录或重试后再打开保存目录。')
            target=Path(receipt['path'])
            if not target.is_file():raise ValueError('保存的材料暂不可用，请核对保存位置或重新收录。')
            return target.resolve()
        receipts=connection.execute('SELECT path FROM collected_materials WHERE project_id=? ORDER BY collected_at DESC,kind',(project_id,)).fetchall()
        if not receipts:raise ValueError('此软件尚未收录材料，暂时没有保存目录。')
        target=next((Path(row['path']).parent for row in receipts if Path(row['path']).is_file()),None)
        if target is None:raise ValueError('保存目录或材料暂不可用，请核对保存位置或重新收录。')
        return target.resolve()


def saved_file_location(service,project_id,*,kind=None,zip_id=None):
    """Resolve current published materials or a specifically selected raw ZIP."""
    with service.db.connect() as c:
        service.db._assert_projects_editable(c,project_id)
        if zip_id is not None:
            row=c.execute('SELECT path FROM original_zip_receipts WHERE project_id=? AND id=?',(project_id,zip_id)).fetchone()
            if not row:raise ValueError('所选原包记录不存在，请刷新后重新选择。')
            path=Path(row['path'])
            if not path.is_file():raise ValueError('原包文件已移动或缺失，请核对保存位置。')
            return path.resolve()
    if not kind:raise ValueError('请选择一份材料后再打开保存文件位置。')
    return collection_location(service,project_id,kind)


def open_location(target):
    target=Path(target)
    if target.is_file() and os.name=='nt':
        try:
            subprocess.Popen([str(Path(os.environ.get('WINDIR','C:/Windows'))/'explorer.exe'),'/select,',str(target)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return True
        except OSError:pass
    folder=target.parent if target.is_file() else target
    if not folder.is_dir():raise ValueError('保存目录不存在，请核对材料位置。')
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):raise OSError('无法打开保存目录：'+str(folder))
    return True
