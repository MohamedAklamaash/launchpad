import httpx
from fastapi import Request, Response
from app.core.config import settings

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
                        for service_url in [settings.AUTH_SERVICE_URL, settings.USER_SERVICE_URL, settings.NOTIFICATION_SERVICE_URL]:
                            if v.startswith(service_url):
                                v = v.replace(service_url, str(request.base_url).rstrip("/"))
                                break
                    response_headers[k] = v
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers
            )
        except httpx.RequestError as exc:
            return Response(
                content=f"Error forwarding request: {exc}",
                status_code=502
            )
