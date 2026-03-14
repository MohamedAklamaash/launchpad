'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Server, Rocket } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';

const navItems = [
  { href: '/dashboard', icon: Home, label: 'Dashboard' },
  { href: '/dashboard/infrastructures', icon: Server, label: 'Infrastructure' },
  { href: '/dashboard/applications', icon: Rocket, label: 'Applications' },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 bg-[#080808] border-r border-[#1a1a1a] flex flex-col">
      <div className="px-5 py-5 border-b border-[#1a1a1a]">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-md bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center shrink-0">
            <LogoMark size={20} />
          </div>
          <span className="text-sm font-semibold tracking-tight text-white">Launchpad</span>
        </div>
      </div>
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {navItems.map((item) => {
          const isActive = pathname === item.href || (item.href !== '/dashboard' && pathname.startsWith(item.href));
          return (
            <Link key={item.href} href={item.href}>
              <div className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-all ${
                isActive
                  ? 'bg-[#1a1a1a] text-white font-medium'
                  : 'text-[#666] hover:text-[#aaa] hover:bg-[#111]'
              }`}>
                <item.icon className={`w-4 h-4 ${isActive ? 'text-violet-400' : ''}`} />
                {item.label}
              </div>
            </Link>
          );
        })}
      </nav>
      <div className="px-3 py-3 border-t border-[#1a1a1a]">
        <p className="text-[10px] text-[#333] font-mono uppercase tracking-widest">v1.0.0</p>
      </div>
    </aside>
  );
}
