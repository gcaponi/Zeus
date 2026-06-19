import { useState } from 'react';
import { Building2, Key, Brain, Bell as BellIcon, CreditCard, FileText, Shield, Save, Eye, EyeOff } from 'lucide-react';

export function SettingsPage() {
  const [showApiKey, setShowApiKey] = useState(false);
  const [activeTab, setActiveTab] = useState('general');

  const tabs = [
    { id: 'general', label: 'Generale', icon: Building2 },
    { id: 'api', label: 'API Keys', icon: Key },
    { id: 'llm', label: 'LLM Config', icon: Brain },
    { id: 'notifications', label: 'Notifiche', icon: BellIcon },
    { id: 'billing', label: 'Billing', icon: CreditCard },
    { id: 'security', label: 'Sicurezza', icon: Shield },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-bold text-white/90">Impostazioni</h2>
        <p className="text-xs text-white/40 mt-1">Configurazione globale del sistema ZEUS</p>
      </div>

      <div className="flex gap-6">
        {/* Settings Tabs */}
        <div className="w-56 flex-shrink-0 space-y-1">
          {tabs.map(tab => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-xs font-medium transition-all text-left ${
                  activeTab === tab.id
                    ? 'bg-[#5b6ef5]/10 text-[#5b6ef5] border border-[#5b6ef5]/20'
                    : 'text-white/50 hover:text-white/75 hover:bg-white/[0.03]'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 space-y-5">
          {activeTab === 'general' && (
            <div className="glass-card p-6 space-y-5">
              <h3 className="text-sm font-semibold text-white/90">Profilo Organizzazione</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Nome Organizzazione</label>
                  <input type="text" defaultValue="C.A.I.S - Consulting AI Strategic" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Domain</label>
                  <input type="text" defaultValue="zeus.cais.uno" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Email Admin</label>
                  <input type="email" defaultValue="admin@cais.uno" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Timezone</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none appearance-none">
                    <option>Europe/Rome (UTC+1)</option>
                    <option>Europe/Berlin (UTC+1)</option>
                    <option>Europe/London (UTC+0)</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'api' && (
            <div className="space-y-4">
              {[
                { name: 'FireCrawl API Key', value: 'fc_live_abc123xyz789', mask: true },
                { name: 'OpenAI API Key', value: 'sk-proj-xxxxxxxxxxxx', mask: true },
                { name: 'Slack Bot Token', value: 'xoxb-1234567890-abc', mask: true },
              ].map((api, i) => (
                <div key={i} className="glass-card p-5">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2.5">
                      <Key className="w-4 h-4 text-[#5b6ef5]" />
                      <span className="text-xs font-semibold text-white/85">{api.name}</span>
                    </div>
                    <span className="status-pill status-online text-[9px]">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#00e1b7]" /> Attiva
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <div className="flex-1 relative">
                      <input
                        type={showApiKey && api.mask ? 'text' : 'password'}
                        defaultValue={api.value}
                        className="w-full h-9 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/60 font-mono focus:outline-none"
                      />
                    </div>
                    <button
                      onClick={() => setShowApiKey(!showApiKey)}
                      className="w-9 h-9 flex items-center justify-center rounded-lg bg-white/[0.04] border border-white/[0.08] hover:bg-white/[0.08] transition-colors"
                    >
                      {showApiKey ? <EyeOff className="w-4 h-4 text-white/40" /> : <Eye className="w-4 h-4 text-white/40" />}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'llm' && (
            <div className="glass-card p-6 space-y-5">
              <h3 className="text-sm font-semibold text-white/90">Configurazione LLM</h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Provider Primario</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none appearance-none">
                    <option>OpenAI GPT-4o</option>
                    <option>Anthropic Claude 3.5 Sonnet</option>
                    <option>Groq Mixtral</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Modello Scraping / Analisi</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none appearance-none">
                    <option>GPT-4o-mini</option>
                    <option>Claude Haiku</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Max Tokens per Richiesta</label>
                  <input type="number" defaultValue="4096" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none" />
                </div>
                <div className="flex items-center justify-between py-3 border-t border-white/[0.06]">
                  <div>
                    <p className="text-xs font-medium text-white/80">Modalità API Cliente</p>
                    <p className="text-[10px] text-white/40 mt-0.5">Il cliente usa le proprie API key</p>
                  </div>
                  <div className="w-11 h-6 rounded-full bg-[#5b6ef5] relative cursor-pointer">
                    <div className="absolute right-1 top-1 w-4 h-4 rounded-full bg-white shadow" />
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'notifications' && (
            <div className="glass-card p-6 space-y-4">
              <h3 className="text-sm font-semibold text-white/90 mb-2">Preferenze Notifiche</h3>
              {[
                { label: 'Nuovo cliente registrato', desc: 'Ricevi notifica quando un cliente completa l\'onboarding', checked: true },
                { label: 'DNA approvato', desc: 'Notifica quando un cliente approva il DNA', checked: true },
                { label: 'Deploy completato', desc: 'Notifica al termine di un deploy', checked: true },
                { label: 'Errore sistema', desc: 'Alert per errori critici di infrastruttura', checked: true },
                { label: 'Warning API', desc: 'Notifica quando le API LLM hanno latenza alta', checked: false },
                { label: 'Report settimanale', desc: 'Summary via email ogni lunedì', checked: true },
              ].map((notif, i) => (
                <div key={i} className="flex items-center justify-between py-3 border-b border-white/[0.04] last:border-0">
                  <div>
                    <p className="text-xs font-medium text-white/80">{notif.label}</p>
                    <p className="text-[10px] text-white/35 mt-0.5">{notif.desc}</p>
                  </div>
                  <div className={`w-11 h-6 rounded-full relative cursor-pointer transition-colors ${notif.checked ? 'bg-[#5b6ef5]' : 'bg-white/10'}`}>
                    <div className={`absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-all ${notif.checked ? 'right-1' : 'left-1'}`} />
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'billing' && (
            <div className="space-y-4">
              <div className="glass-card p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-white/90">Piano Attuale</h3>
                    <p className="text-[11px] text-white/40 mt-0.5">Fatturazione mensile</p>
                  </div>
                  <span className="px-3 py-1.5 gradient-brand rounded-lg text-xs font-bold text-white">ENTERPRISE</span>
                </div>
                <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-white/[0.06]">
                  <div>
                    <p className="text-2xl font-bold text-white">Illimitate</p>
                    <p className="text-[10px] text-white/40 mt-1">Famiglie prodotto</p>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-white">Illimitate</p>
                    <p className="text-[10px] text-white/40 mt-1">Revisioni DNA</p>
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-[#00e1b7]">SLA 99.9%</p>
                    <p className="text-[10px] text-white/40 mt-1">Uptime garantito</p>
                  </div>
                </div>
              </div>

              <div className="glass-card p-5">
                <h3 className="text-sm font-semibold text-white/90 mb-4">Fatture Recenti</h3>
                <div className="space-y-2">
                  {[
                    { id: 'INV-2026-006', date: '01 Giu 2026', amount: '€1,200.00', status: 'paid' },
                    { id: 'INV-2026-005', date: '01 Mag 2026', amount: '€1,200.00', status: 'paid' },
                    { id: 'INV-2026-004', date: '01 Apr 2026', amount: '€1,200.00', status: 'paid' },
                  ].map((inv, i) => (
                    <div key={i} className="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-white/[0.02] transition-colors">
                      <div className="flex items-center gap-3">
                        <FileText className="w-4 h-4 text-white/25" />
                        <div>
                          <p className="text-xs font-medium text-white/80">{inv.id}</p>
                          <p className="text-[10px] text-white/35">{inv.date}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-xs font-semibold text-white/80">{inv.amount}</span>
                        <span className="status-pill status-online text-[9px]">Pagata</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'security' && (
            <div className="glass-card p-6 space-y-5">
              <h3 className="text-sm font-semibold text-white/90">Sicurezza</h3>
              <div className="space-y-4">
                <div className="flex items-center justify-between py-3 border-b border-white/[0.04]">
                  <div>
                    <p className="text-xs font-medium text-white/80">Autenticazione a Due Fattori</p>
                    <p className="text-[10px] text-white/35 mt-0.5">Richiedi 2FA per tutti gli admin</p>
                  </div>
                  <div className="w-11 h-6 rounded-full bg-[#5b6ef5] relative cursor-pointer">
                    <div className="absolute right-1 top-1 w-4 h-4 rounded-full bg-white shadow" />
                  </div>
                </div>
                <div className="flex items-center justify-between py-3 border-b border-white/[0.04]">
                  <div>
                    <p className="text-xs font-medium text-white/80">Session Timeout</p>
                    <p className="text-[10px] text-white/35 mt-0.5">Scollega dopo 30 minuti di inattività</p>
                  </div>
                  <div className="w-11 h-6 rounded-full bg-[#5b6ef5] relative cursor-pointer">
                    <div className="absolute right-1 top-1 w-4 h-4 rounded-full bg-white shadow" />
                  </div>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">JWT Token Expiry</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none appearance-none">
                    <option>24 ore</option>
                    <option>12 ore</option>
                    <option>1 ora</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">IP Whitelist (opzionale)</label>
                  <textarea
                    placeholder="Inserisci IP separati da virgola..."
                    className="w-full h-20 p-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/20 focus:outline-none resize-none"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Save Button */}
      <div className="flex justify-end pt-4">
        <button className="flex items-center gap-2 px-6 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity shadow-lg shadow-[#5b6ef5]/20">
          <Save className="w-3.5 h-3.5" />
          Salva Modifiche
        </button>
      </div>
    </div>
  );
}
