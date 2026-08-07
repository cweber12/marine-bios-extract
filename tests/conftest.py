"""Shared pytest configuration.

Network tests are opt-in. A green default run says the logic is sound; it says
nothing about whether CDFW's file library is up, and conflating those two would
make the suite fail for reasons the code cannot fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: hits a live publisher; excluded unless asked for"
    )


@pytest.fixture
def archive(tmp_path):
    from tests.fixtures import make_archive

    return make_archive(tmp_path)


@pytest.fixture
def raster_archive(tmp_path):
    from tests.fixtures import make_raster_archive

    return make_raster_archive(tmp_path)
