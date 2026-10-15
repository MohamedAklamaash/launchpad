# Two roles, matching the execution-role/task-role split aws/ecs.py already uses for
# customer tasks: the execution role is what ECS itself uses to pull the image and set up
# logging before the container starts; the task role is what the running container can do
# as AWS. Keeping them separate means a compromised build container (the task role) can
# never pull a different image or write to a different log group than ECS already decided.
#
# Both trust ecs-tasks.amazonaws.com with the confused-deputy conditions AWS recommends for
# service-linked trust policies, scoped to this account's ECS tasks only.
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

# --- Task execution role: pull the mirrored image (and the public aws-cli image the init
# --- container uses — anonymous pull, no IAM needed), write the task's own log stream.
# --- Deliberately does NOT attach the AWS-managed AmazonECSTaskExecutionRolePolicy —
# --- that policy's ECR pull permissions are Resource: "*", wider than the one mirror repo
# --- this builder ever needs. No Secrets Manager grant: F7's Decision section drops the
# --- per-build Secrets Manager secret entirely (ECS fixes a container's `secrets` at
# --- task-definition registration, not per RunTask, so a per-build secret would need a
# --- task-definition revision per build) — the credential now travels the same way the
# --- source does, through the init container's S3 copy into the shared `docker-config`
# --- volume, never through this role.
resource "aws_iam_role" "execution" {
  name               = "launchpad-platform-builder-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # Not resource-scopable in AWS's model, same note as platform-dns's GetChange.
  }

  statement {
    sid    = "PullMirrorRepo"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.kaniko_mirror.arn]
  }

  statement {
    sid       = "WriteOwnLogStream"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.builder.arn}:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "launchpad-platform-builder-execution"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- Build task role: the identity the running Kaniko (and init) containers have. No
# --- standing AWS permission beyond reading this one build's own source objects. The
# --- push credential (scoped ECR session, per F7's "Credential flow") is minted per build
# --- by the application-service worker from the customer's own LaunchpadDeploymentRole
# --- session, written to `docker-config.json` beside the source tarball, and copied by
# --- the init container into the `docker-config` volume Kaniko reads from — never a
# --- Secrets Manager env var (see the execution role's comment above for why). The task
# --- role itself carries no ecr:*, no sts:AssumeRole, no secretsmanager:*, and no access
# --- to anything outside this one build's inputs. Per-build narrowing (which exact object
# --- this build may read) happens at runtime via the worker's own request (an unguessable
# --- build id, per F7's Decision section — this role has no s3:ListBucket, so a hostile
# --- Dockerfile's RUN step that reaches this role's credentials still can't enumerate
# --- another build's key), not via a Terraform-managed policy here; this role only
# --- bounds the prefix.
resource "aws_iam_role" "build_task" {
  name               = "launchpad-platform-builder-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

data "aws_iam_policy_document" "build_task" {
  statement {
    sid       = "ReadOwnSourceObject"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.sources.arn}/${var.source_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "build_task" {
  name   = "launchpad-platform-builder-task"
  role   = aws_iam_role.build_task.id
  policy = data.aws_iam_policy_document.build_task.json
}

# --- Builder-launcher policy: exactly what application-service's build-launcher process
# --- needs to launch and monitor a build task — nothing more. Attached, below, to
# --- aws_iam_role.builder_launcher rather than to a user directly — the principal that
# --- assumes into that role (application-service's own IAM user, named out of band by
# --- var.launcher_principal_arn) is still created and wired out of band, the same pattern
# --- as platform-dns's access key: this tree defines the grant shape and the role that
# --- carries it, not the user that assumes into it.
data "aws_iam_policy_document" "builder_launcher" {
  # RunTask takes a task-definition ARN. DescribeTasks/StopTask take a task ARN, not a
  # task-definition ARN — the two resource shapes are not interchangeable, so this is two
  # statements, not one, even though all three actions are "run and monitor build tasks".
  statement {
    sid       = "RunBuildTask"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = ["${aws_ecs_task_definition.kaniko.arn_without_revision}:*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.builder.arn]
    }
  }

  statement {
    sid       = "MonitorBuildTask"
    effect    = "Allow"
    actions   = ["ecs:DescribeTasks", "ecs:StopTask"]
    resources = ["arn:aws:ecs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.builder.name}/*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.builder.arn]
    }
  }

  statement {
    sid       = "PassBuildRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.build_task.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "WriteBuildSourceObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.sources.arn}/${var.source_prefix}/*"]
  }

  statement {
    sid       = "ReadBuildLogs"
    effect    = "Allow"
    actions   = ["logs:GetLogEvents"]
    resources = ["${aws_cloudwatch_log_group.builder.arn}:*"]
  }
}

# --- The real principal: a dedicated role in this account, trusted to exactly the
# --- principal application-service's build-launcher process authenticates as (its own
# --- IAM user, named by var.launcher_principal_arn — no default, must be set explicitly).
# --- That user stays assume-only, the same "assume-only, no policy attached to the user
# --- itself" stance LaunchpadDeploymentRole's own caller already keeps (see
# --- plan/F7-platform-builder.md's Credential flow) — it is given sts:AssumeRole onto
# --- this role, never the builder_launcher policy document directly.
resource "aws_iam_role" "builder_launcher" {
  name = "launchpad-platform-builder-launcher"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { AWS = var.launcher_principal_arn }
      }
    ]
  })
}

resource "aws_iam_role_policy" "builder_launcher" {
  name   = "launchpad-platform-builder-launcher"
  role   = aws_iam_role.builder_launcher.id
  policy = data.aws_iam_policy_document.builder_launcher.json
}
