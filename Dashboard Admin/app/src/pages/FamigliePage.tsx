import { useState } from 'react';
import { Upload, FileText, BookOpen, ChevronRight, ChevronLeft, Check, Plus, CheckCircle } from 'lucide-react';
import { mockClients, mockProductFamilies, mockQuestions } from '@/data/mock';
import type { ProductFamily } from '@/types';

type WizardStep = 'list' | 'upload' | 'questionnaire' | 'review';

export function FamigliePage() {
  const [selectedClient, setSelectedClient] = useState(mockClients[0]);
  const [wizardStep, setWizardStep] = useState<WizardStep>('list');
  const [selectedFamily, setSelectedFamily] = useState<ProductFamily | null>(null);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [uploadedFiles] = useState<{ name: string; progress: number }[]>([
    { name: 'Brochure_X200_2024.pdf', progress: 100 },
    { name: 'Disegni_Tecnici_X200.zip', progress: 100 },
    { name: 'Manuale_Montaggio_X200.pdf', progress: 100 },
  ]);

  const families = mockProductFamilies.filter(f => f.clientId === selectedClient.id);

  const startWizard = (family: ProductFamily) => {
    setSelectedFamily(family);
    setWizardStep('upload');
    setCurrentQuestion(0);
    setAnswers({});
  };

  if (wizardStep === 'upload' && selectedFamily) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white/90">Carica Fonti Tecniche</h2>
            <p className="text-xs text-white/40 mt-1">{selectedFamily.name} — Step 1 di 3</p>
          </div>
          <button onClick={() => setWizardStep('list')} className="text-[11px] text-white/40 hover:text-white/60 transition-colors">Torna alla lista</button>
        </div>

        {/* Upload Zone */}
        <div className="glass-card p-8 text-center border-dashed border-2 border-white/[0.12]">
          <Upload className="w-10 h-10 text-[#5b6ef5] mx-auto mb-3" />
          <h3 className="text-sm font-semibold text-white/80">Drag & Drop</h3>
          <p className="text-xs text-white/40 mt-1">Trascina qui brochure, disegni tecnici e manuali</p>
          <p className="text-[10px] text-white/25 mt-2">PDF, PNG, JPG — massimo 50MB per file</p>
        </div>

        {/* File List */}
        <div className="space-y-2">
          {uploadedFiles.map((file, i) => (
            <div key={i} className="glass-card p-3 flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-[#5b6ef5]/10 flex items-center justify-center">
                <FileText className="w-4 h-4 text-[#5b6ef5]" />
              </div>
              <div className="flex-1">
                <p className="text-xs font-medium text-white/80">{file.name}</p>
                <div className="w-full h-1.5 bg-white/[0.06] rounded-full mt-1.5 overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-[#5b6ef5] to-[#00e1b7] rounded-full transition-all" style={{ width: `${file.progress}%` }} />
                </div>
              </div>
              <span className="text-[11px] text-[#00e1b7] font-medium">{file.progress}%</span>
            </div>
          ))}
        </div>

        <div className="flex justify-end">
          <button onClick={() => setWizardStep('questionnaire')} className="flex items-center gap-2 px-5 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity">
            Continua al Questionario <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    );
  }

  if (wizardStep === 'questionnaire' && selectedFamily) {
    const question = mockQuestions[currentQuestion];
    const progress = ((currentQuestion + 1) / mockQuestions.length) * 100;

    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white/90">Questionario Interattivo</h2>
            <p className="text-xs text-white/40 mt-1">Zeus genera domande dai file caricati — Step 2 di 3</p>
          </div>
          <button onClick={() => setWizardStep('list')} className="text-[11px] text-white/40 hover:text-white/60 transition-colors">Torna alla lista</button>
        </div>

        {/* Progress */}
        <div className="flex items-center gap-3">
          <div className="flex-1 h-2 bg-white/[0.06] rounded-full overflow-hidden">
            <div className="h-full gradient-brand rounded-full transition-all duration-500" style={{ width: `${progress}%` }} />
          </div>
          <span className="text-[11px] text-white/50 font-medium">{currentQuestion + 1}/{mockQuestions.length}</span>
        </div>

        {/* Question Card */}
        <div className="glass-card p-6">
          <div className="flex items-start gap-2 mb-5">
            <span className="px-2 py-0.5 bg-[#5b6ef5]/10 rounded-md text-[10px] text-[#5b6ef5] font-medium">{question.category}</span>
            {question.isFollowUp && <span className="px-2 py-0.5 bg-[#ff4ecd]/10 rounded-md text-[10px] text-[#ff4ecd] font-medium">Follow-up</span>}
          </div>

          <h3 className="text-sm font-semibold text-white/90 leading-relaxed mb-6">{question.text}</h3>

          <div className="space-y-2.5">
            {question.options.map((option, i) => (
              <button
                key={i}
                onClick={() => setAnswers(prev => ({ ...prev, [question.id]: option }))}
                className={`w-full flex items-center gap-3 p-3.5 rounded-xl border transition-all text-left ${
                  answers[question.id] === option
                    ? 'border-[#5b6ef5]/40 bg-[#5b6ef5]/[0.06]'
                    : 'border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]'
                }`}
              >
                <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
                  answers[question.id] === option ? 'border-[#5b6ef5]' : 'border-white/20'
                }`}>
                  {answers[question.id] === option && <div className="w-2 h-2 rounded-full bg-[#5b6ef5]" />}
                </div>
                <span className="text-xs text-white/75">{option}</span>
              </button>
            ))}
          </div>

          <button className="mt-4 text-[11px] text-white/30 hover:text-white/50 transition-colors italic">
            Non so / Non applica
          </button>
        </div>

        {/* Navigation */}
        <div className="flex justify-between">
          <button
            onClick={() => setCurrentQuestion(Math.max(0, currentQuestion - 1))}
            disabled={currentQuestion === 0}
            className="flex items-center gap-2 px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs font-medium text-white/60 hover:bg-white/[0.06] disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            <ChevronLeft className="w-3.5 h-3.5" /> Precedente
          </button>
          {currentQuestion < mockQuestions.length - 1 ? (
            <button
              onClick={() => setCurrentQuestion(currentQuestion + 1)}
              className="flex items-center gap-2 px-5 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity"
            >
              Successivo <ChevronRight className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              onClick={() => setWizardStep('review')}
              className="flex items-center gap-2 px-5 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity"
            >
              Genera DNA <Check className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  if (wizardStep === 'review' && selectedFamily) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white/90">Review DNA Famiglia</h2>
            <p className="text-xs text-white/40 mt-1">{selectedFamily.name} — Step 3 di 3</p>
          </div>
          <button onClick={() => setWizardStep('list')} className="text-[11px] text-white/40 hover:text-white/60 transition-colors">Torna alla lista</button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {selectedFamily.dnaSections.map((section) => (
            <div key={section.id} className="glass-card p-4 border-l-2 border-l-[#5b6ef5]">
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-xs font-semibold text-white/80">{section.title}</h4>
                <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                  section.source === 'sito_web' || section.source === 'file'
                    ? 'bg-[#5b6ef5]/10 text-[#5b6ef5]'
                    : 'bg-[#ff4ecd]/10 text-[#ff4ecd]'
                }`}>
                  {section.source === 'sito_web' || section.source === 'file' ? 'FILE' : 'QST'}
                </span>
              </div>
              <p className="text-[11px] text-white/50 leading-relaxed line-clamp-3">{section.content}</p>
            </div>
          ))}
        </div>

        <div className="flex justify-end gap-3">
          <button onClick={() => setWizardStep('questionnaire')} className="px-5 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-xs font-medium text-white/60 hover:bg-white/[0.06] transition-all">
            Modifica Risposte
          </button>
          <button onClick={() => setWizardStep('list')} className="flex items-center gap-2 px-5 py-2.5 gradient-brand rounded-lg text-xs font-semibold text-white hover:opacity-90 transition-opacity shadow-lg shadow-[#5b6ef5]/20">
            <CheckCircle className="w-3.5 h-3.5" />
            Approva DNA Famiglia
          </button>
        </div>
      </div>
    );
  }

  // List View
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white/90">Famiglie Prodotto</h2>
          <p className="text-xs text-white/40 mt-1">{families.length} famiglie per {selectedClient.name}</p>
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
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {families.map(family => (
          <div key={family.id} className="glass-card p-5 hover:border-[#5b6ef5]/20 transition-all cursor-pointer" onClick={() => startWizard(family)}>
            <div className="flex items-start justify-between mb-4">
              <div className="w-10 h-10 rounded-xl bg-[#5b6ef5]/10 flex items-center justify-center">
                <BookOpen className="w-5 h-5 text-[#5b6ef5]" />
              </div>
              <span className={`status-pill ${
                family.status === 'approved' ? 'status-online' :
                family.status === 'questionnaire' ? 'status-warning' :
                family.status === 'review' ? 'status-info' :
                'status-info'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${
                  family.status === 'approved' ? 'bg-[#00e1b7]' :
                  family.status === 'questionnaire' ? 'bg-[#ffc107]' :
                  'bg-[#5b6ef5]'
                }`} />
                {family.status === 'approved' ? 'Approvato' : family.status === 'questionnaire' ? 'Questionario' : family.status === 'review' ? 'Review' : 'Upload'}
              </span>
            </div>
            <h3 className="text-sm font-semibold text-white/85 mb-2">{family.name}</h3>
            <div className="flex items-center gap-4 text-[11px] text-white/40 mb-3">
              <span>{family.sources.length} file</span>
              <span>{family.dnaSections.length} sezioni DNA</span>
            </div>
            <div className="w-full h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
              <div className="h-full gradient-accent rounded-full" style={{ width: `${family.progress}%` }} />
            </div>
            <p className="text-[10px] text-white/30 mt-1.5">{family.progress}% completato</p>
          </div>
        ))}

        {/* Add Family Card */}
        <button className="glass-card p-5 border-dashed border-2 border-white/[0.1] hover:border-[#5b6ef5]/30 flex flex-col items-center justify-center gap-3 min-h-[200px] transition-all group">
          <div className="w-12 h-12 rounded-2xl bg-white/[0.03] group-hover:bg-[#5b6ef5]/10 flex items-center justify-center transition-colors">
            <Plus className="w-6 h-6 text-white/25 group-hover:text-[#5b6ef5] transition-colors" />
          </div>
          <span className="text-xs font-medium text-white/40 group-hover:text-[#5b6ef5] transition-colors">Aggiungi Famiglia</span>
        </button>
      </div>
    </div>
  );
}
