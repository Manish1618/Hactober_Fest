# Architecture

DataMind answers English questions about data in Snowflake. Google Gemma reads the question and
drafts SQL. Ordinary code checks the SQL. Snowflake runs it under a read-only role. Code checks
every number in the answer and attaches the evidence.

Gemma never runs anything and never decides what is safe.

## Diagram

```
 question
    |
    v
 [1 intake]  clean text, empty and length check (500 chars)          code
    |
 [2 English check]  non-English -> UNSUPPORTED                       code
    |
 [3 code guard]  write/role-switch wording -> BLOCKED                code
    |
 [4 understand]  rewrite follow-up + classify                        Gemma
    |   ambiguous -> CLARIFY, unsupported -> UNSUPPORTED, unsafe -> BLOCKED
    |   (rewritten question goes through the code guard again)
    v
 [5 draft SQL]  schema, metrics, defaults, examples, latest date     Gemma
    |
 [6 validator]  sqlglot: one SELECT, known tables/columns,           code
    |           declared joins, denied functions, row limit
    |   unsafe draft -> BLOCKED (never repaired)
    v
 [7 execute]  Snowflake as DATAMIND_READER, query tag, timeout       Snowflake
    |
    |   validation or execution error -> back to [5] with the error,
    |   at most 2 repairs, then ERROR
    v
 [8 query facts]  tables, filters, grouping, order, limit from SQL   code
    |   no rows / only NULLs -> NO_DATA with likely cause
    v
 [9 answer]  Gemma writes text with {rN.col} placeholders            Gemma
    |        code fills every placeholder from the result rows       code
    v
 [10 verifier]  every number traces to a row, fact or the question;  code
    |           explanation states every key fact; else code text
    v
 [11 response]  status, answer, explanation, assumptions, evidence
```

## Pipeline steps

All steps live in `Pipeline.ask` in `src/datamind/pipeline/run.py` unless noted.

1. **Intake.** `clean_question` strips control characters and extra spaces. An empty question,
   or one longer than `MAX_QUESTION_CHARS` (500, `src/datamind/response.py`), returns
   `UNSUPPORTED`.
2. **English check.** `_looks_english` requires at least 80% Latin letters and no common Spanish,
   French or German question words. Otherwise the status is `UNSUPPORTED` with starter questions
   as suggestions.
3. **Code guard.** `unsafe_reason` in `src/datamind/pipeline/guards.py` matches raw SQL writes,
   role switches (for example "use role ACCOUNTADMIN") and imperative change verbs (delete, drop,
   update, export and others). A match returns `BLOCKED`. The model and Snowflake are never
   called.
4. **Understand (Gemma).** Prompt `understand_v3` (`src/datamind/prompts/understand_v3.txt`)
   gets the dataset description, schema (without joins), default meanings for vague terms, the
   time note, the conversation history and the question. Gemma returns a standalone question and
   a category (`src/datamind/model/schemas.py`). The standalone question passes through the code
   guard again. Categories map to statuses:
   - `unsafe` -> `BLOCKED`
   - `follow_up_unclear`, or a follow-up with no history -> `CLARIFY`
   - `ambiguous` with at least two options -> `CLARIFY`; with fewer, the pipeline answers with
     the most common reading
   - `unsupported` -> `UNSUPPORTED`, with Gemma's suggested alternative if any
5. **Draft SQL (Gemma).** Prompt `draft_sql_v2` gets the schema with declared joins, metric
   definitions, default meanings, the time note, the latest date in the data (read once from the
   dataset's time anchor column), the few-shot examples from `examples.yaml`, and any failed
   attempts with their errors. Gemma returns SQL, assumptions and a confidence.
6. **Validate (code).** `validate` in `src/datamind/pipeline/validator.py` parses the SQL with
   sqlglot's Snowflake dialect and rejects, with a reason code:
   - more than one statement (`multiple_statements`), anything but a query (`not_select`)
   - any write, DDL, `USE`, `SET`, `GRANT`, `COPY`, `SHOW` and similar node (`denied_operation`)
   - `RESULT_SCAN`, `LAST_QUERY_ID`, `IDENTIFIER`, `TABLE`, `GETVARIABLE`, any `SYSTEM$`
     function, and table functions (`denied_function`)
   - tables outside the dataset's database and schema, or not in its config (`unknown_table`)
   - unknown columns (`unknown_column`)
   - **undeclared joins**: a `column = column` comparison between key columns of different key
     classes that is not a declared join path (`undeclared_join`). This catches, for example,
     `ORDERS.O_CUSTKEY = NATION.N_NATIONKEY`, which runs but gives wrong numbers. The rejection
     lists the declared joins so the repair can use them.
   - a non-numeric `LIMIT` (`bad_limit`)

   It adds `LIMIT max_rows` when there is none and lowers a larger limit to `max_rows`
   (`DATAMIND_MAX_ROWS`, default 1000).
7. **Execute (Snowflake).** `SnowflakeExecutor.run` in `src/datamind/snowflake_io/executor.py`
   connects with key-pair auth, the role from the dataset config (`DATAMIND_READER`), the
   configured warehouse, database and schema, and the session parameter
   `STATEMENT_TIMEOUT_IN_SECONDS` (`SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS`, default 30). Before each
   query it runs a fixed, code-issued `ALTER SESSION SET QUERY_TAG = 'datamind:<request id>'`. It
   fetches at most `max_rows + 1` rows to detect truncation and records the query ID, elapsed
   time, role and warehouse.
8. **Repair loop.** `_draft_and_run` allows `MAX_REPAIRS = 2`. A validation error or a Snowflake
   error is fed back to Gemma with the failed SQL. Unsafe validator codes (`not_select`,
   `denied_operation`, `denied_function`, `multiple_statements`) are never repaired; they return
   `BLOCKED`. A connection failure or a timeout is not repaired either; it returns `ERROR`.
9. **Query facts (code).** `extract_facts` in `src/datamind/pipeline/facts.py` reads the executed
   SQL and lists tables, columns, filters, date range, grouping, ordering and row limit. The model
   is not involved. An empty result, or one with only NULLs, returns `NO_DATA`. `_diagnose_empty`
   then checks each equality filter value with a small validated query and suggests values that
   exist, or compares the date range with the data's coverage.
10. **Answer (Gemma, filled by code).** Prompt `answer_v2` gets the question, up to 20 result rows
    (`ANSWER_ROWS`) numbered `r1`, `r2`, and so on, the row count and the query facts. Gemma must
    write numbers as placeholders such as `{r1.REVENUE}`. `fill_placeholders` in
    `src/datamind/pipeline/answer.py` replaces them with formatted cell values. If a placeholder
    does not resolve, or Gemma typed its own numbers that do not trace to the rows, the code-built
    answer (`template_answer`) is used instead.
11. **Verifier (code).** `verify` in `src/datamind/pipeline/verify.py` checks that every number in
    the answer and explanation traces to a result cell, a query fact or the question, and that
    the explanation mentions every key query fact. A failing explanation is replaced by the
    code-built one (`explain`). The result is recorded in `evidence.verification`.
12. **Evidence.** `Evidence` in `src/datamind/response.py` holds the question as understood, the
    SQL, query ID, run time and timestamp, columns and the first 50 rows (`EVIDENCE_ROWS`), tables
    and columns used, query facts, role and warehouse, data coverage and latest date, the
    verification summary, any rejected SQL, repair count, model name, prompt versions and limits
    (for example "Only the first 1000 rows were fetched").

## The six statuses

Defined in `Status`, `src/datamind/response.py`.

| Status | When |
| --- | --- |
| `ANSWERED` | The query ran, returned values, and the answer passed the verifier. |
| `CLARIFY` | The question is ambiguous (with options), or a follow-up has nothing to follow. |
| `NO_DATA` | The query ran but no rows matched, or it returned only NULLs. A likely cause is given. |
| `UNSUPPORTED` | Empty, too long or non-English, or the data cannot answer it (for example a forecast). |
| `BLOCKED` | The request or the drafted SQL would change data, switch roles or reach outside the dataset. |
| `ERROR` | Snowflake or the model is unreachable, the query timed out, or no valid SQL after 2 repairs. |

`datamind statuses` shows one example of each (`STATUS_DEMO` in `src/datamind/cli.py`). It
demonstrates `ERROR` with a simulated Snowflake outage.

## Two independent write barriers

1. **Code.** The code guard stops write requests before the model is called
   (`src/datamind/pipeline/guards.py`). The validator stops any drafted SQL that is not a single
   read-only query over known tables (`src/datamind/pipeline/validator.py`).
2. **Snowflake.** The application connects with the role named in the dataset config,
   `DATAMIND_READER`. `snowflake/01_roles_and_grants.sql` grants it only `USAGE` and `SELECT`
   (plus imported privileges on `SNOWFLAKE_SAMPLE_DATA`). Even SQL that slipped past the
   validator could not write.

The data-change test (T8) is the only code that writes. It uses a separate writer
(`src/datamind/evaluation/writers.py`) with the `DATAMIND_LOADER` role, accepts only one plain
`INSERT`, `UPDATE` or `DELETE` on the demo tables, and is never used by the pipeline.

## Where Snowflake and Gemma meet

They never talk to each other. The pipeline sits between them.

- Gemma receives text built from the dataset config, the question, conversation history, failed
  SQL with error messages, and (for the answer) up to 20 result rows plus the query facts.
- Snowflake receives only SQL that passed the validator, plus fixed statements written by code:
  the query tag, the `MIN`/`MAX` read of the time anchor column from the dataset config, and the
  empty-result diagnosis queries (these also pass the validator).
- Snowflake credentials, the private key and the API key are never put in a prompt.
- User text and row values are escaped before they go into a prompt (`_neutralise` in
  `src/datamind/model/prompts.py`), so they cannot open or close a prompt block.

### Data sent to the model in hosted mode (NFR-15)

With `DATAMIND_MODEL_PROVIDER=google` the prompts go to Google's Gemini API
(`GoogleAIAdapter`, `src/datamind/model/adapter.py`). That means the following leave the
machine: the question and conversation history, the schema description, metrics, defaults and
examples from `dataset.yaml` and `examples.yaml`, failed SQL with Snowflake error text, and up to
20 result rows per answer with the query facts. Use hosted mode only for data you may share with
Google. With a local Ollama server nothing leaves the machine. The UI shows a notice in the
sidebar when the hosted provider is active (`src/datamind/ui/app.py`).

## Module map

| Path | Role |
| --- | --- |
| `src/datamind/cli.py` | `datamind check`, `smoke`, `ask`, `eval`, `benchmark`, `statuses`, `ui` |
| `src/datamind/settings.py` | Settings from environment variables and `.env` |
| `src/datamind/response.py` | `Status`, `Response`, `Evidence`, `Clarification` |
| `src/datamind/config/` | Dataset config models (`models.py`) and loader (`loader.py`) |
| `src/datamind/model/adapter.py` | `OllamaAdapter`, `GoogleAIAdapter`, `ScriptedAdapter` |
| `src/datamind/model/prompts.py`, `src/datamind/prompts/*.txt` | Versioned prompt templates |
| `src/datamind/model/schemas.py` | Shapes Gemma must return (understanding, SQL draft, answer) |
| `src/datamind/pipeline/run.py` | The pipeline and status decisions |
| `src/datamind/pipeline/guards.py` | Code guard on the user's text |
| `src/datamind/pipeline/validator.py` | SQL validator |
| `src/datamind/pipeline/facts.py` | Query facts from the executed SQL |
| `src/datamind/pipeline/answer.py` | Placeholder filling, code-built answer and explanation |
| `src/datamind/pipeline/verify.py` | Number and fact verifier |
| `src/datamind/pipeline/context.py` | Conversation history for follow-ups |
| `src/datamind/snowflake_io/executor.py` | `SnowflakeExecutor`, `LocalExecutor` |
| `src/datamind/snowflake_io/fixture.py` | Offline DuckDB fixture with synthetic numbers |
| `src/datamind/evaluation/` | Golden-set runner, benchmark, T8 writers |
| `src/datamind/ui/app.py` | Streamlit interface |
| `snowflake/*.sql` | Warehouse, roles, OWID schema, demo tables |
| `datasets/<name>/` | Dataset configs |
| `evaluation/golden/`, `evaluation/heldout/` | Test questions |
