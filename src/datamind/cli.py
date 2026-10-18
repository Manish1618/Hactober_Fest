"""Command line: datamind check | smoke | ask."""

from __future__ import annotations

import argparse
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


def example_responder(cfg: DatasetConfig):
    """Scripted stand-in for Gemma: answers only questions that appear in examples.yaml."""
    known = {e.question.strip().lower(): e.sql for e in cfg.examples}

    def respond(prompt: str) -> dict:
        # The question block is the last one in the prompt; earlier mentions are in the rules.
        q = prompt.rpartition("<question>")[2].partition("</question>")[0].strip().lower()
        if q not in known:
            raise KeyError("the offline stub only knows the questions in examples.yaml")
        return {"sql": known[q], "assumptions": [], "confidence": 1.0}

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
    for a in resp.assumptions:
        print(f"Assumes:  {a}")
    if resp.explanation:
        print(f"How:      {resp.explanation}")
    print("--- evidence ---")
    print(f"Question: {ev.question_as_understood}")
    print(f"Dataset:  {ev.dataset}  (backend: {ev.backend})")
    print(f"Model:    {ev.model}  prompt {ev.prompt_version}")
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


def cmd_ask(args) -> int:
    settings = get_settings()
    cfg = load_dataset(args.dataset or settings.dataset)
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

    resp = Pipeline(cfg, model, executor, max_rows=settings.max_rows).ask(args.question)
    if args.json:
        print(json.dumps(resp.model_dump(mode="json"), indent=2))
    else:
        _print_response(resp)
    return 0 if resp.status.value in ("ANSWERED", "NO_DATA") else 2


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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
