'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutGrid, Menu, Rocket, Server, Settings, Users } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { useOrgRole } from '@/lib/use-org-role';
import { cn } from '@/lib/utils';

const navItems = [
  { href: '/dashboard', icon: LayoutGrid, label: 'Overview', match: 'exact' as const },
  { href: '/dashboard/infrastructures', icon: Server, label: 'Infrastructure', match: 'prefix' as const },
  { href: '/dashboard/applications', icon: Rocket, label: 'Applications', match: 'prefix' as const },
];

function Brand() {
  return (
    <Link
      href="/dashboard"
      className="flex items-center gap-2.5 rounded-md px-1 py-1 text-sm font-semibold tracking-tight text-foreground"
    >
      <LogoMark size={22} />
      Launchpad
    </Link>
  );
}

function NavLink({
  href,
  icon: Icon,
  label,
  active,
  onNavigate,
}: {
  href: string;
  icon: typeof LayoutGrid;
  label: string;
  active: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-(--duration-fast)',
        active
          ? 'bg-sidebar-accent font-medium text-foreground'
          : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground',
      )}
    >
      <Icon className={cn('size-4 shrink-0', active ? 'text-foreground' : 'text-muted-foreground')} />
      {label}
    </Link>
  );
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { enabled, org } = useOrgRole();
  const items =
    enabled && org
      ? [...navItems, { href: `/dashboard/organizations/${org.id}/members`, icon: Users, label: 'Members', match: 'prefix' as const }]
      : navItems;
  const isActive = (href: string, match: 'exact' | 'prefix') =>
    match === 'exact' ? pathname === href : pathname === href || pathname.startsWith(href + '/');

  return (
    <nav aria-label="Main" className="flex flex-1 flex-col px-2 pt-1">
      <div className="space-y-0.5">
        {items.map((item) => (
          <NavLink key={item.href} {...item} active={isActive(item.href, item.match)} onNavigate={onNavigate} />
        ))}
      </div>
      <div className="mt-auto pb-3">
        <NavLink
          href="/dashboard/settings"
          icon={Settings}
          label="Settings"
          active={isActive('/dashboard/settings', 'prefix')}
          onNavigate={onNavigate}
        />
      </div>
    </nav>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar lg:flex">
      <div className="flex h-12 items-center px-3">
        <Brand />
      </div>
      <SidebarNav />
    </aside>
  );
}

export function MobileSidebar() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)');
    const close = (e: MediaQueryListEvent) => {
      if (e.matches) setOpen(false);
    };
    mq.addEventListener('change', close);
    return () => mq.removeEventListener('change', close);
  }, []);

  return (
    <>
      <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>
        <Menu className="size-4" />
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" showCloseButton={false} className="w-64 gap-0 bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-12 items-center px-3">
            <Brand />
          </div>
          <SidebarNav onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}
