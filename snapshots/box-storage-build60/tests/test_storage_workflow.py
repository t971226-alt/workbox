import pytest
from PySide6.QtWidgets import QApplication
from scripts.verify_storage_workflow import verify


def test_storage_workflow_real_operations_and_widgets(tmp_path):
    app=QApplication.instance() or QApplication([])
    flags=verify(tmp_path/'storage-workflow')
    assert len(flags)>=14 and all(flags.values())


def test_frozen_transaction_scenarios_in_source(tmp_path):
    from scripts.verify_intake_transactions import verify as transactions
    assert all(transactions(tmp_path/'transactions').values())
