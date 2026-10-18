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
