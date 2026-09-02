"""Runtime configuration and filesystem paths shared by the legacy application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DATA_DIR_ENV = "SCRAPYREALESTATE_DATA_DIR"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Absolute paths for files owned by the running application."""

    data_dir: Path

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        cwd: Path | None = None,
    ) -> RuntimePaths:
        """Build paths from the environment, retaining the legacy ``./data`` default."""
        environment = os.environ if environ is None else environ
        configured = environment.get(DATA_DIR_ENV)
        if configured:
            data_dir = Path(configured).expanduser()
            if not data_dir.is_absolute():
                raise ValueError(f"{DATA_DIR_ENV} must be an absolute path")
        else:
            data_dir = (Path.cwd() if cwd is None else cwd) / "data"
        return cls(data_dir=data_dir.resolve())

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def ids_file(self) -> Path:
        return self.data_dir / "ids.json"

    @property
    def user_agent_file(self) -> Path:
        return self.data_dir / "useragent.txt"

    def crawl_output(self, instance_name: str) -> Path:
        return self.data_dir / f"{instance_name}.json"

    def live_test_output(self, spider_name: str) -> Path:
        return self.data_dir / f"test_{spider_name}.json"

    def live_test_log(self, spider_name: str) -> Path:
        return self.data_dir / f"test_{spider_name}.log"

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


def get_runtime_paths() -> RuntimePaths:
    """Return runtime paths using the current process environment and directory."""
    return RuntimePaths.from_environment()
