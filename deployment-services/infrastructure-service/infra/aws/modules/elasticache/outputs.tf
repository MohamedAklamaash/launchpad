output "endpoint" {
  value = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "port" {
  value = 6379
}

output "secret_arn" {
  value = aws_secretsmanager_secret.this.arn
}
