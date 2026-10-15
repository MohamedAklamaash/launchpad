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
  description = "Key prefix in the sources bucket that every per-build object and IAM grant is scoped to. Not a secret; just the shared narrowing point for the task role, the launcher policy, and the lifecycle rule."
  type        = string
  default     = "launchpad-builds"
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
