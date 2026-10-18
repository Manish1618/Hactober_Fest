# Limitations

What DataMind does not do, or does only partly. Each item points to the code or config that sets
the limit.

## Scope

- **English only.** `_looks_english` in `src/datamind/pipeline/run.py` returns `UNSUPPORTED` for
  questions that are mostly non-Latin letters or contain common Spanish, French or German
  question words. It is a simple heuristic, not language detection.
- **Read-only only.** DataMind never changes data (PRD NG1). Requests to change, delete, copy or
  export are `BLOCKED`.
- **No forecasting.** Questions about the future ("What will revenue be next year?") are declined
  as `UNSUPPORTED` (PRD NG6).
- **Questions up to 500 characters** (`MAX_QUESTION_CHARS` in `src/datamind/response.py`).
- **Result cap.** Every query is limited to `DATAMIND_MAX_ROWS` rows (default 1000). The evidence
  panel shows the first 50 rows; the model sees at most 20. When a cap applies, the response says
  so in its limits.
- **Statement timeout.** Snowflake stops a query after `SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS`
  (default 30). The user is asked to narrow the question.

## Data

- **TPC-H is synthetic.** The main dataset is the TPC-H sample shared by Snowflake. Customer
  names, prices and dates are generated, and orders stop in 1998. Answers are correct for this
  data but say nothing about any real business.
- **OWID indicator licences** must be checked per indicator before data is loaded or published.
  The `ATTRIBUTION` table (`snowflake/02_owid_schema.sql`) records the source and licence of each.
- **The data-change test needs extra grants.** T8 edits `DATAMIND_DB.DEMO` with the
  `DATAMIND_LOADER` role, so `snowflake/03_demo_tables.sql` grants that role to the application
  user. The application itself still connects as `DATAMIND_READER`. If you do not want the app
  user to hold the loader role, run T8 on the local fixture only or revoke the grant after the
  test.

## Model

- **Gemma can draft wrong SQL that passes validation.** The validator checks safety and that
  tables, columns and joins are known and declared. The alignment check then catches near-misses:
  code proves a few cases (a year the query does not use, a missing top-N limit, a missing
  breakdown) and a second Gemma pass judges the rest. That second pass is itself a model and can
  be wrong in both directions, so a model-only doubt is flagged rather than refused, and the check
  adds one model call (a few seconds) per question. Accuracy is measured on the golden and
  held-out sets ([evaluation.md](evaluation.md)), not guaranteed. The evidence panel shows the SQL
  and the alignment result so a reader can check them.
- **Explanations are checked for facts, not for wording.** The verifier
  (`src/datamind/pipeline/verify.py`) checks that every number traces to the result, a query fact
  or the question, and that the explanation mentions every key query fact. The phrasing is the
  model's. If a check fails, the code-built text is shown instead.
- **Classification can be wrong.** Whether a question is ambiguous, unsupported or a follow-up is
  Gemma's judgement (`understand_v3`). Write requests are also caught by code before the model,
  and unsafe SQL by the validator, but a reasonable question may occasionally get `CLARIFY` or
  `UNSUPPORTED`.

## Hosted mode

- **Data goes to Google.** With `DATAMIND_MODEL_PROVIDER=google`, the question, conversation
  history, schema description, examples, failed SQL with error text, and up to 20 result rows per
  answer are sent to Google's Gemini API (NFR-15). Use it only for data you may share with Google.
  Snowflake credentials are never sent.
- **Free-tier limits.** The hosted model can be busy (HTTP 500 or 503) or rate-limited (429).
  `GoogleAIAdapter` retries up to 4 times per model with growing waits, then tries the fallback
  model. If all fail, the status is `ERROR` with "The hosted model is busy". The evaluation runner
  retries such a question once after 20 seconds.
- **Latency depends on the endpoint.** Hosted calls took about 5 to 11 seconds per question in
  our offline runs, but this varies with Google's load. A local `gemma3:12b` on a 4 GB GPU laptop
  took about 65 to 80 seconds per call ([model-setup.md](model-setup.md)).

## Safety approach

- **Validator allow-list.** The validator allows one read-only query over tables and columns in
  the dataset config, and denies a fixed list of operations and functions
  (`src/datamind/pipeline/validator.py`). A Snowflake feature it does not know is rejected only if
  it parses to a denied node or reaches an unknown table; new features need a review of the deny
  lists. This is why the read-only Snowflake role is the second, independent barrier.
- **The code guard is wording-based.** `src/datamind/pipeline/guards.py` matches write verbs at
  the start of a clause. Unusual phrasings can pass it; they then meet Gemma's `unsafe`
  classification, the validator and the read-only role.

## Offline mode

- `DATAMIND_BACKEND=local` uses a small DuckDB fixture (`src/datamind/snowflake_io/fixture.py`)
  with a few hundred synthetic rows. Numbers from it are not real answers, and the CLI and UI say
  so. The `scripted` model only knows the questions in `examples.yaml`.
