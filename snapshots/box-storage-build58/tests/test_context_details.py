from scripts.verify_context_details import verify
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_native_context_actions_and_provenance(tmp_path,app):
    assert all(verify(tmp_path/'context-details').values())
