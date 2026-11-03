import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stage_app.py"


def load_script():
    spec = importlib.util.spec_from_file_location("stage_app", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_copies_the_package_and_replaces_a_stale_copy(tmp_path):
    stage_app = load_script()
    source = tmp_path / "src" / "pkg"
    (source / "sub").mkdir(parents=True)
    (source / "__init__.py").write_text("x = 1")
    (source / "sub" / "mod.py").write_text("y = 2")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "junk.pyc").write_text("junk")
    target = tmp_path / "app" / "pkg"
    target.mkdir(parents=True)
    (target / "stale.py").write_text("old")

    assert stage_app.stage(source, target) == 2
    assert (target / "sub" / "mod.py").read_text() == "y = 2"
    assert not (target / "stale.py").exists()
    assert not (target / "__pycache__").exists()


def test_the_real_package_stages_to_a_runnable_copy(tmp_path):
    stage_app = load_script()
    target = tmp_path / "bakehouse_core"
    assert stage_app.stage(stage_app.SOURCE, target) > 20
    assert (target / "webapp.py").exists()
    assert (target / "params" / "factory.py").exists()
