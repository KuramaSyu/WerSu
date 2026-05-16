from __future__ import annotations

from typing import Optional, Sequence, Tuple, Any

import grpc
from authzed.api.v1 import (
    ExperimentalServiceStub,
    PermissionsServiceStub,
    SchemaServiceStub,
    WatchServiceStub,
)

_Metadata = Sequence[Tuple[str, str]]


def _merge_metadata(existing: Optional[_Metadata], extra: _Metadata) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    if existing is not None:
        merged.extend(list(existing))
    merged.extend(list(extra))
    return merged


class _UnaryUnaryCallable:
    def __init__(self, call, metadata: _Metadata) -> None:
        self._call = call
        self._metadata = metadata

    def __call__(self, request, timeout=None, metadata=None, credentials=None, wait_for_ready=None, compression=None):
        return self._call(
            request,
            timeout=timeout,
            metadata=_merge_metadata(metadata, self._metadata),
            credentials=credentials,
            wait_for_ready=wait_for_ready,
            compression=compression,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._call, name)


class _UnaryStreamCallable:
    def __init__(self, call, metadata: _Metadata) -> None:
        self._call = call
        self._metadata = metadata

    def __call__(self, request, timeout=None, metadata=None, credentials=None, wait_for_ready=None, compression=None):
        return self._call(
            request,
            timeout=timeout,
            metadata=_merge_metadata(metadata, self._metadata),
            credentials=credentials,
            wait_for_ready=wait_for_ready,
            compression=compression,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._call, name)


class _StreamUnaryCallable:
    def __init__(self, call, metadata: _Metadata) -> None:
        self._call = call
        self._metadata = metadata

    def __call__(self, request_iterator, timeout=None, metadata=None, credentials=None, wait_for_ready=None, compression=None):
        return self._call(
            request_iterator,
            timeout=timeout,
            metadata=_merge_metadata(metadata, self._metadata),
            credentials=credentials,
            wait_for_ready=wait_for_ready,
            compression=compression,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._call, name)


class _StreamStreamCallable:
    def __init__(self, call, metadata: _Metadata) -> None:
        self._call = call
        self._metadata = metadata

    def __call__(self, request_iterator, timeout=None, metadata=None, credentials=None, wait_for_ready=None, compression=None):
        return self._call(
            request_iterator,
            timeout=timeout,
            metadata=_merge_metadata(metadata, self._metadata),
            credentials=credentials,
            wait_for_ready=wait_for_ready,
            compression=compression,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._call, name)


class _MetadataChannel:
    def __init__(self, channel: grpc.aio.Channel, metadata: _Metadata) -> None:
        self._channel = channel
        self._metadata = metadata

    def unary_unary(self, *args, **kwargs):
        return _UnaryUnaryCallable(self._channel.unary_unary(*args, **kwargs), self._metadata)

    def unary_stream(self, *args, **kwargs):
        return _UnaryStreamCallable(self._channel.unary_stream(*args, **kwargs), self._metadata)

    def stream_unary(self, *args, **kwargs):
        return _StreamUnaryCallable(self._channel.stream_unary(*args, **kwargs), self._metadata)

    def stream_stream(self, *args, **kwargs):
        return _StreamStreamCallable(self._channel.stream_stream(*args, **kwargs), self._metadata)

    def close(self) -> None:
        return self._channel.close()

    async def __aenter__(self):
        return await self._channel.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        return await self._channel.__aexit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._channel, name)


class SpiceDBAsyncClient(
    SchemaServiceStub,
    PermissionsServiceStub,
    ExperimentalServiceStub,
    WatchServiceStub,
):
    """Async SpiceDB client using an insecure channel with bearer token metadata."""

    def __init__(self, target: str, bearer_token: Optional[str]) -> None:
        channel: grpc.aio.Channel = grpc.aio.insecure_channel(target)
        if bearer_token:
            channel = _MetadataChannel(channel, [("authorization", f"Bearer {bearer_token}")])
        self._channel = channel
        SchemaServiceStub.__init__(self, channel)
        PermissionsServiceStub.__init__(self, channel)
        ExperimentalServiceStub.__init__(self, channel)
        WatchServiceStub.__init__(self, channel)


def create_spicedb_async_client(target: str, bearer_token: Optional[str]) -> SpiceDBAsyncClient:
    return SpiceDBAsyncClient(target=target, bearer_token=bearer_token)
