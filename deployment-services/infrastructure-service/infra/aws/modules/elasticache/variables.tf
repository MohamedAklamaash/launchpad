variable "environment" {
  type = string
}

variable "engine_version" {
  type = string
}

variable "node_type" {
  type = string
}

variable "db_name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "app_security_group_id" {
  type = string
}
