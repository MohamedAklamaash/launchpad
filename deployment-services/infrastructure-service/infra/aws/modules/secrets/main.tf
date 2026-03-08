variable "environment" {
  description = "Environment name"
  type        = string
}

resource "aws_kms_key" "secrets" {
  description             = "KMS key for Secrets Manager"
  enable_key_rotation     = true
  deletion_window_in_days = 10
  policy                  = data.aws_iam_policy_document.secrets_kms_policy.json
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "secrets_kms_policy" {
  statement {
    sid    = "AllowRoot"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowDeploymentRole"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/LaunchpadDeploymentRole"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
      "kms:EnableKeyRotation",
      "kms:ScheduleKeyDeletion"
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AllowSecretsManager"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["secretsmanager.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey"
    ]
    resources = ["*"]
  }
}

resource "aws_secretsmanager_secret" "baseline" {
  name        = "${var.environment}/baseline-secrets"
  description = "Baseline secrets managed via Secrets Manager"
  kms_key_id  = aws_kms_key.secrets.arn
}
