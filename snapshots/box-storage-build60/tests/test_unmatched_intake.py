def test_unmatched_intake_flow(tmp_path):
    from scripts.verify_unmatched_intake import verify
    assert all(verify(tmp_path).values())
