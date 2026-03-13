# Launchpad Frontend - Complete Workflow Diagram

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FRONTEND APPLICATION (React)                         │
│                          Easy Deployment Platform                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTP/HTTPS
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          API GATEWAY (Port 8000)                            │
│                              FastAPI Gateway                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Middleware: JWT Auth, CORS, Rate Limiting                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │                           │
         │                    │                           │
    ┌────▼────┐    ┌──────────▼──────────┐    ┌─────────▼──────────┐
    │  Auth   │    │  Infrastructure     │    │   Application      │
    │ Service │    │     Service         │    │     Service        │
    │ :5001   │    │  (Django - :8002)   │    │  (Django - :8001)  │
    │(Node.js)│    │                     │    │                    │
    └────┬────┘    └──────────┬──────────┘    └─────────┬──────────┘
         │                    │                           │
    ┌────▼────┐    ┌──────────▼──────────┐    ┌─────────▼──────────┐
    │Postgres │    │     PostgreSQL      │    │    PostgreSQL      │
    │  :5432  │    │       :5432         │    │      :5432         │
    └─────────┘    └─────────────────────┘    └────────────────────┘
                            │                           │
                            └───────────┬───────────────┘
                                        │
                                   ┌────▼─────┐
                                   │ RabbitMQ │
                                   │  :5672   │
                                   │ (Events) │
                                   └──────────┘
                            ┌───────────┴───────────┐
                            │                       │
                    ┌───────▼────────┐    ┌────────▼────────┐
                    │ Terraform      │    │  Deployment     │
                    │   Worker       │    │    Worker       │
                    │ (Provision)    │    │   (Deploy)      │
                    └────────────────┘    └─────────────────┘
                            │                       │
                            └───────────┬───────────┘
                                        │
                                   ┌────▼─────┐
                                   │   AWS    │
                                   │ Account  │
                                   │ (User's) │
                                   └──────────┘
```

---

## Complete User Journey

### 1. GitHub OAuth Login (SUPER_ADMIN)
```
User clicks "Login with GitHub"
    │
    ▼
Frontend → Gateway → Auth Service (/auth/github)
                          │
                          ├─→ Redirect to GitHub OAuth
                          │
User authorizes Launchpad on GitHub
                          │
                          ▼
GitHub Callback → Auth Service (/auth/github/callback)
                          │
                          ├─→ Fetch GitHub user data
                          ├─→ Fetch GitHub repositories
                          ├─→ Store GitHub token in metadata
                          ├─→ Create/Update user with role: SUPER_ADMIN
                          ├─→ Publish user.created event → RabbitMQ
                          │
                          ▼
JWT Token (access + refresh) → Frontend
                          │
                          ├─→ Store in httpOnly cookie
                          ├─→ Store user data in state
                          │
                          ▼
Redirect to Dashboard
```

### 3. User Profile Management
```
Frontend → Gateway (/users/{userId})
              │ [Authorization: Bearer JWT]
              ▼
        User Service
              │
              ├─→ MySQL (fetch user data)
              │
              ▼
        User Data → Frontend → Display Profile
```

### 4. Notifications Flow
```
Backend Event (e.g., payment success, deployment complete)
    │
    ▼
Notification Service
    │
    ├─→ MongoDB (store notification)
    ├─→ WebSocket/SSE (real-time push to frontend)
    │
    ▼
Frontend → Display notification badge/toast
    │
User clicks notification icon
    │
    ▼
Frontend → Gateway (/notifications/user/{userId})
              │
              ▼
        Notification Service
              │
              ├─→ MongoDB (fetch notifications)
              │
              ▼
        Notification List → Frontend
```

### 5. Infrastructure Deployment Flow
```
User fills deployment form
    │
    ▼
Frontend → Gateway (/infrastructures)
              │ [POST with config]
              ▼
        Infrastructure Service (Python)
              │
              ├─→ PostgreSQL (store infra config)
              ├─→ AWS/Cloud Provider API (provision resources)
              ├─→ Notification Service (send status updates)
              │
              ▼
        Deployment Status → Frontend (polling/websocket)
```

### 6. Application Deployment Flow
```
User submits app deployment
    │
    ▼
Frontend → Gateway (/applications)
              │ [POST with app details]
              ▼
        Application Service (Python)
              │
              ├─→ PostgreSQL (store app config)
              ├─→ Infrastructure Service (get infra details)
              ├─→ Deploy to K8s/ECS/VM
              ├─→ Notification Service (deployment events)
              │
              ▼
        Deployment Result → Frontend
```

### 7. Payment Flow
```
User initiates payment
    │
    ▼
Frontend → Gateway (/payments)
              │ [POST payment details]
              ▼
        Payment Service (Django)
              │
              ├─→ MySQL (create payment record)
              ├─→ Payment Gateway API (Stripe/Razorpay)
              │
              ▼
        Payment Confirmation
              │
              ├─→ MySQL (update status)
              ├─→ Notification Service (send receipt)
              │
              ▼
        Success Response → Frontend → Show confirmation
```

---

## Frontend Architecture Recommendations

### Pages/Routes
```
/                          → Landing page
/login                     → Login page
/register                  → Registration page
/dashboard                 → Main dashboard (protected)
/profile                   → User profile (protected)
/infrastructures           → Infrastructure list (protected)
/infrastructures/new       → Create infrastructure (protected)
/infrastructures/:id       → Infrastructure details (protected)
/applications              → Application list (protected)
/applications/new          → Deploy application (protected)
/applications/:id          → Application details (protected)
/payments                  → Payment history (protected)
/notifications             → Notification center (protected)
```

### State Management
```
┌─────────────────────────────────────┐
│      Global State (Redux/Zustand)   │
├─────────────────────────────────────┤
│  • auth: { user, token, isAuth }    │
│  • notifications: { unread, list }  │
│  • infrastructures: { list, active }│
│  • applications: { list, active }   │
│  • payments: { history, pending }   │
└─────────────────────────────────────┘
```

### API Client Structure
```javascript
// api/client.js
const API_BASE = 'http://localhost:8000'

// api/auth.js
- login(credentials)
- register(userData)
- githubLogin()
- logout()

// api/users.js
- getUser(userId)
- searchUsers(query)
- updateProfile(userId, data)

// api/infrastructures.js
- listInfrastructures()
- createInfrastructure(config)
- getInfrastructure(id)
- deleteInfrastructure(id)

// api/applications.js
- listApplications()
- deployApplication(config)
- getApplication(id)
- deleteApplication(id)

// api/notifications.js
- getNotifications(userId)
- markAsRead(notificationId)

// api/payments.js
- createPayment(details)
- getPaymentHistory()
```

### Real-time Features
```
WebSocket Connection (optional)
    │
    ├─→ /ws/notifications → Real-time notifications
    ├─→ /ws/deployments   → Live deployment logs
    └─→ /ws/metrics       → Real-time monitoring data
```

---

## API Endpoints Summary

### Gateway Base URL: `http://localhost:8000`

| Service | Prefix | Backend Port |
|---------|--------|--------------|
| Auth | `/auth/*` | 5001 |
| User | `/users/*` | 5002 |
| Notifications | `/notifications/*` | 5003 |
| Payments | `/payments/*` | 5004 |
| Infrastructure | `/infrastructures/*` | 5005 |
| Applications | `/applications/*` | 5006 |

---

## Security Considerations

1. **JWT Authentication**: Store token in httpOnly cookie or localStorage
2. **CSRF Protection**: Use CSRF tokens for state-changing operations
3. **Rate Limiting**: Already implemented in gateway
4. **CORS**: Configure gateway to allow frontend origin
5. **Input Validation**: Validate all user inputs on frontend + backend
6. **HTTPS**: Use HTTPS in production

---

## Next Steps

1. Choose frontend framework (React/Vue/Next.js recommended)
2. Set up API client with axios/fetch
3. Implement authentication flow first
4. Build dashboard with infrastructure/application management
5. Add real-time notifications
6. Integrate payment flow
7. Add monitoring dashboard (Grafana embed)


### 2. Dashboard - View Infrastructures
```
Dashboard loads
    │
    ▼
Frontend → GET /api/infrastructures/
                │ [Authorization: Bearer JWT]
                ▼
Infrastructure Service
                │
                ├─→ Check user permissions
                ├─→ PostgreSQL (fetch user's infrastructures)
                │
                ▼
Infrastructure List → Frontend
                │
Display cards:
- Name, Cloud Provider (AWS)
- Status (PENDING/PROVISIONING/ACTIVE/ERROR)
- Resources (CPU/Memory used vs available)
- Application count
- [Create Infrastructure] button
```

### 3. Create Infrastructure
```
User clicks "Create Infrastructure"
    │
    ▼
Frontend shows form:
- Name (unique per user)
- Cloud Provider: [AWS] (only option)
- AWS Account ID
- Max CPU (vCPU)
- Max Memory (GB)
    │
User fills form & clicks "Create"
    │
    ▼
Frontend → POST /api/infrastructures/
    {
      "name": "production",
      "cloud_provider": "AWS",
      "max_cpu": 4.0,
      "max_memory": 8.0,
      "code": "123456789012"
    }
                │ [Authorization: Bearer JWT]
                ▼
Infrastructure Service
                │
                ├─→ Validate: name unique per user
                ├─→ Authenticate with AWS (AssumeRole)
                ├─→ PostgreSQL (create infrastructure record)
                ├─→ Create Environment (status: PENDING)
                ├─→ Enqueue provisioning job → Redis
                ├─→ Publish infrastructure.created → RabbitMQ
                │
                ▼
Response: 201 Created → Frontend
                │
                ├─→ Show success message
                ├─→ Redirect to infrastructure list
                │
Terraform Worker (background)
                │
                ├─→ Dequeue job from Redis
                ├─→ Generate Terraform config
                ├─→ Provision AWS resources:
                │   ├─ VPC, Subnets, NAT Gateway
                │   ├─ ECS Cluster
                │   ├─ Application Load Balancer
                │   ├─ ECR Repository
                │   └─ IAM Roles, Security Groups
                ├─→ Update Environment (status: ACTIVE)
                ├─→ Publish environment.updated → RabbitMQ
                │
Frontend polls status every 5s
                │
                ▼
Status: PENDING → PROVISIONING → ACTIVE ✅
```

### 4. View Infrastructure Details
```
User clicks infrastructure card
    │
    ▼
Frontend → GET /api/infrastructures/{id}/
                │ [Authorization: Bearer JWT]
                ▼
Infrastructure Service
                │
                ├─→ Check user has access
                ├─→ PostgreSQL (fetch infrastructure + environment)
                │
                ▼
Infrastructure Details → Frontend
                │
Display:
- Name, Status, Cloud Provider
- ALB DNS URL
- Resources: Used vs Available
- Application list
- Buttons (based on role):
  - [Create Application] (SUPER_ADMIN/ADMIN)
  - [Update] (SUPER_ADMIN only)
  - [Delete] (SUPER_ADMIN only)
  - [Invite Users] (SUPER_ADMIN only)
```

### 5. Create Application
```
User clicks "Create Application"
    │
    ▼
Frontend shows form:
- Name (unique per user)
- Description
- GitHub Repository (dropdown from user's repos)
- Branch (default: main)
- Dockerfile Path (default: Dockerfile)
- Port (default: 8080)
- CPU (0.25, 0.5, 1.0, 2.0, 4.0 vCPU)
- Memory (based on CPU selection)
- Environment Variables (key-value pairs)
    │
User fills form & clicks "Create & Deploy"
    │
    ▼
Frontend → POST /api/applications/
    {
      "infrastructure_id": "infra-uuid",
      "name": "my-app",
      "project_remote_url": "https://github.com/user/repo",
      "project_branch": "main",
      "dockerfile_path": "Dockerfile",
      "port": 8080,
      "alloted_cpu": 0.5,
      "alloted_memory": 1.0,
      "envs": {"NODE_ENV": "production"}
    }
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Validate: name unique per user
                ├─→ Check infrastructure quota
                ├─→ Validate GitHub repo access
                ├─→ PostgreSQL (create application record)
                ├─→ Enqueue deployment job → Redis
                │
                ▼
Response: 201 Created → Frontend
                │
                ├─→ Show success message
                ├─→ Redirect to application details
                │
Deployment Worker (background)
                │
                ├─→ Dequeue job from Redis
                ├─→ Acquire deployment lock
                ├─→ 11-step deployment pipeline:
                │   1. Validate infrastructure (ACTIVE)
                │   2. Create AWS session (refresh credentials)
                │   3. Trigger CodeBuild (build Docker image)
                │   4. Wait for build completion
                │   5. Create ECS task definition
                │   6. Create ALB target group
                │   7. Configure ALB routing (/{app-name}/*)
                │   8. Verify target group attached
                │   9. Add security group rules
                │   10. Create ECS service
                │   11. Wait for service stable
                ├─→ Update application (status: ACTIVE)
                ├─→ Release deployment lock
                │
Frontend polls status every 3s
                │
                ▼
Status: CREATED → BUILDING → DEPLOYING → ACTIVE ✅
```


### 6. View Application Details
```
User clicks application
    │
    ▼
Frontend → GET /api/applications/{id}/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check user has access
                ├─→ PostgreSQL (fetch application details)
                │
                ▼
Application Details → Frontend
                │
Display:
- Name, Status, Description
- Deployment URL (clickable)
- Repository, Branch, Commit
- Resources (CPU, Memory, Port)
- Environment Variables
- Is Sleeping status
- Action Buttons (based on role & status):
  - [Re-deploy] (SUPER_ADMIN/ADMIN, if ACTIVE)
  - [Update] (SUPER_ADMIN/ADMIN)
  - [Sleep] (SUPER_ADMIN/ADMIN, if ACTIVE)
  - [Wake] (SUPER_ADMIN/ADMIN, if SLEEPING)
  - [Delete] (SUPER_ADMIN/ADMIN)
```

### 7. Re-deploy Application (Pull Latest Changes)
```
User clicks "Re-deploy"
    │
    ▼
Frontend shows confirmation:
"Pull latest changes from main branch and redeploy?"
    │
User clicks "Confirm"
    │
    ▼
Frontend → POST /api/applications/{id}/deploy/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Enqueue deployment job → Redis
                │
                ▼
Response: 202 Accepted → Frontend
                │
                ├─→ Show "Deployment queued"
                ├─→ Poll status every 3s
                │
Deployment Worker
                │
                ├─→ Pull latest code from GitHub
                ├─→ Build new Docker image
                ├─→ Push to ECR
                ├─→ Update ECS task definition
                ├─→ Update ECS service (rolling update)
                │
                ▼
Status: BUILDING → DEPLOYING → ACTIVE ✅
Frontend shows "Deployment successful"
```

### 8. Sleep Application (Cost Saving)
```
User clicks "Sleep"
    │
    ▼
Frontend shows confirmation:
"Put application to sleep? (Scales to 0 tasks)"
    │
User clicks "Confirm"
    │
    ▼
Frontend → POST /api/applications/{id}/sleep/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Validate status is ACTIVE
                ├─→ Get current ECS desired count
                ├─→ Save desired_count to database
                ├─→ Scale ECS service to 0 tasks
                ├─→ Update application (status: SLEEPING, is_sleeping: true)
                │
                ▼
Response: 200 OK → Frontend
                │
                ├─→ Update UI: Status = SLEEPING 😴
                ├─→ Show [Wake] button
                ├─→ Hide [Sleep] button
```

### 9. Wake Application
```
User clicks "Wake"
    │
    ▼
Frontend → POST /api/applications/{id}/wake/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Validate is_sleeping = true
                ├─→ Get saved desired_count
                ├─→ Scale ECS service to desired_count
                ├─→ Update application (status: ACTIVE, is_sleeping: false)
                │
                ▼
Response: 200 OK → Frontend
                │
                ├─→ Update UI: Status = ACTIVE ✅
                ├─→ Show [Sleep] button
                ├─→ Hide [Wake] button
                │
ECS launches containers (30-60 seconds)
```

### 10. Update Application
```
User clicks "Update"
    │
    ▼
Frontend shows form (pre-filled):
- Description
- Environment Variables
- CPU/Memory
- Port
    │
User modifies & clicks "Save"
    │
    ▼
Frontend → PATCH /api/applications/{id}/update/
    {
      "envs": {"NODE_ENV": "staging"},
      "alloted_cpu": 1.0,
      "alloted_memory": 2.0
    }
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Validate CPU/Memory combinations
                ├─→ Check infrastructure quota
                ├─→ PostgreSQL (update application)
                │
                ▼
Response: 200 OK → Frontend
                │
                ├─→ Show success message
                ├─→ Show "Re-deploy to apply changes" banner
```

### 11. Delete Application
```
User clicks "Delete"
    │
    ▼
Frontend shows confirmation:
"Delete my-app? This cannot be undone."
    │
User types app name to confirm
    │
User clicks "Confirm Delete"
    │
    ▼
Frontend → DELETE /api/applications/{id}/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Check permissions (SUPER_ADMIN/ADMIN)
                ├─→ Cleanup AWS resources:
                │   ├─ Delete ECS service
                │   ├─ Delete target group
                │   ├─ Delete listener rule
                │   ├─ Deregister task definition
                ├─→ PostgreSQL (delete application record)
                │
                ▼
Response: 204 No Content → Frontend
                │
                ├─→ Show success message
                ├─→ Redirect to infrastructure page
```

### 12. Delete Infrastructure
```
User clicks "Delete" (SUPER_ADMIN only)
    │
    ▼
Frontend → GET /api/infrastructures/{id}/validation/
                │ [Authorization: Bearer JWT]
                ▼
Application Service
                │
                ├─→ Count applications in infrastructure
                │
                ▼
Response: {can_delete: false, app_count: 2}
                │
                ▼
Frontend shows error:
"Cannot delete. 2 applications exist. Delete all apps first."
    │
User deletes all applications
    │
User clicks "Delete" again
    │
    ▼
Frontend → GET /api/infrastructures/{id}/validation/
                │
                ▼
Response: {can_delete: true, app_count: 0}
                │
                ▼
Frontend shows confirmation:
"Delete production infrastructure? This cannot be undone."
    │
User types infra name to confirm
    │
User clicks "Confirm Delete"
    │
    ▼
Frontend → DELETE /api/infrastructures/{id}/
                │ [Authorization: Bearer JWT]
                ▼
Infrastructure Service
                │
                ├─→ Check permissions (SUPER_ADMIN only)
                ├─→ Validate no applications exist
                ├─→ Update Environment (status: DESTROYING)
                ├─→ Enqueue destroy job → Redis
                │
                ▼
Response: 204 No Content → Frontend
                │
                ├─→ Show "Infrastructure deletion in progress"
                ├─→ Redirect to dashboard
                │
Terraform Worker (background)
                │
                ├─→ Dequeue destroy job
                ├─→ Run terraform destroy
                ├─→ Delete AWS resources
                ├─→ Update Environment (status: DESTROYED)
                ├─→ Delete database records
```


---

## Frontend Pages & Routes

### Public Routes
```
/                          → Landing page (features, pricing)
/login                     → GitHub OAuth login button
```

### Protected Routes (Require Authentication)
```
/dashboard                 → Infrastructure list, create button
/infrastructures/:id       → Infrastructure details, app list
/applications/:id          → Application details, actions
```

---

## Frontend State Management

### Global State (Redux/Zustand)
```javascript
{
  auth: {
    user: {
      id: "uuid",
      email: "user@example.com",
      user_name: "johndoe",
      role: "super_admin",
      metadata: {
        github: {
          id: "12345",
          username: "johndoe",
          token: "ghp_..."
        }
      }
    },
    token: "jwt-token",
    isAuthenticated: true
  },
  
  infrastructures: {
    list: [...],
    selected: {...},
    loading: false,
    error: null
  },
  
  applications: {
    list: [...],
    selected: {...},
    loading: false,
    error: null
  }
}
```

---

## API Client Structure

### Base Configuration
```javascript
// api/client.js
const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:8000'

const client = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Add JWT token to requests
client.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})
```

### API Modules

#### api/auth.js
```javascript
export const authAPI = {
  githubLogin: () => 
    window.location.href = `${API_BASE}/auth/github`,
  
  logout: () => 
    client.post('/auth/logout'),
  
  getCurrentUser: () => 
    client.get('/auth/me')
}
```

#### api/infrastructures.js
```javascript
export const infrastructureAPI = {
  list: () => 
    client.get('/api/infrastructures/'),
  
  create: (data) => 
    client.post('/api/infrastructures/', data),
  
  get: (id) => 
    client.get(`/api/infrastructures/${id}/`),
  
  update: (id, data) => 
    client.patch(`/api/infrastructures/${id}/update/`, data),
  
  delete: (id) => 
    client.delete(`/api/infrastructures/${id}/`),
  
  validate: (id) => 
    client.get(`/api/infrastructures/${id}/validation/`)
}
```

#### api/applications.js
```javascript
export const applicationAPI = {
  list: (infraId) => 
    client.get(`/api/applications/?infrastructure_id=${infraId}`),
  
  create: (data) => 
    client.post('/api/applications/', data),
  
  get: (id) => 
    client.get(`/api/applications/${id}/`),
  
  update: (id, data) => 
    client.patch(`/api/applications/${id}/update/`, data),
  
  delete: (id) => 
    client.delete(`/api/applications/${id}/`),
  
  deploy: (id) => 
    client.post(`/api/applications/${id}/deploy/`),
  
  sleep: (id) => 
    client.post(`/api/applications/${id}/sleep/`),
  
  wake: (id) => 
    client.post(`/api/applications/${id}/wake/`)
}
```

---

## UI Components

### Dashboard Page
```
┌─────────────────────────────────────────────────────┐
│  Launchpad Dashboard                    [Profile ▼] │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Your Infrastructures          [+ Create]           │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐                │
│  │ production   │  │ staging      │                │
│  │ AWS          │  │ AWS          │                │
│  │ ● ACTIVE     │  │ ● ACTIVE     │                │
│  │ 2.5/4 vCPU   │  │ 0.5/2 vCPU   │                │
│  │ 5/8 GB RAM   │  │ 1/4 GB RAM   │                │
│  │ 3 apps       │  │ 1 app        │                │
│  └──────────────┘  └──────────────┘                │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### Infrastructure Detail Page
```
┌─────────────────────────────────────────────────────┐
│  ← Back to Dashboard                                 │
├─────────────────────────────────────────────────────┤
│  production (AWS)                    [Update] [Delete]│
│  ● ACTIVE                                            │
│                                                      │
│  Resources:                                          │
│  CPU: ████████░░░░░░░░ 2.5/4.0 vCPU (62%)          │
│  Memory: ██████████░░░░ 5.0/8.0 GB (62%)           │
│                                                      │
│  ALB URL: http://infra-019ccc43-alb-123...          │
│                                                      │
│  Applications (3)                [+ Create App]      │
│  ┌──────────────────────────────────────────────┐  │
│  │ my-app          ● ACTIVE    [View] [Sleep]   │  │
│  │ api-service     😴 SLEEPING [View] [Wake]    │  │
│  │ worker          ● ACTIVE    [View] [Sleep]   │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### Application Detail Page
```
┌─────────────────────────────────────────────────────┐
│  ← Back to Infrastructure                            │
├─────────────────────────────────────────────────────┤
│  my-app                                              │
│  ● ACTIVE                                            │
│                                                      │
│  URL: http://infra-019ccc43-alb-123.../my-app      │
│  [Open in Browser →]                                 │
│                                                      │
│  Actions:                                            │
│  [Re-deploy] [Update] [Sleep] [Delete]              │
│                                                      │
│  Repository:                                         │
│  https://github.com/user/repo                       │
│  Branch: main                                        │
│  Commit: abc123                                      │
│                                                      │
│  Resources:                                          │
│  CPU: 0.5 vCPU                                      │
│  Memory: 1.0 GB                                     │
│  Port: 8080                                         │
│                                                      │
│  Environment Variables:                              │
│  NODE_ENV: production                               │
│  DATABASE_URL: postgres://...                       │
│  [+ Add Variable]                                    │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## Status Indicators

### Infrastructure Status
- 🟡 **PENDING** - Just created
- 🔵 **PROVISIONING** - Creating AWS resources (5-10 min)
- 🟢 **ACTIVE** - Ready to use
- 🔴 **ERROR** - Provisioning failed
- 🟠 **DESTROYING** - Being deleted
- ⚫ **DESTROYED** - Deleted

### Application Status
- 🟡 **CREATED** - Just created
- 🔵 **BUILDING** - Building Docker image (2-5 min)
- 🔵 **DEPLOYING** - Deploying to ECS (1-2 min)
- 🟢 **ACTIVE** - Running and accessible
- 😴 **SLEEPING** - Scaled to 0 (cost saving)
- 🔴 **FAILED** - Deployment failed

---

## Polling Strategy

### Infrastructure Status
```javascript
// Poll every 5 seconds while PENDING or PROVISIONING
useEffect(() => {
  if (infra.status === 'PENDING' || infra.status === 'PROVISIONING') {
    const interval = setInterval(() => {
      fetchInfrastructure(infra.id)
    }, 5000)
    return () => clearInterval(interval)
  }
}, [infra.status])
```

### Application Status
```javascript
// Poll every 3 seconds while BUILDING or DEPLOYING
useEffect(() => {
  if (app.status === 'BUILDING' || app.status === 'DEPLOYING') {
    const interval = setInterval(() => {
      fetchApplication(app.id)
    }, 3000)
    return () => clearInterval(interval)
  }
}, [app.status])
```

---

## Error Handling

### Display User-Friendly Errors
```javascript
const errorMessages = {
  'Infrastructure with name': 'This infrastructure name already exists',
  'Application with name': 'This application name already exists',
  'permission to create': 'You need ADMIN or SUPER_ADMIN role',
  'Cannot delete infrastructure': 'Delete all applications first',
  'quota exceeded': 'Not enough resources available',
  'Only the infrastructure owner': 'Only the owner can perform this action'
}

function getErrorMessage(error) {
  const message = error.response?.data?.error || error.message
  for (const [key, value] of Object.entries(errorMessages)) {
    if (message.includes(key)) return value
  }
  return 'An error occurred. Please try again.'
}
```

---

## Security Best Practices

1. **JWT Storage**: Store in httpOnly cookie (preferred) or localStorage
2. **Token Refresh**: Implement refresh token flow
3. **CSRF Protection**: Use CSRF tokens for state-changing operations
4. **Input Validation**: Validate all inputs before sending to API
5. **Sensitive Data**: Never log tokens or sensitive environment variables
6. **HTTPS**: Always use HTTPS in production

---

## Next Steps for Frontend Development

1. ✅ Set up React project with TypeScript
2. ✅ Configure API client with axios
3. ✅ Implement GitHub OAuth flow
4. ✅ Build dashboard with infrastructure list
5. ✅ Create infrastructure creation form
6. ✅ Build application management UI
7. ✅ Add status polling for async operations
8. ✅ Implement sleep/wake controls
9. ✅ Add role-based UI (show/hide buttons)
10. ✅ Add error handling and user feedback

---

**Last Updated:** 2026-03-13  
**Complete Implementation Ready**
