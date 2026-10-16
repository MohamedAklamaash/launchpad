"""`proxy_stream_request`: passthrough of an SSE upstream response's chunks and headers,
and that a client disconnect (`StreamingResponse.body_iterator` being closed, same as
Starlette does when a browser connection drops) closes the upstream connection, the
thing that lets application-service's own generator run its `finally` and release its
concurrency slot instead of holding it until the stream's 10-minute cap."""
import asyncio
from unittest.mock import patch

import httpx
from app.services import proxy
from starlette.requests import Request

# Captured before any test patches `proxy.httpx.AsyncClient`, that patch replaces the
# attribute on the single shared `httpx` module object, so a fake client built *during*
# the patch that called `httpx.AsyncClient(...)` again would recurse into itself. Every
# fake client below builds its real httpx client from this saved reference instead.
_RealAsyncClient = httpx.AsyncClient


def _make_request(path="/api/applications/x/logs/stream"):
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"host", b"gw"), (b"authorization", b"Bearer t")],
        "query_string": b"container=app",
        "client": ("1.2.3.4", 1234),
        "scheme": "http",
        "server": ("gw", 8000),
        "path": path,
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


class _TrackingStream(httpx.AsyncByteStream):
    """A fake upstream SSE body: yields fixed chunks and records whether `aclose()` was
    called, so a test can assert the connection was actually torn down rather than just
    that the generator stopped."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def _fake_async_client_factory(mock_transport):
    class _FakeClient:
        def __init__(self, timeout=None):
            self._real = _RealAsyncClient(transport=mock_transport, timeout=timeout)

        def build_request(self, *args, **kwargs):
            return self._real.build_request(*args, **kwargs)

        async def send(self, *args, **kwargs):
            return await self._real.send(*args, **kwargs)

        async def aclose(self):
            await self._real.aclose()

    return _FakeClient


async def _collect(body_iterator):
    out = []
    async for chunk in body_iterator:
        out.append(chunk)
    return out


def test_sse_passthrough_streams_chunks_and_headers_in_order():
    stream = _TrackingStream([b"event: message\ndata: {}\n\n", b": ping\n\n"])

    def handler(request):
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream", "cache-control": "no-cache, no-store",
                "x-accel-buffering": "no", "x-request-id": "req-1",
            },
            stream=stream,
        )

    with patch.object(proxy.httpx, "AsyncClient", _fake_async_client_factory(httpx.MockTransport(handler))):
        response = asyncio.run(proxy.proxy_stream_request("http://appsvc/api/v1/x/logs/stream/", _make_request()))

    assert isinstance(response, proxy.StreamingResponse)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-request-id"] == "req-1"

    chunks = asyncio.run(_collect(response.body_iterator))
    assert chunks == [b"event: message\ndata: {}\n\n", b": ping\n\n"]
    assert stream.closed is True


def test_client_disconnect_closes_the_upstream_connection():
    stream = _TrackingStream([b"event: message\ndata: {}\n\n", b": ping\n\n", b": ping\n\n"])

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    with patch.object(proxy.httpx, "AsyncClient", _fake_async_client_factory(httpx.MockTransport(handler))):
        response = asyncio.run(proxy.proxy_stream_request("http://appsvc/api/v1/x/logs/stream/", _make_request()))

    async def _pull_one_then_disconnect():
        first = await response.body_iterator.__anext__()
        assert first == b"event: message\ndata: {}\n\n"
        # Starlette calls this on the response's body_iterator when the client
        # disconnects mid-stream, same as it would on our generator here.
        await response.body_iterator.aclose()

    asyncio.run(_pull_one_then_disconnect())
    assert stream.closed is True


def test_non_sse_upstream_response_is_returned_as_an_ordinary_response():
    """A 429 from application-service's pre-stream budget/concurrency checks is plain
    JSON, not text/event-stream, it must pass through with its body and headers intact
    rather than being wrapped in a StreamingResponse."""

    def handler(request):
        return httpx.Response(
            429, headers={"content-type": "application/json", "retry-after": "5"},
            content=b'{"error": "Too many requests for this operation, try again later"}',
        )

    with patch.object(proxy.httpx, "AsyncClient", _fake_async_client_factory(httpx.MockTransport(handler))):
        response = asyncio.run(proxy.proxy_stream_request("http://appsvc/api/v1/x/logs/stream/", _make_request()))

    assert not isinstance(response, proxy.StreamingResponse)
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "5"
    assert b"Too many requests" in response.body
