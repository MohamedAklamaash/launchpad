variable "environment" {
  description = "Environment name"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "enable_elb_subnet_tags" {
  description = "Tag subnets for load balancer discovery by the AWS Load Balancer Controller"
  type        = bool
  default     = false
}
