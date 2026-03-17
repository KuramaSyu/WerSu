from .api import LoggingProvider


def __getattr__(name: str):
	# Keep package import lightweight and load heavy modules only when accessed.
	if name in {"EmbeddingGenerator", "Models"}:
		from .ai import EmbeddingGenerator, Models

		if name == "EmbeddingGenerator":
			return EmbeddingGenerator
		return Models

	if name in {"repos", "entities"}:
		from .db import repos, entities

		if name == "repos":
			return repos
		return entities

	if name in {"converter", "service"}:
		from .grpc_mod import converter, service

		if name == "converter":
			return converter
		return service

	if name == "PermissionService":
		from .services.roles import PermissionService

		return PermissionService

	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
