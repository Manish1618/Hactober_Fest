# Model setup

This page covers FR-H3: how to get Gemma, accept its licence, what hardware it needs, which
quantization we use, and how fast it ran for us.

DataMind talks to Gemma through one adapter interface (`src/datamind/model/adapter.py`). The
provider and model are chosen by configuration only. No code change is needed to switch.

| Provider | Setting | Where Gemma runs |
| --- | --- | --- |
| Hosted | `DATAMIND_MODEL_PROVIDER=google` | Google AI Studio / Gemini API |
| Local | `DATAMIND_MODEL_PROVIDER=ollama` (default) | An Ollama server, local or remote |
| Scripted | `--model scripted` on the CLI | No model: canned replies for offline tests |

## Licence

Gemma is released under the Gemma Terms of Use. Each person who downloads or calls the model
accepts those terms where they get it: on the Ollama model page or Kaggle/Hugging Face for local
weights, or in Google AI Studio for the hosted API. This repository does not contain or
redistribute Gemma weights (see `README.md` and `NOTICE`).

## Option A: hosted Gemma (Google AI Studio)

1. Create an API key in Google AI Studio.
2. In `.env`:

   ```bash
   DATAMIND_MODEL_PROVIDER=google
   DATAMIND_MODEL_API_KEY=<your key>
   ```

3. Check it: `datamind smoke`.

What the code does (`src/datamind/settings.py`, `GoogleAIAdapter`):

- Default model `gemma-4-26b-a4b-it`. Fallback `gemma-4-31b-it` when the first model stays busy.
  Change the fallback list with `DATAMIND_MODEL_FALLBACK` (comma-separated, or `none`).
- An Ollama-style name (one with a `:`) or a non-Google endpoint left in `.env` is ignored in this
  mode; the Gemini API endpoint and the default model are used instead.
- Thinking is turned off by a system instruction (`NO_THINKING`) unless
  `DATAMIND_MODEL_THINKING=on`. The code comment records that this cut a SQL draft from about
  60 s to about 5 s with the same SQL. With thinking on, expect about ten times the time.
- Output tokens are raised to at least 8192.
- Replies with status 429, 500 or 503 are retried up to 4 times per model with growing, jittered
  waits (about 2, 4, 8 and 16 s), then the next fallback model is tried. Gemma on this API has no
  JSON mode, so a malformed reply is retried once with the parse error.

Measured speed: about 5 to 11 seconds per question in our offline-fixture runs with thinking off.
Hosted latency depends on Google's load at the time.

**Data leaves your machine in this mode (NFR-15).** The question, conversation history, the
schema description from `dataset.yaml`, examples, failed SQL with error text, and up to 20 result
rows per answer are sent to Google. Snowflake credentials are never sent. See
[architecture.md](architecture.md#data-sent-to-the-model-in-hosted-mode-nfr-15).

## Option B: local Gemma (Ollama)

1. Install [Ollama](https://ollama.com).
2. Pull the model (this is where you accept the Gemma Terms of Use):

   ```bash
   ollama pull gemma3:12b
   ```

3. In `.env` (these are also the defaults):

   ```bash
   DATAMIND_MODEL_PROVIDER=ollama
   DATAMIND_MODEL_ENDPOINT=http://localhost:11434
   DATAMIND_MODEL_NAME=gemma3:12b
   ```

   Point `DATAMIND_MODEL_ENDPOINT` at another machine to use a remote Ollama server.

4. Check it: `datamind smoke`. The adapter's `ping` lists the models on the server and fails if
   `DATAMIND_MODEL_NAME` is not pulled.

The `OllamaAdapter` calls `/api/chat` with the output JSON schema as `format`, so replies are
constrained to the expected shape. Temperature is 0 by default (`DATAMIND_MODEL_TEMPERATURE`).

**Quantization.** We use Ollama's default build of `gemma3:12b`, which is 4-bit quantized (q4).
`ollama show gemma3:12b` prints the quantization and size of the build you pulled. Other
quantizations can be pulled by tag and set in
`DATAMIND_MODEL_NAME`.

**Hardware and measured speed.** On a laptop with a 4 GB GPU the model did not fit in GPU memory
and ran mostly on CPU, at about 65 to 80 seconds per model call. A question makes at least three
calls (understand, draft SQL, answer), so this is too slow for a live demo. A GPU with enough
memory for the whole model, or hosted mode, is needed for interactive use. Raise
`DATAMIND_MODEL_TIMEOUT_SECONDS` (default 120 in code) on slow hardware.

## Switching

Only `.env` (or environment variables) changes:

```bash
# hosted
DATAMIND_MODEL_PROVIDER=google
# local
DATAMIND_MODEL_PROVIDER=ollama
```

For one command, `--model ollama|google|scripted` on `datamind ask`, `datamind eval` and
`datamind statuses` overrides the provider and uses that provider's defaults.

## Smoke test

```bash
datamind smoke [--dataset tpch_sf1]
```

Reports Snowflake and the model separately (`cmd_smoke` in `src/datamind/cli.py`). For Snowflake
it prints the role, warehouse, row count of the dataset's first table and query ID. For the model it confirms the
model name is available on the endpoint.

## Benchmark

```bash
datamind benchmark \
  --models google:gemma-4-26b-a4b-it,google:gemma-4-31b-it \
  [--split golden|heldout|golden,heldout] [--groups T1,T2] \
  [--backend snowflake|local] [--workers N] [--out docs/benchmark.md]
```

`--models` takes comma-separated `provider:model` entries, for example `ollama:gemma3:12b`. Each
variant runs the chosen split with fallback turned off, so it is measured alone. Results go to
`docs/benchmark.md` (FR-H4). Rows are also kept in a JSON file next to it, so runs from different
machines (for example a laptop with Ollama and a hosted run) add up to one table
(`src/datamind/evaluation/benchmark.py`).
