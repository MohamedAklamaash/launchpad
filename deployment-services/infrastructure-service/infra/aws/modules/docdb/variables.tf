variable "environment" {
  type = string
}

variable "engine_version" {
  type = string
}

variable "instance_class" {
  type = string
}

variable "allocated_storage" {
  description = "Unused by DocumentDB (cluster storage auto-scales) — accepted for API symmetry with rds"
  type        = number
  default     = 0
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

variable "final_snapshot_identifier" {
  type = string
}
