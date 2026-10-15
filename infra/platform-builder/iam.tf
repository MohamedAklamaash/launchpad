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

# --- Task execution role: pull the mirrored image, write the task's own log stream, read
# --- secrets under launchpad-builds/ (scoped by ARN prefix, per F7's per-build Secrets
# --- Manager credential delivery). Deliberately does NOT attach the AWS-managed
# --- AmazonECSTaskExecutionRolePolicy — that policy's ECR pull permissions are
# --- Resource: "*", wider than the one mirror repo this builder ever needs.
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

  statement {
    sid       = "ReadBuildSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:launchpad-builds/*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "launchpad-platform-builder-execution"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- Build task role: the identity the running Kaniko container has. No standing AWS
# --- permission beyond reading its own build's source object. The push credential
# --- (scoped ECR session, per F7's "Credential flow") is minted per build by the
# --- application-service worker from the customer's own LaunchpadDeploymentRole session
# --- and handed to the container as a SECRETS_MANAGER-sourced env var it never has
# --- standing AWS permission to read on its own beyond what the execution role already
# --- resolves at launch — the task role itself carries no ecr:*, no sts:AssumeRole, and no
# --- access to anything outside this one build's inputs. Per-build narrowing (which exact
# --- object this build may read) happens at runtime via the worker's own request, not via
# --- a Terraform-managed policy here; this role only bounds the prefix.
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

# --- Builder-launcher policy: output only, attached to no user or role here. This is
# --- exactly what application-service's build-launcher process needs to launch and
# --- monitor a build task — nothing more. Wiring a real principal to this document (an IAM
# --- user/role in the application-service deployment environment) is out of band, the same
# --- pattern as platform-dns's access key: this skeleton defines the grant shape, not the
# --- principal that holds it.
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
    sid    = "ManageBuildSecrets"
    effect = "Allow"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:TagResource",
    ]
    resources = ["arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:launchpad-builds/*"]
  }

  statement {
    sid       = "ReadBuildLogs"
    effect    = "Allow"
    actions   = ["logs:GetLogEvents"]
    resources = ["${aws_cloudwatch_log_group.builder.arn}:*"]
  }
}
