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
# launches this is aws/platform_builder.py (ticket 5), which supplies the real per-build
# values (build id / S3 keys / --destination) via RunTask containerOverrides; the
# commands below are placeholders proving each container starts and exits cleanly under
# this task definition's resource shape, nothing more.
#
# `docker-config` is a plain ephemeral task-scoped volume (no efs/docker volume config —
# just an anonymous bind mount Fargate shares between containers in one task). The init
# container copies the per-build docker-config.json into it; Kaniko mounts the same
# volume at /kaniko/.docker, where it reads registry auth from by default. Per F7's
# Decision section, this replaces the per-build Secrets Manager secret the original
# design called for — ECS fixes a container's `secrets` block at task-definition
# registration, not per RunTask, so a per-build secret would need a task-definition
# revision per build.
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

  volume {
    name = "docker-config"
  }

  container_definitions = jsonencode([
    {
      name  = "init"
      image = "public.ecr.aws/aws-cli/aws-cli@${var.aws_cli_image_digest}"

      # Placeholder only — exits 0 without any AWS call or credential. The real
      # invocation (`s3 cp s3://<bucket>/<key> /kaniko/.docker/config.json`) is supplied
      # per build via RunTask's containerOverrides.command.
      command = ["--version"]

      # Non-essential + Kaniko's dependsOn (below) is the documented ECS pattern for a
      # run-to-completion init container: this container finishing (exit 0) must not by
      # itself stop the task, but Kaniko must not start until it has.
      essential = false

      mountPoints = [
        {
          sourceVolume  = "docker-config"
          containerPath = "/kaniko/.docker"
          readOnly      = false
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.builder.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "build"
        }
      }
    },
    {
      name  = "kaniko"
      image = "${aws_ecr_repository.kaniko_mirror.repository_url}@${var.kaniko_image_digest}"

      # Placeholder only. The real invocation (--context s3://..., --context-sub-path,
      # --dockerfile, --destination, --digest-file) is supplied per build via RunTask's
      # containerOverrides.command — this proves the mirrored image runs and exits 0,
      # nothing more.
      command = ["--help"]

      essential = true

      dependsOn = [
        {
          containerName = "init"
          condition     = "SUCCESS"
        }
      ]

      mountPoints = [
        {
          sourceVolume  = "docker-config"
          containerPath = "/kaniko/.docker"
          readOnly      = true
        }
      ]

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
