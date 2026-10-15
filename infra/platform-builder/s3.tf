# Per-build source tarballs, fetched by the worker (verified TLS, the customer's own
# GitHub token) and handed to Kaniko as --context s3://..., per F7's "Source checkout"
# section. Nothing here is customer-durable: every object is expected to live minutes, and
# the lifecycle rule below is the backstop if a build never cleans up after itself.
resource "aws_s3_bucket" "sources" {
  bucket        = "launchpad-platform-builder-sources-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = {
    Name = "launchpad-platform-builder-sources"
  }
}

resource "aws_s3_bucket_public_access_block" "sources" {
  bucket                  = aws_s3_bucket.sources.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sources" {
  bucket = aws_s3_bucket.sources.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Versioning deliberately off: a source tarball is single-use and re-fetched from GitHub
# on the next build, so a prior version is never read and would only extend how long a
# customer's source code sits in a platform bucket past the lifecycle expiry below.
resource "aws_s3_bucket_versioning" "sources" {
  bucket = aws_s3_bucket.sources.id

  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "sources" {
  bucket = aws_s3_bucket.sources.id

  rule {
    id     = "expire-build-sources"
    status = "Enabled"

    filter {
      prefix = "${var.source_prefix}/"
    }

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "sources_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.sources.arn, "${aws_s3_bucket.sources.arn}/*"]

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

resource "aws_s3_bucket_policy" "sources" {
  bucket = aws_s3_bucket.sources.id
  policy = data.aws_iam_policy_document.sources_bucket.json
}
