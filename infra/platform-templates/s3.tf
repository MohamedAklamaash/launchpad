# Hosts the generated CFN templates (F8 ticket 1:
# deployment-services/infrastructure-service/api/cloud_providers/aws/iam_policy/cloudformation/)
# at a pinned, per-git-ref key, so a customer's browser can hit
# cloudformation:CreateStack's quick-create link with a TemplateURL that resolves to an
# S3 object location — the only location shape that API accepts (plan §3 "Hosting",
# Non-goals). The backend's own CreateChangeSet call does not read from this bucket; it
# passes TemplateBody from the committed file directly (see
# api/services/cfn_onboarding.py's _template_body_for) until a later ticket, if ever,
# needs otherwise.
resource "aws_s3_bucket" "templates" {
  bucket        = "launchpad-platform-templates-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = {
    Name = "launchpad-platform-templates"
  }
}

# Beyond platform-builder's convention: this bucket carries a public bucket policy, so
# ACLs are disabled outright rather than merely blocked/ignored — there is no legitimate
# use of an ACL on an object only ever written by publish.sh and read anonymously.
resource "aws_s3_bucket_ownership_controls" "templates" {
  bucket = aws_s3_bucket.templates.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Block Public Access is deliberately NOT fully on: the templates prefix is meant to be
# public. ACLs stay blocked/ignored (object ownership above already forbids them, this
# is belt and suspenders); the policy and bucket flags are off so the scoped bucket
# policy below can actually grant public s3:GetObject. No public ListBucket, no public
# write — those stay impossible because the policy below never grants them, not because
# this block forbids them.
resource "aws_s3_bucket_public_access_block" "templates" {
  bucket = aws_s3_bucket.templates.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "templates" {
  bucket = aws_s3_bucket.templates.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# On, unlike platform-builder's sources bucket: a pinned ref must never resolve to
# different bytes later. Versioning plus the conditional-PUT publish step (see
# publish.sh) means a given key's current version can never be silently replaced, and an
# accidental delete becomes a delete marker, not data loss.
resource "aws_s3_bucket_versioning" "templates" {
  bucket = aws_s3_bucket.templates.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "templates_bucket" {
  # Only s3:GetObject, only under the templates prefix, only the current (unversioned)
  # read — GetObjectVersion is not granted, so a historical version is reachable only to
  # whoever already holds write/list credentials on this bucket, never anonymously by
  # guessing a versionId. No s3:ListBucket anywhere: nobody can enumerate which refs
  # have ever been published.
  statement {
    sid       = "AllowPublicReadTemplates"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.templates.arn}/${var.templates_prefix}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.templates.arn, "${aws_s3_bucket.templates.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "templates" {
  bucket = aws_s3_bucket.templates.id
  policy = data.aws_iam_policy_document.templates_bucket.json

  # Attaching a public bucket policy before Block Public Access has actually applied its
  # (false) settings can race and fail with AccessDenied on first apply.
  depends_on = [aws_s3_bucket_public_access_block.templates]
}
