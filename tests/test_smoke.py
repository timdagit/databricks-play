import importlib

import pytest

MODULES = ["params", "simulator", "transforms", "dq", "io", "jobs", "config"]


def test_package_imports():
    assert importlib.import_module("bakehouse_core")


@pytest.mark.parametrize("module", MODULES)
def test_submodule_imports(module):
    assert importlib.import_module(f"bakehouse_core.{module}")
