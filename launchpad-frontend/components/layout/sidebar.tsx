'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutGrid, Server, Rocket } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';

const navItems = [
  { href: '/dashboard', icon: LayoutGrid, label: 'Overview', match: 'exact' as const },
  { href: '/dashboard/infrastructures', icon: Server, label: 'Infrastructure', match: 'prefix' as const },
  { href: '/dashboard/applications', icon: Rocket, label: 'Applications', match: 'prefix' as const },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 shrink-0 bg-sidebar border-r border-hairline flex flex-col">
      <div className="px-5 h-16 flex items-center border-b border-hairline">
        <Link href="/dashboard" className="flex items-center gap-2.5 group">
          <span className="w-8 h-8 rounded-lg bg-surface-2 border border-hairline-strong flex items-center justify-center shrink-0 transition-colors group-hover:border-brand/40">
            <LogoMark size={20} />
          </span>
          <span className="text-[15px] font-display font-semibold tracking-tight text-foreground">Launchpad</span>
        </Link>
      </div>

      <div className="px-5 pt-6 pb-2">
        <span className="eyebrow">Console</span>
      </div>

      <nav className="flex-1 px-3 space-y-1">
        {navItems.map((item) => {
          const isActive =
            item.match === 'exact'
              ? pathname === item.href
              : pathname === item.href || pathname.startsWith(item.href + '/');
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive ? 'page' : undefined}
              className={`relative flex items-center gap-3 px-3 h-9 rounded-lg text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${
                isActive
                  ? 'bg-surface-2 text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-surface-1'
              }`}
            >
              {isActive && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-brand" />
              )}
              <item.icon className={`w-4 h-4 transition-colors ${isActive ? 'text-brand' : 'text-muted-foreground/80'}`} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-hairline flex items-center justify-between">
        <span className="eyebrow">v1.0.0</span>
        <span className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">Live</span>
        </span>
      </div>
    </aside>
  );
}
