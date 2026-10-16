import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

# Read must comfortably outlive the heartbeat interval (shared.sse.framing's 15s) so a
# merely-quiet upstream never looks dead to this timeout. A genuinely dead upstream is
# still caught, just two heartbeats late rather than one. Distinct from proxy_request's
# flat 10.0s, which would otherwise cut every stream off at the 10-second mark.
STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)
# Headers from the upstream SSE response worth forwarding to the browser. Everything
# else (content-encoding, content-length, transfer-encoding, connection) is excluded the
# same way proxy_request excludes them for a normal response.
STREAM_RESPONSE_HEADERS = ("cache-control", "x-accel-buffering", "x-request-id")


async def proxy_request(url: str, request: Request) -> Response:
    async with httpx.AsyncClient() as client:
        method = request.method
        headers = dict(request.headers)
        
        headers["x-forwarded-for"] = request.client.host if request.client else "unknown"
        headers["x-forwarded-proto"] = request.url.scheme
        headers["x-forwarded-host"] = request.headers.get("host", "")
        
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("connection", None)
        # Strip any client-supplied internal token (Starlette lowercases header keys) so the
        # gateway is the sole source of this trusted S2S header — never forward a second copy.
        headers.pop("x-internal-token", None)
        headers.pop("X-INTERNAL-TOKEN", None)
        headers["X-INTERNAL-TOKEN"] = settings.INTERNAL_API_TOKEN
        
        body = await request.body()
        
        try:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=request.query_params,
                content=body,
                timeout=10.0,
                follow_redirects=False # Ensure the gateway doesn't follow redirects internally
            )
            
            exclude_headers = ["content-encoding", "content-length", "transfer-encoding", "connection"]
            response_headers = {}
            for k, v in response.headers.items():
                if k.lower() not in exclude_headers:
                    if k.lower() == "location":                        
                        for service_url in [settings.AUTH_SERVICE_URL, settings.USER_SERVICE_URL, settings.NOTIFICATION_SERVICE_URL, settings.APPLICATION_SERVICE_URL, settings.INFRASTRUCTURE_SERVICE_URL, settings.PAYMENT_SERVICE_URL]:
                            if v.startswith(service_url):
                                v = v.replace(service_url, str(request.base_url).rstrip("/"))
                                break
                    response_headers[k] = v
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers
            )
        except httpx.TimeoutException:
            return Response(content="Gateway Timeout", status_code=504)
        except Exception:
            # No raise_for_status() above, so httpx.HTTPStatusError can't reach here.
            # Keep the upstream URL / transport error in logs only — leaking str(exc)
            # to the client exposes internal hostnames and TCP error detail.
            import json
            logger.exception(f"Error forwarding request to {url}")
            return Response(
                content=json.dumps({"message": "Error forwarding request"}),
                status_code=502,
                media_type="application/json"
            )


async def proxy_stream_request(url: str, request: Request) -> Response:
    """Forwards a GET to one of application-service's SSE log-stream endpoints.

    Unlike `proxy_request`, the `httpx.AsyncClient` is not used as a context manager
    around the whole call: a `StreamingResponse`'s body iterator keeps running after
    this function returns, long after an `async with` block here would have already
    closed the client out from under it. The client (and the upstream response) are
    instead closed from inside that iterator's own `finally`, once Starlette has
    finished (or, on a client disconnect, cancelled) iterating it, so the upstream
    connection never outlives the browser connection it was opened for.

    A non-200 or non-SSE upstream response (403/429/503 JSON errors from the view's own
    pre-stream checks) is read and returned as an ordinary `Response` instead, with its
    body and headers forwarded intact: the same error shape `proxy_request` returns for
    every other endpoint.
    """
    headers = dict(request.headers)
    headers["x-forwarded-for"] = request.client.host if request.client else "unknown"
    headers["x-forwarded-proto"] = request.url.scheme
    headers["x-forwarded-host"] = request.headers.get("host", "")

    headers.pop("host", None)
    headers.pop("content-length", None)
    headers.pop("connection", None)
    # text/event-stream is never compressed: stripped so the upstream doesn't gzip a
    # body this function reads chunk-by-chunk as plain text.
    headers.pop("accept-encoding", None)
    headers.pop("x-internal-token", None)
    headers.pop("X-INTERNAL-TOKEN", None)
    headers["X-INTERNAL-TOKEN"] = settings.INTERNAL_API_TOKEN

    client = httpx.AsyncClient(timeout=STREAM_TIMEOUT)
    try:
        upstream_request = client.build_request(
            "GET", url, headers=headers, params=request.query_params,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        await client.aclose()
        return Response(content="Gateway Timeout", status_code=504)
    except Exception:
        await client.aclose()
        import json
        logger.exception(f"Error forwarding stream request to {url}")
        return Response(
            content=json.dumps({"message": "Error forwarding request"}),
            status_code=502,
            media_type="application/json",
        )

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code != 200 or not content_type.startswith("text/event-stream"):
        body = await upstream.aread()
        exclude_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in exclude_headers}
        await upstream.aclose()
        await client.aclose()
        return Response(content=body, status_code=upstream.status_code, headers=response_headers)

    async def body_iterator():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            # Reached on normal exhaustion (the view ended the stream) and, just as
            # importantly, on cancellation: Starlette cancels this generator once the
            # client disconnects, which raises here at the `async for` the same way a
            # client disconnect propagates through application-service's own generator
            # (see runtime_logs.py's `_stream_generator`). Closing the upstream
            # connection is what lets that generator's `finally` run and release its
            # concurrency slot instead of leaking it for the rest of the stream's
            # 10-minute cap.
            await upstream.aclose()
            await client.aclose()

    response_headers = {k: upstream.headers[k] for k in STREAM_RESPONSE_HEADERS if k in upstream.headers}
    return StreamingResponse(
        body_iterator(), status_code=upstream.status_code,
        media_type="text/event-stream", headers=response_headers,
    )
