import type { Client, CompanyDna, ProductFamily, Question, DeployStatus, KpiMetric, SystemHealth, Notification } from '@/types';

export const mockClients: Client[] = [
  { id: '1', name: 'Rossi Metalli Srl', website: 'rossimetalli.it', sector: 'Lavorazione Metalli', language: 'Italiano', email: 'admin@rossimetalli.it', status: 'active', createdAt: '2026-04-15', dnaVersion: 3, familyCount: 2, lastActivity: '2 min fa' },
  { id: '2', name: 'Bianchi Infissi Spa', website: 'bianchiinfissi.com', sector: 'Infissi e Serramenti', language: 'Italiano', email: 'info@bianchiinfissi.com', status: 'active', createdAt: '2026-04-20', dnaVersion: 2, familyCount: 1, lastActivity: '15 min fa' },
  { id: '3', name: 'TechSteel Industries', website: 'techsteel.eu', sector: 'Acciaio Strutturale', language: 'English', email: 'ops@techsteel.eu', status: 'in_review', createdAt: '2026-05-01', dnaVersion: 1, familyCount: 0, lastActivity: '1 ora fa' },
  { id: '4', name: 'Ceramiche Verdi', website: 'ceramicheverdi.it', sector: 'Ceramica Industriale', language: 'Italiano', email: 'tecnico@ceramicheverdi.it', status: 'onboarding', createdAt: '2026-05-28', dnaVersion: 0, familyCount: 0, lastActivity: '3 ore fa' },
  { id: '5', name: 'AluSystem GmbH', website: 'alusystem.de', sector: 'Alluminio e Leghe', language: 'Deutsch', email: 'kontakt@alusystem.de', status: 'active', createdAt: '2026-03-10', dnaVersion: 4, familyCount: 3, lastActivity: '5 min fa' },
  { id: '6', name: 'Ferramenta Nord', website: 'ferramentanord.it', sector: 'Ferramenta Edile', language: 'Italiano', email: 'gestione@ferramentanord.it', status: 'suspended', createdAt: '2026-02-20', dnaVersion: 1, familyCount: 1, lastActivity: '2 giorni fa' },
  { id: '7', name: 'ProLine Serramenti', website: 'prolineserramenti.com', sector: 'Serramenti PVC', language: 'Italiano', email: 'office@prolineserramenti.com', status: 'active', createdAt: '2026-05-15', dnaVersion: 2, familyCount: 2, lastActivity: '30 min fa' },
  { id: '8', name: 'Metallurgica Sud', website: 'metallurgicasud.eu', sector: 'Lavorazione Metalli', language: 'English', email: 'tech@metallurgicasud.eu', status: 'onboarding', createdAt: '2026-05-30', dnaVersion: 0, familyCount: 0, lastActivity: '1 giorno fa' },
];

export const mockCompanyDna: CompanyDna = {
  id: 'dna-1',
  clientId: '1',
  version: 3,
  status: 'approved',
  createdAt: '2026-04-15',
  updatedAt: '2026-05-20',
  sections: [
    { id: 's1', title: 'Chi Siamo / Missione', content: 'Rossi Metalli Srl è un\'azienda leader nella lavorazione di precisione dei metalli. Fondata nel 1987, opera nel settore industriale con un\'attenzione maniacale alla qualità e all\'innovazione tecnologica. La missione è fornire soluzioni metalliche su misura che superino le aspettative del cliente.', source: 'sito_web', order: 1 },
    { id: 's2', title: 'Settore e Mercato', content: 'Settore: Lavorazione metalli per industria meccanica, architettura e design. Mercato primario: Italia settentrionale. Mercati secondari: Europa centrale, Nord Africa. Clientela: industrie meccaniche, studi architettonici, aziende edili.', source: 'sito_web', order: 2 },
    { id: 's3', title: 'Pilastri Funzionali', content: '1. Taglio laser ad alta precisione (fino a 25mm acciaio)\n2. Piegatura CNC multi-asse\n3. Saldatura TIG/MIG certificata\n4. Verniciatura a polvere in campo\n5. Lavorazioni meccaniche di finitura\n6. Montaggio e assemblaggio conto terzi', source: 'sito_web', order: 3 },
    { id: 's4', title: 'Approccio Tecnico', content: 'Filosofia "Zero Difetti": ogni pezzo viene controllato secondo protocolli ISO 9001:2015. Tracciabilità completa del ciclo produttivo. Utilizzo di software CAD/CAM integrati per la programmazione delle macchine. Investimento continuo in automazione e robotica.', source: 'sito_web', order: 4 },
    { id: 's5', title: 'Valori Distintivi', content: 'Personalizzazione totale: nessun limite alle richieste del cliente. Tempi di consegna brevi grazie alla produzione flessibile. Consulenza tecnica pre-vendita gratuita. Campione gratuito per ordini sopra i 500 pezzi. Certificazioni: ISO 9001, ISO 14001, Welding EN 1090.', source: 'questionario', order: 5 },
  ]
};

export const mockProductFamilies: ProductFamily[] = [
  {
    id: 'pf1',
    clientId: '1',
    name: 'Canale di Drenaggio X200',
    sources: [
      { id: 'f1', name: 'Brochure_X200_2024.pdf', type: 'pdf', size: '2.4 MB', uploadProgress: 100 },
      { id: 'f2', name: 'Disegni_Tecnici_X200.zip', type: 'drawing', size: '8.1 MB', uploadProgress: 100 },
      { id: 'f3', name: 'Manuale_Montaggio_X200.pdf', type: 'manual', size: '4.7 MB', uploadProgress: 100 },
    ],
    dnaSections: [
      { id: 'ds1', title: 'Identità Tecnica', content: 'Sistema di canale modulare in acciaio zincato per drenaggio superficiale.', source: 'sito_web', order: 1 },
      { id: 'ds2', title: 'Gerarchia Componenti', content: 'Canale base, griglia, piastrini di fissaggio, kit giunzione, fondo.', source: 'file', order: 2 },
      { id: 'ds3', title: 'Metodi di Costruzione', content: 'Montaggio integrato in calcestruzzo o montaggio sottotop esistente.', source: 'questionario', order: 3 },
      { id: 'ds4', title: 'Logica del Custom', content: 'Lunghezze su misura da 50cm a 3m. Griglie in acciaio, ghisa o acciaio inox.', source: 'questionario', order: 4 },
    ],
    status: 'approved',
    progress: 100,
    createdAt: '2026-04-18',
  },
  {
    id: 'pf2',
    clientId: '1',
    name: 'Piattaforma Pedonabile P500',
    sources: [
      { id: 'f4', name: 'Brochure_P500.pdf', type: 'pdf', size: '3.1 MB', uploadProgress: 100 },
      { id: 'f5', name: 'Disegni_P500.dwg', type: 'drawing', size: '12.4 MB', uploadProgress: 100 },
    ],
    dnaSections: [
      { id: 'ds5', title: 'Identità Tecnica', content: 'Piattaforma in grigliato elettrosaldato per passaggio pedonale.', source: 'sito_web', order: 1 },
      { id: 'ds6', title: 'Gerarchia Componenti', content: 'Telaio portante, grigliato, staffe di ancoraggio.', source: 'file', order: 2 },
    ],
    status: 'questionnaire',
    progress: 60,
    createdAt: '2026-05-10',
  },
  {
    id: 'pf3',
    clientId: '1',
    name: 'Scala di Servizio S100',
    sources: [],
    dnaSections: [],
    status: 'upload',
    progress: 0,
    createdAt: '2026-05-28',
  },
];

export const mockQuestions: Question[] = [
  { id: 'q1', text: 'Qual è la funzione dei fori sui piastrini laterali?', options: ['Fissaggio strutturale', 'Ventilazione', 'Drenaggio aggiuntivo', 'Tutte le precedenti'], category: 'Chiarimento' },
  { id: 'q2', text: 'Cosa intendete per "sistema di canale"? È un canale di drenaggio, di cablaggio o di ventilazione?', options: ['Drenaggio superficiale', 'Cablaggio infrastrutturale', 'Ventilazione tecnica', 'Multifunzione'], category: 'Terminologia' },
  { id: 'q3', text: 'Quando si usa il montaggio "integrato" rispetto al montaggio "sottotop"?', options: ['Nuove costruzioni vs ristrutturazioni', 'Carichi pesanti vs leggeri', 'Preferenza installatore'], category: 'Metodi Alternativi', isFollowUp: true },
  { id: 'q4', text: 'Cosa è fissamente standard e cosa è personalizzabile in questa famiglia prodotto?', options: ['Sezione e materiale standard', 'Lunghezza e finitura custom', 'Tutto personalizzabile'], category: 'Limiti e Vincoli' },
  { id: 'q5', text: 'Esistono varianti materiali (es. acciaio vs alluminio)? Come cambia il comportamento?', options: ['Acciaio zincato (standard)', 'Acciaio inox (corrosione)', 'Alluminio (peso)', 'Tutti disponibili'], category: 'Compatibilità' },
  { id: 'q6', text: 'Quali sono le tolleranze accettabili per le quote indicate nei disegni?', options: ['±0.5mm', '±1mm', '±2mm', 'Dipende dalla quota'], category: 'Limiti e Vincoli' },
  { id: 'q7', text: 'Quali errori commettono più spesso i clienti durante l\'installazione?', options: ['Fissaggio insufficiente', 'Pendenza errata', 'Giunzioni non sigillate'], category: 'Errori Comuni' },
  { id: 'q8', text: 'Esistono vincoli dimensionali o di materiale per il montaggio sottotop?', options: ['Spessore minimo 8cm', 'Materiale: solo calcestruzzo', 'Nessun vincolo'], category: 'Installazione' },
];

export const mockDeployStatus: DeployStatus = {
  clientId: '1',
  environment: 'test',
  status: 'deployed',
  url: 'https://test-rossimetalli.hermes.cais.uno',
  deployedAt: '2026-05-22T14:30:00Z',
  steps: [
    { id: 'd1', label: 'Generazione Profilo HERMES', icon: 'folder', status: 'completed', timestamp: '14:30:01' },
    { id: 'd2', label: 'Popolamento SOUL.md', icon: 'file', status: 'completed', timestamp: '14:30:03' },
    { id: 'd3', label: 'Configurazione rules.md', icon: 'settings', status: 'completed', timestamp: '14:30:05' },
    { id: 'd4', label: 'Avvio Servizio Gateway', icon: 'rocket', status: 'completed', timestamp: '14:30:08' },
    { id: 'd5', label: 'Connessione Canali', icon: 'message', status: 'completed', timestamp: '14:30:12' },
  ]
};

export const mockKpiMetrics: KpiMetric[] = [
  { label: 'Clienti Attivi', value: '24', trend: 12, trendLabel: '+12%', icon: 'users' },
  { label: 'DNA Generati', value: '18', trend: 8, trendLabel: '+8%', icon: 'file-text' },
  { label: 'Agenti Deployati', value: '15', trend: 20, trendLabel: '+20%', icon: 'bot' },
  { label: 'Conversazioni Oggi', value: '1.2K', trend: 35, trendLabel: '+35%', icon: 'message-square' },
];

export const mockSystemHealth: SystemHealth[] = [
  { name: 'PostgreSQL', status: 'healthy', latency: '2ms', uptime: '99.99%' },
  { name: 'HERMES Gateway', status: 'healthy', latency: '45ms', uptime: '99.95%' },
  { name: 'FireCrawl API', status: 'healthy', latency: '120ms', uptime: '99.90%' },
  { name: 'OpenAI API', status: 'warning', latency: '850ms', uptime: '98.50%' },
  { name: 'Storage VPS', status: 'healthy', latency: '5ms', uptime: '100%' },
  { name: 'Slack Webhook', status: 'healthy', latency: '180ms', uptime: '99.80%' },
];

export const mockNotifications: Notification[] = [
  { id: 'n1', title: 'Nuovo cliente', message: 'TechSteel ha completato l\'onboarding', type: 'success', read: false, timestamp: '2 min fa' },
  { id: 'n2', title: 'DNA Approvato', message: 'Rossi Metalli ha approvato DNA Famiglia X200', type: 'info', read: false, timestamp: '15 min fa' },
  { id: 'n3', title: 'Deploy Completato', message: 'Agente Bianchi Infissi online in produzione', type: 'success', read: true, timestamp: '1 ora fa' },
  { id: 'n4', title: 'Warning API', message: 'Latenza OpenAI API sopra 800ms', type: 'warning', read: false, timestamp: '2 ore fa' },
  { id: 'n5', title: 'Scraping Fallito', message: 'FireCrawl timeout su ceramicverdi.it', type: 'error', read: true, timestamp: '3 ore fa' },
];

export const activityChartData = [
  { day: 'Lun', conversations: 420 },
  { day: 'Mar', conversations: 680 },
  { day: 'Mer', conversations: 890 },
  { day: 'Gio', conversations: 1200 },
  { day: 'Ven', conversations: 950 },
  { day: 'Sab', conversations: 340 },
  { day: 'Dom', conversations: 180 },
];

export const revenueData = [
  { month: 'Gen', starter: 1200, professional: 3500, enterprise: 0 },
  { month: 'Feb', starter: 1400, professional: 4000, enterprise: 2000 },
  { month: 'Mar', starter: 1600, professional: 4500, enterprise: 2000 },
  { month: 'Apr', starter: 1800, professional: 5000, enterprise: 4000 },
  { month: 'Mag', starter: 2000, professional: 5500, enterprise: 4000 },
  { month: 'Giu', starter: 2200, professional: 6000, enterprise: 6000 },
];

export const deployLog = [
  { time: '14:30:12', level: 'success', message: 'Connessione Slack stabilita per Rossi Metalli' },
  { time: '14:30:10', level: 'info', message: 'Webhook configurato: #supporto-rossimetalli' },
  { time: '14:30:08', level: 'success', message: 'Servizio hermes-gateway-rossimetalli avviato (PID 28471)' },
  { time: '14:30:07', level: 'info', message: 'Configurazione systemd caricata' },
  { time: '14:30:05', level: 'success', message: 'rules.md generato (2.4KB)' },
  { time: '14:30:04', level: 'success', message: 'AGENTS.md generato con routing famiglie' },
  { time: '14:30:03', level: 'success', message: 'SOUL.md generato (8.2KB)' },
  { time: '14:30:01', level: 'info', message: 'Profilo HERMES inizializzato in /profiles/rossimetalli/' },
  { time: '14:30:00', level: 'info', message: 'Inizio deploy automatico cliente: Rossi Metalli' },
];
