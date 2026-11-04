"""Verify docking geometry, real timer delivery and interaction recovery."""
def verify(root):
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QPoint,QPointF,QSize,QRect,QEvent
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtTest import QTest,QSignalSpy
    from floating import FloatingBall,edge_targets
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    # Negative coordinates and adjacent monitors use the selected screen's own edge.
    for area in (QRect(0,0,1920,1040),QRect(-1920,-200,1920,1080),QRect(1920,0,2560,1400)):
        for x,side in ((area.left()+25,'left'),(area.right()-90,'right')):
            chosen,tucked,full=edge_targets(area,QPoint(x,area.bottom()),QSize(76,76))
            assert chosen==side and area.contains(QRect(full,QSize(76,76)))
            assert QRect(tucked,QSize(76,76)).intersected(area).width()==38
    orb=FloatingBall();orb.edge_animation.setDuration(0)
    assert orb.idle_timer.interval()==15000
    area=app.primaryScreen().availableGeometry();orb.move(area.left()+100,area.top()+100);orb.show()
    def leave():
        QApplication.sendEvent(orb,QEvent(QEvent.Leave))
    def dock(side):
        orb.reveal();orb.move(area.left()+50 if side=='left' else area.right()-100,area.top()+100);orb._hovered=False;orb.dock_to_edge()
        assert orb._docked_side==side
        assert orb.geometry().intersected(area).width()==38
        assert orb.mask().boundingRect()==area.translated(-orb.pos()).intersected(orb.rect())
    try:
        orb.set_summary(35)
        for side in ('left','right'):
            dock(side)
            visible=area.translated(-orb.pos()).intersected(orb.rect())
            assert visible.contains(orb.badge_rect().adjusted(-1,-1,1,1).toAlignedRect()),'Badge must stay completely on this screen'
            orb.grab().save(str(root/(side+'-half.png')))
            local=QPointF(55,38);QApplication.sendEvent(orb,QEnterEvent(local,local,QPointF(orb.mapToGlobal(QPoint(55,38)))))
            assert not orb._docked_side and area.contains(orb.geometry()) and orb.mask().isEmpty()
            assert not orb.idle_timer.isActive()
        leave();orb.idle_timer.setInterval(40);orb.restart_idle();QTest.qWait(90)
        assert orb._docked_side,'The actual idle timer must dock the orb'
        orb.idle_timer.setInterval(15000)
        orb.reveal();orb.move(area.left()+90,area.top()+100);orb._hovered=False;orb.edge_animation.setDuration(80);orb.dock_to_edge();QTest.qWait(120)
        assert orb.geometry().intersected(area).width()==38 and orb.mask().boundingRect().width()==38
        orb.edge_animation.setDuration(0)
        orb.set_drop_hint('files');assert not orb._docked_side and not orb.idle_timer.isActive()
        orb.set_drop_hint('');leave();assert orb.idle_timer.isActive()
        orb.set_progress(True,10);orb.dock_to_edge();assert not orb._docked_side and not orb.idle_timer.isActive()
        orb.set_progress(False);assert orb.idle_timer.isActive()
        # Routine silent refresh must not restart the user's inactivity countdown.
        orb.idle_timer.setInterval(200);orb.restart_idle();QTest.qWait(25);remaining=orb.idle_timer.remainingTime();orb.set_progress(False)
        assert orb.idle_timer.remainingTime()<=remaining+2
        orb.idle_timer.setInterval(15000)
        orb._menu_open=True;orb.dock_to_edge();assert not orb._docked_side;orb._menu_open=False
        opened=QSignalSpy(orb.open_requested);dock('left')
        QTest.mouseDClick(orb,Qt.LeftButton,pos=QPoint(55,38));assert opened.count()==1 and not orb._docked_side
        orb._hovered=False;orb._press=QPoint(10,10);orb.dock_to_edge();assert not orb._docked_side;orb._press=None
        dock('right');orb.hide();QTest.qWait(70)
        assert not orb.isVisible() and not orb.idle_timer.isActive() and not orb._docked_side
        orb.show();leave();assert orb.idle_timer.isActive()
        dock('left');orb.screen_layout_changed();assert not orb._docked_side and area.contains(orb.geometry())
    finally:orb.hide();orb.deleteLater();app.processEvents()
    return dict(orb_badge_remains_visible_on_both_half_hidden_sides=True,orb_idle_timer_half_hides_and_hover_reveals=True,
        orb_docking_preserves_click_drop_drag_menu_and_screen_bounds=True)
