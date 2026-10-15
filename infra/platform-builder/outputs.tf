output "cluster_name" {
  description = "Pass as the ecs:cluster the launcher's RunTask/DescribeTasks/StopTask calls target."
  value       = aws_ecs_cluster.builder.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.builder.arn
}

output "task_definition_family" {
  description = "Pass to RunTask as taskDefinition (family, or family:revision for a pinned revision)."
  value       = aws_ecs_task_definition.kaniko.family
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.kaniko.arn
}

output "private_subnet_ids" {
  description = "Pass as networkConfiguration.awsvpcConfiguration.subnets on RunTask. assignPublicIp must be set to DISABLED by the caller — that flag lives on the RunTask call, not on these subnets."
  value       = aws_subnet.private[*].id
}

output "task_security_group_id" {
  description = "Pass as networkConfiguration.awsvpcConfiguration.securityGroups on RunTask."
  value       = aws_security_group.builder_task.id
}

output "sources_bucket_name" {
  value = aws_s3_bucket.sources.id
}

output "source_prefix" {
  description = "Key prefix every per-build source object, and every IAM grant scoped to the bucket, must live under."
  value       = var.source_prefix
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.builder.name
}

output "execution_role_arn" {
  description = "Pass as executionRoleArn on the task definition (already wired here) and to iam:PassRole callers."
  value       = aws_iam_role.execution.arn
}

output "build_task_role_arn" {
  description = "Pass as taskRoleArn on the task definition (already wired here) and to iam:PassRole callers."
  value       = aws_iam_role.build_task.arn
}

output "kaniko_mirror_repository_url" {
  description = "Mirror the Chainguard Kaniko image here (see README) before setting kaniko_image_digest."
  value       = aws_ecr_repository.kaniko_mirror.repository_url
}

output "builder_launcher_policy_json" {
  description = "Attach this document to whichever IAM principal application-service's build-launcher process runs as. Not attached to anything by this module — see README."
  value       = data.aws_iam_policy_document.builder_launcher.json
}
