locals {
  port = 27017
}

resource "aws_docdb_subnet_group" "this" {
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

resource "aws_docdb_cluster" "this" {
  cluster_identifier = "${var.environment}-${var.db_name}"
  engine              = "docdb"
  engine_version       = var.engine_version

  master_username              = "lp_admin"
  manage_master_user_password  = true

  db_subnet_group_name   = aws_docdb_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]

  storage_encrypted = true

  skip_final_snapshot       = false
  final_snapshot_identifier = var.final_snapshot_identifier
  deletion_protection       = false

  tags = {
    Name        = "${var.environment}-${var.db_name}"
    Environment = var.environment
    ManagedBy   = "launchpad"
  }
}

resource "aws_docdb_cluster_instance" "this" {
  identifier         = "${var.environment}-${var.db_name}-0"
  cluster_identifier = aws_docdb_cluster.this.id
  instance_class     = var.instance_class
}
