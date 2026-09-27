# F3 — Rollback

**Status:** **done** (mock-verified; see REAL-AWS-VALIDATION)
**Depends on:** nothing further · **Blocked by:** nothing

Pure software, no external dependency, no customer action. The reason to do it after F4 is
only that F4 loses data every day it waits and this does not.

## Goal

One-click rollback to any previously deployed image, restoring the configuration that
shipped with it, skipping CodeBuild entirely.

## Verified on main at `9c8743d`

What #69 already put in place:

- The buildspec pushes three tags: `-latest`, `$IMAGE_TAG`, and `$APP_NAME-$RESOLVED_SHA`.
- `RESOLVED_SHA` comes from `git rev-parse HEAD` *after* checkout — what was actually
  built, not what was requested — and is returned through CodeBuild exported-variables.
- ECS pins its task definition to the resolved SHA
  (`application_deployment_service.py:61` → `_create_task_definition(resolved_sha=…)`).
- EKS uses `_image_tag(application)` from `api/common/naming.py`, chosen before the build
  because the Kubernetes deployer needs a tag up front.
- The GitHub webhook advances `project_commit_hash` to the pushed SHA.
- An ECR lifecycle policy retains the last 10 tagged images and never expires `-latest`.

What is missing: **everything that records a deploy.** `api/models/` has no `Deployment`
model. There is no history to roll back to, only images in ECR.

## Design

**An append-only `Deployment` model.** App FK, `image_tag`, `commit_sha`, `status`,
`triggered_by`, timestamps — plus the config snapshot, which is the part with a hard
constraint.

**H7 is non-negotiable: snapshot the shape, not the values.** `Application.envs` is
plaintext. Snapshotting values per deploy turns one plaintext row per app into one per
deploy, forever, append-only. That breaks rotation as a remediation (old values persist in
every prior snapshot), leaves no purge path for an erasure request, and hands two new
readers (rollback UI, exit export) a copy each.

Store instead: env **key names**, CPU, memory, port, image tag, commit SHA, and a content
hash of the values. Re-read current env values at rollback time. That is also the behaviour
customers want — rolling back code should not roll back a rotated credential.

**The rollback branch** skips CodeBuild entirely: restore the snapshot, then register a
task definition pinned to the old tag (ECS) or patch image + env (EKS). All-or-nothing on
config; the UI shows the diff before confirming.

A tag-pointer field on `Application` was considered and rejected: without a snapshot, the
image rolls back while today's env vars stay applied — silent drift.

## Gaps found in review (now part of the design)

- **The next push silently undoes a rollback.** The GitHub webhook
  (`views/application.py` `application_github_webhook`) redeploys on every push to the
  tracked branch. A rollback therefore sets `Application.auto_deploy_paused`; while set,
  the webhook acknowledges pushes without deploying, and the UI shows the app as pinned
  with a *Resume auto-deploy* action. A manual deploy clears it.
- **EKS is not pinned to what was built.** `api/common/naming.image_tag()` picks the tag
  from the *requested* `project_commit_hash[:12]` before the build (or a random uuid when
  there is none), while ECS pins to the SHA the build actually resolved. If the EKS deploy
  runs after CodeBuild completes, switch it to the `$APP_NAME-$RESOLVED_SHA` tag the
  buildspec already pushes, so `Deployment.commit_sha` means the same thing on both
  compute types. Record which it is on the `Deployment` row either way.

## Files

- `application-service/api/models/deployment.py` — new, plus an additive migration.
- `application-service/api/services/application_deployment_service.py` — write a
  `Deployment` row on every deploy; new rollback branch that skips the build.
- `application-service/api/views/application.py` + `api/routes.py` — list deployments,
  trigger rollback (owner-scoped).
- `gateway-service/app/api/endpoints/application.py` — proxied routes.
- Frontend — deployment history list and the pre-confirm diff.

## Tests

- Rollback restores the old image **and** re-reads current env values, with no
  `start_build` call.
- `-latest` is still pushed (ECS must not regress).
- The snapshot contains key names and a hash, and **no env values** — assert a seeded
  secret value is absent from the stored row.
- Config restore is all-or-nothing.
- Rolling back to a tag ECR has expired fails with a usable message rather than a pull
  error at task placement.

## Security pre-review

**Required.** H7 is the finding this design exists to satisfy, and the reviewer should
confirm the snapshot genuinely holds no values. Also worth checking: who may trigger a
rollback (owner vs invited ADMIN — same decision shape as the logs endpoint, where we
settled on owner-only).

## Decisions

1. **Diff UX:** key-name changes, plus a "values changed" indicator derived from the
   content hash. No values are shown or stored.
2. **In-flight deploys:** rollback is rejected (409) while a deploy is in flight.
3. **Who may roll back:** owner-only, matching the logs endpoint.
4. **HMAC key for the content hash:** `settings.SECRET_KEY`. It is already required to be
   50+ characters by `core.settings.validate_config`, is already present in every
   deployment and in `test_settings.py`, and needs no new secret to provision or rotate.
   Consequence: rotating `SECRET_KEY` makes every historical "values changed" indicator
   read as changed — acceptable and reversible (it only affects a UI hint, never data).
5. **What "restoring the configuration that shipped with it" restores:** image tag, CPU,
   memory, and port are all restored from the snapshot onto the `Application` row itself.
   Env values are never restored from the snapshot — they are always re-read from the
   application's current `envs` at rollback time (H7: rolling back code must not roll back
   a rotated credential). The diff preview shown before confirming exists precisely to warn
   when the live env has drifted from what shipped with the target deploy.
6. **Rollback runs on the deployment worker, not the request thread.** ECS
   `wait_for_service_stable` / target-health polling and the EKS rollout wait are
   minutes-scale; nothing else in this codebase does AWS calls of that duration inside a
   request handler (`deploy_application` only ever runs from `run_worker.py`). The rollback
   endpoint enqueues a `"rollback"` job (`DeploymentQueue.enqueue_rollback`) carrying the
   target `Deployment` id; the worker acquires the same `DeploymentLock` a normal deploy
   uses and calls `ApplicationDeploymentService.rollback_application`. The endpoint itself
   does a synchronous `DeploymentLock.is_locked` check before enqueueing so a caller gets an
   immediate 409 rather than a queued job that silently never runs.
7. **Rollback pins to `Deployment.image_tag` verbatim, never a recomputed `slug-sha`.** The
   app may have been renamed since the target deploy, and `app_slug(name)` would then
   resolve to a tag that was never pushed. `_create_task_definition` grew an `image_tag`
   override parameter for this; the normal deploy path still recomputes the tag from the
   just-resolved commit SHA as before.
8. **Only a `resolved_sha`-tagged `Deployment` row is a valid rollback target.** A row
   recorded with a `-latest` fallback tag (a CodeBuild project predating the two-tag
   buildspec, or a build that started before the buildspec change) names a tag every later
   build overwrites — by the time anyone rolls back to it there may be nothing fixed left
   behind it. `RollbackService._get_target` filters on `tag_source='resolved_sha'`.
9. **EKS is now pinned the same way ECS is (Gap #2 in the review).** `deploy_application`
   calls `_deploy_to_eks` *after* `_wait_for_build` resolves the build's SHA, so the fix was
   available without restructuring: both compute types now pin to
   `$APP_NAME-$RESOLVED_SHA` when the build exports one, falling back to `-latest` only for
   a pre-two-tag-buildspec CodeBuild project. `Deployment.tag_source` records which case
   applied on every row, for both compute types.
10. **Deploy history records both outcomes.** A `Deployment` row is written on every
    completed attempt, `SUCCEEDED` or `FAILED` — but only once a build actually produced an
    image tag to record (a failure before that point, e.g. infra validation, writes no row).
    Writing history is best-effort: a failure to write the row never turns a real deploy or
    rollback success into a reported failure.
11. **A manual deploy or retry clears `auto_deploy_paused`**, not just the dedicated resume
    endpoint — an explicit redeploy is already an override of whatever a rollback pinned.
    The webhook's own pointer (`project_commit_hash`) still advances while paused so that
    resuming (or a later manual deploy) redeploys the latest push, not the pre-rollback
    commit.
12. **Rolling back is owner-only, but clearing the pause is not, and that's deliberate.**
    Manual deploy/retry are gated on `can_update_application` (SUPER_ADMIN or ADMIN), the
    same check every other deploy-triggering endpoint uses — not on ownership. An invited
    ADMIN who cannot see deploy history or trigger a rollback can still clear
    `auto_deploy_paused` by triggering an ordinary deploy. This is consistent rather than a
    gap: that ADMIN could already deploy over the rollback's pinned image at will (rollback
    only pins the *next automatic* deploy, not the ADMIN's ability to redeploy manually), so
    letting a manual deploy also clear the flag it's about to make moot doesn't grant them
    anything they didn't already have.
13. **A queued webhook job re-checks `auto_deploy_paused` at the worker, not just at
    enqueue time.** The webhook already declines to enqueue while paused, but a job
    enqueued moments before a rollback pins the app would otherwise still run once the
    worker dequeues it. `execute_deploy_job` re-reads the application and skips webhook-
    sourced jobs (`job["source"] == "webhook"`) if it finds the pause set, closing that
    race. A manual-deploy job carries no `source` tag and is never skipped this way — the
    view that enqueues it has already cleared the pause synchronously.
14. **Rollback pins to `Deployment.image_digest` when one was recorded, falling back to
    `image_tag`.** The customer's ECR repository is tag-MUTABLE
    (`infra/aws/modules/ecr/main.tf`) and shared across every app on the infrastructure, so
    a later build of the same commit — a re-run, a retagged CI job — can silently repoint a
    tag at different bytes without changing anything recorded on the Deployment row. The
    digest is fetched (best-effort, via `ecr describe_images`) when a deploy or rollback
    succeeds and is checked for continued existence the same way an expired tag is. Rows
    written before this field existed have no digest and roll back pinned by tag, as before.
15. **Enqueue the rollback job before setting `auto_deploy_paused`, not after.** If
    `enqueue_rollback` itself fails (Redis unavailable), the app must not be left pinned
    with nothing actually in flight to justify it. The narrow window between a successful
    enqueue and the pause save landing is closed the same way as decision 13: a webhook job
    that races into that window is caught by the worker's own re-check, not by ordering
    alone.

## Out of scope

Rolling back customer databases. Nothing runs migrations today and the plan is to keep it
that way.
