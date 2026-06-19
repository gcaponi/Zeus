import { Search, Bell, ChevronDown, Zap } from 'lucide-react';
import { useState } from 'react';
import { mockNotifications } from '@/data/mock';
import type { PageId } from '@/types';

interface HeaderProps {
  currentPage: PageId;
}

const pageNames: Record<PageId, string> = {
  dashboard: 'Dashboard',
  clienti: 'Clienti',
  dna: 'DNA Aziendale',
  famiglie: 'Famiglie Prodotto',
  deploy: 'Deploy',
  admin: 'Admin',
  settings: 'Impostazioni',
};

export function Header({ currentPage }: HeaderProps) {
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const unreadCount = mockNotifications.filter(n => !n.read).length;

  return (
    <header className="fixed top-0 right-0 left-0 h-[60px] bg-[#0a0a0f]/90 backdrop-blur-md border-b border-white/[0.06] z-50 flex items-center justify-between px-6"
      style={{ marginLeft: 240 }}>
      {/* Left: Breadcrumb */}
      <div className="flex items-center gap-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.15em] text-white/40">
          ZEUS
        </span>
        <span className="text-white/20">/</span>
        <h1 className="text-sm font-semibold text-white/90">{pageNames[currentPage]}</h1>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-4">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30" />
          <input
            type="text"
            placeholder="Cerca clienti, DNA, deploy..."
            className="w-[280px] h-9 pl-9 pr-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-[#5b6ef5]/50 focus:ring-1 focus:ring-[#5b6ef5]/20 transition-all"
          />
        </div>

        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => setShowNotifications(!showNotifications)}
            className="relative w-9 h-9 flex items-center justify-center rounded-lg bg-white/[0.04] border border-white/[0.08] hover:bg-white/[0.08] hover:border-white/[0.12] transition-all"
          >
            <Bell className="w-4 h-4 text-white/50" />
            {unreadCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-[#ff4ecd] rounded-full text-[9px] font-bold text-white flex items-center justify-center">
                {unreadCount}
              </span>
            )}
          </button>

          {showNotifications && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowNotifications(false)} />
              <div className="absolute right-0 top-11 w-[360px] bg-[#12121f] border border-white/[0.08] rounded-xl shadow-2xl z-50 overflow-hidden">
                <div className="px-4 py-3 border-b border-white/[0.06] flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-white/80">Notifiche</h3>
                  <span className="text-[10px] text-[#5b6ef5] font-medium cursor-pointer hover:underline">Segna tutte come lette</span>
                </div>
                <div className="max-h-[320px] overflow-y-auto">
                  {mockNotifications.map(n => (
                    <div key={n.id} className={`px-4 py-3 border-b border-white/[0.04] hover:bg-white/[0.03] cursor-pointer transition-colors ${!n.read ? 'bg-[#5b6ef5]/[0.03]' : ''}`}>
                      <div className="flex items-start gap-3">
                        <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                          n.type === 'success' ? 'bg-[#00e1b7]' : n.type === 'warning' ? 'bg-[#ffc107]' : n.type === 'error' ? 'bg-[#ff4757]' : 'bg-[#5b6ef5]'
                        }`} />
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-white/80 truncate">{n.title}</p>
                          <p className="text-[11px] text-white/45 mt-0.5 line-clamp-2">{n.message}</p>
                          <p className="text-[10px] text-white/30 mt-1">{n.timestamp}</p>
                        </div>
                        {!n.read && <div className="w-1.5 h-1.5 rounded-full bg-[#5b6ef5] flex-shrink-0 mt-1.5" />}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Profile */}
        <div className="relative">
          <button
            onClick={() => setShowProfile(!showProfile)}
            className="flex items-center gap-2.5 pl-2 pr-3 py-1.5 rounded-lg hover:bg-white/[0.04] transition-all"
          >
            <div className="w-7 h-7 rounded-lg gradient-brand flex items-center justify-center">
              <Zap className="w-3.5 h-3.5 text-white" />
            </div>
            <div className="text-left hidden sm:block">
              <p className="text-[11px] font-semibold text-white/80 leading-tight">Admin CAIS</p>
              <p className="text-[10px] text-white/40 leading-tight">Super Admin</p>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-white/30" />
          </button>

          {showProfile && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowProfile(false)} />
              <div className="absolute right-0 top-11 w-[200px] bg-[#12121f] border border-white/[0.08] rounded-xl shadow-2xl z-50 py-1">
                {['Profilo', 'Impostazioni', 'Logout'].map((item, i) => (
                  <button key={i} className="w-full px-4 py-2 text-left text-xs text-white/70 hover:bg-white/[0.05] hover:text-white/90 transition-colors">
                    {item}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
