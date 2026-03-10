# Automatic Path Prefix Stripping

## Solution

Added an NGINX sidecar container to automatically strip the path prefix before forwarding requests to the application. **No code changes required from users.**

## How It Works

### Before (Broken)
```
User Request: http://alb-dns/apexmarket-v1/api/endpoint
    ↓
ALB forwards: /apexmarket-v1/api/endpoint
    ↓
App receives: /apexmarket-v1/api/endpoint
    ↓
App expects: /api/endpoint
    ↓
Result: 404 Not Found ❌
```

### After (Fixed)
```
User Request: http://alb-dns/apexmarket-v1/api/endpoint
    ↓
ALB forwards to NGINX: /apexmarket-v1/api/endpoint
    ↓
NGINX strips prefix: /api/endpoint
    ↓
NGINX proxies to app: /api/endpoint
    ↓
App receives: /api/endpoint
    ↓
Result: 200 OK ✅
```

## Architecture

Each ECS task now runs two containers:

1. **NGINX Sidecar** (port 80)
   - Receives traffic from ALB
   - Strips path prefix (`/app-name`)
   - Proxies to application container

2. **Application Container** (user's port)
   - Receives clean requests without prefix
   - Works exactly as it does locally
   - No code changes needed

## NGINX Configuration

Automatically generated per application:

```nginx
location /apexmarket-v1/ {
    rewrite ^/apexmarket-v1/(.*) /$1 break;
    proxy_pass http://localhost:8000;
}
location /apexmarket-v1 {
    rewrite ^/apexmarket-v1$ / break;
    proxy_pass http://localhost:8000;
}
```

## Changes Made

### Files Modified
- `aws/ecs.py` - Added NGINX sidecar to task definition
- `api/services/application_deployment_service.py` - Updated to use port 80 and NGINX container

### Key Updates
1. Task definition now includes NGINX container
2. Target group uses port 80 (NGINX) instead of app port
3. ECS service routes to NGINX container
4. Security group allows port 80
5. NGINX config generated dynamically per app

## Benefits

✅ **Zero code changes** - Apps work as-is  
✅ **Works with any framework** - FastAPI, Flask, Express, Django, etc.  
✅ **Transparent** - Apps don't know about the prefix  
✅ **Standard pattern** - Common in production deployments  

## Resource Impact

- Minimal CPU/memory overhead (NGINX is lightweight)
- NGINX container uses ~10MB memory
- No performance impact (NGINX is extremely fast)

## Testing

Deploy any application without path prefix handling:
1. App has routes at `/`, `/api/users`, etc.
2. Deploy to platform
3. Access at `http://alb-dns/app-name/api/users`
4. Works without code changes ✅
