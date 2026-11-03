import pytest

from bakehouse_core.io import silver_tables as st


def test_every_entity_has_a_spec_with_its_key_among_the_columns():
    assert set(st.SPECS) == {"transactions", "reviews", "suppliers", "customers", "franchises"}
    for spec in st.SPECS.values():
        assert spec.key in spec.types


def test_only_scd2_tables_carry_history_columns():
    assert "_is_current" in st.SPECS["customers"].columns
    assert "_valid_from" in st.SPECS["franchises"].columns
    assert "_is_current" not in st.SPECS["transactions"].columns


def test_ingest_metadata_columns_are_on_every_table():
    for spec in st.SPECS.values():
        for col in ("_source_file", "_ingested_at", "_virtual_date", "_rescued_data"):
            assert col in spec.columns


def test_attributes_exclude_the_key():
    assert "customerID" not in st.SPECS["customers"].attributes
    assert "email_address" in st.SPECS["customers"].attributes


def test_create_table_sql():
    sql = st.create_table_sql("c.silver.t", {"a": "bigint", "b": "string"})
    assert sql == "CREATE TABLE IF NOT EXISTS c.silver.t (a BIGINT, b STRING)"


def test_quarantine_table_adds_the_reason_column():
    sql = st.create_quarantine_sql("c.silver.q", st.SPECS["transactions"])
    assert sql.endswith("_dq_reason STRING)")
    assert "transactionID BIGINT" in sql


def test_insert_new_only_inserts_unmatched_rows():
    sql = st.insert_new_sql("t", "v", "id", ["id", "x"])
    assert sql == (
        "MERGE INTO t t USING v s ON t.id = s.id "
        "WHEN NOT MATCHED THEN INSERT (id, x) VALUES (s.id, s.x)"
    )


def test_upsert_updates_everything_but_the_key():
    sql = st.upsert_sql("t", "v", "id", ["id", "x"])
    assert "WHEN MATCHED THEN UPDATE SET t.x = s.x " in sql
    assert "t.id = s.id," not in sql
    assert "WHEN NOT MATCHED THEN INSERT (id, x) VALUES (s.id, s.x)" in sql


def test_scd2_merge_closes_matched_and_inserts_open_rows():
    cols = ["id", "x", "_valid_from", "_valid_to", "_is_current"]
    sql = st.scd2_merge_sql("t", "v", "id", cols)
    assert "ON t.id = s._merge_key AND t._is_current" in sql
    assert "UPDATE SET t._is_current = FALSE, t._valid_to = s._valid_from" in sql
    assert "VALUES (s.id, s.x, s._valid_from, CAST(NULL AS DATE), TRUE)" in sql


def test_unsafe_identifiers_are_rejected():
    with pytest.raises(ValueError):
        st.insert_new_sql("t", "v", "id; DROP", ["id"])
    with pytest.raises(ValueError):
        st.create_table_sql("t", {"a b": "string"})
    with pytest.raises(ValueError):
        st.SilverTables(object(), "bad catalog")


def test_apply_rejects_scd2_and_missing_quarantine():
    tables = st.SilverTables(object(), "cat")
    with pytest.raises(ValueError, match="apply_scd2"):
        tables.apply(st.SPECS["customers"], object())
    with pytest.raises(ValueError, match="no quarantine"):
        tables.append_quarantine(st.SPECS["customers"], object())
