# DataMind

Ask questions about your Snowflake data in everyday language and get answers that show their work:
the exact SQL that ran, the Snowflake query ID, the result rows, and a plain-language explanation.
Google Gemma understands the question and drafts SQL; ordinary code checks it; Snowflake runs it
under a read-only role.

> Status: **phases 3 to 5 built**. Two datasets (TPC-H and Our World in Data), a full golden
> set with PRD section 3 targets, a data-change test and a role demo. See [Build status](#build-status).

## How a question is answered

```
question -> Gemma drafts SQL (structured output) -> validator (sqlglot, read-only, known
tables/columns, row limit) -> Snowflake (read-only role, timeout, query tag) -> answer built from
result rows -> evidence: SQL, query ID, rows, tables
```

Gemma never runs anything and never decides safety. Two independent layers stop writes: the
validator in code and the read-only `DATAMIND_READER` role in Snowflake.

## Quick start

Requirements: Python 3.11+, a Snowflake account, and a Gemma model served by
[Ollama](https://ollama.com) (local or remote).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,ui]"
cp .env.example .env               # then fill it in; never commit .env
```

### 1. Snowflake

Run the scripts in `snowflake/` in order, in Snowsight or SnowSQL. Each is safe to run twice.

| Script | Run as | Creates |
| --- | --- | --- |
| `00_account_setup.sql` | ACCOUNTADMIN | X-Small warehouse `DATAMIND_WH` (60 s auto-suspend), resource monitor, `DATAMIND_DB` |
| `01_roles_and_grants.sql` | ACCOUNTADMIN | Roles `DATAMIND_READER`, `DATAMIND_LOADER`, `DATAMIND_RESTRICTED`; user `DATAMIND_APP` |
| `02_owid_schema.sql` | DATAMIND_LOADER | OWID tables (filled in phase 4) |

Create a key pair for the application user and register the public key:

```bash
mkdir -p keys
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out keys/datamind_app_rsa_key.p8 -nocrypt
openssl rsa -in keys/datamind_app_rsa_key.p8 -pubout -out keys/datamind_app_rsa_key.pub
# In Snowflake: ALTER USER DATAMIND_APP SET RSA_PUBLIC_KEY = '<body of the .pub file>';
```

### 2. Gemma

```bash
ollama pull gemma3:12b
```

Set `DATAMIND_MODEL_ENDPOINT` and `DATAMIND_MODEL_NAME` in `.env` to switch variants or point at
a remote server. Accept the Gemma Terms of Use where you download the weights.

**Hosted Gemma instead (faster on small laptops).** Put a Google AI Studio key in `.env`:

```bash
DATAMIND_MODEL_PROVIDER=google
DATAMIND_MODEL_API_KEY=<your key>
```

DataMind then calls `gemma-4-26b-a4b-it` on the Gemini API (about 5 seconds per call, because
the model's step-by-step thinking is turned off; `DATAMIND_MODEL_THINKING=on` allows it at about
ten times the time) and falls back to `gemma-4-31b-it` when Google is busy. Run `datamind smoke` to check the key.
**Data leaves your machine in this mode** (NFR-15): the question, the schema description and up
to 20 result rows per answer are sent to Google. SQL still runs only in your Snowflake account,
and Snowflake credentials are never sent. Switch back with `DATAMIND_MODEL_PROVIDER=ollama`.

### 3. Check and ask

```bash
datamind check                     # dataset config + golden SQL through the validator
datamind smoke                     # Snowflake and model connections, reported separately
datamind ask "What was total revenue in 1995?"
datamind statuses                  # one example of every response status
datamind eval --groups T1,T2,T7    # golden set accuracy (add --phrase --trust for phase 2 checks)
```

### 4. Interface

```bash
pip install -e ".[ui]"
datamind ui                        # opens the Streamlit app on http://localhost:8501
```

Each answer card shows the status, the answer, assumption chips and a plain-language
explanation, with a collapsed **Evidence** section: the question as understood, the exact SQL,
the Snowflake query ID, run details, the first 50 rows, the query facts read from the SQL,
tables and columns used, the data's date coverage, the model and prompt versions, the answer
check result, the alignment check (whether the SQL answers the question asked) and any limits.

The page also draws what fits the result, using only the rows shown as evidence: one number
becomes a KPI card, a date or year with a number becomes a line chart, and up to 15 labelled
numbers become a bar chart. Snowflake answers show the query ID with a copy button and a link to
that query in Snowsight. Answers end with follow-up buttons built from the question (the year
before, the same by year, the top 5), and the evidence offers the shown rows as CSV and a proof
bundle as JSON (question, SQL, query facts, query ID, role, warehouse, times; no keys or
settings).

Offline (no Snowflake, no model), using a tiny synthetic fixture and a scripted stub that only
knows the questions in `datasets/tpch_sf1/examples.yaml`:

```bash
datamind ask "What was total revenue in 1994?" --backend local --model scripted
```

## Tests

```bash
pytest -q                 # unit + adversarial, offline
pytest -m integration     # live Snowflake and model, needs .env
```

## Repository layout

```
datasets/tpch_sf1/      dataset.yaml (tables, metrics, joins, defaults) and examples.yaml
evaluation/golden/      golden questions with hand-written ground-truth SQL
evaluation/heldout/     held-out questions, never used in prompts or tuning
snowflake/              repeatable setup scripts
src/datamind/
  config/               dataset config models and loader
  model/                Gemma adapters (Ollama, hosted Gemini API), output schemas, prompts
  pipeline/             guard, validator, query facts, verifier, answer writer, pipeline
  evaluation/           golden-set runner: accuracy, faithfulness, evidence completeness
  snowflake_io/         Snowflake executor, offline DuckDB fixture
  ui/                   Streamlit interface
  cli.py                datamind check | smoke | ask | eval | statuses | ui
tests/                  unit/, adversarial/, integration/
```

## Build status

| Phase | Scope | State |
| --- | --- | --- |
| 0 Foundations | Skeleton, setup scripts, TPC-H config, model adapter, validator, golden set started, one question end to end | Done |
| 1 Core pipeline | Classifier, SQL drafting with Gemma, repair loop, answer writer | Done: gate passed on Snowflake (T1 88%, T2 88%, T7 10/10) |
| 2 Trust features | Query facts, number verifier, evidence panel, edge-case statuses | Done: gate passed on Snowflake (faithfulness 100%, evidence 100%, every status shown) |
| 3 Evaluation and hardening | Full golden set, runner, model benchmark | Done on Snowflake: golden 45/45, held-out 19/19, data change 3/3; every target met except latency (see [evaluation](docs/evaluation.md#latest-results)) |
| 4 Reuse and polish | OWID dataset, dataset selector, role demo, UI | Done: OWID loaded on Snowflake (7 tables), golden 10/11 and held-out 3/3; restricted-role fix to re-run |
| 5 Docs and demo | Architecture, model setup, limitations, demo | Done: docs written (see Documentation) |

## Documentation

- [Architecture](docs/architecture.md): pipeline steps, statuses, write barriers, what the model sees
- [Model setup](docs/model-setup.md): hosted and local Gemma, licence, speed, smoke test, benchmark
- [Dataset onboarding](docs/dataset-onboarding.md): adding a dataset with configuration only
- [Evaluation](docs/evaluation.md): test sets, scoring rules, gates, commands, latest results
- [Limitations](docs/limitations.md): what DataMind does not do
- [Demo script](docs/demo-script.md): the ten demo steps and what to do if the network fails

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). TPC-H is referenced, not copied. Gemma weights are not redistributed.
