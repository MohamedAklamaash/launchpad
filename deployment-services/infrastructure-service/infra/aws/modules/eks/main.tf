data "aws_iam_policy_document" "cluster_assume_role" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.cluster_name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume_role.json
}

resource "aws_iam_role_policy_attachment" "cluster" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSComputePolicy",
    "arn:aws:iam::aws:policy/AmazonEKSBlockStoragePolicyV2",
    "arn:aws:iam::aws:policy/AmazonEKSLoadBalancingPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSNetworkingPolicy",
  ])
  role       = aws_iam_role.cluster.name
  policy_arn = each.value
}

data "aws_iam_policy_document" "node_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.cluster_name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume_role.json
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodeMinimalPolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  version  = var.cluster_version
  role_arn = aws_iam_role.cluster.arn

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  bootstrap_self_managed_addons = false

  compute_config {
    enabled       = true
    node_pools    = ["general-purpose"]
    node_role_arn = aws_iam_role.node.arn
  }

  kubernetes_network_config {
    elastic_load_balancing {
      enabled = true
    }
  }

  storage_config {
    block_storage {
      enabled = true
    }
  }

  vpc_config {
    subnet_ids              = var.subnet_ids
    endpoint_public_access  = true
    endpoint_private_access = true
    public_access_cidrs     = var.public_access_cidrs
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = {
    Name        = var.cluster_name
    Environment = var.environment
  }

  depends_on = [
    aws_iam_role_policy_attachment.cluster,
    aws_iam_role_policy_attachment.node,
  ]
}

resource "aws_eks_access_entry" "provisioner" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.provisioner_role_arn
}

resource "aws_eks_access_policy_association" "provisioner_cluster_admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.provisioner_role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.provisioner]
}

data "aws_iam_policy_document" "deploy_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [var.provisioner_role_arn]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.cluster_name}-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_assume_role.json
}

resource "aws_eks_access_entry" "deployer" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = aws_iam_role.deploy.arn

  # AmazonEKSEditPolicy below is deliberately namespace-scoped to app-*, but the deployer
  # also has to create/delete its app namespace (cluster-scoped) and write the namespace's
  # ResourceQuota/LimitRange (which `edit` only reads). This group is bound, by
  # api/services/eks_bootstrap.py running as cluster-admin, to a ClusterRole for exactly
  # those verbs, and a ValidatingAdmissionPolicy denies any of them outside app-*.
  # kubernetes_groups is mutable in place (UpdateAccessEntry, not ForceNew), so applying
  # this on an onboarded cluster doesn't replace the entry or interrupt a deploy.
  kubernetes_groups = ["launchpad:deployers"]
}

resource "aws_eks_access_policy_association" "deployer_namespaced_edit" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = aws_iam_role.deploy.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type       = "namespace"
    namespaces = ["app-*", "launchpad-bootstrap"]
  }

  depends_on = [aws_eks_access_entry.deployer]
}

# Per-app CPU/memory charts need real time series, not a point-in-time metrics-server
# sample, so this installs Container Insights the same way ECS already gets its
# CPU/MemoryUtilization: as CloudWatch metrics api/services/app_metrics_service.py can
# GetMetricData over 1h/6h/24h/7d windows. EKS Auto Mode's own built-in controllers
# (load balancing, storage, compute) use AWS-managed identities with no customer-visible
# role, but this add-on's CloudWatch Agent is a regular workload in the cluster and
# needs its own AWS credentials — Auto Mode clusters run the Pod Identity Agent as a
# built-in system component (no separate eks-pod-identity-agent add-on install), so a
# pod identity association is enough; no IRSA/OIDC provider setup needed.
data "aws_iam_policy_document" "cloudwatch_agent_assume_role" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cloudwatch_agent" {
  name               = "${var.cluster_name}-cloudwatch-agent"
  assume_role_policy = data.aws_iam_policy_document.cloudwatch_agent_assume_role.json
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.cloudwatch_agent.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_eks_addon" "cloudwatch_observability" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "amazon-cloudwatch-observability"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  pod_identity_association {
    role_arn        = aws_iam_role.cloudwatch_agent.arn
    service_account = "cloudwatch-agent"
  }

  depends_on = [
    aws_eks_cluster.main,
    aws_iam_role_policy_attachment.cloudwatch_agent,
  ]
}
