import shutil

import pytest
import yaml

from datamind.config import ConfigError, list_datasets, load_dataset
from tests.conftest import REPO


def test_tpch_config_loads(tpch):
    assert tpch.dataset == "tpch_sf1"
    assert tpch.snowflake.database == "SNOWFLAKE_SAMPLE_DATA"
    assert tpch.snowflake.schema_ == "TPCH_SF1"
    assert set(tpch.tables) == {
        "REGION",
        "NATION",
        "CUSTOMER",
        "ORDERS",
        "LINEITEM",
        "PART",
        "SUPPLIER",
        "PARTSUPP",
    }
    assert "L_EXTENDEDPRICE" in tpch.tables["LINEITEM"].columns
    assert tpch.metrics["revenue"].sql == "SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT))"
    assert len(tpch.examples) >= 3


def test_list_datasets():
    assert "tpch_sf1" in list_datasets(REPO / "datasets")


def test_unknown_dataset_names_available(tmp_path):
    with pytest.raises(ConfigError, match="available"):
        load_dataset("nope", root=REPO / "datasets")


def _copy_with(tmp_path, mutate):
    dst = tmp_path / "ds"
    shutil.copytree(REPO / "datasets" / "tpch_sf1", dst)
    data = yaml.safe_load((dst / "dataset.yaml").read_text())
    mutate(data)
    (dst / "dataset.yaml").write_text(yaml.safe_dump(data))
    return dst


def test_join_to_unknown_column_is_rejected(tmp_path):
    ds = _copy_with(tmp_path, lambda d: d["joins"].append("ORDERS.O_NOPE = CUSTOMER.C_CUSTKEY"))
    with pytest.raises(ConfigError, match="O_NOPE"):
        load_dataset(ds)


def test_unknown_field_is_rejected(tmp_path):
    ds = _copy_with(tmp_path, lambda d: d.update(colour="blue"))
    with pytest.raises(ConfigError, match="colour"):
        load_dataset(ds)


def test_default_must_name_a_known_metric(tmp_path):
    ds = _copy_with(
        tmp_path,
        lambda d: d["defaults"].append({"term": "x", "assumption": "y", "metric": "profit"}),
    )
    with pytest.raises(ConfigError, match="profit"):
        load_dataset(ds)
