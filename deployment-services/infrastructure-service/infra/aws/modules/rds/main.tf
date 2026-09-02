locals {
  port          = var.engine == "postgres" ? 5432 : 3306
  engine_family = var.engine == "postgres" ? "postgres" : "mysql"
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.environment}-${var.db_name}-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name        = "${var.environment}-${var.db_name}-subnet-group"
    Environment = var.environment
  }
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

resource "aws_db_instance" "this" {
  identifier     = "${var.environment}-${var.db_name}"
  engine         = local.engine_family
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_encrypted = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = false
  multi_az               = false

  # AWS creates and owns this secret; the master password never enters terraform state.
  manage_master_user_password = true
  username                    = "lp_admin"
  db_name                     = replace(var.db_name, "-", "_")

  skip_final_snapshot       = false
  final_snapshot_identifier = var.final_snapshot_identifier
  deletion_protection       = false

  tags = {
    Name        = "${var.environment}-${var.db_name}"
    Environment = var.environment
    ManagedBy   = "launchpad"
  }
}
