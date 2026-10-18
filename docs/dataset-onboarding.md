# Dataset onboarding

A new dataset is added with configuration only (PRD 9.4, goal G5). No Python changes. The
pipeline, validator, prompts and UI read everything from `datasets/<name>/`.

Worked examples in this repository:

- `datasets/tpch_sf1/`: the TPC-H sample shared into every Snowflake account
  (`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`), 8 tables.
- `datasets/tpch_demo/`: a changeable copy of two TPC-H tables in `DATAMIND_DB.DEMO`, used by the
  data-change test (T8).

## Steps

### 1. Grant the read-only role

DataMind connects with the role named in `dataset.yaml` (normally `DATAMIND_READER`). Give it
`USAGE` on the database and schema and `SELECT` on the tables, and nothing else. Examples:

- `snowflake/01_roles_and_grants.sql` grants imported privileges on `SNOWFLAKE_SAMPLE_DATA` (for
  `tpch_sf1`) and `SELECT` on all current and future tables in `DATAMIND_DB.OWID`.
- `snowflake/03_demo_tables.sql` grants `USAGE` on `DATAMIND_DB.DEMO` and `SELECT` on its tables
  (for `tpch_demo`).

### 2. Write `datasets/<name>/dataset.yaml`

The shape is checked by `DatasetConfig` in `src/datamind/config/models.py`. Unknown keys are
errors. Table and column names are upper-cased.

| Key | What it holds | Example in `datasets/tpch_sf1/dataset.yaml` |
| --- | --- | --- |
| `dataset` | Folder name | `tpch_sf1` |
| `display_name`, `description` | Shown in the UI and sent to the model | "Wholesale orders (TPC-H sample)" |
| `licence_note` | Shown in the evidence panel | "TPC-H sample data provided by Snowflake ... Synthetic data." |
| `snowflake` | `database`, `schema`, `role` | `SNOWFLAKE_SAMPLE_DATA`, `TPCH_SF1`, `DATAMIND_READER` |
| `time` | `anchor_table`, `anchor_column`, `note`. Relative dates ("last year") resolve against the latest value of this column | `ORDERS.O_ORDERDATE` |
| `defaults` | Vague terms with an agreed meaning: `term`, `assumption`, optional `metric`. The answer states the assumption instead of asking | "sales" means revenue; "open orders" means status 'O' |
| `metrics` | Named formulas: `description`, `sql`, `unit`, `synonyms` | `revenue: SUM(L_EXTENDEDPRICE * (1 - L_DISCOUNT))` |
| `tables` | Per table: `description`, and per column `type`, `description`, optional `unit` and `synonyms` | `O_ORDERDATE: {type: DATE, description: Date the order was placed, synonyms: [order date, date]}` |
| `joins` | Allowed join paths, `TABLE.COL = TABLE.COL` | `ORDERS.O_CUSTKEY = CUSTOMER.C_CUSTKEY` |
| `formats` | Per unit: `prefix`, `suffix`, `decimals` | `currency: {prefix: "$", decimals: 2}` |
| `starter_questions` | Shown as buttons in the UI sidebar | "What was total revenue in 1995?" |

The loader also checks references: every join and the time anchor must name known columns, every
default's `metric` must exist, and every metric `unit` must have an entry in `formats`.

Notes:

- Only tables and columns listed here can be queried. The validator rejects anything else.
- Declared `joins` matter for safety and correctness. The validator rejects a join between key
  columns that does not follow a declared path (`undeclared_join`).
- Column `type` matters: columns typed `DATE`, `TIMESTAMP` or `TIMESTAMP_NTZ` are treated as date
  columns in query facts.
- Good descriptions and synonyms are the main lever on accuracy. `tpch_sf1` puts usage hints in
  descriptions, for example `L_SHIPDATE`: "Use it for questions about items shipped in a period".

### 3. Write `datasets/<name>/examples.yaml`

Question and SQL pairs shown to Gemma when it drafts SQL (FR-B9):

```yaml
examples:
  - question: How many customers are in the BUILDING market segment?
    sql: |
      SELECT COUNT(*) AS customer_count
      FROM CUSTOMER
      WHERE C_MKTSEGMENT = 'BUILDING'
```

`tpch_sf1` has 4 examples; `tpch_demo` has 1. Keep examples different from the golden and
held-out questions, so the test stays honest.

### 4. Add golden questions

Write `evaluation/golden/<name>.yaml` (and optionally `evaluation/heldout/<name>.yaml`). Each
question has an `id`, `group` (T1 to T8), `question`, `expected_status` and, for answerable
questions, hand-written `ground_truth_sql`. See [evaluation.md](evaluation.md) for all fields.
The PRD asks for 10 or more questions per new dataset.

### 5. Check

```bash
datamind check --dataset <name>
```

`cmd_check` in `src/datamind/cli.py` loads the config, runs every example SQL and every golden
and held-out `ground_truth_sql` through the validator, checks that each `change_sql` is a plain
edit of the dataset's tables, and checks that every `attack_sql` is rejected.

Then:

```bash
datamind smoke --dataset <name>       # counts the rows of the first configured table
datamind ask "..." --dataset <name>
datamind eval --dataset <name>
```

The UI lists every folder in `datasets/` that has a `dataset.yaml` in its sidebar selector
(`available_datasets` in `src/datamind/ui/app.py`). The default dataset is `DATAMIND_DATASET`.

The PRD mentions an `init` command that drafts `dataset.yaml` from schema discovery. This build
has no such command; write the file by hand, starting from one of the examples.

## Worked example: `tpch_demo`

`datasets/tpch_demo/dataset.yaml` is a minimal config: two tables (`CUSTOMER`, `ORDERS`) in
`DATAMIND_DB.DEMO`, one join, one default, one metric (`order_count`), two formats and two
starter questions. `snowflake/03_demo_tables.sql` creates the tables as clones of
`ORDERS_BASE` and `CUSTOMER_BASE` and grants `SELECT` to `DATAMIND_READER`. The golden file
`evaluation/golden/tpch_demo.yaml` holds the three T8 questions.

## Second dataset: OWID indicators (in progress)

A second dataset, Our World in Data indicators, is being added as `datasets/owid_indicators`.
The tables are created by `snowflake/02_owid_schema.sql` in `DATAMIND_DB.OWID`: a `COUNTRY`
table, one table per indicator (country code, year, value, projected flag) and an `ATTRIBUTION`
table with source link, licence and download date. The script
`datasets/owid_indicators/download_and_load.py` downloads the indicators and loads them into
Snowflake under the loader role. See that script for its options. Once its `dataset.yaml` is in
place it appears in the UI selector with no code change. Check each indicator's licence before
committing any raw data (PRD 9.2).
