resource "aws_computeoptimizer_enrollment_status" "enrollment" {
  status = "Active"
}

# Optional: Add IAM policy for Compute Optimizer if a specific role needs access
# For now, enabling the service globally satisfies the baseline "AWS Compute Optimizer must be enabled."
