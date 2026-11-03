def test_dialog_sizes_actions_expansion_and_scroll(tmp_path):
    from PySide6.QtWidgets import QApplication
    from scripts.verify_dialog_density import verify
    app=QApplication.instance() or QApplication([])
    assert all(verify(tmp_path/'dialog-previews').values())
