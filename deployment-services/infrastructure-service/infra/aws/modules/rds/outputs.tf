output "endpoint" {
  value = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "secret_arn" {
  value = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "sg_id" {
  value = aws_security_group.this.id
}
