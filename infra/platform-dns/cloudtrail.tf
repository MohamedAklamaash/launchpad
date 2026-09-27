# Alerting on denied ChangeResourceRecordSets calls in this account. The IAM policy in
# main.tf denies writes outside a tenant's own label shape; without this, an attacker (or a
# bug) probing that boundary produces nothing but a silent AccessDenied the writer's own
# logs might never surface loudly enough.
#
# Route53 is a global service, so CloudTrail always records its API calls with eventRegion
# "us-east-1" — and, independent of the trail's own home region, AWS delivers global
# service events to EventBridge's default event bus in us-east-1 only. The trail itself
# (with include_global_service_events = true) can live in var.aws_region; the rule that
# reacts to those events cannot — it is pinned to a us-east-1 provider alias below so
# changing var.aws_region can never silently make this alerting dead.
#
# Required by the #75 platform-dns README ("Also required, not built here") and by the F1b
# security pre-review (§1). Not verified against a real AWS account yet — see
# plan/REAL-AWS-VALIDATION.md for what "CloudTrail alert fires on a denied write" still
# needs to confirm once the zone is applied for real.

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Component = "platform-dns"
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "launchpad-platform-dns-cloudtrail-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "cloudtrail_bucket" {
  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
  }

  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    # Without this, any CloudTrail trail in any AWS account that discovered this bucket's
    # name could write to it (the principal is the whole cloudtrail.amazonaws.com service,
    # not this specific trail). aws:SourceArn scopes the grant to this one trail.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudtrail:us-east-1:${data.aws_caller_identity.current.account_id}:trail/launchpad-platform-dns-trail"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail_bucket.json
}

resource "aws_cloudtrail" "platform_dns" {
  # Pinned to the same us-east-1 alias as the EventBridge rule/target/SNS topic below —
  # the trail's home region has no bearing on whether it captures Route53's global-service
  # events (it would either way, per include_global_service_events), but pinning it here
  # removes any doubt and keeps the whole alerting pipeline in one region to reason about.
  provider                      = aws.us_east_1
  name                          = "launchpad-platform-dns-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = false
  enable_log_file_validation    = true

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

resource "aws_sns_topic" "dns_write_denied" {
  provider = aws.us_east_1
  name     = "launchpad-platform-dns-write-denied"
}

# Alert operators are expected to subscribe out of band (email/Slack/PagerDuty) — deliberately
# not a terraform resource here, matching the writer access key's "create out of band" pattern:
# a subscription endpoint is deployment-specific config, not infrastructure.
resource "aws_cloudwatch_event_rule" "dns_write_denied" {
  provider    = aws.us_east_1
  name        = "launchpad-platform-dns-write-denied"
  description = "Fires on a denied route53:ChangeResourceRecordSets in the platform DNS account"

  event_pattern = jsonencode({
    source      = ["aws.route53"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["route53.amazonaws.com"]
      eventName   = ["ChangeResourceRecordSets"]
      errorCode   = [{ prefix = "AccessDenied" }]
    }
  })

  depends_on = [aws_cloudtrail.platform_dns]
}

resource "aws_cloudwatch_event_target" "dns_write_denied_to_sns" {
  provider = aws.us_east_1
  rule     = aws_cloudwatch_event_rule.dns_write_denied.name
  arn      = aws_sns_topic.dns_write_denied.arn
}

data "aws_iam_policy_document" "dns_write_denied_sns" {
  statement {
    sid    = "AllowEventBridgePublish"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.dns_write_denied.arn]
    # Scopes the grant to this specific rule, in this account — without it, an EventBridge
    # rule in any account that discovered this topic's ARN could publish to it.
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.dns_write_denied.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "dns_write_denied" {
  provider = aws.us_east_1
  arn      = aws_sns_topic.dns_write_denied.arn
  policy   = data.aws_iam_policy_document.dns_write_denied_sns.json
}
