# Path Prefix Issue - Quick Fix

## The Problem
- Your app is deployed at: `http://alb-dns/apexmarket-v1`
- ALB forwards requests with full path: `/apexmarket-v1/endpoint`
- Your app only has routes for `/endpoint`
- Result: 404 Not Found

## The Fix

### For FastAPI (Your Case)
Update your `app.py` or `main.py`:

```python
import os
from fastapi import FastAPI

# Add this line - tells FastAPI about the path prefix
path_prefix = os.environ.get('PATH_PREFIX', '')
app = FastAPI(root_path=path_prefix)

# Your existing routes stay the same
@app.get("/")
def read_root():
    return {"message": "Hello"}
```

That's it! The platform automatically sets `PATH_PREFIX=/apexmarket-v1` in your container.

### Test Locally
```bash
export PATH_PREFIX=/apexmarket-v1
docker run -p 8000:8000 --env-file .env -e PATH_PREFIX=/apexmarket-v1 stock-apex:latest

# Test it
curl http://localhost:8000/apexmarket-v1/
```

### Redeploy
1. Update your code with the fix above
2. Push to GitHub
3. Retry deployment: `POST /applications/{id}/retry/`

## Why This Happens
Multiple apps share one ALB to save costs. Each app gets a unique path:
- App 1: `http://alb-dns/app1/*`
- App 2: `http://alb-dns/app2/*`
- Your app: `http://alb-dns/apexmarket-v1/*`

See `PATH_PREFIX_GUIDE.md` for other frameworks (Express, Flask, Django, etc.)
