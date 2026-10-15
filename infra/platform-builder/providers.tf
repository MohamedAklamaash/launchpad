# Applied against a platform-owned AWS account, never a customer account and never the
# account that holds LAUNCHPAD_PLATFORM_PRINCIPAL_ARN's trust-policy principal if that is
# a separate concern (see README "Apply it in its own account"). Backend config is left to
# the operator, same as infra/platform-dns — there is no backend block here.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Component = "platform-builder"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
