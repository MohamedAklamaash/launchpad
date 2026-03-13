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

## Frontend Architecture

### Technology Stack

**Core Framework**
- Next.js (App Router) with TypeScript
- React 18+

**Styling & UI**
- Tailwind CSS (dark mode default)
- shadcn/ui components
- Radix UI primitives
- Framer Motion animations
- Lucide React icons

**Data Visualization**
- Recharts for metrics/graphs

**Typography**
- Inter (UI text)
- JetBrains Mono (code/logs)

**State Management**
- React Context / Zustand

---

### Project Structure
```
launchpad-frontend/
├── app/
│   ├── (marketing)/
│   │   ├── page.tsx                    # Landing page (Porter-style)
│   │   ├── pricing/page.tsx
│   │   ├── docs/page.tsx
│   │   └── layout.tsx
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   ├── register/page.tsx
│   │   ├── callback/page.tsx           # OAuth callback
│   │   └── layout.tsx
│   ├── (dashboard)/
│   │   ├── layout.tsx                  # Sidebar + Header
│   │   ├── page.tsx                    # Dashboard home
│   │   ├── infrastructures/
│   │   │   ├── page.tsx                # List view
│   │   │   ├── new/page.tsx            # Create form
│   │   │   └── [id]/page.tsx           # Detail view
│   │   ├── applications/
│   │   │   ├── page.tsx
│   │   │   ├── new/page.tsx
│   │   │   └── [id]/
│   │   │       ├── page.tsx
│   │   │       ├── logs/page.tsx
│   │   │       └── metrics/page.tsx
│   │   ├── deployments/page.tsx
│   │   ├── settings/page.tsx
│   │   ├── billing/page.tsx
│   │   └── notifications/page.tsx
│   ├── api/                            # API routes (optional)
│   ├── layout.tsx
│   └── globals.css
├── components/
│   ├── ui/                             # shadcn/ui components
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   ├── dialog.tsx
│   │   ├── dropdown-menu.tsx
│   │   ├── input.tsx
│   │   ├── table.tsx
│   │   ├── tabs.tsx
│   │   ├── toast.tsx
│   │   └── skeleton.tsx
│   ├── layout/
│   │   ├── sidebar.tsx
│   │   ├── header.tsx
│   │   └── page-container.tsx
│   ├── dashboard/
│   │   ├── metric-card.tsx
│   │   ├── deployment-card.tsx
│   │   ├── resource-table.tsx
│   │   ├── log-viewer.tsx
│   │   └── status-badge.tsx
│   ├── charts/
│   │   ├── cpu-chart.tsx
│   │   ├── memory-chart.tsx
│   │   └── request-chart.tsx
│   ├── marketing/
│   │   ├── hero.tsx
│   │   ├── features.tsx
│   │   ├── cta.tsx
│   │   └── footer.tsx
│   └── providers/
│       ├── auth-provider.tsx
│       ├── theme-provider.tsx
│       └── toast-provider.tsx
├── lib/
│   ├── api/
│   │   ├── client.ts
│   │   ├── auth.ts
│   │   ├── infrastructures.ts
│   │   ├── applications.ts
│   │   ├── notifications.ts
│   │   └── payments.ts
│   ├── hooks/
│   │   ├── use-auth.ts
│   │   ├── use-websocket.ts
│   │   └── use-deployments.ts
│   ├── utils.ts
│   └── constants.ts
├── types/
│   ├── api.ts
│   ├── auth.ts
│   └── deployment.ts
├── public/
├── tailwind.config.ts
├── next.config.js
├── tsconfig.json
└── package.json
```

---

### Pages/Routes

**Marketing Site**
```
/                          → Landing page (Porter-inspired)
/pricing                   → Pricing plans
/docs                      → Documentation
```

**Authentication**
```
/login                     → Login page
/register                  → Registration
/callback                  → OAuth callback handler
```

**Dashboard (Protected)**
```
/dashboard                 → Overview with metrics
/infrastructures           → Infrastructure list
/infrastructures/new       → Create infrastructure
/infrastructures/[id]      → Infrastructure details
/applications              → Application list
/applications/new          → Deploy application
/applications/[id]         → App details
/applications/[id]/logs    → Live logs
/applications/[id]/metrics → Metrics dashboard
/deployments               → Deployment history
/settings                  → User settings
/billing                   → Payment & billing
/notifications             → Notification center
```

---

### Layout Structure

**Dashboard Layout**
```
┌─────────────────────────────────────────────────────────────┐
│  Header: Search | Env Switcher | Notifications | Profile    │
├──────┬──────────────────────────────────────────────────────┤
│      │                                                       │
│ Side │              Main Content Area                       │
│ bar  │                                                       │
│      │  ┌─────────┐ ┌─────────┐ ┌─────────┐               │
│ Nav  │  │ Metric  │ │ Metric  │ │ Metric  │               │
│      │  │  Card   │ │  Card   │ │  Card   │               │
│ •    │  └─────────┘ └─────────┘ └─────────┘               │
│ •    │                                                       │
│ •    │  ┌───────────────────────────────────────────┐      │
│      │  │     Deployments / Resources Table         │      │
│      │  └───────────────────────────────────────────┘      │
│      │                                                       │
└──────┴───────────────────────────────────────────────────────┘
```

---

### Visual Design System

**Color Palette (Dark Theme)**
```css
Background:     #0a0a0a (near-black)
Surface:        #141414 (card background)
Border:         #262626 (subtle borders)
Text Primary:   #ffffff
Text Secondary: #a3a3a3
Accent:         #8b5cf6 (purple) or #3b82f6 (blue)
Success:        #10b981
Warning:        #f59e0b
Error:          #ef4444
```

**Typography**
```
UI Text:        Inter (400, 500, 600, 700)
Code/Logs:      JetBrains Mono (400, 500)
```

**Spacing Scale**
```
xs:  4px
sm:  8px
md:  16px
lg:  24px
xl:  32px
2xl: 48px
```

**Component Patterns**
- Cards: `bg-[#141414] border border-[#262626] rounded-lg`
- Hover: `hover:bg-[#1a1a1a] transition-colors`
- Focus: `focus:ring-2 focus:ring-purple-500`

---

### Animation Patterns (Framer Motion)

**Page Transitions**
```tsx
const pageVariants = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -20 }
}
```

**Card Hover**
```tsx
<motion.div
  whileHover={{ scale: 1.02, y: -4 }}
  transition={{ duration: 0.2 }}
/>
```

**Modal/Dialog**
```tsx
const modalVariants = {
  hidden: { opacity: 0, scale: 0.95 },
  visible: { opacity: 1, scale: 1 }
}
```

**Skeleton Loaders**
```tsx
<Skeleton className="h-24 w-full animate-pulse" />
```

---

### State Management
```typescript
// Global State (Zustand)
interface AppState {
  auth: {
    user: User | null
    token: string | null
    isAuthenticated: boolean
  }
  notifications: {
    unread: number
    items: Notification[]
  }
  deployments: {
    active: Deployment[]
    history: Deployment[]
  }
}
```

---

### API Client Structure
```typescript
// lib/api/client.ts
const API_BASE = 'http://localhost:8000'

// lib/api/auth.ts
export const authApi = {
  login: (credentials: LoginDto) => Promise<AuthResponse>
  githubLogin: () => void
  logout: () => Promise<void>
}

// lib/api/infrastructures.ts
export const infraApi = {
  list: () => Promise<Infrastructure[]>
  create: (config: InfraConfig) => Promise<Infrastructure>
  get: (id: string) => Promise<Infrastructure>
  delete: (id: string) => Promise<void>
}

// lib/api/applications.ts
export const appApi = {
  list: () => Promise<Application[]>
  deploy: (config: AppConfig) => Promise<Deployment>
  get: (id: string) => Promise<Application>
  getLogs: (id: string) => Promise<LogStream>
  getMetrics: (id: string) => Promise<Metrics>
}

// lib/api/notifications.ts
export const notificationApi = {
  list: (userId: string) => Promise<Notification[]>
  markRead: (id: string) => Promise<void>
}
```

---

### Real-time Features
```typescript
// WebSocket connections
useWebSocket('/ws/notifications')  // Real-time notifications
useWebSocket('/ws/deployments')    // Live deployment status
useWebSocket('/ws/logs')           // Streaming logs
useWebSocket('/ws/metrics')        // Live metrics
```

---

### Landing Page Structure (Porter-inspired)

**Sections**
1. **Hero**: Bold headline + CTA + animated demo
2. **Features**: 3-column grid with icons
3. **How It Works**: Step-by-step deployment flow
4. **Integrations**: Logos of supported platforms
5. **Pricing**: Tiered pricing cards
6. **CTA**: Final call-to-action
7. **Footer**: Links + social

**Hero Example**
```
Deploy production infrastructure in minutes
[Get Started] [View Demo]
[Animated terminal showing deployment]
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

---

## Key Component Examples

### 1. Metric Card Component
```tsx
// components/dashboard/metric-card.tsx
import { motion } from 'framer-motion'
import { Card } from '@/components/ui/card'
import { LucideIcon } from 'lucide-react'

interface MetricCardProps {
  title: string
  value: string
  change: number
  icon: LucideIcon
}

export function MetricCard({ title, value, change, icon: Icon }: MetricCardProps) {
  return (
    <motion.div whileHover={{ y: -4 }} transition={{ duration: 0.2 }}>
      <Card className="bg-[#141414] border-[#262626] p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-[#a3a3a3]">{title}</p>
            <h3 className="text-2xl font-semibold mt-2">{value}</h3>
            <p className={`text-sm mt-1 ${change >= 0 ? 'text-green-500' : 'text-red-500'}`}>
              {change >= 0 ? '+' : ''}{change}% from last week
            </p>
          </div>
          <Icon className="w-8 h-8 text-purple-500" />
        </div>
      </Card>
    </motion.div>
  )
}
```

### 2. Deployment Card Component
```tsx
// components/dashboard/deployment-card.tsx
import { motion } from 'framer-motion'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Activity, GitBranch } from 'lucide-react'

interface DeploymentCardProps {
  name: string
  status: 'running' | 'deploying' | 'failed'
  branch: string
  lastDeploy: string
}

export function DeploymentCard({ name, status, branch, lastDeploy }: DeploymentCardProps) {
  const statusColors = {
    running: 'bg-green-500/10 text-green-500',
    deploying: 'bg-yellow-500/10 text-yellow-500',
    failed: 'bg-red-500/10 text-red-500'
  }

  return (
    <motion.div
      whileHover={{ scale: 1.02 }}
      transition={{ duration: 0.2 }}
    >
      <Card className="bg-[#141414] border-[#262626] p-4 cursor-pointer hover:bg-[#1a1a1a]">
        <div className="flex items-center justify-between mb-3">
          <h4 className="font-semibold">{name}</h4>
          <Badge className={statusColors[status]}>{status}</Badge>
        </div>
        <div className="flex items-center gap-4 text-sm text-[#a3a3a3]">
          <div className="flex items-center gap-1">
            <GitBranch className="w-4 h-4" />
            <span>{branch}</span>
          </div>
          <div className="flex items-center gap-1">
            <Activity className="w-4 h-4" />
            <span>{lastDeploy}</span>
          </div>
        </div>
      </Card>
    </motion.div>
  )
}
```

### 3. Log Viewer Component
```tsx
// components/dashboard/log-viewer.tsx
import { Card } from '@/components/ui/card'
import { useEffect, useRef } from 'react'

interface LogViewerProps {
  logs: string[]
  isStreaming?: boolean
}

export function LogViewer({ logs, isStreaming }: LogViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs])

  return (
    <Card className="bg-[#0a0a0a] border-[#262626] p-4">
      <div
        ref={scrollRef}
        className="h-96 overflow-y-auto font-mono text-sm text-[#a3a3a3] space-y-1"
      >
        {logs.map((log, i) => (
          <div key={i} className="hover:bg-[#141414] px-2 py-1 rounded">
            <span className="text-[#666] mr-3">{i + 1}</span>
            {log}
          </div>
        ))}
        {isStreaming && (
          <div className="flex items-center gap-2 px-2 py-1">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
            <span className="text-green-500">Streaming...</span>
          </div>
        )}
      </div>
    </Card>
  )
}
```

### 4. Sidebar Navigation
```tsx
// components/layout/sidebar.tsx
'use client'
import { motion } from 'framer-motion'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Home, Server, Rocket, Settings, CreditCard } from 'lucide-react'

const navItems = [
  { href: '/dashboard', icon: Home, label: 'Dashboard' },
  { href: '/infrastructures', icon: Server, label: 'Infrastructure' },
  { href: '/applications', icon: Rocket, label: 'Applications' },
  { href: '/billing', icon: CreditCard, label: 'Billing' },
  { href: '/settings', icon: Settings, label: 'Settings' },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="w-64 bg-[#0a0a0a] border-r border-[#262626] h-screen sticky top-0">
      <div className="p-6">
        <h1 className="text-xl font-bold bg-gradient-to-r from-purple-500 to-blue-500 bg-clip-text text-transparent">
          Launchpad
        </h1>
      </div>
      <nav className="px-3">
        {navItems.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link key={item.href} href={item.href}>
              <motion.div
                whileHover={{ x: 4 }}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg mb-1 transition-colors ${
                  isActive
                    ? 'bg-purple-500/10 text-purple-500'
                    : 'text-[#a3a3a3] hover:bg-[#141414] hover:text-white'
                }`}
              >
                <item.icon className="w-5 h-5" />
                <span className="font-medium">{item.label}</span>
              </motion.div>
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
```

### 5. Header Component
```tsx
// components/layout/header.tsx
'use client'
import { Search, Bell, User } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export function Header() {
  return (
    <header className="h-16 border-b border-[#262626] bg-[#0a0a0a] sticky top-0 z-50">
      <div className="flex items-center justify-between h-full px-6">
        <div className="flex items-center gap-4 flex-1 max-w-xl">
          <Search className="w-5 h-5 text-[#a3a3a3]" />
          <Input
            placeholder="Search services, deployments..."
            className="bg-[#141414] border-[#262626] focus:border-purple-500"
          />
        </div>
        
        <div className="flex items-center gap-4">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="text-[#a3a3a3]">
                Production
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem>Production</DropdownMenuItem>
              <DropdownMenuItem>Staging</DropdownMenuItem>
              <DropdownMenuItem>Development</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button variant="ghost" size="icon" className="relative">
            <Bell className="w-5 h-5" />
            <span className="absolute top-1 right-1 w-2 h-2 bg-red-500 rounded-full" />
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <User className="w-5 h-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem>Profile</DropdownMenuItem>
              <DropdownMenuItem>Settings</DropdownMenuItem>
              <DropdownMenuItem>Logout</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}
```

### 6. Landing Page Hero
```tsx
// components/marketing/hero.tsx
'use client'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/button'
import { ArrowRight, Play } from 'lucide-react'
import Link from 'next/link'

export function Hero() {
  return (
    <section className="relative min-h-screen flex items-center justify-center overflow-hidden">
      {/* Gradient background */}
      <div className="absolute inset-0 bg-gradient-to-br from-purple-900/20 via-transparent to-blue-900/20" />
      
      <div className="container mx-auto px-6 relative z-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="text-center max-w-4xl mx-auto"
        >
          <h1 className="text-6xl font-bold mb-6 bg-gradient-to-r from-white via-purple-200 to-blue-200 bg-clip-text text-transparent">
            Deploy production infrastructure in minutes
          </h1>
          <p className="text-xl text-[#a3a3a3] mb-8 max-w-2xl mx-auto">
            Build, deploy, and scale your applications with zero DevOps overhead.
            From code to production in one click.
          </p>
          
          <div className="flex items-center justify-center gap-4">
            <Link href="/register">
              <Button size="lg" className="bg-purple-600 hover:bg-purple-700">
                Get Started <ArrowRight className="ml-2 w-4 h-4" />
              </Button>
            </Link>
            <Button size="lg" variant="outline" className="border-[#262626]">
              <Play className="mr-2 w-4 h-4" /> View Demo
            </Button>
          </div>
        </motion.div>

        {/* Animated terminal demo */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.2 }}
          className="mt-16 max-w-4xl mx-auto"
        >
          <div className="bg-[#0a0a0a] border border-[#262626] rounded-lg overflow-hidden">
            <div className="bg-[#141414] px-4 py-2 flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <div className="w-3 h-3 rounded-full bg-yellow-500" />
              <div className="w-3 h-3 rounded-full bg-green-500" />
            </div>
            <div className="p-6 font-mono text-sm">
              <div className="text-green-500">$ launchpad deploy</div>
              <div className="text-[#a3a3a3] mt-2">→ Building application...</div>
              <div className="text-[#a3a3a3]">→ Provisioning infrastructure...</div>
              <div className="text-[#a3a3a3]">→ Deploying to production...</div>
              <div className="text-green-500 mt-2">✓ Deployed successfully!</div>
              <div className="text-purple-500">https://your-app.launchpad.dev</div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
```

---

## Setup Instructions

### 1. Initialize Next.js Project
```bash
npx create-next-app@latest launchpad-frontend --typescript --tailwind --app
cd launchpad-frontend
```

### 2. Install Dependencies
```bash
# UI Components
npx shadcn-ui@latest init
npx shadcn-ui@latest add button card input table dialog dropdown-menu tabs toast skeleton badge

# Additional packages
npm install framer-motion lucide-react recharts zustand
npm install @radix-ui/react-dropdown-menu @radix-ui/react-dialog
npm install axios date-fns clsx tailwind-merge
```

### 3. Configure Fonts (app/layout.tsx)
```tsx
import { Inter } from 'next/font/google'
import localFont from 'next/font/local'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const jetbrainsMono = localFont({
  src: '../public/fonts/JetBrainsMono-Regular.woff2',
  variable: '--font-jetbrains-mono'
})
```

### 4. Tailwind Config
```js
// tailwind.config.ts
module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: '#0a0a0a',
        surface: '#141414',
        border: '#262626',
      },
      fontFamily: {
        sans: ['var(--font-inter)'],
        mono: ['var(--font-jetbrains-mono)'],
      },
    },
  },
}
```

### 5. Environment Variables
```bash
# .env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

## Implementation Roadmap

1. **Setup Project**: Initialize Next.js with TypeScript + Tailwind
2. **Install shadcn/ui**: Add all required UI components
3. **Build Landing Page**: Porter-inspired hero, features, pricing
4. **Implement Auth**: Login/register pages with GitHub OAuth
5. **Create Dashboard Layout**: Sidebar + header + protected routes
6. **Build Core Pages**: Infrastructure, applications, deployments
7. **Add Real-time Features**: WebSocket for logs/metrics/notifications
8. **Integrate API**: Connect all pages to gateway service
9. **Add Animations**: Framer Motion page transitions and interactions
10. **Testing & Deploy**: E2E tests, deploy to Vercel


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
