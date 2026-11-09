import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "param_admin"


@pytest.fixture
def stale_copy():
    spec = importlib.util.spec_from_file_location("stale_copy", APP_DIR / "stale_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def layout(tmp_path):
    """A repo-shaped folder: src/bakehouse_core and apps/param_admin/bakehouse_core."""
    source = tmp_path / "src" / "bakehouse_core"
    source.mkdir(parents=True)
    (source / "a.py").write_text("x = 1\n")
    app_dir = tmp_path / "apps" / "param_admin"
    shutil.copytree(source, app_dir / "bakehouse_core")
    return app_dir


def test_an_identical_copy_is_fine(stale_copy, layout):
    stale_copy.check(layout, env={})


def test_no_copy_is_fine(stale_copy, layout):
    shutil.rmtree(layout / "bakehouse_core")
    stale_copy.check(layout, env={})


def test_a_changed_or_extra_file_is_refused(stale_copy, layout):
    (layout / "bakehouse_core" / "a.py").write_text("x = 2\n")
    with pytest.raises(SystemExit, match="old code"):
        stale_copy.check(layout, env={})
    (layout / "bakehouse_core" / "a.py").write_text("x = 1\n")
    (layout / "bakehouse_core" / "b.py").write_text("")
    with pytest.raises(SystemExit, match="old code"):
        stale_copy.check(layout, env={})


def test_pycache_is_ignored(stale_copy, layout):
    cache = layout / "bakehouse_core" / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.pyc").write_bytes(b"x")
    stale_copy.check(layout, env={})


def test_skipped_on_databricks_where_the_copy_is_the_point(stale_copy, layout):
    (layout / "bakehouse_core" / "a.py").write_text("x = 2\n")
    stale_copy.check(layout, env={"DATABRICKS_APP_PORT": "8000"})


def test_skipped_when_there_is_no_src_next_to_the_app(stale_copy, layout):
    shutil.rmtree(layout.parents[1] / "src")
    stale_copy.check(layout, env={})


def test_the_real_staged_copy_is_current():
    # The copy is committed so the app can run from the Git folder. Fails if it is missing or
    # out of date with src/: run `python scripts/stage_app.py` and commit the result.
    assert (APP_DIR / "bakehouse_core").is_dir(), "run python scripts/stage_app.py"
    spec = importlib.util.spec_from_file_location("stale_copy", APP_DIR / "stale_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check(APP_DIR, env={})
