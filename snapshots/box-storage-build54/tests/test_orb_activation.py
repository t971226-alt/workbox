import pytest
from PySide6.QtWidgets import QApplication
from scripts.verify_orb_activation import verify


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


def test_single_double_click_and_native_visibility(app,tmp_path):
    assert all(verify(tmp_path/'orb-activation').values())
