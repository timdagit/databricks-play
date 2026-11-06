import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def init():
    # jobs/ is a plain folder, not a package, so load the file by path.
    spec = importlib.util.spec_from_file_location(
        "run_from_source", ROOT / "jobs" / "run_from_source.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jobs_match_the_wheel_entry_points(init):
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    expected = {
        name.removeprefix("bakehouse-"): target.split(":")[0] for name, target in scripts.items()
    }
    assert init.JOBS == expected


def test_runs_the_named_job_with_the_remaining_arguments(init, monkeypatch):
    import bakehouse_core.jobs.reset as reset

    calls = []
    monkeypatch.setattr(reset, "main", lambda argv: calls.append(argv))
    init.main(["reset", "--catalog", "x", "--level", "data"])
    assert calls == [["--catalog", "x", "--level", "data"]]


@pytest.mark.parametrize("argv", [[], ["no-such-job"]])
def test_unknown_or_missing_job_exits_with_usage(init, argv):
    with pytest.raises(SystemExit, match="usage"):
        init.main(argv)


def test_finds_src_when_run_the_way_serverless_runs_it():
    # Serverless execs the compiled source, so `__file__` is not defined.
    path = ROOT / "jobs" / "run_from_source.py"
    namespace = {"__name__": "not_main"}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    assert "__file__" not in namespace
    assert namespace["src_dir"]() == ROOT / "src"
