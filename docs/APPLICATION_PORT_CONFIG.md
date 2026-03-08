# Application Port Configuration

## How It Works

Launchpad automatically configures your application to listen on **port 8000**.

### Environment Variable

The platform injects a `PORT` environment variable set to `8000` into your container. Your application should read this variable and listen on that port.

### Example Code

**Node.js/Express**:
```javascript
const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {
  res.send('Hello World!');
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server listening on port ${PORT}`);
});
```

**Python/Flask**:
```python
import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello World!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
```

**Go**:
```go
package main

import (
    "fmt"
    "net/http"
    "os"
)

func main() {
    port := os.Getenv("PORT")
    if port == "" {
        port = "8080"
    }

    http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
        fmt.Fprintf(w, "Hello World!")
    })

    http.ListenAndServe(":"+port, nil)
}
```

## Important Notes

### 1. Listen on 0.0.0.0, not localhost

❌ **Wrong**:
```javascript
app.listen(PORT, 'localhost')  // Won't work in container
```

✅ **Correct**:
```javascript
app.listen(PORT, '0.0.0.0')  // Accessible from outside container
```

### 2. Health Check Endpoint

Your application must respond to `GET /` requests. The ALB health check hits this endpoint every 30 seconds.

**Minimum viable health check**:
```javascript
app.get('/', (req, res) => {
  res.status(200).send('OK');
});
```

### 3. Dockerfile EXPOSE

While not strictly required, it's good practice to document the port in your Dockerfile:

```dockerfile
FROM public.ecr.aws/docker/library/node:21-alpine

WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .

EXPOSE 8000

CMD ["node", "index.js"]
```

## Troubleshooting

### 504 Gateway Timeout

**Symptom**: Deployment succeeds but accessing the URL returns 504.

**Causes**:
1. App not listening on port 8000
2. App listening on localhost instead of 0.0.0.0
3. App crashed or not starting
4. Health check endpoint (/) not responding

**Check logs**:
```bash
aws logs tail /ecs/{app-name}-task --region us-west-2 --follow
```

**Check target health**:
```bash
aws elbv2 describe-target-health \
  --target-group-arn {target-group-arn} \
  --region us-west-2
```

### Common Issues

**Issue**: App uses hardcoded port 3000
```javascript
app.listen(3000)  // ❌ Ignores PORT env var
```

**Fix**: Use environment variable
```javascript
app.listen(process.env.PORT || 3000)  // ✅
```

**Issue**: Health check returns 404
```javascript
// No route for /
app.get('/api/health', ...)  // ❌ Health check hits /
```

**Fix**: Add root route
```javascript
app.get('/', (req, res) => res.send('OK'))  // ✅
```

## Health Check Configuration

Current settings:
- **Path**: `/`
- **Interval**: 30 seconds
- **Timeout**: 10 seconds
- **Healthy threshold**: 2 consecutive successes
- **Unhealthy threshold**: 5 consecutive failures
- **Success codes**: 200-499 (any non-5xx)

Your app must respond to `GET /` within 10 seconds with a non-5xx status code.

## Custom Ports (Future)

Currently, all applications must use port 8000. Future versions may support custom ports via application configuration.

## Path-Based Routing

Your application is accessible at:
```
http://{alb-dns}/{app-name}/*
```

The ALB forwards all requests matching `/{app-name}/*` to your container on port 8000.

**Example**:
- Deployment URL: `http://alb-123.us-west-2.elb.amazonaws.com/my-app`
- Your app receives: `GET /my-app` (full path preserved)

If your app expects routes without the prefix, you'll need to handle it:

```javascript
// Option 1: Mount app at subpath
app.use('/my-app', router);

// Option 2: Strip prefix in middleware
app.use((req, res, next) => {
  req.url = req.url.replace(/^\/my-app/, '') || '/';
  next();
});
```
