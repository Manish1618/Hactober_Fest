"""Tiny deterministic TPC-H-shaped database in DuckDB, for offline development and tests only.

Same table and column names as SNOWFLAKE_SAMPLE_DATA.TPCH_SF1 but a few hundred synthetic rows.
Numbers from this fixture are never real answers; the CLI labels them as fixture output.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
# The 25 nations and their regions as defined by the TPC-H specification.
NATIONS = [
    ("ALGERIA", 0),
    ("ARGENTINA", 1),
    ("BRAZIL", 1),
    ("CANADA", 1),
    ("EGYPT", 4),
    ("ETHIOPIA", 0),
    ("FRANCE", 3),
    ("GERMANY", 3),
    ("INDIA", 2),
    ("INDONESIA", 2),
    ("IRAN", 4),
    ("IRAQ", 4),
    ("JAPAN", 2),
    ("JORDAN", 4),
    ("KENYA", 0),
    ("MOROCCO", 0),
    ("MOZAMBIQUE", 0),
    ("PERU", 1),
    ("CHINA", 2),
    ("ROMANIA", 3),
    ("SAUDI ARABIA", 4),
    ("VIETNAM", 2),
    ("RUSSIA", 3),
    ("UNITED KINGDOM", 3),
    ("UNITED STATES", 1),
]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
SHIPMODES = ["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"]

DDL = """
CREATE TABLE REGION (R_REGIONKEY INTEGER, R_NAME VARCHAR, R_COMMENT VARCHAR);
CREATE TABLE NATION (N_NATIONKEY INTEGER, N_NAME VARCHAR, N_REGIONKEY INTEGER, N_COMMENT VARCHAR);
CREATE TABLE CUSTOMER (C_CUSTKEY INTEGER, C_NAME VARCHAR, C_ADDRESS VARCHAR, C_NATIONKEY INTEGER,
  C_PHONE VARCHAR, C_ACCTBAL DECIMAL(15,2), C_MKTSEGMENT VARCHAR, C_COMMENT VARCHAR);
CREATE TABLE SUPPLIER (S_SUPPKEY INTEGER, S_NAME VARCHAR, S_ADDRESS VARCHAR, S_NATIONKEY INTEGER,
  S_PHONE VARCHAR, S_ACCTBAL DECIMAL(15,2), S_COMMENT VARCHAR);
CREATE TABLE PART (P_PARTKEY INTEGER, P_NAME VARCHAR, P_MFGR VARCHAR, P_BRAND VARCHAR,
  P_TYPE VARCHAR, P_SIZE INTEGER, P_CONTAINER VARCHAR, P_RETAILPRICE DECIMAL(15,2),
  P_COMMENT VARCHAR);
CREATE TABLE PARTSUPP (PS_PARTKEY INTEGER, PS_SUPPKEY INTEGER, PS_AVAILQTY INTEGER,
  PS_SUPPLYCOST DECIMAL(15,2), PS_COMMENT VARCHAR);
CREATE TABLE ORDERS (O_ORDERKEY INTEGER, O_CUSTKEY INTEGER, O_ORDERSTATUS VARCHAR,
  O_TOTALPRICE DECIMAL(15,2), O_ORDERDATE DATE, O_ORDERPRIORITY VARCHAR, O_CLERK VARCHAR,
  O_SHIPPRIORITY INTEGER, O_COMMENT VARCHAR);
CREATE TABLE LINEITEM (L_ORDERKEY INTEGER, L_PARTKEY INTEGER, L_SUPPKEY INTEGER,
  L_LINENUMBER INTEGER, L_QUANTITY DECIMAL(15,2), L_EXTENDEDPRICE DECIMAL(15,2),
  L_DISCOUNT DECIMAL(15,2), L_TAX DECIMAL(15,2), L_RETURNFLAG VARCHAR, L_LINESTATUS VARCHAR,
  L_SHIPDATE DATE, L_COMMITDATE DATE, L_RECEIPTDATE DATE, L_SHIPINSTRUCT VARCHAR,
  L_SHIPMODE VARCHAR, L_COMMENT VARCHAR);
"""


def build_fixture(
    seed: int = 7, customers: int = 30, suppliers: int = 10, parts: int = 20, orders: int = 200
):
    import duckdb

    rng = random.Random(seed)
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    con.executemany("INSERT INTO REGION VALUES (?, ?, '')", list(enumerate(REGIONS)))
    con.executemany(
        "INSERT INTO NATION VALUES (?, ?, ?, '')",
        [(i, n, r) for i, (n, r) in enumerate(NATIONS)],
    )
    con.executemany(
        "INSERT INTO CUSTOMER VALUES (?, ?, '', ?, '', ?, ?, '')",
        [
            (
                k,
                f"Customer#{k:09d}",
                rng.randrange(25),
                round(rng.uniform(-999, 9999), 2),
                rng.choice(SEGMENTS),
            )
            for k in range(1, customers + 1)
        ],
    )
    con.executemany(
        "INSERT INTO SUPPLIER VALUES (?, ?, '', ?, '', ?, '')",
        [
            (k, f"Supplier#{k:09d}", rng.randrange(25), round(rng.uniform(-999, 9999), 2))
            for k in range(1, suppliers + 1)
        ],
    )
    part_price = {k: round(rng.uniform(900, 2000), 2) for k in range(1, parts + 1)}
    con.executemany(
        "INSERT INTO PART VALUES (?, ?, ?, ?, 'STANDARD POLISHED BRASS', ?, 'SM BOX', ?, '')",
        [
            (
                k,
                f"part {k}",
                f"Manufacturer#{1 + k % 5}",
                f"Brand#{1 + k % 5}{1 + k % 4}",
                1 + k % 50,
                p,
            )
            for k, p in part_price.items()
        ],
    )
    con.executemany(
        "INSERT INTO PARTSUPP VALUES (?, ?, ?, ?, '')",
        [
            (p, s, rng.randrange(1, 9999), round(rng.uniform(1, 1000), 2))
            for p in part_price
            for s in rng.sample(range(1, suppliers + 1), 2)
        ],
    )
    start = date(1992, 1, 1)
    span = (date(1998, 8, 2) - start).days
    order_rows, line_rows = [], []
    for ok in range(1, orders + 1):
        odate = start + timedelta(days=rng.randrange(span))
        total = 0.0
        for ln in range(1, rng.randint(1, 4) + 1):
            pk = rng.randrange(1, parts + 1)
            qty = rng.randint(1, 50)
            ext = round(qty * part_price[pk], 2)
            disc = rng.randint(0, 10) / 100
            tax = rng.randint(0, 8) / 100
            ship = odate + timedelta(days=rng.randint(1, 120))
            commit = odate + timedelta(days=rng.randint(30, 90))
            receipt = ship + timedelta(days=rng.randint(1, 30))
            total += ext * (1 - disc) * (1 + tax)
            line_rows.append(
                (
                    ok,
                    pk,
                    rng.randrange(1, suppliers + 1),
                    ln,
                    qty,
                    ext,
                    disc,
                    tax,
                    rng.choice("RAN"),
                    rng.choice("OF"),
                    ship,
                    commit,
                    receipt,
                    "NONE",
                    rng.choice(SHIPMODES),
                )
            )
        order_rows.append(
            (
                ok,
                rng.randrange(1, customers + 1),
                rng.choice("FOP"),
                round(total, 2),
                odate,
                rng.choice(PRIORITIES),
                f"Clerk#{rng.randrange(1, 100):09d}",
                0,
            )
        )
    con.executemany("INSERT INTO ORDERS VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')", order_rows)
    con.executemany(
        "INSERT INTO LINEITEM VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')", line_rows
    )
    return con


# ---------- Our World in Data (datasets/owid_indicators) ----------

# (code, name, region, aggregate, life expectancy 2000, yearly gain, GDP per capita 2000,
#  yearly growth, years of schooling 2000, population 2000 in millions, CO2 t per person 2000)
OWID_COUNTRIES = [
    ("JPN", "Japan", "Asia", False, 81.1, 0.16, 33000, 0.008, 11.5, 127.0, 9.6),
    ("CHE", "Switzerland", "Europe", False, 79.8, 0.19, 52000, 0.011, 12.4, 7.2, 6.2),
    ("AUS", "Australia", "Oceania", False, 79.3, 0.19, 38000, 0.014, 12.1, 19.0, 18.2),
    ("NOR", "Norway", "Europe", False, 78.7, 0.21, 61000, 0.010, 12.0, 4.5, 8.9),
    ("DEU", "Germany", "Europe", False, 78.0, 0.17, 41000, 0.010, 13.0, 81.4, 10.1),
    ("USA", "United States", "North America", False, 76.7, 0.11, 49000, 0.013, 12.9, 282.0, 20.5),
    ("CHN", "China", "Asia", False, 71.4, 0.30, 3500, 0.080, 6.5, 1264.0, 2.7),
    ("MEX", "Mexico", "North America", False, 73.7, 0.08, 16000, 0.006, 7.4, 98.0, 3.9),
    ("BRA", "Brazil", "South America", False, 69.9, 0.25, 11500, 0.012, 5.6, 175.0, 1.9),
    ("IND", "India", "Asia", False, 62.7, 0.33, 2600, 0.052, 4.4, 1059.0, 0.9),
    ("ZAF", "South Africa", "Africa", False, 56.5, 0.25, 11000, 0.007, 8.6, 46.0, 8.1),
    ("NGA", "Nigeria", "Africa", False, 46.3, 0.39, 3700, 0.030, 5.1, 122.0, 0.6),
    ("ETH", "Ethiopia", "Africa", False, 50.8, 0.62, 800, 0.065, 1.6, 67.0, 0.1),
    ("OWID_WRL", "World", None, True, 67.5, 0.27, 11000, 0.020, 7.0, 6150.0, 4.2),
    ("OWID_EUR", "Europe", None, True, 74.3, 0.20, 26000, 0.012, 11.0, 727.0, 7.6),
]
OWID_INDICATORS = (
    "LIFE_EXPECTANCY",
    "GDP_PER_CAPITA",
    "YEARS_OF_SCHOOLING",
    "POPULATION",
    "CO2_PER_CAPITA",
)
OWID_YEARS = range(2000, 2023)
# Gaps the golden questions rely on: no 2020 life expectancy for these two countries.
OWID_MISSING_LIFE_2020 = {"NGA", "ETH"}


def build_owid_fixture(seed: int = 11):
    """Synthetic OWID-shaped tables: 13 countries and 2 aggregates, 2000-2022, with gaps."""
    import duckdb

    rng = random.Random(seed)
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE COUNTRY (COUNTRY_CODE VARCHAR, COUNTRY_NAME VARCHAR, REGION VARCHAR, "
        "IS_AGGREGATE BOOLEAN)"
    )
    for t in OWID_INDICATORS:
        con.execute(
            f"CREATE TABLE {t} (COUNTRY_CODE VARCHAR, YEAR INTEGER, VALUE DOUBLE, "
            "IS_PROJECTED BOOLEAN)"
        )
    con.execute(
        "CREATE TABLE ATTRIBUTION (TABLE_NAME VARCHAR, INDICATOR VARCHAR, SOURCE_URL VARCHAR, "
        "LICENCE VARCHAR, DOWNLOADED_AT TIMESTAMP)"
    )
    con.executemany("INSERT INTO COUNTRY VALUES (?, ?, ?, ?)", [c[:4] for c in OWID_COUNTRIES])
    rows: dict[str, list] = {t: [] for t in OWID_INDICATORS}
    for code, _, _, _, life, gain, gdp, growth, school, pop, co2 in OWID_COUNTRIES:
        for y in OWID_YEARS:
            n = y - 2000
            dip = 1.2 if y in (2020, 2021) else 0.0  # pandemic years
            if not (y == 2020 and code in OWID_MISSING_LIFE_2020):
                rows["LIFE_EXPECTANCY"].append((code, y, round(life + gain * n - dip, 2), False))
            gdp_y = gdp * (1 + growth) ** n * rng.uniform(0.98, 1.02)
            rows["GDP_PER_CAPITA"].append((code, y, round(gdp_y), False))
            if y <= 2020:  # schooling estimates end earlier than the other series
                rows["YEARS_OF_SCHOOLING"].append((code, y, round(school + 0.06 * n, 2), False))
            rows["POPULATION"].append((code, y, round(pop * 1e6 * 1.01**n), False))
            co2_y = co2 * (1 + rng.uniform(-0.02, 0.02)) ** n
            rows["CO2_PER_CAPITA"].append((code, y, round(co2_y, 2), False))
        if code in ("IND", "CHN", "OWID_WRL"):  # projected rows, as in OWID's population series
            for y in (2023, 2024):
                rows["POPULATION"].append((code, y, round(pop * 1e6 * 1.01 ** (y - 2000)), True))
    for table, data in rows.items():
        con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?)", data)
    con.executemany(
        "INSERT INTO ATTRIBUTION VALUES (?, ?, ?, 'CC BY 4.0', TIMESTAMP '2026-10-01 00:00:00')",
        [(t, t.replace("_", " ").lower(), "https://ourworldindata.org/") for t in OWID_INDICATORS],
    )
    return con


# Offline fixture per dataset; datasets not listed here use the TPC-H fixture.
_FIXTURES = {"OWID": build_owid_fixture}  # by Snowflake schema, so role variants share it


def build_fixture_for(cfg):
    """The offline fixture that matches a dataset configuration."""
    return _FIXTURES.get(cfg.snowflake.schema_.upper(), build_fixture)()
