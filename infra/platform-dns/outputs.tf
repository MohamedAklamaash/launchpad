output "hosted_zone_id" {
  description = "Set as PLATFORM_DNS_ZONE_ID. The writer must be scoped to this one zone."
  value       = aws_route53_zone.platform.zone_id
}

output "name_servers" {
  description = "Delegate these at the registrar, then confirm with: dig NS <domain> +short"
  value       = aws_route53_zone.platform.name_servers
}

output "dns_writer_user_arn" {
  description = "The Route53 writer. Create its access key out of band — deliberately not a terraform resource, so the secret never enters state."
  value       = aws_iam_user.dns_writer.arn
}
