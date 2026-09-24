variable "platform_base_domain" {
  description = "Apex domain for platform-issued app hostnames, e.g. example.com. App URLs become {slug}.{dns_label}.{this}. No trailing dot."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]*\\.[a-z]{2,}$", var.platform_base_domain))
    error_message = "platform_base_domain must be a bare lowercase domain with no scheme and no trailing dot."
  }
}

variable "aws_region" {
  description = "Region for the provider. Route53 is global; this only decides where API calls are signed."
  type        = string
  default     = "us-east-1"
}
