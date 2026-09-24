# F6 — Exit export

**Status:** not started · **Depends on:** F3 (tag pinning), F5 (generator)
**Blocked by:** nothing · **Priority:** last

Last for a reason: it depends on two other features, and its value only materialises when
a customer actually wants to leave. Worth having before an enterprise deal asks about it.

## Goal

Leave a customer fully operational with `LaunchpadDeploymentRole` deleted.

## Verified on main at `9c8743d`

**The good news is real.** Terraform state, its bucket
(`launchpad-tf-state-{account}-{region}`) and the lock table already live in the customer's
account, and the root config is deterministically regenerable from
`terraform_worker._generate_config`. Nothing to migrate.

**The catch is also real.** The entire application layer is imperative and in no terraform
state: ECS task definitions, services, target groups, listener rules; the CodeBuild project
and its IAM role; the EKS bootstrap IngressClass, namespace and CNI patch; every per-app
Kubernetes object. A terraform state dump hands the customer **a cluster with no
applications in it**.

## Design

**A continuity export, not an IaC reconstruction.** Everything keeps running if Launchpad
simply stops touching it. The export's job is documentation and handover, not
reproduction.

Tarball:

- README with a full inventory and ARNs.
- Revocation instructions — delete `LaunchpadDeploymentRole`.
- The generated root `main.tf` + modules + a `backend.hcl` pointing at their **existing**
  state bucket.
- Cleaned Kubernetes manifest dumps and ECS task-definition JSON.
- The buildspec as a CI seed.

Full terraform import codegen for the imperative layer is the ideal end state, is large and
brittle, and is not needed for continuity.

## H6 is the design — this is a secrets-bearing archive

Three of the four contents carry secrets today:

- Kubernetes manifest dumps — `Secret.data` is base64, not encryption; Deployment `env` is
  plaintext.
- ECS task-definition JSON — `environment` is plaintext by definition.
- The buildspec — `codebuild.py` has a plaintext GitHub-token fallback.

And `backend.hcl` points at the terraform state bucket, where state stores generated
passwords in plaintext.

Requirements, all of them:

- **Owner-only; invited ADMINs refused.** On a shared infrastructure an invited ADMIN could
  otherwise exfiltrate the env vars of every app including other users'.
- Re-authentication for this operation specifically.
- **Redacted by default** — key names with `<redacted>` values.
- Never persisted: temp dir `0600`, stream it, delete in `finally`.
- **No request-supplied path component.**
- Size cap and timeout.
- An audit log.

F3's `Deployment` snapshot helps here: it already stores shape-not-values, so the export
can reuse it rather than re-reading live env values.

## Files

`infrastructure-service/api/services/exit_export.py` · owner-only endpoint with
re-authentication · gateway route · a dashboard action behind a confirmation.

## Tests

`terraform init` with the bundled `backend.hcl` succeeds in a sandbox · **credential scan
of the archive is clean** (seed a secret into every one of the four content types and
assert absence) · invited ADMIN refused · no request-supplied path reaches the filesystem ·
the temp file is gone after both the success and failure paths.

## Security pre-review

**Required.** This is the highest-consequence export surface in the product: a single
archive spanning every application's configuration. H6 exists because the first draft of
this feature would have shipped secrets.

## Open questions

1. Does "redacted by default" need an opt-in un-redacted mode for a genuinely departing
   customer, and if so what gates it?
2. Should the export be rate-limited or throttled per infrastructure, given it is expensive
   and an obvious exfiltration target?

## Out of scope

Terraform import codegen for the imperative layer. Anything that writes to the customer's
account — the export is strictly read-only.
