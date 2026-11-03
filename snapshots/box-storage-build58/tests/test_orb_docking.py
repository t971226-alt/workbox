import pytest
from PySide6.QtWidgets import QApplication
from scripts.verify_orb_docking import verify


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


def test_half_hidden_timer_and_interactions(app,tmp_path):
    assert all(verify(tmp_path/'orb-docking').values())
