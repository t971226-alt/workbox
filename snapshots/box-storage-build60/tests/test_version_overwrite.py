def test_four_same_name_actions(tmp_path):
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    from scripts.verify_version_overwrite import verify
    assert all(verify(tmp_path).values())


def test_enter_does_not_choose_destructive_replacement():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    from duplicate_intake_ui import DuplicateMaterialDialog
    app=QApplication.instance() or QApplication([])
    d=DuplicateMaterialDialog(dict(name='键盘验证系统',files=[],candidates=[dict(
        id=1,batch_name='键盘验证',production_state='已收齐',materials=[])]))
    d.show();app.processEvents();d.tasks.setFocus()
    QTest.keyClick(d,Qt.Key_Return);app.processEvents()
    assert d.decision is None
    d.replace_button.click();assert d.decision['action']=='replace'
    d.close()
