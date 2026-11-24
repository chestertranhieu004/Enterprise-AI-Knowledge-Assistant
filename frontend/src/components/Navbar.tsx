import React, { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { DEMO_ACCOUNTS, HealthResponse, UserRole } from '../types/api';

export type ActiveTab = 'chat' | 'documents' | 'admin';

interface NavbarProps {
  activeTab: ActiveTab;
  onSelectTab: (tab: ActiveTab) => void;
}

const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Quản trị viên (Admin)',
  manager: 'Quản lý (Manager)',
  employee: 'Nhân viên (Employee)',
  guest: 'Khách (Guest)',
};

export const Navbar: React.FC<NavbarProps> = ({ activeTab, onSelectTab }) => {
  const { user, logout, quickSwitchAccount } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [switchingEmail, setSwitchingEmail] = useState<string>('');
  const [switchError, setSwitchError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchHealth = async () => {
      try {
        const res = await apiClient.getHealth();
        if (mounted) {
          setHealth(res);
        }
      } catch {
        if (mounted) {
          setHealth({
            status: 'degraded',
            database: 'disconnected',
            pgvector: 'unavailable',
            llm_provider: 'unknown',
            embedding_provider: 'unknown',
            timestamp: new Date().toISOString(),
          });
        }
      }
    };
    void fetchHealth();
    const timer = window.setInterval(() => {
      void fetchHealth();
    }, 30000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  const handleQuickSwitch = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const targetEmail = e.target.value;
    if (!targetEmail || targetEmail === user?.email) return;
    setSwitchingEmail(targetEmail);
    setSwitchError(null);
    try {
      await quickSwitchAccount(targetEmail);
    } catch (err) {
      setSwitchError(
        err instanceof Error ? err.message : 'Không thể chuyển tài khoản'
      );
    } finally {
      setSwitchingEmail('');
    }
  };

  const isHealthy = health?.status === 'ok';

  return (
    <header className="navbar">
      <div className="navbar-inner">
        {/* Brand Identity */}
        <div className="navbar-brand" onClick={() => onSelectTab('chat')}>
          <div className="brand-logo" aria-hidden="true">
            EKA
          </div>
          <div>
            <div className="brand-title">Enterprise Knowledge Assistant</div>
            <div className="brand-subtitle">
              Pre-Retrieval RBAC &bull; Citation Verification RAG
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <nav className="navbar-tabs" aria-label="Điều hướng chính">
          <button
            type="button"
            className={`nav-tab ${activeTab === 'chat' ? 'active' : ''}`}
            onClick={() => onSelectTab('chat')}
            data-testid="nav-tab-chat"
          >
            <span className="nav-tab-icon">💬</span>
            <span>AI Chat &amp; Tra cứu</span>
          </button>

          <button
            type="button"
            className={`nav-tab ${activeTab === 'documents' ? 'active' : ''}`}
            onClick={() => onSelectTab('documents')}
            data-testid="nav-tab-documents"
          >
            <span className="nav-tab-icon">📄</span>
            <span>Quản lý Tài liệu</span>
          </button>

          <button
            type="button"
            className={`nav-tab ${activeTab === 'admin' ? 'active' : ''}`}
            onClick={() => onSelectTab('admin')}
            data-testid="nav-tab-admin"
          >
            <span className="nav-tab-icon">🛡️</span>
            <span>Quản trị Phân quyền RBAC</span>
          </button>
        </nav>

        {/* System Health & User Controls */}
        <div className="navbar-actions">
          {/* System Health Badge */}
          <div
            className={`health-badge ${isHealthy ? 'health-ok' : 'health-degraded'}`}
            title={
              health
                ? `DB: ${health.database} | pgvector: ${health.pgvector} | LLM: ${health.llm_provider} | Embedding: ${health.embedding_provider}`
                : 'Đang kiểm tra trạng thái hệ thống...'
            }
          >
            <span className="health-dot" />
            <span className="health-text">
              {health
                ? `API: ${health.status.toUpperCase()} (${health.llm_provider})`
                : 'Checking...'}
            </span>
          </div>

          {/* Current User Role & Department Badge */}
          {user && (
            <div className="user-profile-pill">
              <div className="user-profile-info">
                <span className="user-profile-name">{user.full_name}</span>
                <div className="user-badges-row">
                  <span className={`role-badge role-${user.role}`}>
                    {ROLE_LABELS[user.role] || user.role}
                  </span>
                  <span className="dept-badge">Phòng: {user.department}</span>
                </div>
              </div>
            </div>
          )}

          {/* Quick Account Switcher for Scenario 3 Testing */}
          {user && (
            <div className="account-switcher-wrap">
              <select
                aria-label="Chuyển nhanh tài khoản Demo"
                className="account-switcher-select"
                value={user.email}
                disabled={Boolean(switchingEmail)}
                onChange={handleQuickSwitch}
                data-testid="quick-account-switcher"
              >
                <option value={user.email}>
                  🔄 Đổi vai trò ({user.username})
                </option>
                {DEMO_ACCOUNTS.map((acct) => (
                  <option key={acct.email} value={acct.email}>
                    [{acct.role.toUpperCase()} - {acct.department}]{' '}
                    {acct.username} ({acct.enterpriseAlias})
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Logout Button */}
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={logout}
            title="Đăng xuất khỏi phiên làm việc"
            data-testid="logout-button"
          >
            Đăng xuất
          </button>
        </div>
      </div>

      {switchError && (
        <div className="navbar-alert" role="alert">
          <span>⚠️ {switchError}</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setSwitchError(null)}
          >
            Đóng
          </button>
        </div>
      )}
    </header>
  );
};
