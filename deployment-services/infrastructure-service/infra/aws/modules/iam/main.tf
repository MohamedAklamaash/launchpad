variable "environment" {
  description = "Environment name"
  type        = string
}

variable "db_secret_arns" {
  description = "Secrets Manager ARNs the ECS execution role may read (one per live managed database)"
  type        = list(string)
  default     = []
}

# 1. EC2 Execution Role
data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2_execution_role" {
  name               = "${var.environment}-ec2-exec-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 2. ECS Task Execution Role
data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution_role" {
  name               = "${var.environment}-ecs-task-exec-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Scoped to the exact secret ARNs of this environment's live managed databases — never a
# wildcard like `secret:rds!*`, which would read every RDS/Aurora master secret in the
# customer's account, including ones unrelated to Launchpad.
data "aws_iam_policy_document" "db_secrets_access" {
  count = length(var.db_secret_arns) > 0 ? 1 : 0

  statement {
    sid       = "ReadManagedDatabaseSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.db_secret_arns
  }

  statement {
    sid       = "DecryptManagedDatabaseSecrets"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

data "aws_region" "current" {}

resource "aws_iam_role_policy" "db_secrets_access" {
  count  = length(var.db_secret_arns) > 0 ? 1 : 0
  name   = "${var.environment}-db-secrets-access"
  role   = aws_iam_role.ecs_task_execution_role.id
  policy = data.aws_iam_policy_document.db_secrets_access[0].json
}

# 3. Lambda Execution Role
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_execution_role" {
  name               = "${var.environment}-lambda-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

output "ecs_task_execution_role_arn" {
  value = aws_iam_role.ecs_task_execution_role.arn
}

output "ec2_execution_role_arn" {
  value = aws_iam_role.ec2_execution_role.arn
}

output "lambda_execution_role_arn" {
  value = aws_iam_role.lambda_execution_role.arn
}

