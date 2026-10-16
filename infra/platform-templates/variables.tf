variable "aws_region" {
  description = "Region for the provider and for the bucket. Has no bearing on the IAM resources the hosted templates create — IAM is global — only on where this bucket's own object is filed."
  type        = string
  default     = "us-east-1"
}

variable "templates_prefix" {
  description = "Key prefix the bucket policy's public-read grant is scoped to, and under which publish.sh writes <prefix>/<git-ref>/<template-file>. Not a secret; just the narrowing point so a public s3:GetObject grant can't be read as 'this whole bucket is public' even though, for this single-purpose bucket, it effectively is. Must match the path NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL is pointed at (see README)."
  type        = string
  default     = "templates"
}
