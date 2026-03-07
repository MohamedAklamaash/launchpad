#!/bin/bash
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com 2>&1 || echo "Role already exists"
