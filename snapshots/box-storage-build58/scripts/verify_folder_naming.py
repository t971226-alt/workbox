"""Real receipts keep ordinary names; explicitly separate tasks use stable timestamps."""
from pathlib import Path


def verify(root):
    from desktop_service import DesktopService
    from intake_storage import bind,get,task_time
    from intake_rule_options import raw_directory,bind_raw_rule,validate_archive_rule
    from saved_locations import collection_location
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    title='同名收纳验证系统';kind='软件说明书.docx';file=root/(title+'.docx');file.write_bytes(b'first version')
    def create(batch):return s.production.create_batch(batch,[title],[kind])['project_ids'][0]
    first=create('首次收录');parent=root/'材料库/Word文档';spec=dict(root=str(parent),folder=title,base=title)
    with s.db.connect() as c:
        initial=bind(s,c,first,spec,{kind:title})
    s.production.receive(first,kind,file)
    assert Path(initial)==parent/title
    original_path=collection_location(s,first,kind)
    # Real conflict decisions: overwrite must keep the existing folder.
    file.write_bytes(b'updated version')
    pending=s.receive_interactive([str(file)],project_id=first,material_kind=kind)
    group=pending['material_conflicts'][0]
    s.resolve_duplicate_intake(pending['material_plan'],{group['key']:dict(action='overwrite',project_id=first)})
    with s.db.connect() as c:assert get(c,first)['directory']==initial
    assert original_path.parent==Path(initial)
    # Explicitly new same-name task claims a separate folder, never the old one.
    second=create('新建同名任务')
    with s.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-10-31T12:08:09Z' WHERE id=?",(second,))
        separate=bind(s,c,second,spec,{kind:title})
        assert Path(separate).name==title+'_20261031-200809'
    s.production.receive(second,kind,file)
    file.write_bytes(b'second task next version');s.production.receive(second,kind,file)
    with s.db.connect() as c:
        assert get(c,second)['directory']==separate
        assert bind(s,c,second,spec,{kind:title})==separate
    # Same-second collisions cannot share directories or overwrite other tasks.
    third=create('同一秒新建')
    with s.db.connect() as c:
        c.execute("UPDATE projects SET created_at='2026-10-31T12:08:09Z' WHERE id=?",(third,))
        assert Path(bind(s,c,third,spec,{kind:title})).name==title+'_20261031-200809_2'
    # A legacy numbered directory remains pinned even when missing or receiving updates.
    legacy=root/'材料库/历史目录_WB000099'
    with s.db.connect() as c:c.execute('UPDATE task_storage_preferences SET directory=? WHERE project_id=?',(str(legacy),third))
    s.production.receive(third,kind,file)
    with s.db.connect() as c:assert get(c,third)['directory']==str(legacy)
    # The archive fallback and rule path use the same collision policy.
    a=create('压缩包1');b=create('压缩包2')
    with s.db.connect() as c:
        draft=validate_archive_rule('独立压缩包',['.zip'])
        bind_raw_rule(s,c,a,title,draft);bind_raw_rule(s,c,b,title,draft)
        paths=[Path(r[0]) for r in c.execute('SELECT directory FROM task_raw_rules WHERE project_id IN (?,?) ORDER BY project_id',(a,b))]
        assert paths[0].name==title and paths[1].name.startswith(title+'_') and '_WB' not in paths[1].name
    assert raw_directory(s,a,title)==paths[0]
    # The legacy flat Word batch may contain DIFFERENT software names in one folder.
    ids=s.production.create_batch('不同软件的单Word批次',['甲独立软件','乙独立软件'],[kind],package='单手册 Word')['project_ids']
    for pid in ids:s.production.receive(pid,kind,file)
    flat=[collection_location(s,pid,kind) for pid in ids]
    assert flat[0].parent==flat[1].parent and flat[0].name!=flat[1].name
    assert not any('_2026' in p.parent.name for p in flat)
    from zipfile import ZipFile
    from unittest.mock import patch
    from zip_intake import confirm_raw
    from direct_material_intake import identity
    archive=root/'原包失败恢复系统.zip'
    with ZipFile(archive,'w') as z:z.writestr('保留.txt',b'recovery payload')
    request=[dict(path=str(archive),name=archive.stem,project_id=None,action='new',identity=identity(archive),expected=None)]
    with patch('raw_intake_engine._commit',side_effect=OSError('isolated commit failure')):failed=confirm_raw(s,request)
    assert failed['copied_count']==0 and failed['pending_count']==1
    archive.unlink()
    recovered=DesktopService(s.db.path).production.retry_import_files(failed['import_token'])
    assert recovered['copied_count']==1
    return dict(plain_folder_names_and_timestamped_same_name_tasks_are_separate=True,
                overwrite_and_repeated_receipts_keep_pinned_directories=True,
                legacy_numbered_directories_and_same_second_collisions_are_preserved=True,
                different_software_flat_word_batches_do_not_gain_timestamps=True,
                plain_archive_directory_remains_recoverable_after_commit_failure=True)
