terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Backend is configured dynamically via -backend-config during 'terraform init'
  # in TerraformService.run_terraform to ensure per-infrastructure state isolation.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Owner       = var.owner
      Project     = var.project
      ManagedBy   = "Terraform"
    }
  }
}
