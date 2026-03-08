        (control plane)
       Django API
           │
           ▼
      Deployment Worker
           │
           │ AssumeRole
           ▼
       USER AWS ACCOUNT
           │
   ┌───────┴────────┐
   │                │
 CodeBuild       ECS Cluster
   │                │
 Build Image        │
   │                │
 Push → ECR         │
   │                │
   └──────► Deploy ECS
                    │
                    ▼
                 ALB URL