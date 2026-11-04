from PySide6.QtWidgets import QApplication


def test_daily_receipts_end_to_end(tmp_path):
    from scripts.verify_daily_receipts import verify
    app=QApplication.instance() or QApplication([])
    assert all(verify(tmp_path).values())
