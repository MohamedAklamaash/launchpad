# Complete Fix for Environment Variables and 503 Errors

## Issues Fixed

### 1. **Logs Being Deleted**
**Problem:** CloudWatch logs were deleted when app was deleted, making debugging impossible.

**Fix:** Logs are now retained after deletion.
- Location: `/ecs/{app-name}-task`
- Streams: `app/*` (your application), `nginx/*` (NGINX sidecar)

### 2. **Container Startup Order**
**Problem:** NGINX tried to proxy before app was ready → 503 errors.

**Fix:** Added `dependsOn` to ensure app starts before NGINX.
```json
{
  "dependsOn": [{
    "containerName": "app-name-task",
    "condition": "START"
  }]
}
```

### 3. **Proxy Timeouts**
**Problem:** NGINX had default short timeouts.

**Fix:** Added 60s timeouts:
```nginx
proxy_connect_timeout 60s;
proxy_send_timeout 60s;
proxy_read_timeout 60s;
```

### 4. **Environment Variables**
Environment variables ARE being passed correctly. The flow is:

```
API Request with envs
    ↓
Application.objects.create(envs={...})
    ↓
Stored in database (JSONField)
    ↓
Retrieved during deployment
    ↓
Passed to ECS task definition
    ↓
Available in container as process.env
```

## Debugging Environment Variables

### Check if envs are in database:
```bash
# Get application details
GET /applications/{app-id}/

# Response should include:
{
  "envs": {
    "NODE_ENV": "production",
    "FINNHUB_API_KEY": "...",
    "GEMINI_API_KEY": "...",
    "ALPHA_VANTAGE_API_KEY": "..."
  }
}
```

### Check ECS task definition:
1. Go to AWS Console → ECS → Task Definitions
2. Find `{app-name}-task:latest`
3. Click on it → Container definitions → Environment variables
4. Should see all your envs listed

### Check container logs:
```bash
# In your app, log environment variables on startup
console.log('Environment:', {
  NODE_ENV: process.env.NODE_ENV,
  HAS_FINNHUB: !!process.env.FINNHUB_API_KEY,
  HAS_GEMINI: !!process.env.GEMINI_API_KEY,
  HAS_ALPHA: !!process.env.ALPHA_VANTAGE_API_KEY
});
```

Then check CloudWatch logs:
- Log group: `/ecs/{app-name}-task`
- Stream: `app/*`

## Debugging 503 Errors

### Check Target Health:
1. AWS Console → EC2 → Target Groups
2. Find `{app-name}-tg`
3. Targets tab → Should show "Healthy"

If "Unhealthy":
- Check "Health status details" column
- Common reasons:
  - App not responding on port 8000
  - App not responding to `GET /`
  - Container crashed

### Check Container Logs:

**App Container:**
```
Log group: /ecs/{app-name}-task
Stream: app/*
```

Look for:
- Startup errors
- Port binding errors
- Missing environment variables
- Crashes

**NGINX Container:**
```
Log group: /ecs/{app-name}-task
Stream: nginx/*
```

Look for:
- Config errors
- Proxy errors
- Connection refused to localhost:8000

### Check ECS Service:
1. AWS Console → ECS → Clusters
2. Find your cluster
3. Services → `{app-name}-service`
4. Tasks tab → Should show 1 running task

If no running tasks:
- Click on "Events" tab
- Look for error messages

## Common Issues

### Issue: API returns default values (0, empty)
**Cause:** Environment variables not loaded in app

**Fix:**
```javascript
// Make sure you're reading from process.env
const apiKey = process.env.FINNHUB_API_KEY;

// NOT from a .env file (doesn't exist in container)
// NOT from hardcoded values
```

### Issue: Static files return 503
**Cause:** App container not running or not serving static files

**Fix:**
1. Check app container logs
2. Verify app serves static files:
   ```javascript
   app.use(express.static('public'));
   // or
   app.use('/static', express.static('static'));
   ```
3. Test locally: `docker run -p 8000:8000 your-image`

### Issue: HTML/CSS/JS all 503
**Cause:** Entire app is down

**Fix:**
1. Check ECS task is running
2. Check app container logs for crashes
3. Verify Dockerfile CMD is correct
4. Test image locally

## Testing Checklist

After redeployment:

1. ✅ Check target group health (should be "Healthy")
2. ✅ Check ECS task is running (1/1 tasks)
3. ✅ Check app container logs (no errors)
4. ✅ Check NGINX container logs (no errors)
5. ✅ Test health check: `curl http://alb-dns/`
6. ✅ Test app: `curl http://alb-dns/{app-name}/`
7. ✅ Test static files: `curl http://alb-dns/{app-name}/static/app.js`
8. ✅ Verify API returns real data (not zeros)

## Next Steps

1. **Retry deployment** with fixes:
   ```
   POST /applications/{app-id}/retry/
   ```

2. **Wait 2-3 minutes** for:
   - Build to complete
   - Task to start
   - Health checks to pass

3. **Check logs immediately**:
   - CloudWatch → `/ecs/{app-name}-task`
   - Look at both `app/*` and `nginx/*` streams

4. **Verify environment variables** in logs:
   - Add logging to your app startup
   - Check if keys are present

5. **Test endpoints**:
   - Health: `http://alb-dns/`
   - App: `http://alb-dns/{app-name}/`
   - API: `http://alb-dns/{app-name}/api/trending`
