"""Create and inspect the actual viewer using isolated illustrative records."""
from pathlib import Path
from zipfile import ZipFile


def verify(root,screenshot):
    from PySide6.QtWidgets import QApplication,QAbstractItemView
    from PySide6.QtCore import Qt
    from desktop_service import DesktopService
    from production_ui import ProductionPage
    from requirement_intake import proposal
    from saved_locations import saved_file_location
    import saved_locations
    root=Path(root);root.mkdir(parents=True,exist_ok=True);s=DesktopService(root/'data/workbox.db');s.workspaces.configure_materials(root/'材料库',complete_setup=True)
    names=['环网配电开关控制设备状态监测平台','箱式配电开关控制设备联动管控软件','高低压成套开关设备状态监测管理系统','机器人课程课件图文排版编辑软件','高压开关分合闸状态实时监测平台']
    rows=[proposal(n,batch='第二批' if i<3 else '第三批',requirement='单手册 Word' if i in (1,3) else '五件材料') for i,n in enumerate(names)]
    imported=s.production.import_plan(rows,token='master-preview');ids=imported['project_ids']
    for i,pid in enumerate(ids):
        kinds=s.production.task_kinds(pid)
        if i==2:continue
        selected=kinds[:4] if i==0 else kinds
        for kind in sorted(selected,key=lambda k:Path(k).suffix=='.pdf'):
            p=root/(names[i]+'_'+kind);p.write_bytes(('isolated '+kind).encode());s.production.receive(pid,kind,p)
        if i==1:
            m=next(r for r in s.production.rows() if r['id']==pid)['materials'][0];s.production.review(m['id'],True);s.production.submit([m['id']])
    with s.db.connect() as c:bad=Path(c.execute('SELECT path FROM collected_materials WHERE project_id=?',(ids[3],)).fetchone()[0])
    bad.write_bytes(b'externally modified copy')
    z=root/'低压配电参数配置嵌入式管控系统.zip'
    with ZipFile(z,'w') as archive:archive.writestr('readme.txt',b'isolated raw package')
    raw=s.receive_interactive([str(z)],zip_mode='original');page=ProductionPage(s);page.enable_workspace();page.master_ledger_button.click();d=page.master_ledger_dialog
    QApplication.processEvents()
    try:
        assert d.table.rowCount()==6 and d.table.editTriggers()==QAbstractItemView.NoEditTriggers
        for i in range(d.table.rowCount()):
            if d.table.item(i,0).data(Qt.UserRole)==ids[0]:d.table.selectRow(i);break
        d.show_details();assert d.selected()['id']==ids[0] and d.materials.count()==5
        # Select a saved role; verify the exact published file, without opening Explorer during verification.
        for i in range(d.materials.count()):
            kind=d.materials.item(i).data(Qt.UserRole)[1]
            if next(m for m in d.selected()['materials'] if m['kind']==kind).get('collection_saved'):d.materials.setCurrentRow(i);break
        expected=saved_file_location(s,ids[0],kind=d.materials.currentItem().data(Qt.UserRole)[1]);opened=[];original=saved_locations.open_location
        saved_locations.open_location=lambda path:opened.append(Path(path))
        try:d.locate_button.click();d.directory_button.click()
        finally:saved_locations.open_location=original
        assert opened==[expected,expected.parent]
        d.search.setText('WB-'+f'{ids[0]:06d}');assert d.table.rowCount()==1
        out=root/'筛选总表.csv';d.export_to(out);assert ids[0] and names[0] in out.read_text(encoding='utf-8-sig') and names[1] not in out.read_text(encoding='utf-8-sig')
        d.reset();d.table.sortItems(0,Qt.AscendingOrder)
        for i in range(d.table.rowCount()):
            if d.table.item(i,0).data(Qt.UserRole)==ids[0]:d.table.selectRow(i);break
        d.show_details();d.resize(1180,720);QApplication.processEvents();assert d.grab().save(str(screenshot))
        assert '异常 1' in d.statistics.text()
    finally:d.close();page.close()
    return dict(master_ledger_view_and_live_filters=True,master_ledger_locates_actual_saved_file=True,master_ledger_exports_filtered_rows=True)
