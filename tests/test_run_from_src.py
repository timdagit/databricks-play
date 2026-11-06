import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def init():
    # jobs/ is a plain folder, not a package, so load the file by path.
    spec = importlib.util.spec_from_file_location("run_from_src", ROOT / "jobs" / "run_from_src.py")
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


def test_callbacks_unpickle_in_a_process_without_src(init, tmp_path):
    # What foreachBatch does: pickle a handler here, load it in a fresh worker that has no `src`.
    from pyspark import cloudpickle

    import bakehouse_core.jobs.silver_build as silver_build

    stats_dir = str(tmp_path / "stats")

    def handle(batch_id):
        silver_build.write_batch_stats(stats_dir, "sales", batch_id, {"rows_in": 1})

    init.pickle_package_by_value()
    try:
        blob = cloudpickle.dumps(handle)
    finally:
        cloudpickle.unregister_pickle_by_value(__import__("bakehouse_core"))
    pickle_file = tmp_path / "handle.pkl"
    pickle_file.write_bytes(blob)

    worker = (
        "import pickle, sys\n"
        f"sys.path[:] = [p for p in sys.path if {str(ROOT / 'src')!r} not in p]\n"
        f"pickle.loads(open({str(pickle_file)!r}, 'rb').read())(7)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", worker], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "stats" / "sales-000007.json").exists()


def test_finds_src_when_run_the_way_serverless_runs_it():
    # Serverless execs the compiled source, so `__file__` is not defined.
    path = ROOT / "jobs" / "run_from_src.py"
    namespace = {"__name__": "not_main"}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    assert "__file__" not in namespace
    assert namespace["src_dir"]() == ROOT / "src"
