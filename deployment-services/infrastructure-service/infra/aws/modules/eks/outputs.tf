output "cluster_arn" {
  value = aws_eks_cluster.main.arn
}

output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.main.endpoint
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
