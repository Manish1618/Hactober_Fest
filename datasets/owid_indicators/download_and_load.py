"""Download the Our World in Data indicators and load them into DATAMIND_DB.OWID (PRD 9.2).

Run from the repository root, with the same .env as the app (key-pair auth):

    python datasets/owid_indicators/download_and_load.py              # download and load
    python datasets/owid_indicators/download_and_load.py --dry-run    # download, print counts
    python datasets/owid_indicators/download_and_load.py --from-dir data/raw/owid  # no download

Tables are created by snowflake/02_owid_schema.sql. Each run truncates and reloads them, so it is
safe to run again. The loader uses SNOWFLAKE_LOADER_ROLE (default DATAMIND_LOADER); the app only
ever reads with DATAMIND_READER. Data is CC BY 4.0: the ATTRIBUTION table records the source.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

GRAPHER = "https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full&useColumnShortNames=false"
LICENCE = "CC BY 4.0"
RAW_DIR = Path("data/raw/owid")  # git-ignored (data/raw/)


@dataclass(frozen=True)
class Indicator:
    table: str
    slug: str


INDICATORS = [
    Indicator("LIFE_EXPECTANCY", "life-expectancy"),
    Indicator("GDP_PER_CAPITA", "gdp-per-capita-worldbank"),
    Indicator("YEARS_OF_SCHOOLING", "mean-years-of-schooling-long-run"),
    Indicator("POPULATION", "population"),
    Indicator("CO2_PER_CAPITA", "co-emissions-per-capita"),
]

# Columns in a grapher CSV that are not the indicator value.
KEY_COLUMNS = {"entity", "code", "year", "day"}
REGION_COLUMNS = {
    "owid_region",
    "world region according to owid",
    "world regions according to owid",
}
# OWID_ codes that are single (current or former) countries, not groups of countries.
OWID_COUNTRY_CODES = {
    "OWID_KOS",  # Kosovo
    "OWID_CYN",  # Northern Cyprus
    "OWID_SML",  # Somaliland
    "OWID_USS",  # USSR
    "OWID_CZS",  # Czechoslovakia
    "OWID_YGS",  # Yugoslavia
    "OWID_SRM",  # Serbia and Montenegro
}
CODE_WIDTH = 8  # COUNTRY_CODE is VARCHAR(8)
FIRST_YEAR, LAST_YEAR = 1, 9999  # YEAR is NUMBER(4); population estimates reach back to -10000


class LoadError(Exception):
    """A problem the person running the script can fix; printed without a traceback."""


def aggregate_code(name: str) -> str:
    """Stable 8-character code for an aggregate OWID publishes without one (e.g. Europe)."""
    return "AGG" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:5].upper()


def _find(columns: list[str], wanted: str) -> str | None:
    return next((c for c in columns if c.strip().lower() == wanted), None)


def reshape(csv_text: str, table: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """One grapher CSV -> (values, countries, indicator name).

    values: COUNTRY_CODE, YEAR, VALUE, IS_PROJECTED. A column whose name mentions a projection
    only fills years the historical column leaves empty, and those rows are flagged projected.
    countries: COUNTRY_CODE, COUNTRY_NAME, REGION, IS_AGGREGATE.
    """
    df = pd.read_csv(io.StringIO(csv_text))
    cols = list(df.columns)
    entity, code, year = (_find(cols, k) for k in ("entity", "code", "year"))
    if not (entity and year):
        raise LoadError(f"{table}: CSV has no Entity/Year columns (got {cols[:6]})")
    region = next((c for c in cols if c.strip().lower() in REGION_COLUMNS), None)
    value_cols = [
        c
        for c in cols
        if c.strip().lower() not in KEY_COLUMNS | REGION_COLUMNS
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    projected = [c for c in value_cols if "projection" in c.lower()]
    historical = [c for c in value_cols if c not in projected]
    if not historical and not projected:
        raise LoadError(f"{table}: CSV has no numeric value column (got {cols})")

    df["_name"] = df[entity].astype(str).str.strip()
    raw_code = df[code] if code else pd.Series([None] * len(df))
    # Missing codes and codes too long for VARCHAR(8) (such as OWID_EU27) get a stable AGG code.
    df["_code"] = [
        (c.strip() if len(c.strip()) <= CODE_WIDTH else aggregate_code(c.strip()))
        if isinstance(c, str) and c.strip()
        else aggregate_code(n)
        for c, n in zip(raw_code, df["_name"], strict=True)
    ]
    df["_aggregate"] = [
        not (isinstance(c, str) and c.strip())
        or (c.strip().startswith("OWID_") and c.strip() not in OWID_COUNTRY_CODES)
        for c in raw_code
    ]
    hist = df[historical[0]] if historical else pd.Series([float("nan")] * len(df))
    proj = df[projected[0]] if projected else pd.Series([float("nan")] * len(df))
    values = pd.DataFrame(
        {
            "COUNTRY_CODE": df["_code"],
            "YEAR": pd.to_numeric(df[year], errors="coerce"),
            "VALUE": hist.where(hist.notna(), proj),
            "IS_PROJECTED": hist.isna() & proj.notna(),
        }
    )
    values = values.dropna(subset=["YEAR", "VALUE"]).astype({"YEAR": "int64"})
    values = values[values["YEAR"].between(FIRST_YEAR, LAST_YEAR)]
    values = values.drop_duplicates(["COUNTRY_CODE", "YEAR"]).reset_index(drop=True)

    # OWID puts the region on one row per country only; take the first non-empty one.
    regions = df[region] if region else pd.Series([None] * len(df))
    countries = (
        pd.DataFrame(
            {
                "COUNTRY_CODE": df["_code"],
                "COUNTRY_NAME": df["_name"],
                "REGION": regions.where(regions.notna(), None),
                "IS_AGGREGATE": df["_aggregate"],
            }
        )
        .sort_values("REGION", na_position="last")
        .drop_duplicates("COUNTRY_CODE")
    )
    countries.loc[countries["IS_AGGREGATE"], "REGION"] = None
    countries = countries.sort_values("COUNTRY_CODE").reset_index(drop=True)
    name = historical[0] if historical else projected[0]
    return values, countries, name


def merge_countries(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """One COUNTRY row per code across all indicators, keeping a region when any CSV has one."""
    allc = pd.concat(frames, ignore_index=True).sort_values("REGION", na_position="last")
    return allc.drop_duplicates("COUNTRY_CODE").sort_values("COUNTRY_CODE").reset_index(drop=True)


def download(ind: Indicator, raw_dir: Path) -> str:
    import httpx

    url = GRAPHER.format(slug=ind.slug)
    try:
        r = httpx.get(url, timeout=120, follow_redirects=True, headers={"User-Agent": "DataMind"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LoadError(
            f"could not download {ind.table} from {url}: {type(e).__name__}: {e}. If this "
            f"network blocks ourworldindata.org, download the CSV in a browser into {raw_dir} "
            f"as {ind.slug}.csv and rerun with --from-dir {raw_dir}"
        ) from e
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{ind.slug}.csv").write_text(r.text, encoding="utf-8")
    return r.text


def read_local(ind: Indicator, raw_dir: Path) -> str:
    path = raw_dir / f"{ind.slug}.csv"
    if not path.is_file():
        raise LoadError(f"{path} not found; download {GRAPHER.format(slug=ind.slug)} there")
    return path.read_text(encoding="utf-8")


def connect():
    """Loader connection from the same .env as the app. Secrets are never printed."""
    import snowflake.connector

    from datamind.settings import get_settings

    s = get_settings().snowflake
    if not s.configured:
        raise LoadError(
            "Snowflake is not configured. Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and "
            "SNOWFLAKE_PRIVATE_KEY_PATH in .env."
        )
    params = {
        "account": s.account,
        "user": s.user,
        "private_key_file": s.private_key_path,
        "role": s.loader_role,
        "warehouse": s.warehouse,
        "database": "DATAMIND_DB",
        "schema": "OWID",
    }
    if s.private_key_passphrase:
        params["private_key_file_pwd"] = s.private_key_passphrase
    try:
        return snowflake.connector.connect(**params)
    except Exception as e:  # connector raises many types; report the kind, not the parameters
        raise LoadError(
            f"could not connect to Snowflake as {s.loader_role}: {type(e).__name__}"
        ) from e


def load(conn, tables: dict[str, pd.DataFrame]) -> None:
    from snowflake.connector.pandas_tools import write_pandas

    cur = conn.cursor()
    for table, df in tables.items():
        cur.execute(f"TRUNCATE TABLE IF EXISTS {table}")  # fixed table names from this file
        ok, _, rows, _ = write_pandas(conn, df, table, quote_identifiers=False)
        if not ok or rows != len(df):
            raise LoadError(f"{table}: loaded {rows} of {len(df)} rows")
        print(f"loaded {table}: {rows} rows")


def build_tables(texts: dict[str, str], downloaded_at: datetime) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    countries, attribution = [], []
    for ind in INDICATORS:
        values, c, name = reshape(texts[ind.table], ind.table)
        tables[ind.table] = values
        countries.append(c)
        attribution.append(
            {
                "TABLE_NAME": ind.table,
                "INDICATOR": name,
                "SOURCE_URL": f"https://ourworldindata.org/grapher/{ind.slug}",
                "LICENCE": LICENCE,
                "DOWNLOADED_AT": downloaded_at.replace(tzinfo=None),
            }
        )
    tables["COUNTRY"] = merge_countries(countries)
    tables["ATTRIBUTION"] = pd.DataFrame(attribution)
    return tables


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="download and print row counts only")
    p.add_argument("--from-dir", type=Path, help="load CSVs already downloaded to this folder")
    p.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="where downloads are saved")
    args = p.parse_args(argv)
    try:
        texts = {
            ind.table: read_local(ind, args.from_dir)
            if args.from_dir
            else download(ind, args.raw_dir)
            for ind in INDICATORS
        }
        tables = build_tables(texts, datetime.now(UTC))
        for table, df in tables.items():
            extra = ""
            if "YEAR" in df:
                extra = f", years {df['YEAR'].min()}-{df['YEAR'].max()}, "
                extra += f"{int(df['IS_PROJECTED'].sum())} projected"
            print(f"{table}: {len(df)} rows{extra}")
        if args.dry_run:
            print("dry run: nothing loaded")
            return 0
        conn = connect()
        try:
            load(conn, tables)
        finally:
            conn.close()
    except LoadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("done. Data: Our World in Data, CC BY 4.0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
