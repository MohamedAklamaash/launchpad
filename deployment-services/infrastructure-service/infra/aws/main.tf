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
