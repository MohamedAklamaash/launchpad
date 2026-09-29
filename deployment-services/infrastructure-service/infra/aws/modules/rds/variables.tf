variable "environment" {
  type = string
}

variable "engine" {
  description = "postgres or mysql"
  type        = string
  validation {
    condition     = contains(["postgres", "mysql"], var.engine)
    error_message = "engine must be postgres or mysql."
  }
}

variable "engine_version" {
  type = string
}

variable "instance_class" {
  type = string
}

variable "allocated_storage" {
  description = "Storage in GB"
  type        = number
}

variable "db_name" {
  description = "Database identifier (also used as the initial schema name)"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "app_security_group_id" {
  description = "The per-infra Fargate app SG — sole ingress source for this database"
  type        = string
}

variable "final_snapshot_identifier" {
  description = "Fixed at create time from the Database row's own UUID"
  type        = string
}

variable "skip_final_snapshot" {
  description = "True only for a Nuke infrastructure run, which deletes any pre-existing final snapshot separately and must leave none behind"
  type        = bool
  default     = false
}
