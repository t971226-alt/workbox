def test_simple_rules_and_first_receipt(tmp_path):
    from scripts.verify_simple_rules import verify
    assert all(verify(tmp_path).values())
