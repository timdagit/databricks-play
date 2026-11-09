"""The ETL jobs read parameters from Lakebase unless told otherwise (decisions, 2026-11-09)."""

import pytest

from bakehouse_core.jobs import gold_build, load_params


@pytest.mark.parametrize("module", [load_params, gold_build])
def test_the_parameter_backend_defaults_to_lakebase(module):
    args = module.build_parser().parse_args(["--catalog", "c"])
    assert args.param_backend == "lakebase"


@pytest.mark.parametrize("module", [load_params, gold_build])
def test_delta_can_still_be_chosen(module):
    args = module.build_parser().parse_args(["--catalog", "c", "--param-backend", "delta"])
    assert args.param_backend == "delta"
