import React, { useState } from 'react';
import { ActiveTab, Navbar } from './components/Navbar';
import { AuthProvider, useAuth } from './context/AuthContext';
import { AdminPermissionsPage } from './pages/AdminPermissionsPage';
import { ChatPage } from './pages/ChatPage';
import { DocumentsPage } from './pages/DocumentsPage';
import { LoginPage } from './pages/LoginPage';

const AppWorkspace: React.FC = () => {
  const { isAuthenticated, isLoading } = useAuth();
  const [activeTab, setActiveTab] = useState<ActiveTab>('chat');

  if (isLoading) {
    return (
      <div className="app-splash-screen" role="status">
        <div className="splash-card">
          <div className="brand-logo splash-logo">EKA</div>
          <h2>Enterprise Knowledge Assistant</h2>
          <p>Đang xác thực phiên làm việc JWT và kiểm tra kết nối hệ thống...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div className="app-shell">
      <Navbar activeTab={activeTab} onSelectTab={setActiveTab} />
      <main className="app-main">
        {activeTab === 'chat' && <ChatPage />}
        {activeTab === 'documents' && <DocumentsPage />}
        {activeTab === 'admin' && <AdminPermissionsPage />}
      </main>
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <AppWorkspace />
    </AuthProvider>
  );
};

export default App;
