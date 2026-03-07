#!/bin/bash
# Cleanup orphaned VPC resources from failed infrastructure provisions

REGION="${1:-us-west-2}"

echo "Cleaning up orphaned resources in $REGION..."
echo ""

# 1. Find and delete orphaned NAT Gateways
echo "=== NAT Gateways ==="
NAT_GWS=$(aws ec2 describe-nat-gateways --region "$REGION" --filter "Name=tag:Name,Values=infra-*" --query 'NatGateways[?State==`available`].[NatGatewayId,Tags[?Key==`Name`].Value|[0]]' --output text)

if [ -n "$NAT_GWS" ]; then
  echo "$NAT_GWS" | while read nat_id name; do
    echo "Deleting NAT Gateway: $name ($nat_id)"
    aws ec2 delete-nat-gateway --nat-gateway-id "$nat_id" --region "$REGION"
  done
  echo "Waiting 30s for NAT Gateways to delete..."
  sleep 30
else
  echo "No orphaned NAT Gateways found"
fi

# 2. Release orphaned Elastic IPs
echo ""
echo "=== Elastic IPs ==="
EIPS=$(aws ec2 describe-addresses --region "$REGION" --filters "Name=tag:Name,Values=infra-*" --query 'Addresses[?AssociationId==null].[AllocationId,Tags[?Key==`Name`].Value|[0]]' --output text)

if [ -n "$EIPS" ]; then
  echo "$EIPS" | while read alloc_id name; do
    echo "Releasing EIP: $name ($alloc_id)"
    aws ec2 release-address --allocation-id "$alloc_id" --region "$REGION"
  done
else
  echo "No orphaned EIPs found"
fi

# 3. Delete orphaned subnets
echo ""
echo "=== Subnets ==="
SUBNETS=$(aws ec2 describe-subnets --region "$REGION" --filters "Name=tag:Name,Values=infra-*" --query 'Subnets[].[SubnetId,Tags[?Key==`Name`].Value|[0]]' --output text)

if [ -n "$SUBNETS" ]; then
  echo "$SUBNETS" | while read subnet_id name; do
    echo "Deleting subnet: $name ($subnet_id)"
    aws ec2 delete-subnet --subnet-id "$subnet_id" --region "$REGION" 2>&1 | grep -v "DependencyViolation" || true
  done
else
  echo "No orphaned subnets found"
fi

# 4. Delete orphaned Internet Gateways
echo ""
echo "=== Internet Gateways ==="
IGWS=$(aws ec2 describe-internet-gateways --region "$REGION" --filters "Name=tag:Name,Values=infra-*" --query 'InternetGateways[].[InternetGatewayId,Attachments[0].VpcId,Tags[?Key==`Name`].Value|[0]]' --output text)

if [ -n "$IGWS" ]; then
  echo "$IGWS" | while read igw_id vpc_id name; do
    if [ -n "$vpc_id" ]; then
      echo "Detaching IGW: $name ($igw_id) from VPC $vpc_id"
      aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$vpc_id" --region "$REGION" 2>&1 || true
    fi
    echo "Deleting IGW: $name ($igw_id)"
    aws ec2 delete-internet-gateway --internet-gateway-id "$igw_id" --region "$REGION" 2>&1 || true
  done
else
  echo "No orphaned IGWs found"
fi

# 5. Delete orphaned VPCs
echo ""
echo "=== VPCs ==="
VPCS=$(aws ec2 describe-vpcs --region "$REGION" --filters "Name=tag:Name,Values=infra-*" --query 'Vpcs[].[VpcId,Tags[?Key==`Name`].Value|[0]]' --output text)

if [ -n "$VPCS" ]; then
  echo "$VPCS" | while read vpc_id name; do
    echo "Deleting VPC: $name ($vpc_id)"
    aws ec2 delete-vpc --vpc-id "$vpc_id" --region "$REGION" 2>&1 | grep -v "DependencyViolation" || true
  done
else
  echo "No orphaned VPCs found"
fi

echo ""
echo "Cleanup complete. If resources remain, they may have dependencies. Wait a few minutes and re-run."
