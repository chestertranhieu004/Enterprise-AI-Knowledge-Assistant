import React, { useCallback, useEffect, useState } from 'react';
import { apiClient, ApiClientError } from '../api/client';
import { useAuth } from '../context/AuthContext';
import {
  DEPARTMENT_CODES,
  DepartmentCode,
  USER_ROLES,
  UserRead,
  UserRole,
} from '../types/api';

interface MatrixScenarioRow {
  documentTitle: string;
  fileName: string;
  department: DepartmentCode;
  accessLevel: 'public' | 'department' | 'restricted';
  allowedRoles: UserRole[];
  accessByPersona: {
    admin: boolean;
    hrManager: boolean;
    hrEmployee: boolean;
    engEmployee: boolean;
    financeManager: boolean;
    guest: boolean;
  };
}

const SCENARIO_3_ACCESS_MATRIX: readonly MatrixScenarioRow[] = [
  {
    documentTitle: 'Quy chế làm việc & Nghỉ phép chung 2026',
    fileName: 'Quy_che_lam_viec_chung.pdf',
    department: 'General',
    accessLevel: 'public',
    allowedRoles: ['admin', 'manager', 'employee', 'guest'],
    accessByPersona: {
      admin: true,
      hrManager: true,
      hrEmployee: true,
      engEmployee: true,
      financeManager: true,
      guest: true,
    },
  },
  {
    documentTitle: 'Hướng dẫn Kỹ thuật & Vận hành Hệ thống Microservices',
    fileName: 'Huong_dan_ky_thuat_he_thong.docx',
    department: 'Engineering',
    accessLevel: 'department',
    allowedRoles: ['admin', 'manager', 'employee'],
    accessByPersona: {
      admin: true,
      hrManager: false,
      hrEmployee: false,
      engEmployee: true,
      financeManager: false,
      guest: false,
    },
  },
  {
    documentTitle: 'Chính sách Lương Thưởng & Đãi ngộ Cấp cao HR 2026',
    fileName: 'Chinh_sach_luong_thuong_cap_cao_HR.xlsx',
    department: 'HR',
    accessLevel: 'restricted',
    allowedRoles: ['admin', 'manager'],
    accessByPersona: {
      admin: true,
      hrManager: true,
      hrEmployee: false,
      engEmployee: false,
      financeManager: false,
      guest: false,
    },
  },
] as const;

export const AdminPermissionsPage: React.FC = () => {
  const { user, isAdmin, canViewUsers, quickSwitchAccount } = useAuth();
  const [users, setUsers] = useState<UserRead[]>([]);
  const [isLoadingUsers, setIsLoadingUsers] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [updatingUserId, setUpdatingUserId] = useState<string | null>(null);

  // Create User Form State
  const [newEmail, setNewEmail] = useState<string>('');
  const [newUsername, setNewUsername] = useState<string>('');
  const [newFullName, setNewFullName] = useState<string>('');
  const [newPassword, setNewPassword] = useState<string>('Password123!');
  const [newRole, setNewRole] = useState<UserRole>('employee');
  const [newDepartment, setNewDepartment] = useState<DepartmentCode>('General');
  const [isCreatingUser, setIsCreatingUser] = useState<boolean>(false);

  const loadUsers = useCallback(async () => {
    if (!canViewUsers) {
      setUsers([]);
      return;
    }
    setIsLoadingUsers(true);
    setErrorMessage(null);
    try {
      const list = await apiClient.listUsers();
      setUsers(list);
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 403) {
        setErrorMessage(
          `Từ chối truy cập (403 Forbidden): Vai trò ${user?.role} không có quyền xem danh sách người dùng.`
        );
      } else {
        setErrorMessage(
          err instanceof Error
            ? err.message
            : 'Không thể tải danh sách người dùng.'
        );
      }
    } finally {
      setIsLoadingUsers(false);
    }
  }, [canViewUsers, user?.role]);

  useEffect(() => {
    void loadUsers();
  }, [user?.id, loadUsers]);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (
      !newEmail.trim() ||
      !newUsername.trim() ||
      !newFullName.trim() ||
      newPassword.length < 8
    ) {
      setErrorMessage(
        'Vui lòng điền đầy đủ Email, Username (>= 3 ký tự), Họ tên và Mật khẩu (>= 8 ký tự).'
      );
      return;
    }

    setIsCreatingUser(true);
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      const created = await apiClient.createUser({
        email: newEmail.trim(),
        username: newUsername.trim(),
        full_name: newFullName.trim(),
        password: newPassword,
        role: newRole,
        department: newDepartment,
        is_active: true,
      });

      setSuccessMessage(
        `Đã tạo người dùng mới "${created.full_name}" (${created.email}) với vai trò ${created.role.toUpperCase()} thuộc phòng ${created.department}.`
      );
      setNewEmail('');
      setNewUsername('');
      setNewFullName('');
      await loadUsers();
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Không thể tạo tài khoản mới.'
      );
    } finally {
      setIsCreatingUser(false);
    }
  };

  const handleUpdatePermission = async (
    targetUser: UserRead,
    patch: {
      role?: UserRole;
      department?: DepartmentCode;
      is_active?: boolean;
    }
  ) => {
    setUpdatingUserId(targetUser.id);
    setErrorMessage(null);
    setSuccessMessage(null);
    try {
      const updated = await apiClient.updateUserPermissions(
        targetUser.id,
        patch
      );
      setUsers((prev) =>
        prev.map((u) => (u.id === updated.id ? updated : u))
      );
      setSuccessMessage(
        `Đã cập nhật quyền cho ${updated.full_name} (${updated.email}): Role=${updated.role.toUpperCase()}, Dept=${updated.department}, Active=${String(updated.is_active)}.`
      );
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Không thể cập nhật phân quyền người dùng.'
      );
    } finally {
      setUpdatingUserId(null);
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">
            🛡️ Quản trị Người dùng &amp; Ma trận Phân quyền RBAC (Scenario 3)
          </h1>
          <p className="page-subtitle">
            Quản lý vai trò (<code>admin</code>, <code>manager</code>,{' '}
            <code>employee</code>, <code>guest</code>), phòng ban (
            <code>HR</code>, <code>Finance</code>, <code>Legal</code>,{' '}
            <code>Engineering</code>, <code>General</code>) và kiểm chứng cơ chế
            lọc SQL Tiền Truy xuất.
          </p>
        </div>
      </div>

      {errorMessage && (
        <div className="alert alert-error" role="alert">
          <span>⚠️ {errorMessage}</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setErrorMessage(null)}
          >
            Đóng
          </button>
        </div>
      )}

      {successMessage && (
        <div className="alert alert-success" role="status">
          <span>✓ {successMessage}</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setSuccessMessage(null)}
          >
            Đóng
          </button>
        </div>
      )}

      {/* Interactive Scenario 3 Pre-Retrieval RBAC & Department Matrix */}
      <section className="card matrix-card">
        <div className="card-header">
          <div>
            <h2>
              🔐 Ma trận Kiểm soát Truy cập Tiền Truy xuất (Scenario 3
              Pre-Retrieval SQL Isolation Matrix)
            </h2>
            <p className="card-subtitle-text">
              Minh họa cách <code>PreRetrievalRBACRetriever</code> lọc quyền
              ngay trong câu lệnh SQL <code>WHERE</code> của PostgreSQL +
              pgvector trước khi tính khoảng cách Cosine.
            </p>
          </div>
        </div>

        <div className="table-responsive">
          <table className="data-table matrix-table" data-testid="rbac-matrix-table">
            <thead>
              <tr>
                <th>Tài liệu Mẫu trong DB</th>
                <th>Phòng ban &amp; Cấp độ ACL</th>
                <th>Admin (General)</th>
                <th>HR Manager (HR)</th>
                <th>HR Employee (HR)</th>
                <th>Eng Employee (Engineering)</th>
                <th>Finance Manager (Finance)</th>
                <th>Guest (General)</th>
              </tr>
            </thead>
            <tbody>
              {SCENARIO_3_ACCESS_MATRIX.map((row) => (
                <tr key={row.fileName}>
                  <td>
                    <div className="doc-cell-title">{row.documentTitle}</div>
                    <div className="doc-cell-filename">
                      <code>{row.fileName}</code>
                    </div>
                  </td>
                  <td>
                    <span className="dept-badge">{row.department}</span>{' '}
                    <span className={`access-badge access-${row.accessLevel}`}>
                      {row.accessLevel.toUpperCase()}
                    </span>
                  </td>
                  <td>
                    <span className="matrix-allow">✓ Cho phép</span>
                  </td>
                  <td>
                    {row.accessByPersona.hrManager ? (
                      <span className="matrix-allow">✓ Cho phép</span>
                    ) : (
                      <span className="matrix-deny">✗ Chặn tại SQL</span>
                    )}
                  </td>
                  <td>
                    {row.accessByPersona.hrEmployee ? (
                      <span className="matrix-allow">✓ Cho phép</span>
                    ) : (
                      <span className="matrix-deny">✗ Chặn tại SQL</span>
                    )}
                  </td>
                  <td>
                    {row.accessByPersona.engEmployee ? (
                      <span className="matrix-allow">✓ Cho phép</span>
                    ) : (
                      <span className="matrix-deny">✗ Chặn tại SQL</span>
                    )}
                  </td>
                  <td>
                    {row.accessByPersona.financeManager ? (
                      <span className="matrix-allow">✓ Cho phép</span>
                    ) : (
                      <span className="matrix-deny">✗ Chặn tại SQL</span>
                    )}
                  </td>
                  <td>
                    {row.accessByPersona.guest ? (
                      <span className="matrix-allow">✓ Cho phép</span>
                    ) : (
                      <span className="matrix-deny">✗ Chặn tại SQL</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Non-admin warning banner with 1-click elevation to Admin for testing */}
      {!canViewUsers ? (
        <section className="card rbac-restricted-card">
          <h3>🔒 Khu vực Quản trị Tài khoản yêu cầu quyền Admin hoặc Manager</h3>
          <p>
            Bạn đang đăng nhập với tài khoản <strong>{user?.full_name}</strong>{' '}
            (Vai trò: <code>{user?.role}</code>, Phòng:{' '}
            <code>{user?.department}</code>). Endpoint{' '}
            <code>GET /api/v1/users</code> và{' '}
            <code>PATCH /api/v1/users/&#123;id&#125;/permissions</code> yêu cầu
            quyền quản trị.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void quickSwitchAccount('admin@company.vn')}
            data-testid="switch-to-admin-btn"
          >
            ⚡ Chuyển nhanh sang tài khoản System Admin (admin@company.vn)
          </button>
        </section>
      ) : (
        <div className="admin-grid">
          {/* Left Column: Create User Form (Admin Only) */}
          <section className="card">
            <div className="card-header">
              <h2>👤 Tạo Tài khoản Người dùng Mới</h2>
              <span className="card-badge">POST /api/v1/users</span>
            </div>

            {!isAdmin && (
              <div className="alert alert-warning">
                Bạn đang có vai trò <strong>Manager</strong> (có quyền xem danh
                sách nhân sự). Chỉ <strong>Admin</strong> mới có thể tạo mới
                hoặc thay đổi phân quyền người dùng.
              </div>
            )}

            <form onSubmit={handleCreateUser} className="upload-form">
              <div className="form-group">
                <label htmlFor="new-user-fullname" className="form-label">
                  Họ và tên *
                </label>
                <input
                  id="new-user-fullname"
                  type="text"
                  className="form-input"
                  value={newFullName}
                  onChange={(e) => setNewFullName(e.target.value)}
                  placeholder="VD: Nguyễn Văn An"
                  disabled={!isAdmin || isCreatingUser}
                  data-testid="create-user-fullname"
                />
              </div>

              <div className="form-row-2col">
                <div className="form-group">
                  <label htmlFor="new-user-email" className="form-label">
                    Email doanh nghiệp *
                  </label>
                  <input
                    id="new-user-email"
                    type="email"
                    className="form-input"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder="an.nguyen@company.vn"
                    disabled={!isAdmin || isCreatingUser}
                    data-testid="create-user-email"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="new-user-username" className="form-label">
                    Username *
                  </label>
                  <input
                    id="new-user-username"
                    type="text"
                    className="form-input"
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    placeholder="an.nguyen"
                    disabled={!isAdmin || isCreatingUser}
                    data-testid="create-user-username"
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="new-user-password" className="form-label">
                  Mật khẩu khởi tạo (&ge; 8 ký tự) *
                </label>
                <input
                  id="new-user-password"
                  type="password"
                  className="form-input"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  disabled={!isAdmin || isCreatingUser}
                  data-testid="create-user-password"
                />
              </div>

              <div className="form-row-2col">
                <div className="form-group">
                  <label htmlFor="new-user-role" className="form-label">
                    Vai trò RBAC (Role)
                  </label>
                  <select
                    id="new-user-role"
                    className="form-select"
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value as UserRole)}
                    disabled={!isAdmin || isCreatingUser}
                    data-testid="create-user-role"
                  >
                    {USER_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="new-user-dept" className="form-label">
                    Phòng ban (Department)
                  </label>
                  <select
                    id="new-user-dept"
                    className="form-select"
                    value={newDepartment}
                    onChange={(e) =>
                      setNewDepartment(e.target.value as DepartmentCode)
                    }
                    disabled={!isAdmin || isCreatingUser}
                    data-testid="create-user-department"
                  >
                    {DEPARTMENT_CODES.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <button
                type="submit"
                className="btn btn-primary btn-block"
                disabled={!isAdmin || isCreatingUser}
                data-testid="create-user-submit-btn"
              >
                {isCreatingUser
                  ? 'Đang khởi tạo tài khoản...'
                  : '+ Tạo Người dùng & Gán quyền RBAC'}
              </button>
            </form>
          </section>

          {/* Right Column: User Directory & Inline RBAC Editor */}
          <section className="card">
            <div className="card-header">
              <div>
                <h2>👥 Danh sách Người dùng &amp; Phân quyền ({users.length})</h2>
                <p className="card-subtitle-text">
                  Thay đổi trực tiếp <code>role</code>, <code>department</code>{' '}
                  hoặc trạng thái <code>is_active</code> qua{' '}
                  <code>PATCH /api/v1/users/&#123;user_id&#125;/permissions</code>.
                </p>
              </div>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => void loadUsers()}
                disabled={isLoadingUsers}
              >
                {isLoadingUsers ? 'Đang tải...' : '🔄 Làm mới'}
              </button>
            </div>

            <div className="table-responsive">
              <table className="data-table" data-testid="users-rbac-table">
                <thead>
                  <tr>
                    <th>Người dùng</th>
                    <th>Vai trò (Role)</th>
                    <th>Phòng ban (Department)</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {isLoadingUsers ? (
                    <tr>
                      <td colSpan={4} className="table-empty-cell">
                        Đang tải danh sách tài khoản...
                      </td>
                    </tr>
                  ) : users.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="table-empty-cell">
                        Chưa có dữ liệu người dùng.
                      </td>
                    </tr>
                  ) : (
                    users.map((u) => {
                      const isRowUpdating = updatingUserId === u.id;
                      return (
                        <tr key={u.id} data-testid={`user-row-${u.username}`}>
                          <td>
                            <div className="doc-cell-title">{u.full_name}</div>
                            <div className="doc-cell-filename">
                              <code>{u.email}</code> (@{u.username})
                            </div>
                          </td>
                          <td>
                            <select
                              aria-label={`Vai trò của ${u.username}`}
                              className="form-select form-select-sm"
                              value={u.role}
                              disabled={!isAdmin || isRowUpdating}
                              onChange={(e) =>
                                void handleUpdatePermission(u, {
                                  role: e.target.value as UserRole,
                                })
                              }
                              data-testid={`user-role-select-${u.username}`}
                            >
                              {USER_ROLES.map((r) => (
                                <option key={r} value={r}>
                                  {r.toUpperCase()}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            <select
                              aria-label={`Phòng ban của ${u.username}`}
                              className="form-select form-select-sm"
                              value={u.department}
                              disabled={!isAdmin || isRowUpdating}
                              onChange={(e) =>
                                void handleUpdatePermission(u, {
                                  department: e.target.value as DepartmentCode,
                                })
                              }
                              data-testid={`user-dept-select-${u.username}`}
                            >
                              {DEPARTMENT_CODES.map((d) => (
                                <option key={d} value={d}>
                                  {d}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            <label className="status-toggle-label">
                              <input
                                type="checkbox"
                                checked={u.is_active}
                                disabled={
                                  !isAdmin || isRowUpdating || u.id === user?.id
                                }
                                onChange={(e) =>
                                  void handleUpdatePermission(u, {
                                    is_active: e.target.checked,
                                  })
                                }
                                data-testid={`user-active-toggle-${u.username}`}
                              />
                              <span
                                className={
                                  u.is_active
                                    ? 'status-active-text'
                                    : 'status-inactive-text'
                                }
                              >
                                {u.is_active ? 'Đang hoạt động' : 'Đã khóa'}
                              </span>
                            </label>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </div>
  );
};
