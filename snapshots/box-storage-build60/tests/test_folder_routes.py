def test_folder_rule_routing(tmp_path):
    from scripts.verify_folder_routes import verify
    assert all(verify(tmp_path).values())
