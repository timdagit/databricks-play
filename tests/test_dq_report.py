import json

from bakehouse_core.dq.report import parse_silver_details

DETAILS = json.dumps(
    {
        "transactions": {
            "batches": [
                {
                    "rows_in": 100,
                    "null_rates": {"paymentMethod": 0.25},
                    "applied": 80,
                    "quarantined": 15,
                    "reasons": {"null_rate_exceeded:paymentMethod": 10, "non_positive_quantity": 5},
                }
            ]
        },
        "customers": {"batches": [{"days": ["2024-05-01"], "dropped_null_key": 0}]},
        "suppliers": {"batches": [{"rows_in": 5, "applied": 5}]},
    }
)


def test_batch_rows_count_what_silver_did_not_take():
    batches, _ = parse_silver_details(DETAILS)
    tx = next(b for b in batches if b["entity"] == "transactions")
    assert (tx["rows_in"], tx["applied"], tx["quarantined"], tx["already_seen"]) == (100, 80, 15, 5)
    assert tx["reasons"] == "non_positive_quantity=5, null_rate_exceeded:paymentMethod=10"


def test_scd2_batches_have_no_applied_count():
    batches, _ = parse_silver_details(DETAILS)
    customers = next(b for b in batches if b["entity"] == "customers")
    assert customers["rows_in"] is None
    assert customers["applied"] is None
    assert customers["already_seen"] is None
    assert customers["quarantined"] == 0


def test_null_rates_are_flattened():
    _, nulls = parse_silver_details(DETAILS)
    assert nulls == [
        {"entity": "transactions", "batch": 0, "column": "paymentMethod", "null_rate": 0.25}
    ]


def test_unusable_details_give_no_rows():
    for details in (None, "", "not json", "[1, 2]", json.dumps({"error": "Boom: failed"})):
        assert parse_silver_details(details) == ([], [])
