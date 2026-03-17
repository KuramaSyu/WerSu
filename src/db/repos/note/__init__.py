def __getattr__(name: str):
	if name in {"embedding", "content", "note", "search_strategy", "permission"}:
		from importlib import import_module

		return import_module(f"{__name__}.{name}")

	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")