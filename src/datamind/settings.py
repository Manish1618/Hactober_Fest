"""Runtime settings from environment variables, with an optional .env file.

Secrets live only in the environment or in .env (git-ignored). Nothing here is ever logged
with its value; use Settings.redacted() for display.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader: KEY=VALUE lines, no overriding of variables already set."""
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


class SnowflakeSettings(BaseModel):
    account: str = ""
    user: str = ""
    private_key_path: str = ""
    private_key_passphrase: str = ""
    role: str = "DATAMIND_READER"
    warehouse: str = "DATAMIND_WH"
    statement_timeout_seconds: int = 30

    @property
    def configured(self) -> bool:
        return bool(self.account and self.user and self.private_key_path)


GOOGLE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_MODEL = "gemma-4-26b-a4b-it"  # about 5-10 s per call; 31b took about 30 s
GOOGLE_FALLBACK = "gemma-4-31b-it"


class ModelSettings(BaseModel):
    provider: str = "ollama"  # ollama (local) | google (hosted Gemma) | scripted (tests)
    endpoint: str = "http://localhost:11434"
    name: str = "gemma3:12b"
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    max_tokens: int = 1024
    api_key: SecretStr = SecretStr("")
    fallback_models: list[str] = Field(default_factory=list)
    thinking: bool = False  # hosted Gemma: let the model reason before replying (much slower)


class Settings(BaseModel):
    snowflake: SnowflakeSettings
    model: ModelSettings
    backend: str = "snowflake"
    dataset: str = "tpch_sf1"
    max_rows: int = 1000

    def redacted(self) -> dict:
        d = self.model_dump()
        if d["snowflake"]["private_key_passphrase"]:
            d["snowflake"]["private_key_passphrase"] = "***"
        return d


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "")
    return value if value != "" else default


def _model_settings() -> ModelSettings:
    provider = _env("DATAMIND_MODEL_PROVIDER", "ollama")
    endpoint = _env("DATAMIND_MODEL_ENDPOINT", "http://localhost:11434")
    name = _env("DATAMIND_MODEL_NAME", "gemma3:12b")
    max_tokens = int(_env("DATAMIND_MODEL_MAX_TOKENS", "1024"))
    fallback = _env("DATAMIND_MODEL_FALLBACK", "")
    if provider == "google":
        # Ollama values left in .env from local runs do not apply to the hosted API.
        if "googleapis.com" not in endpoint:
            endpoint = GOOGLE_ENDPOINT
        if ":" in name:
            name = GOOGLE_MODEL
        max_tokens = max(max_tokens, 8192)  # the model's thinking counts against this
        fallback = fallback or GOOGLE_FALLBACK
    return ModelSettings(
        provider=provider,
        endpoint=endpoint,
        name=name,
        temperature=float(_env("DATAMIND_MODEL_TEMPERATURE", "0.0")),
        timeout_seconds=float(_env("DATAMIND_MODEL_TIMEOUT_SECONDS", "120")),
        max_tokens=max_tokens,
        api_key=SecretStr(_env("DATAMIND_MODEL_API_KEY", "")),
        fallback_models=[m.strip() for m in fallback.split(",") if m.strip() and m.strip() != name],
        thinking=_env("DATAMIND_MODEL_THINKING", "off").lower() in ("on", "true", "1"),
    )


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        snowflake=SnowflakeSettings(
            account=_env("SNOWFLAKE_ACCOUNT", ""),
            user=_env("SNOWFLAKE_USER", ""),
            private_key_path=_env("SNOWFLAKE_PRIVATE_KEY_PATH", ""),
            private_key_passphrase=_env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ""),
            role=_env("SNOWFLAKE_ROLE", "DATAMIND_READER"),
            warehouse=_env("SNOWFLAKE_WAREHOUSE", "DATAMIND_WH"),
            statement_timeout_seconds=int(_env("SNOWFLAKE_STATEMENT_TIMEOUT_SECONDS", "30")),
        ),
        model=_model_settings(),
        backend=_env("DATAMIND_BACKEND", "snowflake"),
        dataset=_env("DATAMIND_DATASET", "tpch_sf1"),
        max_rows=int(_env("DATAMIND_MAX_ROWS", "1000")),
    )
