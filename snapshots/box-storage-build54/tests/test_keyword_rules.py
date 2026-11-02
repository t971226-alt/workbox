from PySide6.QtWidgets import QApplication

def test_keyword_rule_end_to_end(tmp_path):
    from scripts.verify_keyword_rules import verify
    app=QApplication.instance() or QApplication([])
    assert all(verify(tmp_path).values())
