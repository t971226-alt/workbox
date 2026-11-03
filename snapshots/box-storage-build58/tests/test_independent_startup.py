from PySide6.QtWidgets import QApplication


def test_independent_lifecycle_and_existing_data_protection(tmp_path):
    from scripts.verify_independent_startup import verify
    app=QApplication.instance() or QApplication([])
    flags=verify(tmp_path)
    assert len(flags)==4 and all(flags.values())
