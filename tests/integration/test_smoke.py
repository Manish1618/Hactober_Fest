"""Live checks. Run with: pytest -m integration (needs .env with Snowflake and a model)."""

import pytest

from datamind.model.adapter import OllamaAdapter
from datamind.settings import get_settings
from datamind.snowflake_io.executor import SnowflakeExecutor

pytestmark = pytest.mark.integration


def test_snowflake_reader_role_runs_a_query(tpch):
    s = get_settings()
    out = SnowflakeExecutor(s.snowflake, tpch).smoke()
    assert "REGION rows=5" in out


def test_model_endpoint_has_the_configured_model():
    assert "available" in OllamaAdapter(get_settings().model).ping()
