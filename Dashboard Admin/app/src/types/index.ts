export interface Client {
  id: string;
  name: string;
  website: string;
  sector: string;
  language: string;
  email: string;
  status: 'onboarding' | 'active' | 'suspended' | 'in_review';
  logo?: string;
  createdAt: string;
  dnaVersion: number;
  familyCount: number;
  lastActivity: string;
}

export interface CompanyDna {
  id: string;
  clientId: string;
  version: number;
  sections: DnaSection[];
  status: 'draft' | 'approved' | 'archived';
  createdAt: string;
  updatedAt: string;
}

export interface DnaSection {
  id: string;
  title: string;
  content: string;
  source: 'sito_web' | 'questionario' | 'manuale' | 'file';
  order: number;
  isEditing?: boolean;
}

export interface ProductFamily {
  id: string;
  clientId: string;
  name: string;
  sources: SourceFile[];
  dnaSections: DnaSection[];
  status: 'upload' | 'questionnaire' | 'review' | 'approved';
  progress: number;
  createdAt: string;
}

export interface SourceFile {
  id: string;
  name: string;
  type: 'pdf' | 'drawing' | 'manual';
  size: string;
  uploadProgress: number;
}

export interface Question {
  id: string;
  text: string;
  options: string[];
  category: string;
  isFollowUp?: boolean;
  parentQuestion?: string;
}

export interface DeployStatus {
  clientId: string;
  environment: 'test' | 'production';
  status: 'pending' | 'in_progress' | 'deployed' | 'failed';
  steps: DeployStep[];
  url?: string;
  deployedAt?: string;
}

export interface DeployStep {
  id: string;
  label: string;
  icon: string;
  status: 'pending' | 'active' | 'completed' | 'error';
  timestamp?: string;
}

export interface KpiMetric {
  label: string;
  value: string;
  trend: number;
  trendLabel: string;
  icon: string;
}

export interface SystemHealth {
  name: string;
  status: 'healthy' | 'warning' | 'error';
  latency?: string;
  uptime?: string;
}

export interface Notification {
  id: string;
  title: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
  read: boolean;
  timestamp: string;
}

export type PageId = 'dashboard' | 'clienti' | 'dna' | 'famiglie' | 'deploy' | 'admin' | 'settings';
