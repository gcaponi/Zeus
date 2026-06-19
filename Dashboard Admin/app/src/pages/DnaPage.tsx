import { useState } from 'react';
import { ChevronDown, ChevronUp, Edit2, Check, X, RotateCcw, FileText, MessageSquare, CheckCircle, Clock } from 'lucide-react';
import { mockClients, mockCompanyDna } from '@/data/mock';
import type { DnaSection } from '@/types';

export function DnaPage() {
  const [selectedClient, setSelectedClient] = useState(mockClients[0]);
  const [dna, setDna] = useState(mockCompanyDna);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['s1']));
  const [editingSection, setEditingSection] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');

  const toggleExpand = (id: string) => {
    const next = new Set(expandedSections);
    if (next.has(id)) next.delete(id); else next.add(id);
    setExpandedSections(next);
  };

  const startEdit = (section: DnaSection) => {
    setEditingSection(section.id);
    setEditContent(section.content);
  };

  const saveEdit = (sectionId: string) => {
    setDna(prev => ({
      ...prev,
      sections: prev.sections.map(s => s.id === sectionId ? { ...s, content: editContent } : s),
      version: prev.version + 1,
      updatedAt: new Date().toISOString().split('T')[0],
    }));
    setEditingSection(null);
  };

  const cancelEdit = () => {
    setEditingSection(null);
    setEditContent('');
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white/90">DNA Aziendale</h2>
          <p className="text-xs text-white/40 mt-1">Profilo aziendale generato dallo scraping</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Client Selector */}
          <div className="relative">
            <select
              value={selectedClient.id}
              onChange={e => setSelectedClient(mockClients.find(c => c.id === e.target.value) || mockClients[0])}
              className="h-9 pl-3 pr-8 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 focus:outline-none focus:border-[#5b6ef5]/40 transition-all appearance-none cursor-pointer"
            >
              {mockClients.filter(c => c.dnaVersion > 0).map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/30 pointer-events-none" />
          </div>

          {/* Status Badge */}
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${
            dna.status === 'approved'
              ? 'bg-[#00e1b7]/8 border-[#00e1b7]/25 text-[#00e1b7]'
              : dna.status === 'draft'
              ? 'bg-[#ffc107]/8 border-[#ffc107]/25 text-[#ffc107]'
              : 'bg-white/[0.04] border-white/[0.08] text-white/50'
          }`}>
            {dna.status === 'approved' ? <CheckCircle className="w-3.5 h-3.5" /> : <Clock className="w-3.5 h-3.5" />}
            <span className="text-[11px] font-semibold">{dna.status === 'approved' ? 'Approvato' : 'In Revisione'}</span>
          </div>

          {/* Version */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-white/[0.04] border border-white/[0.08] rounded-lg">
            <RotateCcw className="w-3.5 h-3.5 text-white/40" />
            <span className="text-[11px] text-white/60">v{dna.version}</span>
          </div>
        </div>
      </div>

      {/* Client Info Bar */}
      <div className="glass-card p-4 flex items-center gap-6">
        <div className="w-10 h-10 rounded-xl bg-white/[0.06] flex items-center justify-center text-base font-bold text-white/60">
          {selectedClient.name.charAt(0)}
        </div>
        <div>
          <p className="text-sm font-semibold text-white/85">{selectedClient.name}</p>
          <p className="text-[11px] text-white/40">{selectedClient.sector} — {selectedClient.website}</p>
        </div>
        <div className="h-8 w-px bg-white/[0.08]" />
        <div className="flex items-center gap-4 text-[11px] text-white/50">
          <span>Sezioni: <span className="text-white/80 font-semibold">{dna.sections.length}</span></span>
          <span>Ultima modifica: <span className="text-white/80 font-semibold">{dna.updatedAt}</span></span>
          <span>Fonte: <span className="text-white/80 font-semibold">Sito Web + Questionario</span></span>
        </div>
      </div>

      {/* DNA Sections */}
      <div className="space-y-3">
        {dna.sections.map(section => {
          const isExpanded = expandedSections.has(section.id);
          const isEditing = editingSection === section.id;
          return (
            <div key={section.id} className={`glass-card overflow-hidden transition-all ${isExpanded ? 'border-[#5b6ef5]/20' : ''}`}>
              {/* Section Header */}
              <button
                onClick={() => toggleExpand(section.id)}
                className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-white/[0.02] transition-colors"
              >
                <div className="flex items-center gap-4">
                  <span className="text-base">{section.order}</span>
                  <h3 className="text-sm font-semibold text-white/85">{section.title}</h3>
                  <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-medium ${
                    section.source === 'sito_web'
                      ? 'bg-[#5b6ef5]/10 text-[#5b6ef5]'
                      : 'bg-[#ff4ecd]/10 text-[#ff4ecd]'
                  }`}>
                    {section.source === 'sito_web' ? <FileText className="w-3 h-3" /> : <MessageSquare className="w-3 h-3" />}
                    {section.source === 'sito_web' ? 'Sito Web' : 'Questionario'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {!isEditing && (
                    <button
                      onClick={e => { e.stopPropagation(); startEdit(section); }}
                      className="w-7 h-7 rounded-lg hover:bg-white/[0.06] flex items-center justify-center transition-colors"
                    >
                      <Edit2 className="w-3.5 h-3.5 text-white/35" />
                    </button>
                  )}
                  {isExpanded ? <ChevronUp className="w-4 h-4 text-white/30" /> : <ChevronDown className="w-4 h-4 text-white/30" />}
                </div>
              </button>

              {/* Section Content */}
              {isExpanded && (
                <div className="px-5 pb-4 border-t border-white/[0.04]">
                  {isEditing ? (
                    <div className="pt-4 space-y-3">
                      <textarea
                        value={editContent}
                        onChange={e => setEditContent(e.target.value)}
                        className="w-full h-32 p-3 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs text-white/80 leading-relaxed focus:outline-none focus:border-[#5b6ef5]/40 transition-all resize-none"
                      />
                      <div className="flex gap-2">
                        <button onClick={() => saveEdit(section.id)} className="flex items-center gap-1.5 px-3 py-1.5 bg-[#00e1b7]/10 border border-[#00e1b7]/25 rounded-lg text-[11px] text-[#00e1b7] font-medium hover:bg-[#00e1b7]/20 transition-colors">
                          <Check className="w-3 h-3" /> Salva
                        </button>
                        <button onClick={cancelEdit} className="flex items-center gap-1.5 px-3 py-1.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-[11px] text-white/50 font-medium hover:bg-white/[0.06] transition-colors">
                          <X className="w-3 h-3" /> Annulla
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="pt-4 text-xs text-white/60 leading-relaxed whitespace-pre-line">
                      {section.content}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Action Buttons */}
      <div className="flex justify-end gap-3 pt-2">
        <button className="flex items-center gap-2 px-5 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs font-medium text-white/60 hover:bg-white/[0.06] hover:text-white/80 transition-all">
          <RotateCcw className="w-3.5 h-3.5" />
          Richiedi Revisione
        </button>
        <button className="flex items-center gap-2 px-5 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity shadow-lg shadow-[#5b6ef5]/20">
          <CheckCircle className="w-3.5 h-3.5" />
          Approva DNA
        </button>
      </div>
    </div>
  );
}
