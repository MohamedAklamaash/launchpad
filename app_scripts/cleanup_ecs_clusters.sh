#!/bin/bash
# Cleanup orphaned ECS clusters from failed provisions

REGION="${1:-us-west-2}"

echo "Fetching ECS clusters in $REGION..."

CLUSTERS=$(aws ecs list-clusters --region "$REGION" --query 'clusterArns[]' --output text)

if [ -z "$CLUSTERS" ]; then
  echo "No clusters found"
  exit 0
fi

echo "Found clusters:"
for cluster in $CLUSTERS; do
  cluster_name=$(basename "$cluster")
  
  # Check if it's a launchpad infra cluster
  if [[ $cluster_name == infra-* ]]; then
    echo ""
    echo "Cluster: $cluster_name"
    
    # Check for running tasks/services
    services=$(aws ecs list-services --cluster "$cluster_name" --region "$REGION" --query 'serviceArns' --output text)
    tasks=$(aws ecs list-tasks --cluster "$cluster_name" --region "$REGION" --query 'taskArns' --output text)
    
    if [ -z "$services" ] && [ -z "$tasks" ]; then
      echo "  ✓ No services or tasks running"
      read -p "  Delete this cluster? (y/n) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        aws ecs delete-cluster --cluster "$cluster_name" --region "$REGION"
        echo "  ✓ Deleted"
      fi
    else
      echo "  ✗ Has active services/tasks - skipping"
    fi
  fi
done

echo ""
echo "Done"
