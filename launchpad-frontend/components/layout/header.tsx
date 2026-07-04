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

export function Header() {
  const router = useRouter();
  const { user, logout } = useAuthStore();

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  return (
    <header className="h-16 shrink-0 border-b border-hairline bg-surface-1/60 backdrop-blur-sm flex items-center justify-between px-6">
      <div className="flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full bg-success" />
        <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
          Mission Control
        </span>
      </div>

      <div className="flex items-center gap-3">
        <DropdownMenu>
          <DropdownMenuTrigger className="rounded-full w-9 h-9 flex items-center justify-center transition-all ring-1 ring-hairline-strong hover:ring-brand/40 overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
            {user?.profile_url ? (
              <Image src={user.profile_url} alt={user.user_name} width={36} height={36} className="w-9 h-9 rounded-full object-cover" />
            ) : (
              <User className="w-4 h-4 text-muted-foreground" />
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-[220px] bg-popover border-hairline-strong shadow-xl shadow-black/40">
            <div className="px-3 py-2.5 space-y-0.5">
              <p className="font-medium text-sm text-foreground truncate">{user?.user_name}</p>
              <p className="text-xs text-muted-foreground truncate">{user?.email}</p>
            </div>
            <DropdownMenuSeparator className="bg-hairline" />
            <DropdownMenuItem
              onClick={() => router.push('/dashboard/settings')}
              className="cursor-pointer mx-1 mt-1 rounded-md"
            >
              <Settings className="w-3.5 h-3.5 mr-2" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={handleLogout}
              className="text-destructive focus:text-destructive focus:bg-destructive/10 cursor-pointer mx-1 mb-1 rounded-md"
            >
              <LogOut className="w-3.5 h-3.5 mr-2" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
