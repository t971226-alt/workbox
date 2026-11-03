def test_folder_naming_receipts_and_duplicates(tmp_path):
    from scripts.verify_folder_naming import verify
    assert all(verify(tmp_path).values())
