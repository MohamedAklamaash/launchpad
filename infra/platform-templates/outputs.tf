output "bucket_name" {
  value = aws_s3_bucket.templates.id
}

output "bucket_arn" {
  value = aws_s3_bucket.templates.arn
}

output "templates_prefix" {
  value = var.templates_prefix
}

# Virtual-hosted-style base URL, already including the prefix — pass straight into
# NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL (launchpad-frontend/lib/onboarding-cloudformation.ts
# appends "/<ref>/<template-file>" to this, nothing else). bucket_regional_domain_name
# renders as "<bucket>.s3.<region>.amazonaws.com" (or the legacy unqualified
# "<bucket>.s3.amazonaws.com" for us-east-1, depending on provider version) — both shapes
# match that function's virtual-hosted-style regex.
output "template_base_url" {
  value = "https://${aws_s3_bucket.templates.bucket_regional_domain_name}/${var.templates_prefix}"
}
