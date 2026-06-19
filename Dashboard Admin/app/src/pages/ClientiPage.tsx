import { useState } from 'react';
import { Search, Plus, MoreHorizontal, Globe, Mail, Calendar, Package, FileText, X, Upload } from 'lucide-react';
import { mockClients } from '@/data/mock';

type FilterStatus = 'all' | 'active' | 'onboarding' | 'in_review' | 'suspended';

export function ClientiPage() {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<FilterStatus>('all');
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [expandedClient, setExpandedClient] = useState<string | null>(null);

  const filters: { key: FilterStatus; label: string; count: number }[] = [
    { key: 'all', label: 'Tutti', count: mockClients.length },
    { key: 'active', label: 'Attivi', count: mockClients.filter(c => c.status === 'active').length },
    { key: 'onboarding', label: 'Onboarding', count: mockClients.filter(c => c.status === 'onboarding').length },
    { key: 'in_review', label: 'In Review', count: mockClients.filter(c => c.status === 'in_review').length },
    { key: 'suspended', label: 'Sospesi', count: mockClients.filter(c => c.status === 'suspended').length },
  ];

  const filteredClients = mockClients.filter(c => {
    const matchesSearch = c.name.toLowerCase().includes(search.toLowerCase()) || c.sector.toLowerCase().includes(search.toLowerCase());
    const matchesFilter = filter === 'all' || c.status === filter;
    return matchesSearch && matchesFilter;
  });

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white/90">Gestione Clienti</h2>
          <p className="text-xs text-white/40 mt-1">{mockClients.length} clienti nel sistema</p>
        </div>
        <button
          onClick={() => setShowOnboarding(true)}
          className="flex items-center gap-2 px-4 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity shadow-lg shadow-[#5b6ef5]/20"
        >
          <Plus className="w-4 h-4" />
          Nuovo Cliente
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
          <input
            type="text"
            placeholder="Cerca clienti..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full h-9 pl-9 pr-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-[#5b6ef5]/40 transition-all"
          />
        </div>
        <div className="flex items-center gap-1.5">
          {filters.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all ${
                filter === f.key
                  ? 'bg-[#5b6ef5]/15 text-[#5b6ef5] border border-[#5b6ef5]/30'
                  : 'bg-white/[0.03] text-white/50 border border-white/[0.06] hover:bg-white/[0.06]'
              }`}
            >
              {f.label} ({f.count})
            </button>
          ))}
        </div>
      </div>

      {/* Clients Table */}
      <div className="glass-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-5 py-3">Cliente</th>
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-4 py-3">Settore</th>
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-4 py-3">Stato</th>
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-4 py-3">DNA</th>
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-4 py-3">Famiglie</th>
                <th className="text-left text-[10px] font-semibold uppercase tracking-wider text-white/40 px-4 py-3">Creato</th>
                <th className="text-right text-[10px] font-semibold uppercase tracking-wider text-white/40 px-5 py-3">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {filteredClients.map(client => (
                <>
                  <tr
                    key={client.id}
                    className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors cursor-pointer"
                    onClick={() => setExpandedClient(expandedClient === client.id ? null : client.id)}
                  >
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-xl bg-white/[0.06] flex items-center justify-center text-sm font-bold text-white/60">
                          {client.name.charAt(0)}
                        </div>
                        <div>
                          <p className="text-xs font-semibold text-white/85">{client.name}</p>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            <Globe className="w-3 h-3 text-white/25" />
                            <span className="text-[10px] text-white/30">{client.website}</span>
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-xs text-white/60">{client.sector}</td>
                    <td className="px-4 py-3.5">
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
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1.5">
                        <FileText className="w-3.5 h-3.5 text-[#5b6ef5]" />
                        <span className="text-xs text-white/60">v{client.dnaVersion}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1.5">
                        <Package className="w-3.5 h-3.5 text-[#ff4ecd]" />
                        <span className="text-xs text-white/60">{client.familyCount}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-xs text-white/40">{client.createdAt}</td>
                    <td className="px-5 py-3.5 text-right">
                      <button className="w-7 h-7 rounded-lg hover:bg-white/[0.06] flex items-center justify-center ml-auto transition-colors">
                        <MoreHorizontal className="w-4 h-4 text-white/35" />
                      </button>
                    </td>
                  </tr>
                  {expandedClient === client.id && (
                    <tr>
                      <td colSpan={7} className="px-5 py-4 bg-white/[0.02]">
                        <div className="grid grid-cols-3 gap-4">
                          <div className="flex items-center gap-2.5">
                            <Mail className="w-4 h-4 text-white/30" />
                            <span className="text-xs text-white/60">{client.email}</span>
                          </div>
                          <div className="flex items-center gap-2.5">
                            <Calendar className="w-4 h-4 text-white/30" />
                            <span className="text-xs text-white/60">Registrato: {client.createdAt}</span>
                          </div>
                          <div className="flex items-center gap-2.5">
                            <Globe className="w-4 h-4 text-white/30" />
                            <span className="text-xs text-white/60">Lingua: {client.language}</span>
                          </div>
                        </div>
                        <div className="flex gap-2 mt-3">
                          <button className="px-3 py-1.5 bg-[#5b6ef5]/10 border border-[#5b6ef5]/25 rounded-lg text-[11px] text-[#5b6ef5] font-medium hover:bg-[#5b6ef5]/20 transition-colors">
                            Vedi DNA
                          </button>
                          <button className="px-3 py-1.5 bg-[#00e1b7]/10 border border-[#00e1b7]/25 rounded-lg text-[11px] text-[#00e1b7] font-medium hover:bg-[#00e1b7]/20 transition-colors">
                            Gestisci Famiglie
                          </button>
                          <button className="px-3 py-1.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-[11px] text-white/50 font-medium hover:bg-white/[0.06] transition-colors">
                            Deploy
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Onboarding Modal */}
      {showOnboarding && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowOnboarding(false)} />
          <div className="relative bg-[#12121f] border border-white/[0.08] rounded-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
              <div>
                <h3 className="text-sm font-bold text-white/90">Nuovo Cliente</h3>
                <p className="text-[11px] text-white/40 mt-0.5">Onboarding — Step 1 di 2</p>
              </div>
              <button onClick={() => setShowOnboarding(false)} className="w-8 h-8 rounded-lg hover:bg-white/[0.06] flex items-center justify-center transition-colors">
                <X className="w-4 h-4 text-white/40" />
              </button>
            </div>

            {/* Form */}
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-[11px] font-medium text-white/50 mb-1.5">Nome Azienda</label>
                <input type="text" placeholder="Es. Rossi Metalli Srl" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/20 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-white/50 mb-1.5">URL Sito Web</label>
                <input type="text" placeholder="https://..." className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/20 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Settore</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all appearance-none">
                    <option>Lavorazione Metalli</option>
                    <option>Infissi e Serramenti</option>
                    <option>Ceramica</option>
                    <option>Edilizia</option>
                    <option>Altro</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-white/50 mb-1.5">Lingua Primaria</label>
                  <select className="w-full h-10 px-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all appearance-none">
                    <option>Italiano</option>
                    <option>English</option>
                    <option>Deutsch</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-[11px] font-medium text-white/50 mb-1.5">Email Amministratore</label>
                <input type="email" placeholder="admin@azienda.it" className="w-full h-10 px-4 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 placeholder:text-white/20 focus:outline-none focus:border-[#5b6ef5]/40 transition-all" />
              </div>

              {/* Logo Upload */}
              <div>
                <label className="block text-[11px] font-medium text-white/50 mb-1.5">Logo (opzionale)</label>
                <div className="border-2 border-dashed border-white/[0.1] rounded-xl p-6 text-center hover:border-[#5b6ef5]/30 transition-colors cursor-pointer">
                  <Upload className="w-6 h-6 text-white/25 mx-auto mb-2" />
                  <p className="text-[11px] text-white/40">Trascina qui o clicca per caricare</p>
                  <p className="text-[10px] text-white/25 mt-1">PNG, JPG — max 2MB</p>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-white/[0.06]">
              <div className="flex items-center gap-2">
                <div className="w-6 h-1 rounded-full bg-[#5b6ef5]" />
                <div className="w-6 h-1 rounded-full bg-white/[0.1]" />
              </div>
              <div className="flex gap-2">
                <button onClick={() => setShowOnboarding(false)} className="px-4 py-2 text-xs text-white/50 hover:text-white/80 transition-colors">Annulla</button>
                <button className="px-5 py-2 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity">
                  Avvia Scraping →
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
