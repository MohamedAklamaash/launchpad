variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "alb_security_group_id" {
  type = string
}

# F1b (TLS activation): default off so every ECS plan generated before this feature stays
# byte-identical (see terraform_worker.py's _generate_config_ecs) — the listener is a
# distinct resource gated by count, not a toggle on an always-created one, so "disabled"
# means "does not exist" rather than "exists but inert".
variable "enable_https" {
  type    = bool
  default = false
}

# Only read when enable_https = true. Validated at the interpolation sink
# (terraform_worker.py's _validate_certificate_arn) before this variable is ever set to
# anything but "".
variable "certificate_arn" {
  type    = string
  default = ""
}

resource "aws_lb" "main" {
  name               = "${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection       = false
  enable_http2                     = true
  enable_cross_zone_load_balancing = true

  tags = {
    Name = "${var.environment}-alb"
  }
}

resource "aws_lb_target_group" "main" {
  name        = "${var.environment}-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
  }

  deregistration_delay = 30

  tags = {
    Name = "${var.environment}-tg"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}

# F1b (TLS activation): count-gated, not a toggle on an always-created resource — with
# enable_https = false (the default) this resource does not exist, and terraform plans no
# change to an ECS environment that has never issued a certificate. The default action is
# a fixed 404, never a forward: every real app is reached only via its own host-header rule
# (application-service's aws/alb.py), so an unmatched Host on 443 must not fall through to
# some other app's target group.
resource "aws_lb_listener" "https" {
  count             = var.enable_https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }
}

output "alb_arn" {
  value = aws_lb.main.arn
}

output "alb_dns" {
  value = aws_lb.main.dns_name
}

output "target_group_arn" {
  value = aws_lb_target_group.main.arn
}

output "https_listener_arn" {
  value = var.enable_https ? aws_lb_listener.https[0].arn : null
}
