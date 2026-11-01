import Link from 'next/link';
import {
  ArrowRight,
  Boxes,
  Database,
  Globe,
  History,
  KeyRound,
  ScrollText,
  GitBranch,
} from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';
import { ThemeToggle } from '@/components/theme-toggle';
import { AuthRedirect } from '@/components/landing/auth-redirect';
import { buttonVariants } from '@/components/ui/button-variants';
import { isCfnOnboardingEnabled } from '@/lib/onboarding-cloudformation';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { cn } from '@/lib/utils';

const NAV = [
  { href: '#how-it-works', label: 'Onboarding' },
  { href: '#compute', label: 'Compute' },
  { href: '#security', label: 'Security' },
  ...(ORGS_ENABLED ? [{ href: '#teams', label: 'Teams' }] : []),
];

const STEPS = [
  {
    title: 'Add your AWS account',
    body: 'Give it a name and choose where apps will run. Launchpad shows a one-time token, once.',
  },
  {
    title: isCfnOnboardingEnabled() ? 'Run the script, or launch a stack' : 'Run the script',
    body: 'It creates a single IAM role named LaunchpadDeploymentRole in your account. It is safe to run again whenever the policy changes.',
  },
  {
    title: 'Launchpad checks the role',
    body: 'It assumes the role using an ExternalId tied to your setup, confirms the account number matches, and burns the token.',
  },
  {
    title: 'Provisioning starts',
    body: 'Terraform builds the VPC, load balancer, container registry and cluster. When it finishes, connect a repository and deploy.',
  },
];

const FEATURES = [
  {
    icon: GitBranch,
    title: 'Deploys from GitHub',
    body: 'Choose a repo and a branch. Pushes arrive through a webhook signed with a secret that belongs to that one app.',
  },
  {
    icon: ScrollText,
    title: 'Logs and metrics',
    body: 'Follow a build while it runs, then read the running app’s logs and graphs without opening the AWS console.',
  },
  {
    icon: History,
    title: 'Rollbacks',
    body: 'Redeploy an earlier release from the history. A preview shows what will change before you confirm.',
  },
  {
    icon: KeyRound,
    title: 'Environment variables',
    body: 'Set them per app in the dashboard. They apply on the next deploy.',
  },
  {
    icon: Database,
    title: 'Databases',
    body: 'PostgreSQL, MySQL and Redis, provisioned in your account next to the apps that use them.',
  },
  {
    icon: Globe,
    title: 'Domains and costs',
    body: 'Attach your own domain to an app. See what each app spends in AWS, pulled from Cost Explorer.',
  },
];

const COMPUTE = [
  {
    name: 'ECS Fargate',
    tag: 'Default',
    rows: [
      ['What it is', 'Containers on serverless capacity. No nodes to patch or size.'],
      ['Pick it when', 'You want the least to look after.'],
      ['Cost view', 'Actual figures from AWS.'],
    ],
  },
  {
    name: 'EKS',
    tag: 'Where enabled',
    rows: [
      ['What it is', 'A Kubernetes cluster in your account. Launchpad deploys to it through the Kubernetes API.'],
      ['Pick it when', 'Your team already works in Kubernetes.'],
      ['Cost view', 'Estimates. Kubernetes gives AWS no per-app split to bill against.'],
    ],
  },
];

const SECURITY = [
  {
    title: 'One role, scoped to your setup',
    body: 'Launchpad reaches your account only through LaunchpadDeploymentRole. Its trust policy requires an ExternalId unique to your infrastructure, which stops anyone else from borrowing the role.',
  },
  {
    title: 'A policy you can read',
    body: 'The permission set is published in full and carries a version number. When it changes, the dashboard flags the account and gives you a refresh command. Nothing widens without you running it.',
  },
  {
    title: 'Tokens that burn',
    body: 'The onboarding token works once and is stored as a SHA-256 hash. After the callback it is gone.',
  },
  {
    title: 'What does leave your account',
    body: 'Source code is built on Launchpad’s own builders, and the finished image is pushed to a registry in your account. Running apps, databases and their data stay with you. Delete the role and Launchpad has no way back in.',
  },
];

const ROLES = [
  ['Owner', 'Everything, including billing, deleting infrastructure and transferring ownership.'],
  ['Admin', 'Adds AWS accounts, invites developers and viewers, manages databases, domains and costs.'],
  ['Developer', 'Deploys, rolls back, edits environment variables, reads logs and metrics.'],
  ['Viewer', 'Read-only. Sees what is running; changes nothing.'],
];

const container = 'mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-8';

function SectionHead({ id, label, title, children }: { id: string; label: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="max-w-2xl">
      <p className="text-xs font-medium text-brand">{label}</p>
      <h2 id={id} className="mt-2 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
        {title}
      </h2>
      {children && <p className="mt-3 text-base text-muted-foreground">{children}</p>}
    </div>
  );
}

function StatusDot({ tone }: { tone: 'success' | 'brand' }) {
  return <span className={cn('size-1.5 rounded-full', tone === 'success' ? 'bg-success' : 'bg-brand')} />;
}

function ProductSketch() {
  const apps = [
    { name: 'api', branch: 'main', sha: '3f9a2c1', state: 'Active', tone: 'success' as const },
    { name: 'web', branch: 'main', sha: 'b81d07e', state: 'Building', tone: 'brand' as const },
    { name: 'worker', branch: 'release', sha: '0c44e9a', state: 'Active', tone: 'success' as const },
  ];
  return (
    <div aria-hidden="true" className="overflow-hidden rounded-lg border border-border bg-card shadow-popover">
      <div className="flex items-center gap-1.5 border-b border-border bg-surface-1 px-3 py-2.5">
        <span className="size-2 rounded-full bg-border" />
        <span className="size-2 rounded-full bg-border" />
        <span className="size-2 rounded-full bg-border" />
        <span className="ml-3 text-xs text-muted-foreground">Infrastructure / production</span>
      </div>
      <div className="grid gap-px bg-border sm:grid-cols-3">
        {[
          ['AWS account', '1111 2222 3333'],
          ['Region', 'us-east-1'],
          ['Compute', 'ECS Fargate'],
        ].map(([k, v]) => (
          <div key={k} className="bg-card px-4 py-3">
            <p className="text-xs text-muted-foreground">{k}</p>
            <p className="mt-0.5 font-mono text-xs text-foreground">{v}</p>
          </div>
        ))}
      </div>
      <div className="border-t border-border">
        <div className="grid grid-cols-[1fr_5rem] gap-4 px-4 py-2 text-xs text-muted-foreground sm:grid-cols-[1.2fr_1fr_1fr_5rem]">
          <span>Application</span>
          <span className="hidden sm:block">Branch</span>
          <span className="hidden sm:block">Commit</span>
          <span>Status</span>
        </div>
        {apps.map((a) => (
          <div
            key={a.name}
            className="grid grid-cols-[1fr_5rem] items-center gap-4 border-t border-border px-4 py-2.5 text-xs sm:grid-cols-[1.2fr_1fr_1fr_5rem]"
          >
            <span className="font-medium text-foreground">{a.name}</span>
            <span className="hidden font-mono text-muted-foreground sm:block">{a.branch}</span>
            <span className="hidden font-mono text-muted-foreground sm:block">{a.sha}</span>
            <span className="flex items-center gap-1.5 text-foreground">
              <StatusDot tone={a.tone} />
              {a.state}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <AuthRedirect />

      <header className="sticky top-0 z-40 border-b border-border bg-background/90 backdrop-blur-sm">
        <div className={cn(container, 'flex h-14 items-center justify-between')}>
          <div className="flex items-center gap-8">
            <Link href="/" className="flex items-center gap-2.5 rounded-md text-sm font-semibold tracking-tight">
              <LogoMark size={22} />
              Launchpad
            </Link>
            <nav aria-label="Sections" className="hidden items-center gap-1 md:flex">
              {NAV.map((n) => (
                <a
                  key={n.href}
                  href={n.href}
                  className="rounded-md px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  {n.label}
                </a>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Link href="/login" className={buttonVariants({ size: 'default' })}>
              Sign in
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className={cn(container, 'pb-20 pt-16 sm:pt-24 lg:pb-28')}>
          <div className="max-w-3xl">
            <h1 className="reveal text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
              Deploy to the AWS account you already own.
            </h1>
            <p className="reveal reveal-2 mt-5 max-w-2xl text-base text-muted-foreground sm:text-lg">
              Launchpad sets up the network, load balancer, registry and cluster inside your account, then builds and ships your apps from GitHub. The resources and the bill are yours. Remove one IAM role and Launchpad is out.
            </p>
            <div className="reveal reveal-3 mt-8 flex flex-wrap items-center gap-3">
              <Link href="/login" className={buttonVariants({ size: 'lg' })}>
                Sign in
                <ArrowRight className="size-4" />
              </Link>
              <a href="#how-it-works" className={buttonVariants({ variant: 'outline', size: 'lg' })}>
                How onboarding works
              </a>
            </div>
            <p className="reveal reveal-3 mt-4 text-xs text-muted-foreground">
              ECS Fargate or EKS. {isCfnOnboardingEnabled() ? 'Onboard with a shell script or a CloudFormation stack.' : 'Onboard with a single shell script.'}
            </p>
          </div>

          <div className="reveal reveal-3 mt-14 sm:mt-16">
            <ProductSketch />
          </div>
        </section>

        <section id="how-it-works" aria-labelledby="how-title" className="border-t border-border py-20 sm:py-24">
          <div className={container}>
            <SectionHead id="how-title" label="Onboarding" title="Connecting an account takes one command">
              You run it once, in a shell that already has AWS credentials for the account. After that Launchpad works through the role it created.
            </SectionHead>
            <ol className="mt-12 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2">
              {STEPS.map((s, i) => (
                <li key={s.title} className="bg-background p-6">
                  <span className="font-mono text-xs text-muted-foreground">0{i + 1}</span>
                  <h3 className="mt-3 text-base font-medium text-foreground">{s.title}</h3>
                  <p className="mt-1.5 text-sm text-muted-foreground">{s.body}</p>
                </li>
              ))}
            </ol>
            {isCfnOnboardingEnabled() && (
              <p className="mt-5 text-sm text-muted-foreground">
                Prefer clicking? The CloudFormation option opens a quick-create link in the AWS console and sets up the same role.
              </p>
            )}
          </div>
        </section>

        <section id="compute" aria-labelledby="compute-title" className="border-t border-border bg-surface-1 py-20 sm:py-24">
          <div className={container}>
            <SectionHead id="compute-title" label="Compute" title="Fargate by default, EKS if you live in Kubernetes">
              You choose when you add the account. It can’t be changed afterwards, so the cluster never shifts under a running app.
            </SectionHead>
            <div className="mt-12 grid gap-4 md:grid-cols-2">
              {COMPUTE.map((c) => (
                <div key={c.name} className="rounded-lg border border-border bg-card p-6">
                  <div className="flex items-center justify-between">
                    <h3 className="text-base font-medium text-foreground">{c.name}</h3>
                    <span className="rounded-md border border-border px-1.5 py-0.5 text-xs text-muted-foreground">{c.tag}</span>
                  </div>
                  <dl className="mt-5 divide-y divide-border text-sm">
                    {c.rows.map(([k, v]) => (
                      <div key={k} className="grid gap-1 py-3 sm:grid-cols-[7rem_1fr] sm:gap-4">
                        <dt className="text-muted-foreground">{k}</dt>
                        <dd className="text-foreground">{v}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section aria-labelledby="after-title" className="border-t border-border py-20 sm:py-24">
          <div className={container}>
            <SectionHead id="after-title" label="After onboarding" title="What you can do once it’s running" />
            <ul className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-2">
              {FEATURES.map((f) => (
                <li key={f.title} className="flex gap-4">
                  <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-surface-1 text-muted-foreground">
                    <f.icon className="size-4" />
                  </span>
                  <div>
                    <h3 className="text-sm font-medium text-foreground">{f.title}</h3>
                    <p className="mt-1 text-sm text-muted-foreground">{f.body}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="security" aria-labelledby="security-title" className="border-t border-border bg-surface-1 py-20 sm:py-24">
          <div className={cn(container, 'grid gap-12 lg:grid-cols-[1fr_1.3fr] lg:gap-16')}>
            <div className="lg:sticky lg:top-24 lg:self-start">
              <SectionHead id="security-title" label="Security" title="Access that you grant and can revoke">
                Launchpad doesn’t hold long-lived keys to your account. It borrows a role, and the role is yours to delete.
              </SectionHead>
              <div className="mt-8 rounded-lg border border-border bg-card p-4 font-mono text-xs leading-relaxed">
                <p className="text-muted-foreground">LaunchpadDeploymentRole</p>
                <p className="mt-2 text-foreground">Principal: Launchpad platform user</p>
                <p className="text-foreground">Condition: sts:ExternalId = your infrastructure id</p>
              </div>
            </div>
            <dl className="divide-y divide-border rounded-lg border border-border bg-card">
              {SECURITY.map((s) => (
                <div key={s.title} className="p-6">
                  <dt className="text-sm font-medium text-foreground">{s.title}</dt>
                  <dd className="mt-1.5 text-sm text-muted-foreground">{s.body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {ORGS_ENABLED && (
          <section id="teams" aria-labelledby="teams-title" className="border-t border-border py-20 sm:py-24">
            <div className={container}>
              <SectionHead id="teams-title" label="Teams" title="Organizations with four roles">
                Invite people by email and give each one only what they need. The server enforces these roles; the dashboard just hides what you can’t use.
              </SectionHead>
              <dl className="mt-12 divide-y divide-border rounded-lg border border-border">
                {ROLES.map(([role, can]) => (
                  <div key={role} className="grid gap-1 px-6 py-4 sm:grid-cols-[9rem_1fr] sm:gap-6">
                    <dt className="flex items-center gap-2 text-sm font-medium text-foreground">
                      <Boxes className="size-3.5 text-muted-foreground" />
                      {role}
                    </dt>
                    <dd className="text-sm text-muted-foreground">{can}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>
        )}

        <section className="border-t border-border py-20 sm:py-24">
          <div className={cn(container, 'flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-center')}>
            <div>
              <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Start with one AWS account.</h2>
              <p className="mt-2 text-base text-muted-foreground">Sign in with GitHub, or with an email and password.</p>
            </div>
            <Link href="/login" className={buttonVariants({ size: 'lg' })}>
              Sign in
              <ArrowRight className="size-4" />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-border py-8">
        <div className={cn(container, 'flex flex-col items-start justify-between gap-4 text-sm text-muted-foreground sm:flex-row sm:items-center')}>
          <span className="flex items-center gap-2.5 font-medium text-foreground">
            <LogoMark size={18} />
            Launchpad
          </span>
          <nav aria-label="Footer" className="flex gap-5">
            <a href="#how-it-works" className="transition-colors hover:text-foreground">Onboarding</a>
            <a href="#security" className="transition-colors hover:text-foreground">Security</a>
            <Link href="/login" className="transition-colors hover:text-foreground">Sign in</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
