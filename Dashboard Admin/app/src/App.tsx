import { useState, useCallback } from 'react';
import { Sidebar } from '@/components/layout/Sidebar';
import { Header } from '@/components/layout/Header';
import { DashboardPage } from '@/pages/DashboardPage';
import { ClientiPage } from '@/pages/ClientiPage';
import { DnaPage } from '@/pages/DnaPage';
import { FamigliePage } from '@/pages/FamigliePage';
import { DeployPage } from '@/pages/DeployPage';
import { AdminPage } from '@/pages/AdminPage';
import { SettingsPage } from '@/pages/SettingsPage';
import type { PageId } from '@/types';

const pageComponents: Record<PageId, React.ComponentType> = {
  dashboard: DashboardPage,
  clienti: ClientiPage,
  dna: DnaPage,
  famiglie: FamigliePage,
  deploy: DeployPage,
  admin: AdminPage,
  settings: SettingsPage,
};

export default function App() {
  const [currentPage, setCurrentPage] = useState<PageId>('dashboard');

  const handleNavigate = useCallback((page: PageId) => {
    setCurrentPage(page);
  }, []);

  const PageComponent = pageComponents[currentPage];

  return (
    <div className="h-screen w-screen bg-[#0a0a0f] text-white overflow-hidden">
      <Sidebar currentPage={currentPage} onNavigate={handleNavigate} />
      <Header currentPage={currentPage} />
      
      <main
        className="h-full overflow-y-auto"
        style={{ marginLeft: 240, paddingTop: 60 }}
      >
        <div className="p-6 max-w-[1400px]">
          <PageComponent />
        </div>
      </main>
    </div>
  );
}
