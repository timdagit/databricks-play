import os
import shutil

import pytest


@pytest.fixture(scope="session")
def spark():
    """A small local Spark session. Tests that use it are skipped when Java is missing."""
    if not (os.environ.get("JAVA_HOME") or shutil.which("java")):
        pytest.skip("Java is not installed, so local Spark is unavailable")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("bakehouse-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()
