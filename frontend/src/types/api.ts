/**
 * Strict TypeScript interfaces matching `docs/openapi.yaml` and `backend/app/schemas/*.py`
 * for the Enterprise Knowledge Assistant API (v1.0.0).
 */

export type UserRole = 'admin' | 'manager' | 'employee' | 'guest';

export type DepartmentCode = 'HR' | 'Finance' | 'Legal' | 'Engineering' | 'General';

export type AccessLevel = 'public' | 'department' | 'restricted';

export type DocumentStatus = 'processing' | 'ready' | 'failed';

export type PermissionType = 'read' | 'manage';

export type VerificationStatus = 'verified' | 'stripped' | 'unverified';

export type DocumentFileType = 'pdf' | 'docx' | 'xlsx' | 'txt' | 'md';

/**
 * Mandatory Scenario 1 exact Vietnamese abstention message from `docs/openapi.yaml` & `CONTRACTS.md`
 */
export const ABSTENTION_MESSAGE_VI =
  'Không tìm thấy thông tin phù hợp trong các tài liệu bạn có quyền truy cập.';

export const USER_ROLES: readonly UserRole[] = [
  'admin',
  'manager',
  'employee',
  'guest',
] as const;

export const DEPARTMENT_CODES: readonly DepartmentCode[] = [
  'HR',
  'Finance',
  'Legal',
  'Engineering',
  'General',
] as const;

export const ACCESS_LEVELS: readonly AccessLevel[] = [
  'public',
  'department',
  'restricted',
] as const;

export const DOCUMENT_STATUSES: readonly DocumentStatus[] = [
  'processing',
  'ready',
  'failed',
] as const;

export const ALLOWED_FILE_EXTENSIONS: readonly DocumentFileType[] = [
  'pdf',
  'docx',
  'xlsx',
  'txt',
  'md',
] as const;

export const MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB

export interface ErrorResponse {
  detail: string;
  error_code?: string | null;
}

export interface LoginRequest {
  email?: string | null;
  username?: string | null;
  password: string;
}

export interface UserRead {
  id: string;
  email: string;
  username: string;
  full_name: string;
  role: UserRole;
  department: DepartmentCode;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserRead;
}

export interface UserCreateRequest {
  email: string;
  username: string;
  password: string;
  full_name: string;
  role: UserRole;
  department: DepartmentCode;
  is_active?: boolean;
}

export interface UserPermissionUpdateRequest {
  role?: UserRole;
  department?: DepartmentCode;
  is_active?: boolean | null;
}

export interface DocumentPermissionEntry {
  id?: string | null;
  user_id?: string | null;
  department?: DepartmentCode | null;
  role?: UserRole | null;
  permission_type?: PermissionType;
}

export interface DocumentRead {
  id: string;
  title: string;
  file_name: string;
  file_type: DocumentFileType | string;
  file_size_bytes: number;
  department: DepartmentCode;
  access_level: AccessLevel;
  allowed_roles: UserRole[];
  uploaded_by: string;
  status: DocumentStatus;
  error_message?: string | null;
  chunk_count: number;
  permissions?: DocumentPermissionEntry[];
  created_at: string;
  updated_at: string;
}

export interface DocumentACLUpdateRequest {
  department?: DepartmentCode;
  access_level?: AccessLevel;
  allowed_roles?: UserRole[];
  permissions?: DocumentPermissionEntry[];
}

export interface DocumentUploadPayload {
  file: File;
  title?: string;
  department: DepartmentCode;
  access_level: AccessLevel;
  allowed_roles?: UserRole[] | string;
  permissions?: DocumentPermissionEntry[];
}

export interface DocumentDeleteResponse {
  deleted: boolean;
  document_id: string;
}

export interface CitationItem {
  document_id: string;
  file_name: string;
  page_or_sheet: string;
  chunk_id: string;
  quote: string;
  score: number;
  verification_status: VerificationStatus;
}

export interface ChatQueryRequest {
  query: string;
  session_id?: string | null;
  top_k?: number;
}

export interface ChatQueryResponse {
  session_id: string;
  message_id: string;
  query: string;
  answer: string;
  found: boolean;
  citations: CitationItem[];
  suggested_queries: string[];
  latency_ms: number;
}

export interface ChatMessageRead {
  id: string;
  session_id: string;
  role: 'user' | 'assistant';
  content: string;
  found?: boolean | null;
  citations: CitationItem[];
  suggested_queries: string[];
  latency_ms?: number | null;
  created_at: string;
}

export interface ChatSessionSummary {
  id: string;
  user_id: string;
  title: string;
  message_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail {
  id: string;
  user_id: string;
  title: string;
  messages: ChatMessageRead[];
  created_at: string;
  updated_at: string;
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | string;
  database: 'connected' | 'disconnected' | string;
  pgvector: 'enabled' | 'unavailable' | string;
  llm_provider: string;
  embedding_provider: string;
  timestamp: string;
}

export interface DemoAccountPreset {
  email: string;
  enterpriseAlias: string;
  username: string;
  password: string;
  fullName: string;
  role: UserRole;
  department: DepartmentCode;
  description: string;
}

export const DEMO_PASSWORD = 'Password123!';

export const DEMO_ACCOUNTS: readonly DemoAccountPreset[] = [
  {
    email: 'admin@company.vn',
    enterpriseAlias: 'admin@enterprise.vn',
    username: 'admin',
    password: DEMO_PASSWORD,
    fullName: 'Nguyễn Quản Trị (System Admin)',
    role: 'admin',
    department: 'General',
    description: 'Toàn quyền hệ thống, xem tất cả tài liệu & phân quyền RBAC/ACL.',
  },
  {
    email: 'hr.manager@company.vn',
    enterpriseAlias: 'hr.manager@enterprise.vn',
    username: 'hr.manager',
    password: DEMO_PASSWORD,
    fullName: 'Trần Thu Hà (HR Manager)',
    role: 'manager',
    department: 'HR',
    description: 'Truy cập tài liệu Public, Phòng HR & tài liệu Mật (Restricted) của HR.',
  },
  {
    email: 'hr.employee@company.vn',
    enterpriseAlias: 'hr.staff@enterprise.vn',
    username: 'hr.employee',
    password: DEMO_PASSWORD,
    fullName: 'Đỗ Nhân Sự (HR Employee)',
    role: 'employee',
    department: 'HR',
    description: 'Truy cập tài liệu Public & Phòng HR (không xem được bảng lương cấp cao Restricted).',
  },
  {
    email: 'eng.employee@company.vn',
    enterpriseAlias: 'eng.staff@enterprise.vn',
    username: 'eng.employee',
    password: DEMO_PASSWORD,
    fullName: 'Phạm Kỹ Thuật (Engineering Employee)',
    role: 'employee',
    department: 'Engineering',
    description: 'Truy cập tài liệu Public & Khối Kỹ thuật (bị chặn tài liệu HR/Finance ở tầng SQL).',
  },
  {
    email: 'finance.manager@company.vn',
    enterpriseAlias: 'fin.manager@enterprise.vn',
    username: 'finance.manager',
    password: DEMO_PASSWORD,
    fullName: 'Lê Minh Tài (Finance Manager)',
    role: 'manager',
    department: 'Finance',
    description: 'Quản lý tài liệu Phòng Tài chính - Kế toán & tài liệu Public chung.',
  },
  {
    email: 'legal.manager@company.vn',
    enterpriseAlias: 'legal.staff@enterprise.vn',
    username: 'legal.manager',
    password: DEMO_PASSWORD,
    fullName: 'Vũ Pháp Chế (Legal Manager)',
    role: 'manager',
    department: 'Legal',
    description: 'Quản lý hợp đồng, tài liệu Phòng Pháp chế & tài liệu Public chung.',
  },
  {
    email: 'guest@company.vn',
    enterpriseAlias: 'guest@enterprise.vn',
    username: 'guest',
    password: DEMO_PASSWORD,
    fullName: 'Khách Thực Tập (Guest User)',
    role: 'guest',
    department: 'General',
    description: 'Chỉ truy cập tài liệu Public cho phép vai trò Guest (Scenario 3 isolation).',
  },
] as const;
