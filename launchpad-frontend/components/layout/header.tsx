'use client';

import { User, LogOut, Settings } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAuthStore } from '@/lib/store/auth';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { OrgSwitcher } from '@/components/org-switcher';
import { MobileSidebar } from '@/components/layout/sidebar';
import { ThemeToggle } from '@/components/theme-toggle';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';

export function Header() {
  const router = useRouter();
  const { user, logout } = useAuthStore();

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  return (
    <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border bg-background px-3 sm:px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MobileSidebar />
        {ORGS_ENABLED && <OrgSwitcher />}
      </div>

      <div className="flex items-center gap-1">
        <ThemeToggle />
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label="Account menu"
            className="flex size-8 items-center justify-center overflow-hidden rounded-full border border-border bg-surface-2 outline-none transition-colors hover:border-hairline-strong focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {user?.profile_url ? (
              <Image src={user.profile_url} alt="" width={32} height={32} className="size-8 rounded-full object-cover" />
            ) : (
              <User className="size-4 text-muted-foreground" />
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-[220px]">
            <div className="space-y-0.5 px-2 py-2">
              <p className="truncate text-sm font-medium text-foreground">{user?.user_name}</p>
              <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => router.push('/dashboard/settings')} className="cursor-pointer">
              <Settings className="size-3.5" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={handleLogout}
              className="cursor-pointer text-destructive focus:bg-destructive/10 focus:text-destructive"
            >
              <LogOut className="size-3.5" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
