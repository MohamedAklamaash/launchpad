# Launchpad Platform — User Onboarding Guide

## Overview

Launchpad deploys your applications into **your own AWS account**. You keep full
control and ownership of your infrastructure and data — Launchpad never holds
long-lived credentials to your account; it assumes a role you create, scoped by a
per-infrastructure secret, and the temporary session credentials expire after ~1 hour.

Onboarding is **mostly automated**: you create an infrastructure in the dashboard,
copy the one-time setup command it generates, run it in a shell with AWS access to
your account, and provisioning starts automatically. You do **not** create IAM users
or paste access keys anywhere.

---

## Prerequisites

- An AWS account (and a shell with credentials/CLI for it — e.g. AWS CloudShell, or
  local `aws` configured for the account).
- A GitHub account.
- A GitHub repository containing a `Dockerfile`.

---

## Step 1 — Sign in to Launchpad

1. Go to the [Launchpad dashboard](https://launchpad-five-lilac.vercel.app/).
2. Click **Sign in with GitHub**.
3. Authorize Launchpad. The GitHub OAuth token lets Launchpad read the repos you
   deploy (including private ones) and register push webhooks for auto-deploy.

---

## Step 2 — Create an Infrastructure

An *infrastructure* is one AWS environment (one VPC/cluster/ALB) that your apps run in.

1. In the dashboard, click **Create Infrastructure**.
2. Fill in:
   - **Name** — e.g. `production`.
   - **AWS Account ID** — your 12-digit account number (e.g. `123456789012`). This is
     verified during onboarding: the setup script must run in *this* account.
   - **Region** — the AWS region to provision in.
   - **Max CPU / Max Memory** — the total vCPU/GB ceiling shared across all apps in this
     infrastructure (a guardrail so a runaway app can't exhaust the account).
3. Click **Create**.

The infrastructure is created in `PENDING` state and the dashboard shows a
**one-time onboarding token** plus a generated setup command. Nothing has been
provisioned in AWS yet — that starts after Step 3.

> ⚠️ **The onboarding token is shown only once and expires in 24 hours.** Copy the
> command now. If you lose it or it expires, delete the infrastructure and recreate
> it to get a fresh token. The token is single-use and stored only as a hash on our
> side — we cannot show it again.

---

## Step 3 — Run the AWS setup command

The dashboard generates a **Bootstrap script** snippet. It looks like this (the values
are pre-filled for your infrastructure):

```bash
export LAUNCHPAD_INFRA_ID=<your-infra-uuid>
export LAUNCHPAD_EXTERNAL_ID=<your-infra-uuid>
export LAUNCHPAD_CALLBACK_URL=https://<gateway>/api/infrastructures/onboarding/callback
export LAUNCHPAD_ONBOARDING_TOKEN=<one-time-token>
curl -sSL https://raw.githubusercontent.com/MohamedAklamaash/launchpad/<pinned-ref>/app_scripts/create_aws_role.sh | bash
```

Run it in a shell that has admin (or sufficient IAM) access to the AWS account you
named in Step 2 — **AWS CloudShell** in that account is the simplest option.

What the script does:

1. Creates the IAM role **`LaunchpadDeploymentRole`** (exact name required) with a
   trust policy that allows **only** Launchpad's platform principal to assume it, and
   **only** when presenting your infrastructure's `ExternalId` (a confused-deputy
   guard — see [IAM_POLICIES.md](./IAM_POLICIES.md)).
2. Attaches **`LaunchpadDeploymentPolicy`** (the permissions Launchpad needs — VPC,
   ECS, ECR, ELB, CloudWatch Logs, S3, DynamoDB, CodeBuild, IAM, KMS).
3. Calls back to Launchpad with your account ID and the onboarding token. Launchpad
   verifies the token and that the account matches, assumes the role to confirm access,
   then **automatically queues provisioning.**

> The script is idempotent and safe to re-run — if the role already exists it refreshes
> the trust policy in place. It requires the onboarding token to be valid (single-use,
> 24-hour TTL); a re-run after the token is consumed will skip the callback.

### What gets provisioned (in *your* account)

VPC with public/private subnets · NAT Gateway · ECS (Fargate) cluster · Application
Load Balancer · ECR repository · IAM service roles · S3 + DynamoDB for Terraform state.

**Status** moves `PENDING → PROVISIONING → ACTIVE` (typically 5–10 minutes). If it ends
in `ERROR`, the dashboard shows the reason; fix it and use **Reprovision**.

---

## Step 4 — Prepare your application

Your repo needs a `Dockerfile`. Two rules make deploys reliable:

- **Listen on `0.0.0.0`** (not `localhost`) and read the `PORT` env var.
- **Respond to `GET /`** with any 2xx–4xx so the load balancer health check passes.

```dockerfile
FROM public.ecr.aws/docker/library/node:18-alpine   # ECR Public avoids Docker Hub rate limits
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 8080
CMD ["npm", "start"]
```

```js
const PORT = process.env.PORT || 8080;
app.listen(PORT, "0.0.0.0");
```

See [DEPLOYMENT_EDGE_CASES.md](./DEPLOYMENT_EDGE_CASES.md) for port/health-check pitfalls.

---

## Step 5 — Create and deploy an Application

1. In the infrastructure, click **Create Application** and fill in:
   - **Name** — used in the app's URL path.
   - **Repository URL** and **Branch**.
   - **Dockerfile path** — default `Dockerfile`.
   - **CPU / Memory** — must fit a valid Fargate combination (see Resource Limits) and
     stay within the infrastructure's remaining quota.
   - **Environment variables** — your app's config.
2. The first deployment starts automatically. Status moves
   `CREATED → BUILDING → PUSHING_IMAGE → DEPLOYING → ACTIVE`:
   - **BUILDING** — CodeBuild builds your image (in your account).
   - **PUSHING_IMAGE** — image pushed to ECR.
   - **DEPLOYING** — ECS service + ALB rule created.
   - **ACTIVE** — reachable at `http://<alb-dns>/<app-name>`.

---

## Step 6 — Auto-deploy on `git push` (optional)

Launchpad can redeploy automatically whenever you push to the tracked branch.

1. On the application page, click **Generate webhook secret**. The dashboard shows a
   **webhook URL** and a **secret** (shown once).
2. In GitHub: **Settings → Webhooks → Add webhook**. Paste the URL and secret, set
   **Content type** to `application/json`, and select **Just the push event**.
3. Pushes to your tracked branch now trigger a deploy. Each delivery is HMAC-verified
   and de-duplicated, so redeliveries/retries won't double-deploy.

Re-generating the secret rotates it (the old one stops working immediately).

---

## Keeping IAM permissions current — the refresh script

Occasionally Launchpad widens the IAM permissions it needs (new AWS features). When
that happens, deployments may start failing with `AccessDenied`. The dashboard provides
a **Refresh policy script** (`update_aws_role.sh`) that re-applies the latest
`LaunchpadDeploymentPolicy` and trust policy in place — no need to recreate anything.

The refresh snippet includes a **per-user API key** so Launchpad can record *who* ran
the refresh, against which account, and when:

```bash
export LAUNCHPAD_INFRA_ID=<your-infra-uuid>
export LAUNCHPAD_EXTERNAL_ID=<your-infra-uuid>
export LAUNCHPAD_CALLBACK_URL=https://<gateway>/api/infrastructures/policy-refresh/callback
export LAUNCHPAD_API_KEY=<generated-key>
curl -sSL https://raw.githubusercontent.com/MohamedAklamaash/launchpad/<pinned-ref>/app_scripts/update_aws_role.sh | bash
```

Click **Generate API key** next to the snippet to fill in `LAUNCHPAD_API_KEY`. The key
is shown once and stored only as a hash; generating a new one revokes the previous key.
The attribution callback is best-effort — if it can't reach Launchpad the IAM refresh
still succeeded.

---

## Resource Limits

### Fargate CPU/Memory combinations

| CPU (vCPU) | Memory (GB) |
|------------|-------------|
| 0.25       | 0.5 – 2     |
| 0.5        | 1 – 4       |
| 1          | 2 – 8       |
| 2          | 4 – 16      |
| 4          | 8 – 30      |

### Infrastructure quota

`max_cpu` / `max_memory` cap the total across all apps in an infrastructure. Example:
with `max_cpu = 4` you can run four 1-vCPU apps, eight 0.5-vCPU apps, or any combination
totalling ≤ 4 vCPU. Raise the limits in infrastructure settings, or delete unused apps.

---

## Monitoring

- **App logs** — CloudWatch log group `/ecs/<app-name>-task`.
- **Build logs** — `/aws/codebuild/launchpad-build-<infra-id>`.
- **Containers** — ECS → Clusters → your cluster → services/tasks.
- **Cost** — everything runs in your account; use AWS Cost Explorer (filter ECS/ECR/ALB/
  NAT Gateway).

---

## Cleanup

- **Delete Application** — removes its ECS service, task definition, target group, and
  ALB listener rule.
- **Delete Infrastructure** — tears down the whole environment (VPC, NAT, ALB, ECS, ECR,
  state). You must delete all applications first. A `PENDING` infrastructure that was
  never onboarded can be deleted immediately.
- **Revoke Launchpad's access** — delete `LaunchpadDeploymentRole` and
  `LaunchpadDeploymentPolicy` in your account (clean up provisioned resources first).
  See [IAM_POLICIES.md](./IAM_POLICIES.md#revoking-access).

---

## Security model (how your account stays yours)

- **No long-lived keys.** Launchpad assumes `LaunchpadDeploymentRole` via STS; the
  session credentials expire (~1h) and are refreshed as needed. They are never returned
  through the API.
- **Confused-deputy protection.** The role's trust policy requires both Launchpad's
  platform principal *and* your infrastructure's `ExternalId`. Treat the setup command
  as sensitive while onboarding.
- **Single-use onboarding token**, hashed at rest, 24-hour TTL.
- **Per-app webhook secrets** with HMAC verification and delivery de-duplication.
- **Network isolation** — apps run in private subnets; only the ALB is public.

---

## Troubleshooting

### Onboarding didn't start provisioning
- Confirm the setup command ran in the AWS account whose ID you entered (it must match).
- Confirm the onboarding token hadn't expired (24h) — if so, recreate the infrastructure.
- Re-run the bootstrap command; it's safe to re-run.

### Provisioning failed (`ERROR`)
- The dashboard shows the reason. Common causes: the IAM role/policy wasn't created
  (re-run the bootstrap script), an unsupported region, or a service quota in your
  account. Use **Reprovision** after fixing.

### Deployments fail with `AccessDenied`
- Run the **Refresh policy script** (`update_aws_role.sh`) — Launchpad's required
  permissions likely widened since you onboarded.

### Application won't deploy or isn't reachable
- See [DEPLOYMENT_EDGE_CASES.md](./DEPLOYMENT_EDGE_CASES.md): port detection, health
  checks on `GET /`, Docker Hub rate limits, and the CloudWatch/ECS debugging checklist.

---

## FAQ

**Do you store my AWS credentials?** No long-lived keys. We use temporary STS session
credentials from AssumeRole (≈1-hour lifetime, auto-refreshed) and never return them via
the API.

**Why an ExternalId / onboarding token?** They prevent anyone else from binding *your*
account to Launchpad and stop a confused-deputy attack on the assume-role.

**Can I use my existing VPC?** Not yet — Launchpad provisions a dedicated VPC for isolation.

**What regions are supported?** Selectable at infrastructure creation (the UI may
restrict the list while the feature stabilizes).

**Can I SSH into containers?** No. Use CloudWatch Logs. ECS Exec is planned.

**How do I update my app?** Push to the tracked branch (with the webhook configured), or
click **Deploy** again in the dashboard.

**Can I deploy databases?** Not managed by Launchpad — use AWS RDS for production databases.

**Is there a free tier?** The Launchpad platform is free; you pay only for the AWS
resources in your own account.

---

## Support

- **API reference** — `/docs` on the API gateway endpoint.
- **Scripts** — [`app_scripts/`](https://github.com/MohamedAklamaash/launchpad/tree/main/app_scripts).
- **Contact** — aklamaash78@gmail.com
