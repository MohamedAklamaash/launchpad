# Build tasks run in the private subnets only and get no public IP (enforced at RunTask
# time via assignPublicIp = DISABLED — not a Terraform-managed setting, see README). The
# public subnets exist for exactly one reason: the NAT gateway needs a public subnet to
# live in. Nothing else is ever placed there.
resource "aws_vpc" "builder" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "launchpad-platform-builder-vpc"
  }
}

resource "aws_internet_gateway" "builder" {
  vpc_id = aws_vpc.builder.id

  tags = {
    Name = "launchpad-platform-builder-igw"
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.builder.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "launchpad-platform-builder-public-${count.index + 1}"
    Type = "public"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.builder.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 2)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "launchpad-platform-builder-private-${count.index + 1}"
    Type = "private"
  }
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "launchpad-platform-builder-nat-eip"
  }
}

# One NAT, in the first public subnet. Build tasks need outbound only (pull the mirrored
# Kaniko image, fetch base images the customer Dockerfile names, push to the customer's
# ECR) — there is deliberately no path back in.
resource "aws_nat_gateway" "builder" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name = "launchpad-platform-builder-nat"
  }

  depends_on = [aws_internet_gateway.builder]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.builder.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.builder.id
  }

  tags = {
    Name = "launchpad-platform-builder-public-rt"
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.builder.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.builder.id
  }

  tags = {
    Name = "launchpad-platform-builder-private-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# No ingress rules at all — nothing ever initiates a connection into a build task. Egress
# is wide (all protocols/ports) at the security-group layer because the real narrowing for
# this threat model is the task role's IAM scope and the lack of a route back into any
# other platform VPC, not the security group; a build legitimately needs to reach ECR,
# the mirrored image registry, S3, Secrets Manager, and whatever registries the customer's
# Dockerfile names, none of which resolve to a fixed, enumerable CIDR/port set up front.
resource "aws_security_group" "builder_task" {
  name        = "launchpad-platform-builder-task-sg"
  description = "Build task security group: no ingress, egress only"
  vpc_id      = aws_vpc.builder.id

  tags = {
    Name = "launchpad-platform-builder-task-sg"
  }
}

resource "aws_security_group_rule" "builder_task_egress_all" {
  type              = "egress"
  security_group_id = aws_security_group.builder_task.id
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Outbound only: pull mirrored image, fetch base images, push to customer ECR, read platform S3/Secrets Manager"
}
