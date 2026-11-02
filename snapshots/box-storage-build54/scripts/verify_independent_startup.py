"""Exercise startup selection and a genuinely empty, separate local ledger."""
from pathlib import Path
from unittest.mock import patch
import json,sqlite3,hashlib


def verify(root):
    import portable_update as update
    import portable_update_ui as ui
    from portable_release import RELEASE_KEY,APP_ID
    from desktop_service import DesktopService
    from PySide6.QtWidgets import QApplication
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    source=root/'新版';target=root/'原BOX';index=root/'locations.json'
    def release(folder,key):
        folder.mkdir();(folder/'WorkBox.exe').write_text(json.dumps(list(key)))
        (folder/'_internal').mkdir();(folder/'_internal/runtime.dll').write_bytes(b'isolated fixture')
        files={name:dict(size=(folder/name).stat().st_size,sha256=update.digest(folder/name)) for name in ('WorkBox.exe','_internal/runtime.dll')}
        update.write_json(folder/update.MANIFEST,dict(schema=1,app_id=APP_ID,version='.'.join(map(str,key[:3])),build=key[3],files=files))
    release(source,RELEASE_KEY);release(target,(1,1,0,1))
    original=DesktopService(target/'data/workbox.db')
    original.workspaces.configure_materials(root/'原材料',complete_setup=True)
    material=root/'原材料/原件.txt';material.write_bytes(b'original bytes')
    before=hashlib.sha256((target/'data/workbox.db').read_bytes()).hexdigest()
    flags={};calls=[];imports=[target/'data/workbox.db']
    with patch.object(update,'pe_identity',side_effect=lambda p:tuple(json.loads(Path(p).read_text()))):
        update.register(target,index);old_index=index.read_bytes()
        choices=[]
        def select(title,body,options):choices.extend(options);return 'independent'
        with patch.object(ui,'choose',side_effect=select),patch.object(ui,'spawn_helper',side_effect=AssertionError('must not update')),patch.object(ui,'launch',side_effect=AssertionError('must not redirect')):
            assert ui.preflight(source,index=index,roots=[],import_sources=imports)
        assert [v for v,label in choices]==['update','independent','open']
        assert imports==[] and update.independent_instance(source)
        assert not (source/'data/workbox.db').exists()
        independent=DesktopService(source/'data/workbox.db')
        with sqlite3.connect(independent.db.path) as c:assert c.execute('select count(*) from projects').fetchone()[0]==0
        assert independent.workspaces.settings()['work_root']!=original.workspaces.settings()['work_root']
        independent.workspaces.configure_materials(root/'新材料',complete_setup=True)
        update.mark_data(source);update.register(source,index)
        assert index.read_bytes()==old_index
        # An unrelated pending update and a changed global preference cannot capture this instance.
        update.write_json(index,dict(schema=1,preferred=str(target),locations=[str(target)],pending=str(root/'unrelated.json')))
        update.write_json(root/'unrelated.json',{'unrelated':True})
        activated=[]
        with patch.object(ui,'choose',side_effect=AssertionError('must not prompt again')),patch.object(ui,'discover',side_effect=AssertionError('must not discover other installs')),patch.object(ui,'activate_existing',side_effect=lambda folder,**kw:activated.append(folder) or False):
            assert ui.preflight(source,index=index,roots=[],import_sources=imports)
        assert activated==[source.resolve()] and imports==[]
        assert DesktopService(source/'data/workbox.db').workspaces.settings()['work_root']==independent.workspaces.settings()['work_root']
        assert not update.discover(target,index=index,roots=[])
        assert hashlib.sha256((target/'data/workbox.db').read_bytes()).hexdigest()==before
        assert material.read_bytes()==b'original bytes'
        flags['independent_startup_creates_empty_ledger_without_copying_old_data']=True
        flags['independent_restart_and_registration_never_redirect_or_change_original_default']=True
        # Declining the upgrade opens the original and leaves the download empty.
        another=root/'取消更新';release(another,RELEASE_KEY)
        update.write_json(index,dict(schema=1,preferred=str(target),locations=[str(target)],pending=''))
        with patch.object(ui,'choose',return_value='open'),patch.object(ui,'launch',side_effect=lambda *args,**kw:calls.append(args)):
            assert not ui.preflight(another,index=index,roots=[])
        assert calls==[(target.resolve(),)] and not (another/'data').exists()
        # The independent choice refuses to erase an existing ledger.
        existing=root/'已有数据';release(existing,RELEASE_KEY);(existing/'data').mkdir();(existing/'data/workbox.db').write_bytes(b'keep this ledger')
        try:update.create_independent_instance(existing)
        except ValueError:pass
        else:raise AssertionError('existing data must not be reset')
        assert (existing/'data/workbox.db').read_bytes()==b'keep this ledger'
        flags['startup_decline_and_independent_collision_preserve_existing_data']=True
    d=ui.ChoiceDialog('新版 BOX 怎么使用？','更新原 BOX：自动备份，保留原总表和材料。\n独立使用新版：从空总表开始，不复制旧数据。',[('update','更新原 BOX（推荐）'),('independent','独立使用新版 · 从空总表开始'),('open','暂不使用新版 · 打开原 BOX')])
    try:
        d.show();app.processEvents();assert d.width()==520 and d.height()<450
        d.grab().save(str(root/'启动选择.png'));d.buttons['independent'].click();assert d.choice=='independent'
    finally:d.close();d.deleteLater();app.processEvents()
    flags['compact_startup_choices_are_operable']=True
    return flags
