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

variable "skip_final_snapshot" {
  description = "True only for a Nuke infrastructure run, which deletes any pre-existing final snapshot separately and must leave none behind"
  type        = bool
  default     = false
}
