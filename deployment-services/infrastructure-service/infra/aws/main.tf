module "vpc" {
  source = "./modules/vpc"

  environment = var.environment
  vpc_cidr    = var.vpc_cidr
}

module "iam" {
  source      = "./modules/iam"
  environment = var.environment
}

module "security" {
  source = "./modules/security"

  environment = var.environment
}

module "cloud_optimizer" {
  source = "./modules/cloud_optimizer"
}

module "secrets" {
  source = "./modules/secrets"

  environment = var.environment
}

module "ecs" {
  source = "./modules/ecs"

  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
}

module "alb" {
  source = "./modules/alb"

  environment           = var.environment
  vpc_id                = module.vpc.vpc_id
  public_subnet_ids     = module.vpc.public_subnet_ids
  alb_security_group_id = module.vpc.alb_security_group_id
}

module "ecr" {
  source = "./modules/ecr"

  environment = var.environment
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "cluster_arn" {
  value = module.ecs.cluster_arn
}

output "alb_arn" {
  value = module.alb.alb_arn
}

output "alb_dns" {
  value = module.alb.alb_dns
}

output "target_group_arn" {
  value = module.alb.target_group_arn
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "ecs_task_execution_role_arn" {
  value = module.iam.ecs_task_execution_role_arn
}

output "alb_security_group_id" {
  value = module.vpc.alb_security_group_id
}
