# A delegated subdomain, not the root. The root zone keeps its own records and its own
# nameservers; only this label is delegated here, so the platform's DNS credential cannot
# reach the root's mail or website even by mistake — they are not in this zone.
variable "platform_base_domain" {
  description = "Zone apex for platform-issued app hostnames. App URLs become {slug}.{dns_label}.{this}. No trailing dot."
  type        = string
  default     = "launchpad.aklamaash.me"

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
