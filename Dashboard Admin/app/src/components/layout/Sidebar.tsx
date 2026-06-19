import { LayoutDashboard, Users, Dna, Boxes, Rocket, Shield, Settings } from 'lucide-react';
import type { PageId } from '@/types';

interface SidebarProps {
  currentPage: PageId;
  onNavigate: (page: PageId) => void;
}

const navItems: { id: PageId; label: string; icon: React.ElementType }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'clienti', label: 'Clienti', icon: Users },
  { id: 'dna', label: 'DNA Aziendale', icon: Dna },
  { id: 'famiglie', label: 'Famiglie Prodotto', icon: Boxes },
  { id: 'deploy', label: 'Deploy', icon: Rocket },
  { id: 'admin', label: 'Admin', icon: Shield },
  { id: 'settings', label: 'Impostazioni', icon: Settings },
];

export function Sidebar({ currentPage, onNavigate }: SidebarProps) {
  return (
    <aside className="fixed left-0 top-0 bottom-0 w-[240px] bg-[#12121f] border-r border-white/[0.06] z-50 flex flex-col">
      {/* Logo */}
      <div className="h-[60px] flex items-center gap-3 px-5 border-b border-white/[0.06]">
        <svg width="28" height="28" viewBox="0 0 200 200" className="flex-shrink-0">
          <circle cx="100" cy="100" r="90" fill="none" stroke="#5b6ef5" strokeWidth="12" opacity="0.9" />
          <path d="M110 20 L75 95 L95 95 L85 180 L125 85 L105 85 Z" fill="#5b6ef5" />
        </svg>
        <div>
          <span className="text-base font-bold tracking-[0.15em] text-white">ZEUS</span>
          <span className="block text-[9px] text-white/40 tracking-[0.2em] uppercase -mt-0.5">Knowledge Engine</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(item => {
          const isActive = currentPage === item.id;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-medium transition-all ${
                isActive
                  ? 'nav-active text-[#5b6ef5]'
                  : 'text-white/50 hover:text-white/80 hover:bg-white/[0.03]'
              }`}
            >
              <Icon className="w-[18px] h-[18px] flex-shrink-0" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* System Status */}
      <div className="px-4 py-4 border-t border-white/[0.06]">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] text-white/40 uppercase tracking-wider">Sistema</span>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-[#00e1b7] animate-pulse" />
            <span className="text-[10px] text-[#00e1b7] font-medium">Online</span>
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-white/30">Gateway</span>
          <span className="text-[10px] text-white/50">15 attivi</span>
        </div>
        <div className="flex items-center justify-between mt-1">
          <span className="text-[10px] text-white/30">Versione</span>
          <span className="text-[10px] text-white/50">v2.0.0</span>
        </div>
      </div>
    </aside>
  );
}
