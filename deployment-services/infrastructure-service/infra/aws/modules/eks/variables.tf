variable "environment" {
  description = "Environment name"
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name; must stay infra-* prefixed to match the onboarding IAM scoping"
  type        = string

  validation {
    condition     = startswith(var.cluster_name, "infra-")
    error_message = "cluster_name must be prefixed with infra-."
  }
}

variable "cluster_version" {
  description = "Kubernetes control plane version"
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the cluster and its nodes"
  type        = list(string)
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint; never 0.0.0.0/0"
  type        = list(string)

  validation {
    condition     = length(var.public_access_cidrs) > 0 && !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "public_access_cidrs must be non-empty and must not contain 0.0.0.0/0."
  }
}

variable "provisioner_role_arn" {
  description = "IAM role the provisioning worker assumes; gets the cluster-admin access entry"
  type        = string
}
