import { Users, Euro, TrendingUp, Database, Server, Flame, Brain, HardDrive, MessageSquare } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { revenueData } from '@/data/mock';

export function AdminPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-bold text-white/90">Admin Dashboard</h2>
        <p className="text-xs text-white/40 mt-1">Metriche SaaS e stato infrastruttura</p>
      </div>

      {/* SaaS KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Totale Clienti', value: '24', sub: '+3 questo mese', icon: Users, color: '#5b6ef5' },
          { label: 'Fatturazione Mensile', value: '€8.2K', sub: '+15% vs mese scorso', icon: Euro, color: '#00e1b7' },
          { label: 'Costi LLM', value: '€1.8K', sub: '23% del fatturato', icon: Brain, color: '#ff4ecd' },
          { label: 'Margine Netto', value: '68%', sub: '+5% vs mese scorso', icon: TrendingUp, color: '#00e1b7' },
        ].map((kpi, i) => {
          const Icon = kpi.icon;
          return (
            <div key={i} className="glass-card p-5">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-white/40 mb-2">{kpi.label}</p>
                  <p className="text-2xl font-bold" style={{ color: kpi.color }}>{kpi.value}</p>
                  <p className="text-[10px] text-white/40 mt-1.5">{kpi.sub}</p>
                </div>
                <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: `${kpi.color}15` }}>
                  <Icon className="w-5 h-5" style={{ color: kpi.color }} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Revenue Chart + System Health */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Revenue Chart */}
        <div className="lg:col-span-2 glass-card p-5">
          <div className="flex items-center justify-between mb-5">
            <div>
              <h3 className="text-sm font-semibold text-white/90">Fatturazione per Piano</h3>
              <p className="text-[11px] text-white/40 mt-0.5">Ultimi 6 mesi</p>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={revenueData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="month" tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#1a1a2e', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12 }}
                itemStyle={{ fontSize: 11 }}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: 'rgba(255,255,255,0.5)' }} />
              <Bar dataKey="starter" name="Starter (€200)" fill="#5b6ef5" radius={[4, 4, 0, 0]} />
              <Bar dataKey="professional" name="Professional (€500)" fill="#ff4ecd" radius={[4, 4, 0, 0]} />
              <Bar dataKey="enterprise" name="Enterprise" fill="#00e1b7" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Plan Distribution */}
        <div className="glass-card p-5">
          <h3 className="text-sm font-semibold text-white/90 mb-4">Distribuzione Piani</h3>
          <div className="space-y-4">
            {[
              { plan: 'Starter', count: 12, revenue: '€2.4K', color: '#5b6ef5', percent: 50 },
              { plan: 'Professional', count: 8, revenue: '€4.0K', color: '#ff4ecd', percent: 33 },
              { plan: 'Enterprise', count: 4, revenue: '€1.8K', color: '#00e1b7', percent: 17 },
            ].map((p, i) => (
              <div key={i}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full" style={{ background: p.color }} />
                    <span className="text-xs text-white/70">{p.plan}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] text-white/50">{p.count} clienti</span>
                    <span className="text-[11px] font-semibold" style={{ color: p.color }}>{p.revenue}</span>
                  </div>
                </div>
                <div className="w-full h-2 bg-white/[0.06] rounded-full overflow-hidden">
                  <div className="h-full rounded-full transition-all" style={{ width: `${p.percent}%`, background: p.color }} />
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6 pt-4 border-t border-white/[0.06]">
            <div className="flex items-center justify-between">
              <span className="text-xs text-white/50">Totale MRR</span>
              <span className="text-lg font-bold text-[#00e1b7]">€8.2K</span>
            </div>
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs text-white/50">ARR stimato</span>
              <span className="text-sm font-semibold text-white/80">€98.4K</span>
            </div>
          </div>
        </div>
      </div>

      {/* System Health Grid */}
      <div>
        <h3 className="text-sm font-semibold text-white/90 mb-4">Health Checks</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {[
            { name: 'PostgreSQL', icon: Database, status: 'healthy', latency: '2ms', uptime: '99.99%' },
            { name: 'HERMES GW', icon: Server, status: 'healthy', latency: '45ms', uptime: '99.95%' },
            { name: 'FireCrawl', icon: Flame, status: 'healthy', latency: '120ms', uptime: '99.90%' },
            { name: 'OpenAI API', icon: Brain, status: 'warning', latency: '850ms', uptime: '98.50%' },
            { name: 'Storage', icon: HardDrive, status: 'healthy', latency: '5ms', uptime: '100%' },
            { name: 'Slack', icon: MessageSquare, status: 'healthy', latency: '180ms', uptime: '99.80%' },
          ].map((sys, i) => {
            const Icon = sys.icon;
            return (
              <div key={i} className="glass-card p-4 text-center">
                <div className={`w-10 h-10 rounded-xl mx-auto mb-3 flex items-center justify-center ${
                  sys.status === 'healthy' ? 'bg-[#00e1b7]/10' : sys.status === 'warning' ? 'bg-[#ffc107]/10' : 'bg-[#ff4757]/10'
                }`}>
                  <Icon className={`w-5 h-5 ${
                    sys.status === 'healthy' ? 'text-[#00e1b7]' : sys.status === 'warning' ? 'text-[#ffc107]' : 'text-[#ff4757]'
                  }`} />
                </div>
                <p className="text-xs font-medium text-white/80 mb-1">{sys.name}</p>
                <div className={`flex items-center justify-center gap-1.5 ${
                  sys.status === 'healthy' ? 'text-[#00e1b7]' : sys.status === 'warning' ? 'text-[#ffc107]' : 'text-[#ff4757]'
                }`}>
                  <div className={`w-1.5 h-1.5 rounded-full ${
                    sys.status === 'healthy' ? 'bg-[#00e1b7]' : sys.status === 'warning' ? 'bg-[#ffc107]' : 'bg-[#ff4757]'
                  }`} />
                  <span className="text-[10px] font-medium">{sys.status === 'healthy' ? 'Healthy' : sys.status === 'warning' ? 'Warning' : 'Error'}</span>
                </div>
                <p className="text-[9px] text-white/30 mt-1.5">{sys.latency} — {sys.uptime}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
