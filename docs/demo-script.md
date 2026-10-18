# Demo script

PRD section 17's ten steps, mapped to what this build does. Times are a guide for a 5 to 7 minute
slot.

## Before the demo

```bash
datamind smoke                    # Snowflake and model both "ok"
datamind statuses                 # every status shown once
datamind ui                       # http://localhost:8501
```

Have a Snowsight tab open on the same account, and a terminal in the repository.

## The ten steps

| # | PRD step | What to do in DataMind |
| --- | --- | --- |
| 1 | Problem and user | One sentence: "People who know their business but not SQL can ask Snowflake questions in English and see proof for every answer." |
| 2 | Judge-supplied question, then evidence | Ask the judge's question in the UI. If they have none, click a starter: "What was total revenue in 1995?" or "Which five customers spent the most, and which country are they in?". Point at the KPI card or chart, then the Snowflake audit trail: copy the query ID or open it in Snowsight. Open **Evidence**: question as understood, SQL, query ID, rows, query facts, answer check, and the CSV and proof-bundle downloads. Click a follow-up button to show the conversation continues. |
| 3 | Re-run the SQL in Snowflake | Copy the SQL from Evidence into Snowsight and run it. Show the same numbers. In Query History, filter on query tag `datamind:<request id>` or search the query ID shown in Evidence. |
| 4 | Vague question | "Which products are doing best?" returns `CLARIFY` with options (revenue or quantity). For a stated assumption instead, try "Who are our best customers?": the `best customer` default in `datasets/tpch_sf1/dataset.yaml` tells the model to read it as highest revenue and state that as an assumption chip. Rehearse it first; the model decides. |
| 5 | Unsafe request | "Delete all orders from 1994." returns `BLOCKED` before the model is called. Mention the second barrier: the read-only `DATAMIND_READER` role. |
| 6 | Empty result and missing data | "Show sales for Atlantis." returns `NO_DATA` with the likely cause and, when the filter is on a known column, values that do exist. "What will revenue be next year?" returns `UNSUPPORTED` with a reason. |
| 7 | Data change | Run `datamind eval --dataset tpch_demo` in the terminal. Each T8 line shows the answer before and after the change and passes only with a new query ID. Manual version below. |
| 8 | Follow-up | "What was total revenue in 1996?" then "And in 1997?". Evidence shows the rewritten question and "Previous question: ...". |
| 9 | Second dataset, no code change | Switch the **Dataset** selector in the sidebar (for example to `tpch_demo`, or the OWID dataset once its config is in `datasets/`). Click a starter question. |
| 10 | Results, repository, setup | Show the latest results table in [evaluation.md](evaluation.md) and `docs/benchmark.md`, the repository, and the quick start in `README.md`. |

### Step 7 by hand

Uses the demo tables from `snowflake/03_demo_tables.sql`, mirroring golden question T8-02.

1. In the UI select dataset `tpch_demo` and ask "What is the account balance of
   Customer#000000001?". Note the answer and query ID.
2. In Snowsight, as `DATAMIND_LOADER`:

   ```sql
   UPDATE DATAMIND_DB.DEMO.CUSTOMER SET C_ACCTBAL = C_ACCTBAL + 1000 WHERE C_CUSTKEY = 1;
   ```

3. Ask the same question again. The answer is 1000 higher and the query ID is new.
4. Restore:

   ```sql
   CREATE OR REPLACE TABLE DATAMIND_DB.DEMO.CUSTOMER CLONE DATAMIND_DB.DEMO.CUSTOMER_BASE COPY GRANTS;
   ```

## Same flow from the terminal

```bash
datamind ask "What was total revenue in 1995?"
datamind ask "Which products are doing best?"
datamind ask "Delete all orders from 1994."
datamind ask "Show sales for Atlantis."
datamind ask "What will revenue be next year?"
datamind ask "What is the account balance of Customer#000000001?" --dataset tpch_demo
datamind statuses
datamind eval --dataset tpch_demo
```

`datamind ask` handles one question at a time with no history, so show follow-ups in the UI.

## If the network fails

- **Snowflake unreachable.** Set `DATAMIND_BACKEND=local` in `.env` (or pass `--backend local`).
  The app then uses the offline DuckDB fixture (`src/datamind/snowflake_io/fixture.py`). Numbers
  are synthetic, and the CLI and UI say so. Query IDs start with `local-`. Say this out loud.
- **Model unreachable.** With no internet the hosted model cannot be used. Options: a local Ollama
  server with Gemma already pulled, or `--model scripted`, which only knows the questions in
  `datasets/tpch_sf1/examples.yaml`, for example:

  ```bash
  datamind ask "What was total revenue in 1994?" --backend local --model scripted
  datamind statuses --backend local --model scripted
  ```

  With the scripted model, `BLOCKED` still comes from the code guard. `CLARIFY`, `NO_DATA` and
  `UNSUPPORTED` need the real model, so `datamind statuses` will not show all of them.
- **Screenshots.** Keep screenshots of each step (answer card with Evidence open, Snowsight query
  history with the matching query ID, `CLARIFY`, `BLOCKED`, `NO_DATA`, `UNSUPPORTED`, the T8 eval
  output, the dataset selector) and the backup video from the submission checklist. Show those if
  neither backend works.
