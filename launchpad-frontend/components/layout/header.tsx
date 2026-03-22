'use client';

import { User, LogOut } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAuthStore } from '@/lib/store/auth';
import { useRouter } from 'next/navigation';
import Image from 'next/image'

const ROLE_LABELS: Record<string, { label: string; color: string }> = {
  super_admin: { label: 'Super Admin', color: 'text-purple-400' },
  admin: { label: 'Admin', color: 'text-blue-400' },
  user: { label: 'User', color: 'text-green-400' },
  guest: { label: 'Guest', color: 'text-[#666]' },
};

export function Header() {
  const router = useRouter();
  const { user, logout } = useAuthStore();

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  const roleInfo = ROLE_LABELS[user?.role ?? 'guest'] ?? ROLE_LABELS.guest;

  return (
    <header className="h-14 border-b border-[#1a1a1a] bg-[#080808] flex items-center justify-between px-5">
      <div className="flex-1" />
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 mr-1">
          <span className={`text-xs font-mono px-2 py-0.5 rounded-full border ${roleInfo.color === 'text-purple-400' ? 'border-violet-500/30 bg-violet-500/10 text-violet-400' :
            roleInfo.color === 'text-blue-400' ? 'border-blue-500/30 bg-blue-500/10 text-blue-400' :
              roleInfo.color === 'text-green-400' ? 'border-green-500/30 bg-green-500/10 text-green-400' :
                'border-[#2a2a2a] bg-[#111] text-[#666]'
            }`}>{roleInfo.label}</span>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger className="rounded-full w-8 h-8 flex items-center justify-center hover:bg-[#141414] transition-colors ring-1 ring-[#222] overflow-hidden">
            {user?.profile_url ? (
              <Image src={user.profile_url} alt={user.user_name} className="w-8 h-8 rounded-full object-cover" />
            ) : (
              <User className="w-4 h-4 text-[#666]" />
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="bg-[#0f0f0f] border-[#1e1e1e] min-w-[200px] shadow-xl shadow-black/50">
            <div className="px-3 py-2.5 space-y-0.5">
              <p className="font-semibold text-sm text-white">{user?.user_name}</p>
              <p className="text-xs text-[#555]">{user?.email}</p>
            </div>
            <DropdownMenuSeparator className="bg-[#1e1e1e]" />
            <DropdownMenuItem onClick={handleLogout} className="text-red-400 focus:text-red-400 focus:bg-red-500/10 cursor-pointer mx-1 mb-1 rounded-md">
              <LogOut className="w-3.5 h-3.5 mr-2" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
