import React, { useCallback, useEffect, useState } from 'react';
import { apiClient, ApiClientError } from '../api/client';
import { useAuth } from '../context/AuthContext';
import {
  ACCESS_LEVELS,
  AccessLevel,
  ALLOWED_FILE_EXTENSIONS,
  DEPARTMENT_CODES,
  DepartmentCode,
  DocumentPermissionEntry,
  DocumentRead,
  MAX_UPLOAD_SIZE_BYTES,
  PermissionType,
  USER_ROLES,
  UserRole,
} from '../types/api';

const ACCESS_LEVEL_LABELS: Record<AccessLevel, string> = {
  public: 'Public (Toàn công ty theo Role)',
  department: 'Department (Nội bộ Phòng ban)',
  restricted: 'Restricted (Mật / Chỉ định ACL)',
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export const DocumentsPage: React.FC = () => {
  const { user, canUploadDocuments, isAdmin, isManagerOrAdmin } = useAuth();

  // Document list & filters
  const [documents, setDocuments] = useState<DocumentRead[]>([]);
  const [filterDepartment, setFilterDepartment] = useState<DepartmentCode | ''>(
    ''
  );
  const [filterAccessLevel, setFilterAccessLevel] = useState<AccessLevel | ''>(
    ''
  );
  const [isLoadingDocs, setIsLoadingDocs] = useState<boolean>(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [pageSuccess, setPageSuccess] = useState<string | null>(null);

  // Upload Form State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState<string>('');
  const [uploadDepartment, setUploadDepartment] = useState<DepartmentCode>(
    user?.department || 'General'
  );
  const [uploadAccessLevel, setUploadAccessLevel] =
    useState<AccessLevel>('department');
  const [uploadAllowedRoles, setUploadAllowedRoles] = useState<UserRole[]>([
    'admin',
    'manager',
    'employee',
  ]);
  const [isUploading, setIsUploading] = useState<boolean>(false);

  // Inline ACL Editor Modal State
  const [editingDoc, setEditingDoc] = useState<DocumentRead | null>(null);
  const [aclDepartment, setAclDepartment] = useState<DepartmentCode>('General');
  const [aclAccessLevel, setAclAccessLevel] =
    useState<AccessLevel>('department');
  const [aclAllowedRoles, setAclAllowedRoles] = useState<UserRole[]>([
    'admin',
    'manager',
    'employee',
  ]);
  const [aclPermissions, setAclPermissions] = useState<
    DocumentPermissionEntry[]
  >([]);
  const [newPermDept, setNewPermDept] = useState<DepartmentCode | ''>('');
  const [newPermRole, setNewPermRole] = useState<UserRole | ''>('');
  const [newPermType, setNewPermType] = useState<PermissionType>('read');
  const [isSavingAcl, setIsSavingAcl] = useState<boolean>(false);

  const loadDocuments = useCallback(async () => {
    setIsLoadingDocs(true);
    setPageError(null);
    try {
      const list = await apiClient.listDocuments({
        department: filterDepartment,
        access_level: filterAccessLevel,
      });
      setDocuments(list);
    } catch (err) {
      setPageError(
        err instanceof Error
          ? err.message
          : 'Không thể tải danh sách tài liệu được phân quyền.'
      );
    } finally {
      setIsLoadingDocs(false);
    }
  }, [filterDepartment, filterAccessLevel]);

  useEffect(() => {
    void loadDocuments();
  }, [user?.id, loadDocuments]);

  useEffect(() => {
    if (user?.department) {
      setUploadDepartment(user.department);
    }
  }, [user?.department]);

  const toggleRoleInList = (
    role: UserRole,
    currentList: UserRole[],
    setter: (next: UserRole[]) => void
  ) => {
    if (currentList.includes(role)) {
      if (currentList.length <= 1) return; // Keep at least 1 role
      setter(currentList.filter((r) => r !== role));
    } else {
      setter([...currentList, role]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    setPageError(null);
    setPageSuccess(null);
    if (!file) {
      setSelectedFile(null);
      return;
    }

    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    if (
      !ALLOWED_FILE_EXTENSIONS.includes(
        ext as (typeof ALLOWED_FILE_EXTENSIONS)[number]
      )
    ) {
      setPageError(
        `Định dạng file '.${ext}' không được hỗ trợ. Chỉ chấp nhận: ${ALLOWED_FILE_EXTENSIONS.map((x) => `.${x}`).join(', ')}`
      );
      e.target.value = '';
      setSelectedFile(null);
      return;
    }

    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      setPageError('Kích thước tệp vượt quá giới hạn tối đa 50MB.');
      e.target.value = '';
      setSelectedFile(null);
      return;
    }

    setSelectedFile(file);
    if (!uploadTitle.trim()) {
      setUploadTitle(file.name.replace(/\.[^/.]+$/, '').replace(/[_-]+/g, ' '));
    }
  };

  const handleUploadSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) {
      setPageError('Vui lòng chọn tệp tài liệu (.pdf, .docx, .xlsx, .txt, .md).');
      return;
    }

    setIsUploading(true);
    setPageError(null);
    setPageSuccess(null);

    try {
      const created = await apiClient.uploadDocument({
        file: selectedFile,
        title: uploadTitle.trim() || selectedFile.name,
        department: uploadDepartment,
        access_level: uploadAccessLevel,
        allowed_roles: uploadAllowedRoles,
      });

      setPageSuccess(
        `Đã tải lên và bóc tách thành công tài liệu "${created.title}" (${created.chunk_count} chunks vector 768 chiều, trạng thái: ${created.status}).`
      );
      setSelectedFile(null);
      setUploadTitle('');
      const fileInput = document.getElementById(
        'doc-file-input'
      ) as HTMLInputElement | null;
      if (fileInput) {
        fileInput.value = '';
      }
      await loadDocuments();
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 403) {
        setPageError(
          `Từ chối truy cập (403 Forbidden): Chỉ Admin hoặc Manager mới có quyền tải lên tài liệu. (${err.detail})`
        );
      } else {
        setPageError(
          err instanceof Error
            ? err.message
            : 'Lỗi trong quá trình tải lên và bóc tách tài liệu.'
        );
      }
    } finally {
      setIsUploading(false);
    }
  };

  const openAclEditor = (doc: DocumentRead) => {
    setEditingDoc(doc);
    setAclDepartment(doc.department);
    setAclAccessLevel(doc.access_level);
    setAclAllowedRoles(
      doc.allowed_roles && doc.allowed_roles.length > 0
        ? [...doc.allowed_roles]
        : ['admin', 'manager', 'employee']
    );
    setAclPermissions(doc.permissions ? [...doc.permissions] : []);
    setNewPermDept('');
    setNewPermRole('');
    setNewPermType('read');
    setPageError(null);
  };

  const handleAddPermissionRule = () => {
    if (!newPermDept && !newPermRole) return;
    const entry: DocumentPermissionEntry = {
      user_id: null,
      department: newPermDept || null,
      role: newPermRole || null,
      permission_type: newPermType,
    };
    setAclPermissions((prev) => [...prev, entry]);
    setNewPermDept('');
    setNewPermRole('');
  };

  const handleRemovePermissionRule = (index: number) => {
    setAclPermissions((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleSaveAcl = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingDoc) return;

    setIsSavingAcl(true);
    setPageError(null);
    setPageSuccess(null);

    try {
      const updated = await apiClient.updateDocumentACL(editingDoc.id, {
        department: aclDepartment,
        access_level: aclAccessLevel,
        allowed_roles: aclAllowedRoles,
        permissions: aclPermissions,
      });

      setPageSuccess(
        `Đã cập nhật phân quyền ACL cho tài liệu "${updated.title}" (Phòng: ${updated.department}, Mức độ: ${updated.access_level}).`
      );
      setEditingDoc(null);
      await loadDocuments();
    } catch (err) {
      if (err instanceof ApiClientError && (err.status === 403 || err.status === 404)) {
        setPageError(
          `Không thể cập nhật ACL (HTTP ${err.status}): ${err.detail}`
        );
      } else {
        setPageError(
          err instanceof Error
            ? err.message
            : 'Lỗi khi lưu cấu hình phân quyền ACL.'
        );
      }
    } finally {
      setIsSavingAcl(false);
    }
  };

  const handleDeleteDocument = async (doc: DocumentRead) => {
    const confirmed = window.confirm(
      `Bạn có chắc chắn muốn xóa tài liệu "${doc.title}" (${doc.file_name}) và toàn bộ ${doc.chunk_count} vector chunks của tài liệu này?`
    );
    if (!confirmed) return;

    setPageError(null);
    setPageSuccess(null);
    try {
      const res = await apiClient.deleteDocument(doc.id);
      if (res.deleted) {
        setPageSuccess(
          `Đã xóa tài liệu "${doc.title}" (ID: ${res.document_id}) và thu hồi chỉ mục pgvector.`
        );
        await loadDocuments();
      }
    } catch (err) {
      setPageError(
        err instanceof Error ? err.message : 'Không thể xóa tài liệu.'
      );
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1 className="page-title">
            📄 Quản lý Tài liệu Đa định dạng &amp; Phân quyền ACL
          </h1>
          <p className="page-subtitle">
            Bóc tách tài liệu (<code>.pdf</code>, <code>.docx</code>,{' '}
            <code>.xlsx</code>, <code>.txt</code>, <code>.md</code> tối đa 50MB)
            qua LlamaIndex Node Parser và kiểm soát truy cập Tiền Truy xuất
            (Scenario 3).
          </p>
        </div>

        {user && (
          <div className="header-context-badge">
            <span>Đang xem dưới quyền:</span>
            <strong className={`role-badge role-${user.role}`}>
              {user.role.toUpperCase()}
            </strong>
            <span className="dept-badge">{user.department}</span>
          </div>
        )}
      </div>

      {pageError && (
        <div className="alert alert-error" role="alert">
          <span>⚠️ {pageError}</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setPageError(null)}
          >
            Đóng
          </button>
        </div>
      )}

      {pageSuccess && (
        <div className="alert alert-success" role="status">
          <span>✓ {pageSuccess}</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => setPageSuccess(null)}
          >
            Đóng
          </button>
        </div>
      )}

      <div className="documents-grid">
        {/* Left Column: Upload & Ingest Document Card */}
        <section className="card upload-card">
          <div className="card-header">
            <h2>📤 Tải lên &amp; Bóc tách Tài liệu Mới</h2>
            <span className="card-badge">LlamaIndex 512-token Chunker</span>
          </div>

          {!canUploadDocuments && (
            <div className="alert alert-warning">
              <strong>Lưu ý phân quyền (RBAC):</strong> Tài khoản hiện tại (
              <code>{user?.role}</code>) chỉ có quyền tra cứu tài liệu. Để tải
              lên tài liệu mới, vui lòng chuyển sang tài khoản{' '}
              <strong>Admin</strong> hoặc <strong>Manager</strong> trên thanh
              điều hướng.
            </div>
          )}

          <form onSubmit={handleUploadSubmit} className="upload-form">
            <div className="form-group">
              <label htmlFor="doc-file-input" className="form-label">
                Chọn tệp tài liệu (.pdf, .docx, .xlsx, .txt, .md &le; 50MB) *
              </label>
              <input
                id="doc-file-input"
                type="file"
                accept=".pdf,.docx,.xlsx,.txt,.md"
                onChange={handleFileChange}
                disabled={isUploading || !canUploadDocuments}
                className="form-file-input"
                data-testid="upload-file-input"
              />
              {selectedFile && (
                <div className="selected-file-pill">
                  <span>📎 {selectedFile.name}</span>
                  <span>({formatFileSize(selectedFile.size)})</span>
                </div>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="doc-title-input" className="form-label">
                Tiêu đề hiển thị (Title)
              </label>
              <input
                id="doc-title-input"
                type="text"
                className="form-input"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
                placeholder="VD: Quy chế Tài chính & Thanh toán 2026"
                disabled={isUploading || !canUploadDocuments}
                data-testid="upload-title-input"
              />
            </div>

            <div className="form-row-2col">
              <div className="form-group">
                <label htmlFor="doc-dept-select" className="form-label">
                  Phòng ban sở hữu (Department)
                </label>
                <select
                  id="doc-dept-select"
                  className="form-select"
                  value={uploadDepartment}
                  onChange={(e) =>
                    setUploadDepartment(e.target.value as DepartmentCode)
                  }
                  disabled={isUploading || !canUploadDocuments}
                  data-testid="upload-department-select"
                >
                  {DEPARTMENT_CODES.map((dept) => (
                    <option key={dept} value={dept}>
                      {dept}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="doc-access-select" className="form-label">
                  Cấp độ truy cập (Access Level)
                </label>
                <select
                  id="doc-access-select"
                  className="form-select"
                  value={uploadAccessLevel}
                  onChange={(e) =>
                    setUploadAccessLevel(e.target.value as AccessLevel)
                  }
                  disabled={isUploading || !canUploadDocuments}
                  data-testid="upload-access-level-select"
                >
                  {ACCESS_LEVELS.map((lvl) => (
                    <option key={lvl} value={lvl}>
                      {ACCESS_LEVEL_LABELS[lvl]}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="form-group">
              <span className="form-label">
                Các vai trò được phép truy cập (Allowed Roles):
              </span>
              <div className="roles-checkbox-group">
                {USER_ROLES.map((role) => {
                  const checked = uploadAllowedRoles.includes(role);
                  return (
                    <label
                      key={role}
                      className={`role-checkbox-pill ${
                        checked ? 'role-checkbox-checked' : ''
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={isUploading || !canUploadDocuments}
                        onChange={() =>
                          toggleRoleInList(
                            role,
                            uploadAllowedRoles,
                            setUploadAllowedRoles
                          )
                        }
                      />
                      <span>{role.toUpperCase()}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <button
              type="submit"
              className="btn btn-primary btn-block"
              disabled={isUploading || !canUploadDocuments || !selectedFile}
              data-testid="upload-submit-button"
            >
              {isUploading
                ? 'Đang bóc tách & tạo Vector Embeddings...'
                : 'Tải lên & Lập chỉ mục Vector'}
            </button>
          </form>
        </section>

        {/* Right Column: Authorized Documents Table */}
        <section className="card documents-table-card">
          <div className="card-header">
            <div>
              <h2>
                📚 Danh sách Tài liệu Được Phép Truy cập ({documents.length})
              </h2>
              <p className="card-subtitle-text">
                Kết quả trả về từ <code>GET /api/v1/documents</code> đã được lọc
                bởi Pre-Retrieval RBAC SQL Filter. Tài liệu không có quyền tuyệt
                đối không hiển thị.
              </p>
            </div>

            <div className="table-filters">
              <select
                aria-label="Lọc theo phòng ban"
                className="form-select form-select-sm"
                value={filterDepartment}
                onChange={(e) =>
                  setFilterDepartment(e.target.value as DepartmentCode | '')
                }
                data-testid="filter-department-select"
              >
                <option value="">Tất cả Phòng ban</option>
                {DEPARTMENT_CODES.map((dept) => (
                  <option key={dept} value={dept}>
                    Phòng: {dept}
                  </option>
                ))}
              </select>

              <select
                aria-label="Lọc theo cấp độ truy cập"
                className="form-select form-select-sm"
                value={filterAccessLevel}
                onChange={(e) =>
                  setFilterAccessLevel(e.target.value as AccessLevel | '')
                }
                data-testid="filter-access-level-select"
              >
                <option value="">Tất cả Cấp độ ACL</option>
                {ACCESS_LEVELS.map((lvl) => (
                  <option key={lvl} value={lvl}>
                    ACL: {lvl.toUpperCase()}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => void loadDocuments()}
                disabled={isLoadingDocs}
              >
                {isLoadingDocs ? 'Đang tải...' : '🔄 Làm mới'}
              </button>
            </div>
          </div>

          <div className="table-responsive">
            <table className="data-table" data-testid="documents-table">
              <thead>
                <tr>
                  <th>Tài liệu &amp; Tệp nguồn</th>
                  <th>Định dạng</th>
                  <th>Phòng ban</th>
                  <th>Cấp độ ACL &amp; Roles</th>
                  <th>Trạng thái</th>
                  <th>Số Chunks</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {isLoadingDocs ? (
                  <tr>
                    <td colSpan={7} className="table-empty-cell">
                      Đang truy vấn danh sách tài liệu theo quyền RBAC...
                    </td>
                  </tr>
                ) : documents.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="table-empty-cell">
                      Không có tài liệu nào khớp với bộ lọc trong phạm vi quyền
                      truy cập của bạn.
                    </td>
                  </tr>
                ) : (
                  documents.map((doc) => {
                    const canManageDoc =
                      isAdmin ||
                      doc.uploaded_by === user?.id ||
                      (isManagerOrAdmin && user?.department === doc.department);

                    return (
                      <tr
                        key={doc.id}
                        data-testid={`document-row-${doc.file_name}`}
                      >
                        <td>
                          <div className="doc-cell-title">{doc.title}</div>
                          <div className="doc-cell-filename">
                            <code>{doc.file_name}</code> &bull;{' '}
                            {formatFileSize(doc.file_size_bytes)}
                          </div>
                        </td>
                        <td>
                          <span
                            className={`filetype-badge filetype-${doc.file_type}`}
                          >
                            {doc.file_type.toUpperCase()}
                          </span>
                        </td>
                        <td>
                          <span className="dept-badge">{doc.department}</span>
                        </td>
                        <td>
                          <div className="acl-cell-stack">
                            <span
                              className={`access-badge access-${doc.access_level}`}
                            >
                              {doc.access_level.toUpperCase()}
                            </span>
                            <div className="allowed-roles-mini">
                              {doc.allowed_roles.map((r) => (
                                <span
                                  key={r}
                                  className={`role-mini-tag role-${r}`}
                                >
                                  {r}
                                </span>
                              ))}
                            </div>
                          </div>
                        </td>
                        <td>
                          <span
                            className={`doc-status-badge doc-status-${doc.status}`}
                            title={doc.error_message || undefined}
                          >
                            {doc.status === 'ready' && '✓ READY'}
                            {doc.status === 'processing' && '⏳ PROCESSING'}
                            {doc.status === 'failed' && '✗ FAILED'}
                          </span>
                          {doc.error_message && (
                            <div className="doc-error-sub">
                              {doc.error_message}
                            </div>
                          )}
                        </td>
                        <td>
                          <strong>{doc.chunk_count}</strong> chunks
                        </td>
                        <td>
                          <div className="table-actions">
                            <button
                              type="button"
                              className="btn btn-outline btn-xs"
                              disabled={!canManageDoc}
                              onClick={() => openAclEditor(doc)}
                              title={
                                canManageDoc
                                  ? 'Chỉnh sửa phân quyền ACL tài liệu'
                                  : 'Chỉ Admin hoặc Manager sở hữu mới được sửa ACL'
                              }
                              data-testid={`edit-acl-btn-${doc.id}`}
                            >
                              ⚙️ Sửa ACL
                            </button>
                            <button
                              type="button"
                              className="btn btn-danger btn-xs"
                              disabled={!canManageDoc}
                              onClick={() => void handleDeleteDocument(doc)}
                              title={
                                canManageDoc
                                  ? 'Xóa tài liệu và vector chunks'
                                  : 'Không đủ quyền xóa tài liệu này'
                              }
                              data-testid={`delete-doc-btn-${doc.id}`}
                            >
                              🗑️ Xóa
                            </button>
                          </div>
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

      {/* Inline ACL Permission Editor Modal */}
      {editingDoc && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="acl-modal-title"
        >
          <div className="modal-card">
            <div className="modal-header">
              <div>
                <h3 id="acl-modal-title">
                  🔐 Cấu hình Phân quyền ACL: {editingDoc.title}
                </h3>
                <div className="modal-subtitle">
                  Tệp: <code>{editingDoc.file_name}</code> (ID:{' '}
                  <code>{editingDoc.id}</code>)
                </div>
              </div>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setEditingDoc(null)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSaveAcl} className="modal-body">
              <div className="form-row-2col">
                <div className="form-group">
                  <label htmlFor="acl-dept" className="form-label">
                    Phòng ban sở hữu (Department)
                  </label>
                  <select
                    id="acl-dept"
                    className="form-select"
                    value={aclDepartment}
                    onChange={(e) =>
                      setAclDepartment(e.target.value as DepartmentCode)
                    }
                  >
                    {DEPARTMENT_CODES.map((dept) => (
                      <option key={dept} value={dept}>
                        {dept}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="acl-level" className="form-label">
                    Cấp độ truy cập (Access Level)
                  </label>
                  <select
                    id="acl-level"
                    className="form-select"
                    value={aclAccessLevel}
                    onChange={(e) =>
                      setAclAccessLevel(e.target.value as AccessLevel)
                    }
                  >
                    {ACCESS_LEVELS.map((lvl) => (
                      <option key={lvl} value={lvl}>
                        {ACCESS_LEVEL_LABELS[lvl]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="form-group">
                <span className="form-label">
                  Vai trò được phép đọc (Allowed Roles):
                </span>
                <div className="roles-checkbox-group">
                  {USER_ROLES.map((role) => {
                    const checked = aclAllowedRoles.includes(role);
                    return (
                      <label
                        key={role}
                        className={`role-checkbox-pill ${
                          checked ? 'role-checkbox-checked' : ''
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            toggleRoleInList(
                              role,
                              aclAllowedRoles,
                              setAclAllowedRoles
                            )
                          }
                        />
                        <span>{role.toUpperCase()}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Explicit DocumentPermissionEntry rules */}
              <div className="form-group">
                <span className="form-label">
                  Quy tắc cấp quyền chi tiết (Explicit ACL Entries cho tài liệu
                  Restricted):
                </span>

                <div className="acl-rules-list">
                  {aclPermissions.length === 0 ? (
                    <div className="acl-rules-empty">
                      Chưa có quy tắc ACL bổ sung (Áp dụng theo Phòng ban &amp;
                      Allowed Roles ở trên).
                    </div>
                  ) : (
                    aclPermissions.map((perm, idx) => (
                      <div key={idx} className="acl-rule-item">
                        <span>
                          Phòng:{' '}
                          <strong>{perm.department || 'Tất cả'}</strong> &bull;
                          Vai trò: <strong>{perm.role || 'Tất cả'}</strong>{' '}
                          &bull; Quyền:{' '}
                          <code>{perm.permission_type || 'read'}</code>
                          {perm.user_id && (
                            <>
                              {' '}
                              &bull; User ID:{' '}
                              <code>{perm.user_id.slice(0, 8)}&hellip;</code>
                            </>
                          )}
                        </span>
                        <button
                          type="button"
                          className="btn-link text-danger"
                          onClick={() => handleRemovePermissionRule(idx)}
                        >
                          Xóa
                        </button>
                      </div>
                    ))
                  )}
                </div>

                <div className="acl-add-rule-row">
                  <select
                    className="form-select form-select-sm"
                    value={newPermDept}
                    onChange={(e) =>
                      setNewPermDept(e.target.value as DepartmentCode | '')
                    }
                  >
                    <option value="">-- Chọn Phòng ban --</option>
                    {DEPARTMENT_CODES.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>

                  <select
                    className="form-select form-select-sm"
                    value={newPermRole}
                    onChange={(e) =>
                      setNewPermRole(e.target.value as UserRole | '')
                    }
                  >
                    <option value="">-- Chọn Vai trò --</option>
                    {USER_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>

                  <select
                    className="form-select form-select-sm"
                    value={newPermType}
                    onChange={(e) =>
                      setNewPermType(e.target.value as PermissionType)
                    }
                  >
                    <option value="read">READ</option>
                    <option value="manage">MANAGE</option>
                  </select>

                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={handleAddPermissionRule}
                  >
                    + Thêm Rule
                  </button>
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setEditingDoc(null)}
                  disabled={isSavingAcl}
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSavingAcl}
                  data-testid="save-acl-submit-btn"
                >
                  {isSavingAcl ? 'Đang lưu ACL...' : 'Lưu Phân quyền ACL'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
