"""Command line: datamind check | smoke | ask | eval | benchmark | statuses | ui."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys

import yaml

from datamind.config import ConfigError, datasets_dir, load_dataset
from datamind.config.models import DatasetConfig
from datamind.evaluation.writers import LocalWriter, SnowflakeWriter, check_change
from datamind.model.adapter import ModelError, ScriptedAdapter, build_adapter
from datamind.pipeline.run import Pipeline
from datamind.pipeline.validator import Catalog, validate
from datamind.response import Response
from datamind.settings import get_settings
from datamind.snowflake_io.executor import (
    BackendUnavailable,
    ExecutionError,
    LocalExecutor,
    SnowflakeExecutor,
)


def prompt_question(prompt: str) -> str:
    """The question block is the last one in every prompt; earlier mentions are in the rules."""
    return prompt.rpartition("<question>")[2].partition("</question>")[0].strip()


def example_responder(cfg: DatasetConfig, extra: dict[str, str] | None = None):
    """Scripted stand-in for Gemma: answers only questions that appear in examples.yaml.

    `extra` maps more questions to SQL (the evaluation tests use it with the golden SQL).
    """
    known = {e.question.strip().lower(): e.sql for e in cfg.examples}
    known.update({k.strip().lower(): v for k, v in (extra or {}).items()})

    def respond(prompt: str, prompt_version: str) -> dict:
        q = html.unescape(prompt_question(prompt))
        if prompt_version.startswith("understand"):
            return {"standalone_question": q, "category": "answerable"}
        if prompt_version.startswith("answer"):
            return {"answer": ""}  # empty answer: the pipeline uses its code-only answer
        if q.lower() not in known:
            raise KeyError("the offline stub only knows the questions in examples.yaml")
        return {"sql": known[q.lower()], "assumptions": [], "confidence": 1.0}

    return respond


def _catalog(cfg: DatasetConfig) -> Catalog:
    return Catalog(
        cfg.catalog(), cfg.snowflake.database, cfg.snowflake.schema_, joins=cfg.join_pairs()
    )


def cmd_check(args) -> int:
    """Validate the dataset config and run every example and golden SQL through the validator."""
    try:
        cfg = load_dataset(args.dataset)
    except ConfigError as e:
        print(f"FAIL config: {e}")
        return 1
    print(
        f"ok   config {cfg.dataset}: {len(cfg.tables)} tables, {len(cfg.metrics)} metrics, "
        f"{len(cfg.joins)} joins, {len(cfg.examples)} examples"
    )
    failures = 0
    catalog = _catalog(cfg)
    for e in cfg.examples:
        r = validate(e.sql, catalog)
        failures += not r.ok
        print(f"{'ok  ' if r.ok else 'FAIL'} example: {e.question} {r.reason}")
    eval_dir = datasets_dir().parent / "evaluation"
    for split in ("golden", "heldout"):
        path = eval_dir / split / f"{cfg.dataset}.yaml"
        if not path.is_file():
            print(f"skip {split} set: {path} not found")
            continue
        for q in yaml.safe_load(path.read_text(encoding="utf-8")).get("questions", []):
            if q.get("ground_truth_sql"):
                r = validate(q["ground_truth_sql"], catalog)
                failures += not r.ok
                print(f"{'ok  ' if r.ok else 'FAIL'} {q['id']} ground truth parses {r.reason}")
            for change in q.get("change_sql") or []:
                try:
                    check_change(change, set(cfg.tables))
                    print(f"ok   {q['id']} change_sql is a plain edit of the demo tables")
                except ExecutionError as e:
                    failures += 1
                    print(f"FAIL {q['id']} {e}")
            if q.get("attack_sql"):
                r = validate(q["attack_sql"], catalog)
                failures += r.ok
                print(f"{'FAIL' if r.ok else 'ok  '} {q['id']} attack blocked ({r.code})")
    print("all checks passed" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0


def cmd_smoke(args) -> int:
    """Connect to Snowflake and the model endpoint and report each separately."""
    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
    status = 0
    try:
        print("ok   snowflake:", SnowflakeExecutor(settings.snowflake, cfg).smoke())
    except ExecutionError as e:
        print("FAIL snowflake:", e)
        status = 1
    try:
        print("ok   model:", build_adapter(settings.model).ping())
    except ModelError as e:
        print("FAIL model:", e)
        status = 1
    return status


def _print_response(resp: Response) -> None:
    ev = resp.evidence
    print(f"Status:   {resp.status.value}")
    if resp.answer:
        print(f"Answer:   {resp.answer}")
    if resp.reason:
        print(f"Reason:   {resp.reason}")
    if resp.clarification:
        print(f"Asks:     {resp.clarification.question}")
        for o in resp.clarification.options:
            print(f"          - {o}")
    for s in resp.suggestions:
        print(f"Try:      {s}")
    for a in resp.assumptions:
        print(f"Assumes:  {a}")
    if resp.explanation:
        print(f"How:      {resp.explanation}")
    print("--- evidence ---")
    print(f"Question: {ev.question_as_understood}")
    if ev.context_used:
        print(f"Context:  {ev.context_used}")
    print(f"Dataset:  {ev.dataset}  (backend: {ev.backend}, data up to {ev.data_latest_date})")
    print(f"Model:    {ev.model}  prompts {', '.join(ev.prompt_versions) or 'none'}")
    if ev.repair_attempts:
        print(f"Repairs:  {ev.repair_attempts}")
    if ev.alignment:
        print(f"Aligned:  {ev.alignment}")
    if ev.sql:
        print("SQL:\n" + "\n".join("  " + line for line in ev.sql.splitlines()))
    if ev.query_id:
        print(
            f"Query ID: {ev.query_id}  ({ev.elapsed_seconds}s, {ev.rows_returned} rows, "
            f"ran {ev.executed_at})"
        )
        print("Rows:     " + " | ".join(ev.columns))
        for row in ev.rows[:10]:
            print("          " + " | ".join(str(v) for v in row))
    for limit in ev.limits:
        print(f"Limit:    {limit}")


def _build(args, settings, cfg, phrase_answers: bool = True) -> Pipeline:
    backend = args.backend or settings.backend
    provider = args.model or settings.model.provider
    if provider == "scripted":
        model = ScriptedAdapter(example_responder(cfg))
    else:
        if provider != settings.model.provider:  # --model overrides .env: use its defaults
            os.environ["DATAMIND_MODEL_PROVIDER"] = provider
            settings.model = get_settings().model
        model = build_adapter(settings.model)
    if backend == "local":
        from datamind.snowflake_io.fixture import build_fixture_for

        executor = LocalExecutor(build_fixture_for(cfg))
        print("NOTE: local fixture backend. Numbers are synthetic, not from Snowflake.\n")
    else:
        executor = SnowflakeExecutor(settings.snowflake, cfg)
    return Pipeline(cfg, model, executor, max_rows=settings.max_rows, phrase_answers=phrase_answers)


def cmd_ask(args) -> int:
    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
    resp = _build(args, settings, cfg).ask(args.question)
    if args.json:
        print(json.dumps(resp.model_dump(mode="json"), indent=2))
    else:
        _print_response(resp)
    return 0 if resp.status.value in ("ANSWERED", "NO_DATA") else 2


def _writer(pipeline: Pipeline, settings, cfg: DatasetConfig):
    """Writer for the T8 data-change test, matching the pipeline's backend."""
    if isinstance(pipeline.executor, LocalExecutor):
        return LocalWriter(pipeline.executor.connection, cfg)
    return SnowflakeWriter(settings.snowflake, cfg, settings.snowflake.loader_role)


def _print_report(report) -> tuple[bool, bool, bool]:
    for g, (p, n) in report.by_group().items():
        print(f"     {g}: {p}/{n} ({p / n:.0%})")
    ok, summary = report.gate()
    print(f"Gate {'PASSED' if ok else 'FAILED'}: {summary}")
    trust_ok, trust_summary = report.trust()
    print(f"Trust {'PASSED' if trust_ok else 'FAILED'}: {trust_summary}")
    targets = report.targets()
    print("Targets (PRD section 3):")
    for t in targets:
        print(f"  {'met ' if t.passed else 'GAP '} {t.name}: {t.value} (target {t.goal})")
    targets_ok = all(t.passed for t in targets)
    print(f"Targets {'MET' if targets_ok else 'NOT MET'}")
    return ok, trust_ok, targets_ok


def _show(r) -> None:
    mark = "PASS" if r.passed else "FAIL"
    trust = f"  [{r.trust_detail}]" if r.trust_detail else ""
    retried = " (retried: model busy)" if r.retried else ""
    line = f"{mark} {r.id:6} {r.status:11} {r.seconds:6.1f}s  {r.detail}{retried}{trust}"
    print(line, flush=True)


def cmd_eval(args) -> int:
    """Run the golden set through the pipeline and report accuracy per group."""
    from datamind.evaluation import load_set, run_eval
    from datamind.evaluation.golden import evaluation_dir

    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
    pipeline = _build(args, settings, cfg, phrase_answers=args.phrase)
    groups = set(args.groups.split(",")) if args.groups else None
    status = 0
    for split in ("golden", "heldout") if args.split == "all" else (args.split,):
        if not (evaluation_dir() / split / f"{cfg.dataset}.yaml").is_file():
            print(f"== {split}: no {cfg.dataset} questions, skipped ==")
            continue
        golden = load_set(cfg.dataset, split)
        print(f"== {split} ({cfg.dataset}, model {pipeline.model.name}) ==")
        needs_writer = any(q.change_sql for q in golden.questions)
        writer = _writer(pipeline, settings, cfg) if needs_writer else None
        report = run_eval(
            pipeline, golden, groups, progress=_show, workers=args.workers, writer=writer
        )
        ok, trust_ok, targets_ok = _print_report(report)
        if args.trust:
            ok = ok and trust_ok
        if args.targets:
            ok = ok and targets_ok
        print(f"Report: {report.save(evaluation_dir() / 'reports')}")
        status |= 0 if ok else 1
    return status


def cmd_benchmark(args) -> int:
    """Run the golden set once per model variant and write docs/benchmark.md (FR-H4)."""
    from datamind.evaluation import load_set, run_eval
    from datamind.evaluation.benchmark import BenchmarkRow, write_benchmark
    from datamind.evaluation.golden import evaluation_dir

    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
    groups = set(args.groups.split(",")) if args.groups else None
    rows = []
    for spec in args.models.split(","):
        provider, _, name = spec.partition(":")
        os.environ["DATAMIND_MODEL_PROVIDER"] = provider
        os.environ["DATAMIND_MODEL_NAME"] = name
        os.environ["DATAMIND_MODEL_FALLBACK"] = "none"  # measure this variant alone
        run_settings = get_settings()
        args.model = provider
        pipeline = _build(args, run_settings, cfg, phrase_answers=True)
        for split in args.split.split(","):
            golden = load_set(cfg.dataset, split)
            print(f"== {provider}:{name} on {split} ==")
            report = run_eval(pipeline, golden, groups, progress=_show, workers=args.workers)
            _print_report(report)
            saved = report.save(evaluation_dir() / "reports")
            rows.append(BenchmarkRow.from_report(report, provider, saved.name))
    path = write_benchmark(rows, args.out)
    print(f"Benchmark written to {path}")
    return 0


STATUS_DEMO = [
    ("ANSWERED", "What was total revenue in 1995?"),
    ("CLARIFY", "Which products are doing best?"),
    ("NO_DATA", "Show sales for Atlantis."),
    ("UNSUPPORTED", "What will revenue be next year?"),
    ("BLOCKED", "Delete all orders from 1994."),
    ("ERROR", "What was total revenue in 1995?"),
]


class _Outage:
    """Executor that behaves like Snowflake being down, to demonstrate ERROR."""

    backend = "simulated-outage"

    def run(self, *a, **k):
        raise BackendUnavailable("Could not connect to Snowflake (simulated outage).")


def cmd_ui(args) -> int:
    """Start the Streamlit app (needs the ui extra: pip install -e ".[ui]")."""
    import subprocess
    from pathlib import Path

    app = Path(__file__).parent / "ui" / "app.py"
    theme = ["--theme.base", "dark", "--theme.primaryColor", "#8b5cf6"]
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *theme])


def cmd_statuses(args) -> int:
    """Show one example of every response status (PRD 8.2, phase 2 gate)."""
    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
    pipeline = _build(args, settings, cfg)
    failures = 0
    for expected, question in STATUS_DEMO:
        p = pipeline
        if expected == "ERROR":
            p = Pipeline(cfg, pipeline.model, _Outage(), max_rows=settings.max_rows)
            question += "  [Snowflake outage simulated]"
        resp = p.ask(question.split("  [")[0])
        ok = resp.status.value == expected
        failures += not ok
        shown = resp.answer or resp.reason or (resp.clarification and resp.clarification.question)
        print(f"{'ok  ' if ok else 'FAIL'} {expected:11} got {resp.status.value:11} {question}")
        print(f"     {shown}")
    print("every status demonstrated" if not failures else f"{failures} status(es) not shown")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="datamind", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate a dataset config and the golden SQL")
    c.add_argument("--dataset", default="tpch_sf1")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("smoke", help="test the Snowflake and model connections")
    s.add_argument("--dataset")
    s.set_defaults(func=cmd_smoke)

    a = sub.add_parser("ask", help="ask one question")
    a.add_argument("question")
    a.add_argument("--dataset")
    a.add_argument("--backend", choices=["snowflake", "local"])
    a.add_argument("--model", choices=["ollama", "google", "scripted"])
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_ask)

    e = sub.add_parser("eval", help="run the golden set and report accuracy")
    e.add_argument("--dataset")
    e.add_argument("--split", choices=["golden", "heldout", "all"], default="golden")
    e.add_argument("--groups", help="comma-separated groups, for example T1,T2,T7")
    e.add_argument("--backend", choices=["snowflake", "local"])
    e.add_argument("--model", choices=["ollama", "google", "scripted"])
    e.add_argument(
        "--phrase", action="store_true", help="also phrase answers with the model (slower)"
    )
    e.add_argument(
        "--workers",
        type=int,
        default=1,
        help="questions run in parallel (useful with --model google)",
    )
    e.add_argument(
        "--trust", action="store_true", help="also require the phase 2 trust gate to pass"
    )
    e.add_argument("--targets", action="store_true", help="also require the PRD section 3 targets")
    e.set_defaults(func=cmd_eval)

    b = sub.add_parser("benchmark", help="compare Gemma variants on the golden set")
    b.add_argument(
        "--models",
        default="google:gemma-4-26b-a4b-it,google:gemma-4-31b-it",
        help="comma-separated provider:model, for example ollama:gemma3:12b",
    )
    b.add_argument("--dataset")
    b.add_argument("--split", default="golden", help="golden, heldout or golden,heldout")
    b.add_argument("--groups")
    b.add_argument("--backend", choices=["snowflake", "local"])
    b.add_argument("--workers", type=int, default=1)
    b.add_argument("--out", default="docs/benchmark.md")
    b.set_defaults(func=cmd_benchmark, model=None)

    st = sub.add_parser("statuses", help="show one example of every response status")
    st.add_argument("--dataset")
    st.add_argument("--backend", choices=["snowflake", "local"])
    st.add_argument("--model", choices=["ollama", "google", "scripted"])
    st.set_defaults(func=cmd_statuses)

    u = sub.add_parser("ui", help="start the Streamlit interface")
    u.set_defaults(func=cmd_ui)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
