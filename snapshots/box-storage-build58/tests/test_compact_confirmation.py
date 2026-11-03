def test_compact_confirmation_round_trip_and_file_safety(tmp_path):
    from scripts.verify_compact_confirmation import verify
    assert all(verify(tmp_path/'compact').values())
