import { Users, FileText, Bot, MessageSquare, ArrowUpRight, ArrowDownRight, CheckCircle, Activity } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { mockKpiMetrics, activityChartData, mockClients, mockSystemHealth, deployLog } from '@/data/mock';

const iconMap: Record<string, React.ElementType> = {
  users: Users,
  'file-text': FileText,
  bot: Bot,
  'message-square': MessageSquare,
};

export function DashboardPage() {
  return (
    <div className="space-y-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {mockKpiMetrics.map((kpi, i) => {
          const Icon = iconMap[kpi.icon] || Activity;
          const isPositive = kpi.trend > 0;
          return (
            <div key={i} className="glass-card p-5">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-white/40 mb-2">{kpi.label}</p>
                  <p className="text-2xl font-bold text-white">{kpi.value}</p>
                  <div className={`flex items-center gap-1 mt-2 text-[11px] font-medium ${isPositive ? 'text-[#00e1b7]' : 'text-[#ff4757]'}`}>
                    {isPositive ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                    <span>{kpi.trendLabel}</span>
                  </div>
                </div>
                <div className="w-10 h-10 rounded-xl bg-[#5b6ef5]/10 flex items-center justify-center">
                  <Icon className="w-5 h-5 text-[#5b6ef5]" />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Activity Chart */}
        <div className="lg:col-span-2 glass-card p-5">
          <div className="flex items-center justify-between mb-5">
            <div>
              <h3 className="text-sm font-semibold text-white/90">Conversazioni Giornaliere</h3>
              <p className="text-[11px] text-white/40 mt-0.5">Ultimi 7 giorni — tutti gli agenti</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-[#5b6ef5]" />
              <span className="text-[10px] text-white/40">Conversazioni</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={activityChartData}>
              <defs>
                <linearGradient id="convGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#5b6ef5" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#5b6ef5" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="day" tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#1a1a2e', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12 }}
                itemStyle={{ color: '#5b6ef5' }}
                labelStyle={{ color: 'rgba(255,255,255,0.6)' }}
              />
              <Area type="monotone" dataKey="conversations" stroke="#5b6ef5" strokeWidth={2} fill="url(#convGrad)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* System Health */}
        <div className="glass-card p-5">
          <h3 className="text-sm font-semibold text-white/90 mb-4">Stato Sistema</h3>
          <div className="space-y-3">
            {mockSystemHealth.map((sys, i) => (
              <div key={i} className="flex items-center justify-between py-2 border-b border-white/[0.04] last:border-0">
                <div className="flex items-center gap-2.5">
                  <div className={`w-2 h-2 rounded-full ${
                    sys.status === 'healthy' ? 'bg-[#00e1b7]' : sys.status === 'warning' ? 'bg-[#ffc107]' : 'bg-[#ff4757]'
                  }`} />
                  <span className="text-xs text-white/70">{sys.name}</span>
                </div>
                <div className="text-right">
                  <span className="text-[10px] text-white/40">{sys.latency}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Bottom Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Clients */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white/90">Clienti Recenti</h3>
            <button className="text-[10px] text-[#5b6ef5] font-medium hover:underline">Vedi tutti</button>
          </div>
          <div className="space-y-2">
            {mockClients.slice(0, 5).map(client => (
              <div key={client.id} className="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-white/[0.03] transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-white/[0.06] flex items-center justify-center text-xs font-bold text-white/60">
                    {client.name.charAt(0)}
                  </div>
                  <div>
                    <p className="text-xs font-medium text-white/80">{client.name}</p>
                    <p className="text-[10px] text-white/35">{client.sector}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`status-pill ${
                    client.status === 'active' ? 'status-online' :
                    client.status === 'onboarding' ? 'status-info' :
                    client.status === 'in_review' ? 'status-warning' :
                    'status-error'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      client.status === 'active' ? 'bg-[#00e1b7]' :
                      client.status === 'onboarding' ? 'bg-[#5b6ef5]' :
                      client.status === 'in_review' ? 'bg-[#ffc107]' :
                      'bg-[#ff4757]'
                    }`} />
                    {client.status === 'active' ? 'Attivo' : client.status === 'onboarding' ? 'Onboarding' : client.status === 'in_review' ? 'In Review' : 'Sospeso'}
                  </span>
                  <span className="text-[10px] text-white/30">{client.lastActivity}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Deploy Log */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-white/90">Log Deploy Recente</h3>
            <span className="text-[10px] text-[#00e1b7] font-medium flex items-center gap-1">
              <CheckCircle className="w-3 h-3" /> Completato
            </span>
          </div>
          <div className="bg-[#0a0a0f] rounded-lg p-3 font-mono text-[11px] space-y-1.5 max-h-[240px] overflow-y-auto">
            {deployLog.map((log, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-white/25 flex-shrink-0">{log.time}</span>
                <span className={`${
                  log.level === 'success' ? 'text-[#00e1b7]' :
                  log.level === 'error' ? 'text-[#ff4757]' :
                  log.level === 'warning' ? 'text-[#ffc107]' :
                  'text-white/50'
                }`}>
                  {log.level === 'success' && '✓ '}
                  {log.level === 'error' && '✗ '}
                  {log.level === 'warning' && '▲ '}
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
