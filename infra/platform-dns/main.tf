terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Applied against the dedicated DNS account — not the platform account that holds the
# AssumeRole principal, and not any customer account. Keeping the zone in an account of
# its own means a leaked credential here grants DNS for one zone and nothing else.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Component = "platform-dns"
    }
  }
}

resource "aws_route53_zone" "platform" {
  name    = var.platform_base_domain
  comment = "Launchpad platform zone: per-infrastructure app hostnames and ACM validation records"
}

# The writer is a user, not a role: the provisioning path needs long-lived credentials and
# has no AWS identity of its own to assume from. It is deliberately given no
# sts:AssumeRole — compromise of the existing platform principal already yields every
# customer account, and this credential must not extend that to minting certificates for
# arbitrary platform hostnames.
resource "aws_iam_user" "dns_writer" {
  name = "launchpad-platform-dns-writer"
}

data "aws_iam_policy_document" "dns_writer" {
  statement {
    sid    = "ReadZone"
    effect = "Allow"
    actions = [
      "route53:GetHostedZone",
      "route53:ListResourceRecordSets",
    ]
    resources = [aws_route53_zone.platform.arn]
  }

  # GetChange is not resource-scopable: the API takes an opaque change id, and AWS
  # publishes no ARN format for it.
  statement {
    sid       = "PollChangeStatus"
    effect    = "Allow"
    actions   = ["route53:GetChange"]
    resources = ["*"]
  }

  # Record writes are allowed only for names at least two labels below the apex, which is
  # where every Launchpad-written record lives: edge.<label>.<domain>, *.<label>.<domain>,
  # and the ACM validation CNAME. The apex itself, www, MX/SPF/DKIM and DMARC records all
  # sit one label up and are therefore unreachable from this credential — so a bug in
  # hostname construction cannot take the platform's own mail or website offline.
  #
  # ForAllValues, not ForAnyValue: a single batch containing one disallowed name is
  # rejected whole rather than partially applied.
  statement {
    sid       = "WriteTenantRecordsOnly"
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = [aws_route53_zone.platform.arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = ["*.*.${var.platform_base_domain}"]
    }
  }
}

resource "aws_iam_user_policy" "dns_writer" {
  name   = "launchpad-platform-dns-writer"
  user   = aws_iam_user.dns_writer.name
  policy = data.aws_iam_policy_document.dns_writer.json
}
