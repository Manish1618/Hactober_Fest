"""Command line: datamind check | smoke | ask | eval."""

from __future__ import annotations

import argparse
import html
import json
import sys

import yaml

from datamind.config import ConfigError, datasets_dir, load_dataset
from datamind.config.models import DatasetConfig
from datamind.model.adapter import ModelError, OllamaAdapter, ScriptedAdapter
from datamind.pipeline.run import Pipeline
from datamind.pipeline.validator import Catalog, validate
from datamind.response import Response
from datamind.settings import get_settings
from datamind.snowflake_io.executor import ExecutionError, LocalExecutor, SnowflakeExecutor


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
    return Catalog(cfg.catalog(), cfg.snowflake.database, cfg.snowflake.schema_)


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
        print("ok   model:", OllamaAdapter(settings.model).ping())
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
        model = OllamaAdapter(settings.model)
    if backend == "local":
        from datamind.snowflake_io.fixture import build_fixture

        executor = LocalExecutor(build_fixture())
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
        golden = load_set(cfg.dataset, split)
        print(f"== {split} ({cfg.dataset}, model {pipeline.model.name}) ==")

        def show(r):
            mark = "PASS" if r.passed else "FAIL"
            print(f"{mark} {r.id:6} {r.status:11} {r.seconds:6.1f}s  {r.detail}", flush=True)

        report = run_eval(pipeline, golden, groups, progress=show)
        for g, (p, n) in report.by_group().items():
            print(f"     {g}: {p}/{n} ({p / n:.0%})")
        ok, summary = report.gate()
        print(f"Gate {'PASSED' if ok else 'FAILED'}: {summary}")
        print(f"Report: {report.save(evaluation_dir() / 'reports')}")
        status |= 0 if ok else 1
    return status


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
    a.add_argument("--model", choices=["ollama", "scripted"])
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_ask)

    e = sub.add_parser("eval", help="run the golden set and report accuracy")
    e.add_argument("--dataset")
    e.add_argument("--split", choices=["golden", "heldout", "all"], default="golden")
    e.add_argument("--groups", help="comma-separated groups, for example T1,T2,T7")
    e.add_argument("--backend", choices=["snowflake", "local"])
    e.add_argument("--model", choices=["ollama", "scripted"])
    e.add_argument(
        "--phrase", action="store_true", help="also phrase answers with the model (slower)"
    )
    e.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
