def test_working_names_back_up_rewrite_and_roll_back(tmp_path):
    from scripts.verify_working_names import verify
    assert all(verify(tmp_path).values())
