"""Exercise real saved files, daily deduplication and the visible drilldown."""
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from zipfile import ZipFile
from unittest.mock import patch


def verify(root):
    from PySide6.QtWidgets import QApplication
    from desktop_service import DesktopService
    from production_ui import ProductionPage
    from intake_chat_ui import IntakeConversationPage
    from daily_receipts import snapshot,today
    from workspace_queries import parse_query,answer
    from theme import install_theme,set_theme
    from material_templates import PRESETS
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'saved',complete_setup=True);install_theme(s)
    def file(name,data):p=root/name;p.write_bytes(data);return p
    ids=[]
    for name,package,kinds in [('电子元器件成品性能测试管理系统','五件材料',PRESETS['五件材料']),
                               ('集成电路元器件电性能检测管理系统','五件材料',PRESETS['五件材料']),
                               ('全源人才聚合研判招聘系统','单手册 Word',['软件说明书.docx'])]:
        pid=s.production.create_batch(name,[name],kinds,package=package)['project_ids'][0];ids.append(pid)
        for i,kind in enumerate(kinds):s.production.receive(pid,kind,file(name+'-'+kind,(name+str(i)).encode()))
    raw=root/'软件运行包系统.zip'
    with ZipFile(raw,'w') as z:z.writestr('原件.txt',b'raw')
    rawid=s.receive_interactive([str(raw)],zip_mode='original')['project_ids'][0]
    # Another version of the same Word is still one material today.
    s.production.receive(ids[2],'软件说明书.docx',file('更新文档.docx',b'updated'))
    data=snapshot(s);assert (data['materials'],data['software'])==(12,4),data
    assert {c['title']:(c['materials'],c['software']) for c in data['categories']}=={'五件材料':(10,2),'单 Word':(1,1),'压缩包收纳':(1,1)}
    assert any(v['raw'] for v in data['entries']) and len(s.production.history(ids[2],'软件说明书.docx'))==2
    text=answer(s.production,parse_query('今天收录了多少材料'))
    assert '12 份材料' in text['response'] and text['spec']['intent']=='daily_receipts'
    ledger=ProductionPage(s);page=IntakeConversationPage(s);page.attach_ledger(ledger);page.query_requested.connect(ledger.apply_query)
    page.resize(1050,750);page.show()
    for _ in range(8):app.processEvents()
    summary=page.daily_summary;assert len(summary.category_buttons)==3 and '12 份材料' in summary.total.text()
    summary.total.click();app.processEvents();detail=ledger.daily_receipts_page
    assert detail.isVisible() and detail.table.rowCount()==12 and not ledger.task_workspace.isVisible()
    word=next(b for b in summary.category_buttons if '单 Word' in b.text());word.click();app.processEvents()
    assert detail.table.rowCount()==1 and detail.entries[0]['project_id']==ids[2]
    with patch('daily_receipts_ui.QDesktopServices.openUrl',return_value=True) as opened:
        detail.open_location();assert Path(opened.call_args.args[0].toLocalFile())==Path(detail.entries[0]['path']).parent
        detail.open_file(0);assert Path(opened.call_args.args[0].toLocalFile()).is_file()
    detail.back.click();app.processEvents();assert ledger.task_workspace.isVisible() and not detail.isVisible()
    composer_frame=page.composer.parentWidget();assert composer_frame.geometry().bottom()>page.scroll.geometry().bottom()
    assert summary.height()<200,summary.height()
    # Capture the actual native surface in both themes, with the preserved composer.
    cid=page.ensure_conversation('今日材料查询')
    tid=page.store.begin_query(cid,'今天收录多少材料',parse_query('今天收录多少材料'))
    page.store.run_query(tid);page.refresh_turns()
    for _ in range(6):app.processEvents()
    summary.total.click();app.processEvents();page.grab().save(str(root/'今日分类统计-浅色.png'))
    set_theme('dark',persist=False);app.processEvents();page.grab().save(str(root/'今日分类统计-深色.png'));set_theme('light',persist=False)
    # Deleting a task removes it from statistics without deleting source bytes.
    s.production.delete_tasks([rawid],confirmed=True);assert snapshot(s)['materials']==11 and raw.exists()
    s.production.restore_tasks([rawid]);assert snapshot(s)['materials']==12
    # Modified raw archives do not continue to count as successful saved materials.
    v=next(v for v in snapshot(s)['entries'] if v['raw']);Path(v['path']).write_bytes(b'tampered')
    assert snapshot(s)['materials']==11
    # Local midnight uses China time, not the UTC date, for both versions and totals.
    local_start=datetime.combine(date.fromisoformat(today()),datetime.min.time(),tzinfo=timezone(timedelta(hours=8)))
    before=(local_start-timedelta(seconds=1)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    after=local_start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with s.db.connect() as c:
        c.execute('UPDATE material_versions SET created_at=?',(before,))
        c.execute('UPDATE original_zip_receipts SET created_at=?',(before,))
        vid=c.execute('SELECT MAX(id) FROM material_versions WHERE project_id=?',(ids[2],)).fetchone()[0]
        c.execute('UPDATE material_versions SET created_at=? WHERE id=?',(after,vid))
    assert snapshot(s)['materials']==1
    summary.refresh();assert '1 份材料' in summary.total.text() and len(summary.category_buttons)==1
    tomorrow=(date.fromisoformat(today())+timedelta(days=1)).isoformat()
    assert snapshot(s,tomorrow)['materials']==0
    # A damaged latest Word must not fall back to an older version from today.
    from saved_locations import collection_location
    published=collection_location(s,ids[2],'软件说明书.docx');published.write_bytes(b'bad latest')
    assert snapshot(s)['materials']==0
    # New custom rules appear automatically; excess categories use one compact menu.
    for n in range(5):
        pid=s.production.create_batch('自定义批次'+str(n),['自定义软件'+str(n)],['文档.pdf'],package='自定义清单'+str(n))['project_ids'][0]
        s.production.receive(pid,'文档.pdf',file('额外'+str(n)+'.pdf',b'pdf'+bytes([n])))
    summary.refresh();assert len(summary.category_buttons)==5 and summary.category_buttons[-1].menu() is not None
    assert summary.height()<220
    page.shutdown();page.close();page.deleteLater();app.processEvents();install_theme()
    return dict(daily_receipts_count_materials_and_software_by_actual_rule=True,
                daily_receipts_deduplicate_versions_and_include_raw_archives=True,
                daily_summary_drills_down_to_exact_files_and_locations=True,
                compact_summary_preserves_bottom_composer_and_chat_history=True,
                daily_receipts_respect_china_midnight_and_deleted_or_damaged_files=True)
