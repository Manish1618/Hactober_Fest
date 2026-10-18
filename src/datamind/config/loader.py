"""Load and check dataset configurations from datasets/<name>/."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from datamind.config.models import DatasetConfig


class ConfigError(Exception):
    """A dataset configuration is missing or invalid."""


def datasets_dir() -> Path:
    """Folder holding one sub-folder per dataset. Override with DATAMIND_DATASETS_DIR."""
    env = os.environ.get("DATAMIND_DATASETS_DIR")
    if env:
        return Path(env)
    # Walk up from the working directory so the CLI works from any sub-folder of the repo.
    here = Path.cwd()
    for candidate in (here, *here.parents):
        if (candidate / "datasets").is_dir():
            return candidate / "datasets"
    return here / "datasets"


def list_datasets(root: Path | None = None) -> list[str]:
    root = root or datasets_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "dataset.yaml").is_file())


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def load_dataset(name_or_path: str | Path, root: Path | None = None) -> DatasetConfig:
    """Load a dataset by folder name (under datasets/) or by a path to its folder."""
    folder = Path(name_or_path)
    if not (folder / "dataset.yaml").is_file():
        folder = (root or datasets_dir()) / str(name_or_path)
    main = folder / "dataset.yaml"
    if not main.is_file():
        available = ", ".join(list_datasets(root)) or "none"
        raise ConfigError(f"no dataset {str(name_or_path)!r} (available: {available})")

    data = _read_yaml(main)
    examples_file = folder / "examples.yaml"
    if examples_file.is_file():
        data["examples"] = _read_yaml(examples_file).get("examples", [])

    try:
        return DatasetConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{main}: {e}") from e
