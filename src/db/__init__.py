from .database import Database
from .table import Table, TableABC


def __getattr__(name: str):
	if name in {"repos", "entities"}:
		from importlib import import_module

		return import_module(f"{__name__}.{name}")

	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
