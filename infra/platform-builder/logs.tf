# F7 describes "a CloudWatch log group for build logs, named per-build (include the build
# id, not the infra id)". The awslogs driver's log group is baked into the task
# definition's container definition at register time, and ECS's RunTask overrides do not
# let a caller repoint logConfiguration per invocation — so a literal per-build *group*
# would mean registering a new task-definition revision per build, which this skeleton does
# not do (no application code here, see CLAUDE.md scope). The shape that actually exists is
# one log group with the awslogs-stream-prefix below, giving one *stream* per task
# (<prefix>/<container name>/<ecs task id>) — which still satisfies the underlying intent
# (retention/access control keyed off a build-scoped identifier, not an infra id). If a
# true per-build log group is required later, that is a task-definition-per-build design,
# tracked as an open question in the PR, not decided here.
resource "aws_cloudwatch_log_group" "builder" {
  name              = "/launchpad/platform-builder"
  retention_in_days = var.build_log_retention_days

  tags = {
    Name = "launchpad-platform-builder-logs"
  }
}
