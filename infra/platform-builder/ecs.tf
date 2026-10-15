resource "aws_ecs_cluster" "builder" {
  name = "launchpad-platform-builder"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = {
    Name = "launchpad-platform-builder-cluster"
  }
}

# Fargate only — no EC2 capacity to patch, matching F7's "no host patching, no capacity
# planning beyond quota" framing for the Fargate-per-build decision.
resource "aws_ecs_cluster_capacity_providers" "builder" {
  cluster_name       = aws_ecs_cluster.builder.name
  capacity_providers = ["FARGATE"]
}

# One task per build (ecs:RunTask, not a long-lived service) — the application code that
# launches this is out of scope here (ticket 5), but the task definition it launches
# against is this skeleton's contract with that future code. The actual build command is
# supplied per build via RunTask containerOverrides; the command below is a placeholder
# that proves the container starts and exits cleanly, nothing more.
resource "aws_ecs_task_definition" "kaniko" {
  family                   = "launchpad-platform-builder-kaniko"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.build_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name  = "kaniko"
      image = "${aws_ecr_repository.kaniko_mirror.repository_url}@${var.kaniko_image_digest}"

      # Placeholder only. The real invocation (--context s3://..., --destination, --cache
      # repo/tag) is supplied per build via RunTask's containerOverrides.command — this
      # proves the mirrored image runs and exits 0 under this task definition's resource
      # shape, nothing more.
      command = ["--help"]

      essential = true

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.builder.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "build"
        }
      }
    }
  ])

  tags = {
    Name = "launchpad-platform-builder-kaniko-taskdef"
  }
}
