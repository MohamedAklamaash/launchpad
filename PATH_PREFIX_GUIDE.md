# Path Prefix Handling Guide

## Problem

Your application is deployed at: `http://alb-dns/your-app-name`

But your app expects requests at the root path `/`.

When users access `http://alb-dns/your-app-name/api/endpoint`, your app receives the request with the full path `/your-app-name/api/endpoint`, but it only has routes for `/api/endpoint`.

## Solution

The platform automatically injects a `PATH_PREFIX` environment variable (e.g., `/your-app-name`) into your container. Configure your application to use this prefix.

## Implementation by Framework

### FastAPI (Python)

```python
import os
from fastapi import FastAPI

# Get path prefix from environment
path_prefix = os.environ.get('PATH_PREFIX', '')

# Create app with root_path
app = FastAPI(root_path=path_prefix)

@app.get("/")
def read_root():
    return {"message": "Hello World"}

@app.get("/api/users")
def get_users():
    return {"users": []}
```

Now your app will work at both:
- `http://localhost:8000/` (local)
- `http://alb-dns/your-app-name/` (production)

### Flask (Python)

```python
import os
from flask import Flask

app = Flask(__name__)

# Get path prefix from environment
path_prefix = os.environ.get('PATH_PREFIX', '')

# Apply prefix to all routes
if path_prefix:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)
    app.config['APPLICATION_ROOT'] = path_prefix

@app.route('/')
def index():
    return {"message": "Hello World"}

@app.route('/api/users')
def get_users():
    return {"users": []}
```

### Express (Node.js)

```javascript
const express = require('express');
const app = express();

// Get path prefix from environment
const pathPrefix = process.env.PATH_PREFIX || '';

// Mount all routes under the prefix
const router = express.Router();

router.get('/', (req, res) => {
  res.json({ message: 'Hello World' });
});

router.get('/api/users', (req, res) => {
  res.json({ users: [] });
});

// Apply prefix
if (pathPrefix) {
  app.use(pathPrefix, router);
} else {
  app.use(router);
}

const PORT = process.env.PORT || 8080;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server running on port ${PORT} with prefix ${pathPrefix}`);
});
```

### Django (Python)

In `settings.py`:
```python
import os

# Get path prefix from environment
PATH_PREFIX = os.environ.get('PATH_PREFIX', '').strip('/')

# Set URL prefix
FORCE_SCRIPT_NAME = f'/{PATH_PREFIX}' if PATH_PREFIX else None
```

In `urls.py`:
```python
from django.urls import path, include
from django.conf import settings

urlpatterns = [
    path('', include('myapp.urls')),
]

# No need to manually add prefix - Django handles it via FORCE_SCRIPT_NAME
```

### NestJS (Node.js)

In `main.ts`:
```typescript
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  
  // Get path prefix from environment
  const pathPrefix = process.env.PATH_PREFIX || '';
  if (pathPrefix) {
    app.setGlobalPrefix(pathPrefix);
  }
  
  const port = process.env.PORT || 8080;
  await app.listen(port, '0.0.0.0');
}
bootstrap();
```

## Testing Locally

To test with the path prefix locally:

```bash
# Set the PATH_PREFIX environment variable
export PATH_PREFIX=/your-app-name

# Run your app
docker run -p 8000:8000 --env-file .env -e PATH_PREFIX=/your-app-name your-image:latest

# Test it
curl http://localhost:8000/your-app-name/
```

## Health Check

Make sure your health check endpoint works with the prefix:

```python
# FastAPI example
@app.get("/")
def health_check():
    return {"status": "healthy"}
```

The ALB will check: `http://container-ip:port/your-app-name/`

## Alternative: Use Root Path (Not Recommended)

If you want your app at the root (`http://alb-dns/`), you would need:
1. Only one app per infrastructure
2. Custom domain setup
3. Different routing strategy

The current path-based routing allows multiple apps to share one ALB, reducing costs.

## Common Issues

### Issue: 404 Not Found
**Cause:** App not configured to handle PATH_PREFIX
**Fix:** Implement one of the solutions above

### Issue: Static files not loading
**Cause:** Static file paths don't include prefix
**Fix:** Use relative paths or configure static file serving with prefix

### Issue: Redirects go to wrong URL
**Cause:** App generating absolute URLs without prefix
**Fix:** Use framework's URL generation that respects root_path/prefix
