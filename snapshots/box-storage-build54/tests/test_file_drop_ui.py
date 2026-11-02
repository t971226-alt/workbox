def test_workbench_child_surfaces_and_controller_intake(tmp_path):
    from PySide6.QtWidgets import QApplication
    from scripts.verify_office_drop import verify
    app=QApplication.instance() or QApplication([])
    assert all(verify(tmp_path/'file-drop-ui').values())
