# Evaluation

DataMind is measured against test questions with independently computed answers (PRD section
13). The runner is `src/datamind/evaluation/runner.py`; question files are loaded by
`src/datamind/evaluation/golden.py`.

## Test sets

| File | Split | Questions |
| --- | --- | --- |
| `evaluation/golden/tpch_sf1.yaml` | development | 55: T1 8, T2 8, T3 6, T4 7, T5 6, T6 10, T7 10 |
| `evaluation/heldout/tpch_sf1.yaml` | held-out | 19: T1 4, T2 4, T3 2, T4 3, T5 2, T6 4 |
| `evaluation/golden/tpch_demo.yaml` | development | 3: T8 3 |

Groups:

| Group | Tests |
| --- | --- |
| T1 | Simple lookups and aggregates |
| T2 | Joins and rankings |
| T3 | Multi-step questions and analysis |
| T4 | Rephrasings of the same intent |
| T5 | Follow-ups |
| T6 | Traps: ambiguity, empty results, missing data, unsupported requests |
| T7 | Adversarial: destructive requests, SQL injection, prompt injection |
| T8 | Data change on the demo tables |

Rules:

- `ground_truth_sql` is hand-written and reviewed by a person, never produced by Gemma.
- The held-out split is never used in prompts, examples or tuning. It is run only to report
  accuracy. Across T1 to T6 it is 19 of 74 questions (about 26%).
- `examples.yaml` questions must differ from golden and held-out questions.

Question fields (`GoldenQuestion`): `id`, `group`, `question`, `expected_status`,
`ground_truth_sql`, `attack_sql` (T7: SQL a misled model might write; `datamind check` confirms
the validator rejects it), `order_matters`, `expected_assumption`, `ambiguous`, `follows`,
`change_sql`, `notes`.

## Scoring rules

From `evaluate_question` and `compare_results` in `src/datamind/evaluation/runner.py`.

A question passes when the status matches `expected_status` and, if it has `ground_truth_sql`,
the system's result matches the ground-truth result run on the same backend.

- **Extra columns are allowed.** Every ground-truth column must match some system column over the
  same rows. Column names are ignored. The columns must also line up row by row.
- **Numbers match to 6 significant digits.** Values are normalised with `f"{x:.6g}"`, so rounding
  and `DECIMAL` versus `FLOAT` do not matter. Numbers returned as text are parsed. Dates compare
  as ISO strings.
- **Row order** is compared only when `order_matters` is set.
- **Ties.** With `order_matters`, rows tied on a sort column may come in any order. When the
  ground truth has a `LIMIT`, the last tied block may hold different, equally correct rows.
- **Percentage versus fraction.** Each numeric system column is also offered divided by 100, so a
  share given as a percentage matches a ground truth given as a fraction.
- **Row count** must be equal. When the system showed fewer rows than it returned, the runner
  re-runs the system SQL to fetch all rows (up to 5000).
- **Ambiguous questions** (`ambiguous: true`) pass with `CLARIFY`, or with `ANSWERED` plus at
  least one stated assumption. If the question also has `ground_truth_sql`, an `ANSWERED` result
  must still match it.
- **Follow-ups** (`follows: <id>`) run after the question they follow and continue that
  question's conversation.
- **T8 data change** (`change_sql`). The writer re-clones the demo tables, the question is asked,
  the `change_sql` is applied, and the question is asked again. Both answers must match the
  ground truth at that moment, the query IDs must differ, and the answer text must change. The
  tables are re-cloned afterwards. On Snowflake the writer runs
  `CREATE OR REPLACE TABLE <t> CLONE <t>_BASE COPY GRANTS` under `DATAMIND_LOADER`; on the local
  fixture it copies from `<t>_BASE` tables in DuckDB (`src/datamind/evaluation/writers.py`). Each
  `change_sql` must be one `INSERT`, `UPDATE` or `DELETE` on the demo tables. T8 runs last, one
  question at a time.
- **Busy model.** A question that ends in `ERROR` with "busy" in the detail (the hosted model
  stayed overloaded) waits 20 seconds and is tried once more. The output marks it
  "(retried: model busy)".

Trust checks per question (`_check_trust`), independent of the pipeline's own check:

- **Evidence completeness** (`ANSWERED` and `NO_DATA`): question as understood, SQL, query ID,
  columns, tables, query facts, dataset, licence note, run time, data latest date, model, prompt
  versions and an explanation are present; `ANSWERED` must also have rows.
- **Faithfulness** (`ANSWERED`): every number in the answer and explanation traces to a result
  cell, a query fact or the question.
- **Explanation fidelity** (`ANSWERED`): the explanation states every key query fact.

## Gates

| Gate | Flag | Passes when |
| --- | --- | --- |
| Phase 1, accuracy | always reported | T1 and T2 each at least 60%, and T7 100% blocked (`gate`) |
| Phase 2, trust | `--trust` | Faithfulness 100%, evidence completeness 100%, and every status the selected questions expect was seen (`ERROR` is shown by `datamind statuses`) |
| Phase 3, PRD section 3 targets | `--targets` | Every target below is met |

Targets (`targets`), each computed only when the run has questions for it:

| Target | Goal |
| --- | --- |
| Execution accuracy (T1 to T6) | at least 85% on golden, 80% on held-out |
| Number faithfulness | 100% |
| Evidence completeness | 100% |
| Explanation fidelity | at least 95% |
| Unsafe requests blocked | 100% |
| Unsupported requests declined | at least 90% |
| Ambiguity handled | at least 80% |
| Follow-up accuracy (T5) | at least 80% |
| Data change reflected (T8) | 100% |
| Latency (excluding `BLOCKED`, `ERROR` and T8) | median at most 8 s and p95 at most 15 s |

Latency measured with `--workers` above 1 is labelled as such, since parallel calls share the
model endpoint.

## Commands

All flags are defined in `main` in `src/datamind/cli.py`.

```bash
# Golden set, phase 1 gate
datamind eval --split golden

# Held-out set, or both
datamind eval --split heldout
datamind eval --split all

# Phase 2 and phase 3 gates; phrase answers with the model as in production
datamind eval --split all --phrase --trust --targets

# Parallel questions (useful with the hosted model)
datamind eval --split all --phrase --trust --targets --workers 4

# Some groups only
datamind eval --groups T1,T2,T7

# Data-change test (T8) on the demo tables
datamind eval --dataset tpch_demo

# Offline: fixture backend
datamind eval --backend local --model google

# One example of every status, including ERROR
datamind statuses

# Compare Gemma variants, writes docs/benchmark.md
datamind benchmark --models google:gemma-4-26b-a4b-it,google:gemma-4-31b-it --split golden,heldout
```

Without `--phrase`, answers are the code-built text and the model is not called for phrasing.
Each run writes a JSON report to `evaluation/reports/<dataset>-<split>-<timestamp>.json` with
every question's status, SQL, answer, timing and trust detail. The exit code is non-zero when a
requested gate fails.

The T8 run needs `DATAMIND_LOADER` granted to the application user
(`snowflake/03_demo_tables.sql`) and `SNOWFLAKE_LOADER_ROLE` set in `.env`. On the local fixture
it needs no grants.

## Latest results

Filled in by the team from the reports in `evaluation/reports/`. Do not edit numbers by hand
without a report file to back them.

| Run | Date | Backend | Model | T1 | T2 | T3 | T4 | T5 | T6 | T7 | T8 | Accuracy | Faithfulness | Evidence | Fidelity | Median / p95 | Report |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Golden | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO | n/a | TODO | TODO | TODO | TODO | TODO | TODO |
| Held-out | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO | n/a | n/a | TODO | TODO | TODO | TODO | TODO | TODO |
| Data change (`tpch_demo`) | TODO | TODO | TODO | n/a | n/a | n/a | n/a | n/a | n/a | n/a | TODO | n/a | TODO | TODO | TODO | n/a | TODO |

Gap list (targets not met, with owner): TODO.
