# Applied against a platform-owned AWS account, never a customer account. The bucket
# this tree creates is deliberately public-readable (see s3.tf and README "What the
# bucket policy allows"), so it belongs in an account whose account-level S3 Block
# Public Access is not forced on globally — that setting silently overrides the
# bucket-level configuration below. Backend config is left to the operator, same as
# infra/platform-dns and infra/platform-builder — there is no backend block here.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Component = "platform-templates"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
