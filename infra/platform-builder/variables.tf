variable "aws_region" {
  description = "Region for the provider and for every regional resource below."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR for the platform-builder VPC. Only build tasks and their NAT live here."
  type        = string
  default     = "10.90.0.0/16"
}

variable "build_log_retention_days" {
  description = "CloudWatch retention for build output. Short by design (see logs.tf) — build output can carry Launchpad-side detail alongside customer Dockerfile output, per F7's Risks section."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.build_log_retention_days)
    error_message = "build_log_retention_days must be one of CloudWatch Logs' allowed retention values."
  }
}

variable "source_prefix" {
  description = "Key prefix in the sources bucket that every per-build object and IAM grant is scoped to. Not a secret; just the shared narrowing point for the task role, the launcher policy, and the lifecycle rule. Must equal PLATFORM_BUILDER_SOURCE_PREFIX in application-service's env — aws/github_source.py writes `<prefix>/<build_id>/source.tar.gz` and `<prefix>/<build_id>/docker-config.json` under exactly this value."
  type        = string
  default     = "sources"
}

# No default: the platform worker's own IAM user ARN must be named deliberately, never
# left to resolve to something unintended — the same "no silent default" stance
# kaniko_image_digest below takes. This user stays assume-only (no policy attached to it
# directly): it is granted sts:AssumeRole onto aws_iam_role.builder_launcher, nothing more.
variable "launcher_principal_arn" {
  description = "ARN of the IAM principal (application-service's build-launcher process, typically a dedicated IAM user) that assumes into aws_iam_role.builder_launcher. Must not be the infrastructure-service provisioning worker's identity — see README 'Why this can't live in the customer tree'."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::\\d{12}:(user|role)/\\S+$", var.launcher_principal_arn))
    error_message = "launcher_principal_arn must be an IAM user or role ARN (arn:aws:iam::<account>:user/... or :role/...)."
  }
}

# No default, same reasoning as kaniko_image_digest: a digest must be deliberately
# chosen. Pulled from the public ECR Gallery (public.ecr.aws/aws-cli/aws-cli), not Docker
# Hub — a Docker Hub 429 on this image's pull would be a platform-side failure that the
# build's own error path would otherwise misreport as a customer Dockerfile problem (see
# the Docker Hub rate-limit special case this tree's README and aws/platform_builder.py
# both carry for the *customer's* FROM line; the init container must never hit the same
# failure mode for a platform-chosen base image). Anonymous pulls from the public gallery
# need no IAM grant, unlike the mirrored Kaniko image.
variable "aws_cli_image_digest" {
  description = "sha256 digest of public.ecr.aws/aws-cli/aws-cli, pinned by digest, never by tag. Used by the init container that copies docker-config.json into Kaniko's shared volume."
  type        = string

  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.aws_cli_image_digest))
    error_message = "aws_cli_image_digest must be a sha256 digest in the form sha256:<64 hex chars>."
  }
}

variable "task_cpu" {
  description = "Fargate task CPU units for the Kaniko executor. Fixed per F7's 'no per-customer tuning in v1' resource ceiling."
  type        = string
  default     = "1024"
}

variable "task_memory" {
  description = "Fargate task memory (MiB) for the Kaniko executor."
  type        = string
  default     = "2048"
}

# No default: a digest must be deliberately chosen by whoever mirrors the image (see
# README), never a tag, so this can never silently resolve to an unpinned "latest" image.
variable "kaniko_image_digest" {
  description = "sha256 digest of the Kaniko (Chainguard fork) image already mirrored into aws_ecr_repository.kaniko_mirror. Pinned by digest, never by tag."
  type        = string

  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.kaniko_image_digest))
    error_message = "kaniko_image_digest must be a sha256 digest in the form sha256:<64 hex chars>."
  }
}
