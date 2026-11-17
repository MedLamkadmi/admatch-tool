"""Loads the config files. Nothing about stores, weeks or brands is hard-coded in code."""
from __future__ import annotations
from pathlib import Path
import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Config:
    def __init__(self, config_dir: Path | str | None = None):
        self.dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self.settings = self._load("settings.yaml")
        self._cache: dict = {}

    def _load(self, name: str) -> dict:
        path = self.dir / name
        if not path.exists():
            raise FileNotFoundError(f"Config file missing: {path}")
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def table(self, name: str) -> dict:
        """Any other config file (store brands, French words, ...), loaded once."""
        if name not in self._cache:
            self._cache[name] = self._load(name)
        return self._cache[name]

    @property
    def home_store(self) -> str:
        return self.settings["home_store"]

    @property
    def competitors(self) -> list[str]:
        return list(self.settings["competitors"])

    @property
    def all_stores(self) -> list[str]:
        return [self.home_store] + self.competitors

    def canonical_store(self, raw: str | None) -> str | None:
        """Source store name -> configured store name. None if not a configured store."""
        if raw is None:
            return None
        key = " ".join(str(raw).lower().replace("-", " ").split())
        for store, aliases in self.settings["store_aliases"].items():
            names = {store.lower()} | {" ".join(a.lower().replace("-", " ").split()) for a in aliases}
            if key in names and store in self.all_stores:
                return store
        return None
