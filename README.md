# Launchpad

**Deploy applications to your AWS infrastructure in minutes, not hours.**

Launchpad is a cloud deployment platform that transforms GitHub repositories into production-ready applications running in your own AWS account. No vendor lock-in. No shared infrastructure. Complete control.

## Why Launchpad?

**For Development Teams:**
- Deploy from GitHub with a single API call
- Automatic Docker builds, container orchestration, and load balancing
- Path-based routing - run multiple apps on one load balancer
- Zero infrastructure knowledge required

**For Platform Engineers:**
- Infrastructure provisioned via Terraform in customer AWS accounts
- Isolated VPCs, private subnets, and security groups per environment
- Event-driven architecture with async job processing
- Full observability with CloudWatch logs and metrics

**For CTOs:**
- Reduce cloud costs - customers pay AWS directly, no markup
- Eliminate vendor lock-in - all resources in customer accounts
- Faster time-to-market - developers ship features, not infrastructure
- Enterprise-grade security with IAM role assumption and temporary credentials

## How It Works

1. **Connect Your AWS Account** - Create an IAM role, Launchpad provisions your infrastructure
2. **Connect Your GitHub** - OAuth integration for seamless repository access
3. **Deploy** - Point to a repo, branch, and Dockerfile - we handle the rest
4. **Scale** - Configure CPU, memory, and replicas per application

## Architecture

```
Your GitHub Repo → Launchpad Control Plane → Your AWS Account
                                              ├─ VPC
                                              ├─ ECS Cluster
                                              ├─ Application Load Balancer
                                              ├─ ECR Registry
                                              └─ CloudWatch Logs
```

All compute, networking, and storage runs in **your AWS account**. Launchpad orchestrates, you own the infrastructure.

## Key Features

- **GitHub Integration** - Deploy from public or private repositories
- **Automatic Builds** - CodeBuild compiles and pushes Docker images to your ECR
- **Container Orchestration** - ECS Fargate manages scaling and health checks
- **Load Balancing** - ALB with path-based routing (`/app-name/*`)
- **Environment Variables** - Secure configuration management per application
- **Real-time Logs** - Stream container logs via CloudWatch
- **Infrastructure as Code** - Terraform provisions reproducible environments

## Use Cases

- **Microservices Deployment** - Run multiple services behind a single load balancer
- **Staging Environments** - Spin up isolated infrastructure per team or feature
- **Multi-tenant SaaS** - Deploy customer workloads in isolated AWS accounts
- **CI/CD Pipelines** - Integrate with GitHub Actions or Jenkins for automated deployments

## Getting Started

See [docs/USER_ONBOARDING_GUIDE.md](docs/USER_ONBOARDING_GUIDE.md) for setup instructions.

## Documentation

- [System Architecture](docs/SYSTEM_ARCHITECTURE.md) - Technical deep dive
- [User Workflows](docs/USER_WORKFLOWS.md) - Step-by-step guides
- [IAM Policies](docs/IAM_POLICIES.md) - AWS permissions setup
- [Deployment Edge Cases](docs/DEPLOYMENT_EDGE_CASES.md) - Troubleshooting
- [Update Endpoints](docs/UPDATE_ENDPOINTS.md) - Application and infrastructure updates
- [Update Quick Reference](docs/UPDATE_QUICK_REFERENCE.md) - Quick API reference
- [Sleep/Wake Feature](docs/SLEEP_WAKE_FEATURE.md) - Cost-saving sleep mode
- [System Context](context.md) - Complete technical reference

## Support

For issues, questions, or feature requests, see [docs/](docs/).

## License

This software is proprietary and confidential. See [LICENSE](LICENSE) for details.

For commercial licensing inquiries, contact Mohamed Aklamaash.

---

**Built for developers who want Heroku simplicity with AWS control.**

---

Copyright © 2026 Mohamed Aklamaash. All rights reserved.