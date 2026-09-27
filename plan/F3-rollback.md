# F3 — Rollback

**Status:** prerequisites done (#69), rollback itself not started
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

## Out of scope

Rolling back customer databases. Nothing runs migrations today and the plan is to keep it
that way.
