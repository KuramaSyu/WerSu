"""Converters for the gRPC and storage layers.

Submodules are imported lazily to avoid a circular-import warning when
repo modules load before src.api finishes initialising.
"""