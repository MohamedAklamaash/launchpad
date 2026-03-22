# Deployment Edge Cases & Troubleshooting

This document covers common deployment issues and their solutions.

---

## Port Configuration Issues

### Issue: App listens on different port than configured

**Symptom**: 503 errors, NGINX logs show "waiting for port", task fails after 180s

**Cause**: Application hardcodes a port (e.g., 8080) but deployment is configured for different port (e.g., 8000)

**Solution**: Platform auto-detects app port. NGINX tries:
1. Configured port (from `application.port` field)
2. Common ports: 8080, 8000, 3000, 5000

**Fix**: Redeploy application. NGINX will detect the actual port.

**Best Practice**: Make your app read `PORT` environment variable:
```javascript
// Node.js
const PORT = process.env.PORT || 8080;
app.listen(PORT, '0.0.0.0');
```

```python
# Python
import os
port = int(os.getenv("PORT", 8000))
uvicorn.run(app, host="0.0.0.0", port=port)
```

---

### Issue: API endpoints return 404

**Symptom**: App loads but API calls fail with 404

**Cause**: App defines routes with base path prefix (e.g., `/app-name/api/users`)

**Solution**: Don't use `BASE_PATH` in route definitions. NGINX handles path stripping.

**Wrong** (FastAPI):
```python
app = FastAPI(root_path="/app-name")  # Don't do this
```

**Correct** (FastAPI):
```python
app = FastAPI()  # Define routes at root level
@app.get("/api/users")  # NGINX strips /app-name before forwarding
```

---

## Build Issues

### Issue: Docker Hub rate limit (429 errors)

**Symptom**: Build fails with "toomanyrequests: You have reached your pull rate limit"

**Cause**: Docker Hub limits anonymous pulls to 100 per 6 hours

**Solution**: Use ECR Public Gallery for base images

**Wrong**:
```dockerfile
FROM node:21-alpine
```

**Correct**:
```dockerfile
FROM public.ecr.aws/docker/library/node:21-alpine
```

---

### Issue: Build succeeds but image not found

**Symptom**: Task fails with "CannotPullContainerError"

**Cause**: Image tag mismatch between build and task definition

**Solution**: Platform uses `{app-name}-latest` tag consistently

**Verify**:
```bash
aws ecr describe-images \
  --repository-name infra-{id}-repo \
  --region us-west-2
```

---

## Target Group Issues

### Issue: Service created but targets never healthy

**Symptom**: ALB returns 503, target group shows "unhealthy"

**Cause**: App doesn't respond to health check on `/`

**Solution**: Ensure app responds to `GET /` with 200-499 status

**Example** (Express):
```javascript
app.get('/', (req, res) => {
  res.send('OK');
});
```

**Example** (FastAPI):
```python
@app.get("/")
def health():
    return {"status": "healthy"}
```

---

### Issue: Target group not attached to ALB

**Symptom**: Listener rule created but traffic doesn't route

**Cause**: AWS propagation delay

**Solution**: Platform waits 5s + verifies attachment before proceeding

---

## Resource Configuration Issues

### Issue: Task fails with "Invalid CPU or memory value"

**Symptom**: Task definition creation fails

**Cause**: Fargate requires specific CPU/memory combinations

**Solution**: Platform auto-rounds to valid combinations:
- 0.25 vCPU: 0.5-2 GB
- 0.5 vCPU: 1-4 GB
- 1 vCPU: 2-8 GB
- 2 vCPU: 4-16 GB
- 4 vCPU: 8-30 GB

---

## Networking Issues

### Issue: Container can't reach internet

**Symptom**: App fails to fetch external APIs, install packages

**Cause**: Tasks in private subnet without NAT Gateway

**Solution**: Platform provisions NAT Gateway in public subnet

**Verify**:
```bash
aws ec2 describe-nat-gateways \
  --filter "Name=vpc-id,Values=vpc-{id}" \
  --region us-west-2
```

---

### Issue: ALB can't reach containers

**Symptom**: Health checks fail, targets marked unhealthy

**Cause**: Security group doesn't allow traffic from ALB

**Solution**: Platform auto-adds ingress rule:
- Source: ALB security group
- Port: Application port (or 80 for NGINX)
- Protocol: TCP

---

## Deployment Workflow Issues

### Issue: Deployment stuck in BUILDING state

**Symptom**: Status never progresses past BUILDING

**Cause**: CodeBuild job failed or timed out

**Solution**: Check CodeBuild logs:
```bash
aws logs tail /aws/codebuild/launchpad-build-{infra-id} \
  --region us-west-2 \
  --follow
```

---

### Issue: Deployment stuck in DEPLOYING state

**Symptom**: Task definition created but service never stable

**Cause**: Tasks failing health checks or crashing

**Solution**: Check ECS task logs:
```bash
aws logs tail /ecs/{app-name}-task \
  --region us-west-2 \
  --follow
```

---

## Common Error Messages

### "App never opened port after 180 seconds"

**Cause**: App not listening on any detected port

**Fix**: 
1. Check app logs for startup errors
2. Verify Dockerfile CMD starts the server
3. Ensure app listens on `0.0.0.0`, not `localhost`

---

### "Target group not attached to listener"

**Cause**: Listener rule creation failed

**Fix**: Check ALB listener rules:
```bash
aws elbv2 describe-rules \
  --listener-arn {listener-arn} \
  --region us-west-2
```

---

### "Service did not become stable within 300 seconds"

**Cause**: Tasks failing health checks repeatedly

**Fix**:
1. Check target group health: `aws elbv2 describe-target-health`
2. Verify app responds to `GET /` with 200-499
3. Check security group allows ALB → container traffic

---

## Debugging Checklist

When deployment fails:

1. **Check application logs**:
   ```bash
   aws logs tail /ecs/{app-name}-task --region us-west-2
   ```

2. **Check NGINX logs** (if using sidecar):
   ```bash
   aws logs tail /ecs/{app-name}-task --filter-pattern nginx --region us-west-2
   ```

3. **Check build logs**:
   ```bash
   aws logs tail /aws/codebuild/launchpad-build-{infra-id} --region us-west-2
   ```

4. **Check ECS service events**:
   ```bash
   aws ecs describe-services \
     --cluster {cluster-name} \
     --services {service-name} \
     --region us-west-2
   ```

5. **Check target group health**:
   ```bash
   aws elbv2 describe-target-health \
     --target-group-arn {tg-arn} \
     --region us-west-2
   ```

---

## Prevention Best Practices

1. **Use ECR Public Gallery** for base images
2. **Listen on 0.0.0.0** not localhost
3. **Respond to GET /** for health checks
4. **Read PORT env var** instead of hardcoding
5. **Define routes at root level** (no base path in app code)
6. **Test locally** with Docker before deploying
7. **Check logs immediately** if deployment fails

---

## Getting Help

If you encounter an issue not covered here:

1. Check application logs in CloudWatch
2. Verify AWS resources are created correctly
3. Review [System Architecture](SYSTEM_ARCHITECTURE.md) for expected setup
4. Check [context.md](../context.md) for technical details
