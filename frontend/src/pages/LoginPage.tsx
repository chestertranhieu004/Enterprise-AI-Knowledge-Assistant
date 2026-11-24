import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  DEMO_ACCOUNTS,
  DEMO_PASSWORD,
  DemoAccountPreset,
} from '../types/api';

export const LoginPage: React.FC = () => {
  const { login } = useAuth();
  const [identifier, setIdentifier] = useState<string>('admin@company.vn');
  const [password, setPassword] = useState<string>(DEMO_PASSWORD);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = identifier.trim();
    if (!cleaned || !password) {
      setErrorMessage('Vui lòng nhập Email / Tên đăng nhập và Mật khẩu.');
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      const isEmail = cleaned.includes('@');
      await login({
        email: isEmail ? cleaned : null,
        username: isEmail ? null : cleaned,
        password,
      });
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Đăng nhập thất bại. Vui lòng kiểm tra thông tin xác thực.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOneClickDemoLogin = async (preset: DemoAccountPreset) => {
    setIdentifier(preset.email);
    setPassword(preset.password);
    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      await login({
        email: preset.email,
        username: preset.username,
        password: preset.password,
      });
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : `Không thể đăng nhập tài khoản demo ${preset.email}`
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        {/* Left column: System introduction & 3 Mandatory Scenarios */}
        <section className="login-hero-panel">
          <div className="hero-badge">
            Enterprise RAG &bull; LlamaIndex + FastAPI + pgvector
          </div>
          <h1 className="hero-title">
            Hệ thống Trợ lý Tri thức Doanh nghiệp
          </h1>
          <p className="hero-description">
            Nền tảng tra cứu tài liệu nội bộ thông minh tích hợp{' '}
            <strong>Kiểm soát truy cập RBAC &amp; Phòng ban Tiền Truy xuất</strong>{' '}
            cùng cơ chế{' '}
            <strong>Hậu kiểm Trích dẫn Nguồn (Citation Verification)</strong>.
          </p>

          <div className="scenario-highlights">
            <div className="scenario-highlight-card">
              <div className="scenario-num">Scenario 1</div>
              <div>
                <h3>Chống Bịa đặt Khi Không Tìm Thấy Thông Tin</h3>
                <p>
                  Khi độ tương đồng ngữ nghĩa thấp hơn ngưỡng (<code>&lt; 0.65</code>),
                  AI trả về <code>found: false</code> cùng thông báo chuẩn và gợi ý
                  2&ndash;3 cách đặt lại câu hỏi.
                </p>
              </div>
            </div>

            <div className="scenario-highlight-card">
              <div className="scenario-num">Scenario 2</div>
              <div>
                <h3>Chống Trả lời Sai Nguồn (Citation Verification)</h3>
                <p>
                  Mọi câu trả lời đều gắn mã <code>[chunk_id]</code>, đối chiếu nguyên
                  văn đoạn trích (<code>quote</code>), tên file (<code>file_name</code>)
                  và số trang/sheet (<code>page_or_sheet</code>).
                </p>
              </div>
            </div>

            <div className="scenario-highlight-card">
              <div className="scenario-num">Scenario 3</div>
              <div>
                <h3>Phân quyền RBAC Tiền Truy xuất (Zero-Leakage SQL Filter)</h3>
                <p>
                  Lọc quyền <code>role</code>, <code>department</code> và{' '}
                  <code>access_level</code> ngay trong câu lệnh SQL{' '}
                  <code>WHERE</code> của PostgreSQL + pgvector trước khi xếp hạng
                  vector.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Right column: Login Form + One-Click Demo Accounts */}
        <section className="login-form-panel">
          <div className="login-card">
            <h2 className="login-card-title">Đăng nhập Hệ thống</h2>
            <p className="login-card-subtitle">
              Sử dụng tài khoản doanh nghiệp hoặc chọn nhanh 1 trong 7 tài khoản
              Demo bên dưới (Mật khẩu mặc định: <code>{DEMO_PASSWORD}</code>)
            </p>

            {errorMessage && (
              <div className="alert alert-error" role="alert">
                <strong>Lỗi xác thực:</strong> {errorMessage}
              </div>
            )}

            <form onSubmit={handleSubmit} className="login-form">
              <div className="form-group">
                <label htmlFor="login-identifier" className="form-label">
                  Email hoặc Tên đăng nhập
                </label>
                <input
                  id="login-identifier"
                  type="text"
                  className="form-input"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  placeholder="admin@company.vn hoặc admin@enterprise.vn"
                  autoComplete="username"
                  disabled={isSubmitting}
                  data-testid="login-identifier-input"
                />
              </div>

              <div className="form-group">
                <label htmlFor="login-password" className="form-label">
                  Mật khẩu
                </label>
                <input
                  id="login-password"
                  type="password"
                  className="form-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Nhập mật khẩu"
                  autoComplete="current-password"
                  disabled={isSubmitting}
                  data-testid="login-password-input"
                />
              </div>

              <button
                type="submit"
                className="btn btn-primary btn-block"
                disabled={isSubmitting}
                data-testid="login-submit-button"
              >
                {isSubmitting ? 'Đang xác thực JWT...' : 'Đăng nhập'}
              </button>
            </form>

            {/* One-Click Demo Account Selector Cards */}
            <div className="demo-accounts-section">
              <div className="demo-accounts-header">
                <span>⚡ Chọn nhanh Tài khoản Demo (1-Click Login)</span>
                <span className="demo-password-tag">
                  Pass: {DEMO_PASSWORD}
                </span>
              </div>

              <div className="demo-accounts-grid">
                {DEMO_ACCOUNTS.map((acct) => (
                  <button
                    key={acct.email}
                    type="button"
                    className="demo-account-card"
                    disabled={isSubmitting}
                    onClick={() => handleOneClickDemoLogin(acct)}
                    data-testid={`demo-account-${acct.username}`}
                    data-email={acct.email}
                    data-enterprise-alias={acct.enterpriseAlias}
                  >
                    <div className="demo-account-top">
                      <span className={`role-badge role-${acct.role}`}>
                        {acct.role.toUpperCase()}
                      </span>
                      <span className="dept-badge">{acct.department}</span>
                    </div>
                    <div className="demo-account-name">{acct.fullName}</div>
                    <div className="demo-account-emails">
                      <code>{acct.email}</code>
                      <span className="demo-alias-sub">
                        ({acct.enterpriseAlias})
                      </span>
                    </div>
                    <div className="demo-account-desc">{acct.description}</div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
