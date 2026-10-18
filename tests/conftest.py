import os

os.environ["WAYFINDER_MEMORY_PATH"] = ":memory:"
for _key in ("WAYFINDER_DEVKIT_PORT", "WAYFINDER_BOX3_PORT", "WAYFINDER_P4_PORT"):
    os.environ.pop(_key, None)

import pytest

from wayfinder.state import reset_runtime


@pytest.fixture(autouse=True)
def clean_runtime():
    reset_runtime()
    yield
    reset_runtime()
