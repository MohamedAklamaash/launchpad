locals {
  port = 6379
  # replication_group_id has a hard 40-char AWS limit; "${environment}-${db_name}" alone
  # can exceed that once db_name is near its own 31-char max, so derive a short,
  # collision-resistant id instead of using the full names directly.
  short_id = substr(md5("${var.environment}-${var.db_name}"), 0, 12)
  rg_id    = "lp-${substr(var.db_name, 0, 20)}-${local.short_id}"
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.environment}-${var.db_name}-subnet-group"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "this" {
  name        = "${var.environment}-${var.db_name}-sg"
  description = "Ingress solely from the per-infra app SG"
  vpc_id      = var.vpc_id

  tags = {
    Name        = "${var.environment}-${var.db_name}-sg"
    Environment = var.environment
  }
}

resource "aws_security_group_rule" "app_ingress" {
  type                     = "ingress"
  security_group_id        = aws_security_group.this.id
  from_port                = local.port
  to_port                  = local.port
  protocol                 = "tcp"
  source_security_group_id = var.app_security_group_id
}

resource "aws_security_group_rule" "egress_all" {
  type              = "egress"
  security_group_id = aws_security_group.this.id
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
}

# No AWS-managed auth-token equivalent exists for ElastiCache, unlike RDS/DocDB's
# manage_master_user_password — this is the one credential this feature writes into
# terraform state, and that state lives in the customer's own versioned S3 bucket.
resource "random_password" "auth_token" {
  length  = 32
  special = false
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.rg_id
  description          = "Launchpad managed Redis for ${var.db_name}"

  engine         = "redis"
  engine_version = var.engine_version
  node_type      = var.node_type

  num_cache_clusters = 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.this.id]

  transit_encryption_enabled = true
  auth_token                 = random_password.auth_token.result

  tags = {
    Name        = "${var.environment}-${var.db_name}"
    Environment = var.environment
    ManagedBy   = "launchpad"
  }
}

resource "aws_secretsmanager_secret" "this" {
  name = "launchpad/${var.environment}/${var.db_name}"
  # A delete-then-recreate at the same name must not hit AWS's default 7–30 day
  # "scheduled for deletion" hold — the row's final RDS/DocDB-style snapshot is the
  # durable delete artifact here, not the secret itself.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "this" {
  secret_id = aws_secretsmanager_secret.this.id
  secret_string = jsonencode({
    auth_token = random_password.auth_token.result
    host       = aws_elasticache_replication_group.this.primary_endpoint_address
    port       = local.port
    tls        = true
  })
}
