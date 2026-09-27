# Runtime log tailing — data handling

The dashboard's **Logs** panel on an application lets the infrastructure owner tail that
application's own runtime output on demand. This page states exactly what Launchpad does
and does not do with that data.

## What it is

- Reads a bounded window (up to 60 minutes) of your application's own log output, live,
  directly from **your own AWS account** — CloudWatch Logs on ECS infrastructures, the pod
  logs of your own EKS cluster on Kubernetes infrastructures — through the same assumed
  `LaunchpadDeploymentRole` (or the cluster's `{cluster}-deploy` role on EKS) used for
  deployment.
- Every read is scoped server-side to the application you're viewing: which log streams or
  pods to read is derived from that application's own ECS service or Kubernetes namespace,
  never from anything a request could specify.

## What it is not

- **Nothing is stored, cached, or indexed by Launchpad.** Each view is a fresh read; closing
  the panel discards it. There is no history, no search, and no log export.
- **Never used for analytics or product telemetry**, and excluded from any future
  integration of that kind.
- **Never written to Launchpad's own application logs or error reports.** A failure while
  reading your logs is reported back as a generic error; the platform's own logs record
  only the failure's type and, where applicable, the AWS/Kubernetes error code — never your
  log content.
- **Not redacted.** These are your own application's logs, shown back to only you (the
  infrastructure owner); withholding lines by pattern-matching would mean the feature shows
  you nothing useful. Anything your application writes to stdout/stderr — including
  anything sensitive your application logs — appears exactly as written. Launchpad injects
  no platform credentials or settings into your application's environment, so this view
  cannot surface a Launchpad secret; it can surface whatever your own code writes.

## Who can see it

- **Owner only.** The infrastructure's owner — not an invited admin, even one who created
  the application. This is stricter than most of the dashboard, because this is the one
  place Launchpad reads your application's own output rather than metadata about it.

## What Launchpad keeps a record of

Every view (including a denied one) is recorded: who, which application, when, the time
window requested, and whether it succeeded — never the log content itself, never the
underlying CloudWatch stream or Kubernetes pod name, and never the pagination token. This
record exists so you (and we) can answer "who looked at this application's logs and when,"
not to reconstruct what was seen.

## Cost and rate limits

Each view costs a CloudWatch `FilterLogEvents` call (or a Kubernetes API call on EKS) **in
your own account**, subject to your account's own API throttling and normal AWS charges.
Launchpad also budgets this per user, independent of other endpoints, so one view of one
application's logs cannot exhaust another feature's allowance — or run up your bill by
polling faster than you asked it to.

## Auditability

On ECS infrastructures, every read shows up in your own CloudTrail as `FilterLogEvents`
issued by `LaunchpadDeploymentRole`. On EKS infrastructures, a pod-log read is issued by the
cluster's `{cluster}-deploy` role and is visible if you have Kubernetes/EKS audit logging
enabled on your cluster (verify this against a real cluster — see
[`plan/REAL-AWS-VALIDATION.md`](../plan/REAL-AWS-VALIDATION.md)).
