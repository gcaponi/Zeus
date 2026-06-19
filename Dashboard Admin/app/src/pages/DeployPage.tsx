import { useState } from 'react';
import { FolderOpen, FileText, Settings, Rocket, MessageSquare, CheckCircle, Circle, ExternalLink, Terminal, Server, Globe } from 'lucide-react';
import { mockClients, mockDeployStatus, deployLog } from '@/data/mock';

const stepIcons = [FolderOpen, FileText, Settings, Rocket, MessageSquare];
const stepLabels = ['Generazione Profilo HERMES', 'Popolamento File', 'Configurazione Gateway', 'Avvio Servizio', 'Connessione Canali'];

export function DeployPage() {
  const [selectedClient, setSelectedClient] = useState(mockClients[0]);
  const [environment, setEnvironment] = useState<'test' | 'production'>('test');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white/90">Deploy</h2>
          <p className="text-xs text-white/40 mt-1">Pipeline di deploy automatico per HERMES</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <select
              value={selectedClient.id}
              onChange={e => setSelectedClient(mockClients.find(c => c.id === e.target.value) || mockClients[0])}
              className="h-9 pl-3 pr-8 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none appearance-none cursor-pointer"
            >
              {mockClients.filter(c => c.dnaVersion > 0).map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <div className="flex bg-white/[0.04] border border-white/[0.08] rounded-lg overflow-hidden">
            <button
              onClick={() => setEnvironment('test')}
              className={`px-4 py-2 text-[11px] font-medium transition-all ${environment === 'test' ? 'bg-[#5b6ef5]/15 text-[#5b6ef5]' : 'text-white/50 hover:text-white/70'}`}
            >
              Test
            </button>
            <button
              onClick={() => setEnvironment('production')}
              className={`px-4 py-2 text-[11px] font-medium transition-all ${environment === 'production' ? 'bg-[#00e1b7]/15 text-[#00e1b7]' : 'text-white/50 hover:text-white/70'}`}
            >
              Produzione
            </button>
          </div>
        </div>
      </div>

      {/* Deploy Pipeline */}
      <div className="glass-card p-6">
        <h3 className="text-sm font-semibold text-white/90 mb-6">Pipeline Deploy</h3>
        <div className="flex items-center gap-2">
          {stepLabels.map((label, i) => {
            const Icon = stepIcons[i];
            const step = mockDeployStatus.steps[i];
            const status = step?.status || 'pending';

            return (
              <div key={i} className="flex-1 flex flex-col items-center">
                <div className={`w-12 h-12 rounded-2xl flex items-center justify-center mb-3 transition-all ${
                  status === 'completed'
                    ? 'bg-[#00e1b7]/10 border border-[#00e1b7]/30'
                    : status === 'active'
                    ? 'bg-[#5b6ef5]/15 border border-[#5b6ef5]/40 shadow-lg shadow-[#5b6ef5]/10'
                    : 'bg-white/[0.03] border border-white/[0.08]'
                }`}>
                  {status === 'completed' ? (
                    <CheckCircle className="w-5 h-5 text-[#00e1b7]" />
                  ) : status === 'active' ? (
                    <Icon className="w-5 h-5 text-[#5b6ef5]" />
                  ) : (
                    <Circle className="w-5 h-5 text-white/20" />
                  )}
                  {status === 'active' && (
                    <div className="absolute w-12 h-12 rounded-2xl border-2 border-[#5b6ef5]/40 animate-ping" />
                  )}
                </div>
                <p className={`text-[10px] font-medium text-center leading-tight ${
                  status === 'completed' ? 'text-[#00e1b7]' : status === 'active' ? 'text-[#5b6ef5]' : 'text-white/30'
                }`}>
                  {label}
                </p>
                {step?.timestamp && (
                  <p className="text-[9px] text-white/25 mt-1">{step.timestamp}</p>
                )}
                {i < stepLabels.length - 1 && (
                  <div className="absolute right-0 top-6 w-full h-px" />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Environment Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Test Environment */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-[#5b6ef5]/10 flex items-center justify-center">
                <Server className="w-4 h-4 text-[#5b6ef5]" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white/90">Ambiente Test</h3>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00e1b7] animate-pulse" />
                  <span className="text-[10px] text-[#00e1b7] font-medium">Online</span>
                </div>
              </div>
            </div>
            <span className="status-pill status-online">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00e1b7]" /> Attivo
            </span>
          </div>

          <div className="space-y-3 mb-4">
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">URL Gateway</span>
              <span className="text-[11px] text-[#5b6ef5] font-medium">{mockDeployStatus.url}</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">Slack Channel</span>
              <span className="text-[11px] text-white/70">#test-rossimetalli</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">Deployato il</span>
              <span className="text-[11px] text-white/70">22 Mag 2026, 14:30</span>
            </div>
          </div>

          <div className="flex gap-2">
            <button className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs font-medium text-white/70 hover:bg-white/[0.06] transition-all">
              <ExternalLink className="w-3.5 h-3.5" /> Apri Chat
            </button>
            <button className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity shadow-lg shadow-[#5b6ef5]/20">
              <Rocket className="w-3.5 h-3.5" /> Deploy in Produzione
            </button>
          </div>
        </div>

        {/* Production Environment */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-[#00e1b7]/10 flex items-center justify-center">
                <Globe className="w-4 h-4 text-[#00e1b7]" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-white/90">Produzione</h3>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-[#00e1b7]" />
                  <span className="text-[10px] text-[#00e1b7] font-medium">Online</span>
                </div>
              </div>
            </div>
            <span className="status-pill status-online">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00e1b7]" /> Attivo
            </span>
          </div>

          <div className="space-y-3 mb-4">
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">URL Gateway</span>
              <span className="text-[11px] text-[#5b6ef5] font-medium">https://rossimetalli.hermes.cais.uno</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">Slack Channel</span>
              <span className="text-[11px] text-white/70">#supporto-rossimetalli</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-white/[0.04]">
              <span className="text-[11px] text-white/50">Uptime</span>
              <span className="text-[11px] text-[#00e1b7] font-medium">99.97%</span>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="text-center p-2 bg-white/[0.03] rounded-lg">
              <p className="text-lg font-bold text-white/90">1.2K</p>
              <p className="text-[9px] text-white/35">Conv/giorno</p>
            </div>
            <div className="text-center p-2 bg-white/[0.03] rounded-lg">
              <p className="text-lg font-bold text-[#5b6ef5]">2.3s</p>
              <p className="text-[9px] text-white/35">Risposta avg</p>
            </div>
            <div className="text-center p-2 bg-white/[0.03] rounded-lg">
              <p className="text-lg font-bold text-[#00e1b7]">94%</p>
              <p className="text-[9px] text-white/35">Soddisfazione</p>
            </div>
          </div>
        </div>
      </div>

      {/* Deploy Log */}
      <div className="glass-card p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-white/90">Log Deploy</h3>
          <div className="flex items-center gap-1.5 text-[10px] text-white/40">
            <Terminal className="w-3.5 h-3.5" />
            <span>Live</span>
          </div>
        </div>
        <div className="bg-[#0a0a0f] rounded-xl p-4 font-mono text-[11px] space-y-1.5 max-h-[280px] overflow-y-auto">
          {deployLog.map((log, i) => (
            <div key={i} className="flex gap-3 hover:bg-white/[0.02] rounded px-1 -mx-1">
              <span className="text-white/20 flex-shrink-0 w-16">{log.time}</span>
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
  );
}
