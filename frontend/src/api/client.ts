import {
  AccessLevel,
  ChatQueryRequest,
  ChatQueryResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  DEMO_ACCOUNTS,
  DepartmentCode,
  DocumentACLUpdateRequest,
  DocumentDeleteResponse,
  DocumentRead,
  DocumentUploadPayload,
  ErrorResponse,
  HealthResponse,
  LoginRequest,
  TokenResponse,
  UserCreateRequest,
  UserPermissionUpdateRequest,
  UserRead,
} from '../types/api';

const RAW_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
export const API_BASE_URL = RAW_BASE_URL.replace(/\/+$/, '');

export const TOKEN_STORAGE_KEY = 'eka_access_token';
export const USER_STORAGE_KEY = 'eka_current_user';

/**
 * Maps enterprise alias emails (e.g., `admin@enterprise.vn`, `hr.staff@enterprise.vn`)
 * to seeded database emails in `backend/app/db/seed.py` (`admin@company.vn`, `hr.employee@company.vn`)
 * so both identifiers authenticate seamlessly.
 */
const ENTERPRISE_EMAIL_ALIAS_MAP: Record<string, { email: string; username: string }> = {};
for (const preset of DEMO_ACCOUNTS) {
  ENTERPRISE_EMAIL_ALIAS_MAP[preset.enterpriseAlias.toLowerCase()] = {
    email: preset.email,
    username: preset.username,
  };
  ENTERPRISE_EMAIL_ALIAS_MAP[preset.email.toLowerCase()] = {
    email: preset.email,
    username: preset.username,
  };
}

interface ValidationErrorItem {
  msg?: string;
  loc?: unknown[];
}

interface RawErrorPayload {
  detail?: string | ValidationErrorItem[] | unknown;
  error_code?: string | null;
}

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly detail: string;
  public readonly error_code: string | null;

  constructor(status: number, detail: string, errorCode: string | null = null) {
    super(detail);
    this.name = 'ApiClientError';
    this.status = status;
    this.detail = detail;
    this.error_code = errorCode;
  }
}

type UnauthorizedHandler = () => void;

class EnterpriseApiClient {
  private unauthorizedHandler: UnauthorizedHandler | null = null;

  public setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
    this.unauthorizedHandler = handler;
  }

  public getStoredToken(): string | null {
    try {
      return localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  public setStoredToken(token: string | null): void {
    try {
      if (token) {
        localStorage.setItem(TOKEN_STORAGE_KEY, token);
      } else {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
      }
    } catch {
      // Ignore storage errors in restricted environments
    }
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    skipAuthRedirect = false
  ): Promise<T> {
    const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
    const headers = new Headers(options.headers || {});

    const token = this.getStoredToken();
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const isFormData =
      typeof FormData !== 'undefined' && options.body instanceof FormData;
    if (!isFormData && options.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      let errorPayload: ErrorResponse = {
        detail: `HTTP ${response.status}: ${response.statusText || 'Yêu cầu thất bại'}`,
        error_code: null,
      };
      try {
        const parsed = (await response.json()) as RawErrorPayload | null;
        if (typeof parsed?.detail === 'string') {
          errorPayload = {
            detail: parsed.detail,
            error_code: parsed.error_code ?? null,
          };
        } else if (Array.isArray(parsed?.detail)) {
          const msg = parsed.detail
            .map((item: unknown) => {
              if (
                typeof item === 'object' &&
                item !== null &&
                'msg' in item &&
                typeof (item as ValidationErrorItem).msg === 'string'
              ) {
                return (item as ValidationErrorItem).msg as string;
              }
              return JSON.stringify(item);
            })
            .join('; ');
          errorPayload = {
            detail: msg || errorPayload.detail,
            error_code: 'VALIDATION_ERROR',
          };
        }
      } catch {
        // Fallback to statusText if body is not valid JSON
      }

      if (response.status === 401 && !skipAuthRedirect) {
        this.setStoredToken(null);
        if (this.unauthorizedHandler) {
          this.unauthorizedHandler();
        }
      }

      throw new ApiClientError(
        response.status,
        errorPayload.detail,
        errorPayload.error_code ?? null
      );
    }

    return (await response.json()) as T;
  }

  // ---------------------------------------------------------------------------
  // 1. Health Endpoint: GET /api/v1/health
  // ---------------------------------------------------------------------------
  public async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health', { method: 'GET' }, true);
  }

  // ---------------------------------------------------------------------------
  // 2. Auth Endpoints: POST /api/v1/auth/login & GET /api/v1/auth/me
  // ---------------------------------------------------------------------------
  public async login(payload: LoginRequest): Promise<TokenResponse> {
    const rawEmail = payload.email?.trim() || null;
    const rawUsername = payload.username?.trim() || null;

    try {
      const tokenRes = await this.request<TokenResponse>(
        '/auth/login',
        {
          method: 'POST',
          body: JSON.stringify({
            email: rawEmail,
            username: rawUsername,
            password: payload.password,
          }),
        },
        true
      );
      this.setStoredToken(tokenRes.access_token);
      return tokenRes;
    } catch (err) {
      // If user entered an @enterprise.vn demo alias and DB is seeded with @company.vn, fall back gracefully
      const candidateKey = (rawEmail || rawUsername || '').toLowerCase();
      const aliasTarget = ENTERPRISE_EMAIL_ALIAS_MAP[candidateKey];
      if (
        err instanceof ApiClientError &&
        err.status === 401 &&
        aliasTarget &&
        aliasTarget.email.toLowerCase() !== candidateKey
      ) {
        const retryRes = await this.request<TokenResponse>(
          '/auth/login',
          {
            method: 'POST',
            body: JSON.stringify({
              email: aliasTarget.email,
              username: aliasTarget.username,
              password: payload.password,
            }),
          },
          true
        );
        this.setStoredToken(retryRes.access_token);
        return retryRes;
      }
      throw err;
    }
  }

  public async getCurrentUser(): Promise<UserRead> {
    return this.request<UserRead>('/auth/me', { method: 'GET' });
  }

  // ---------------------------------------------------------------------------
  // 3. Users & RBAC Endpoints: GET /api/v1/users, POST /api/v1/users, PATCH /api/v1/users/{user_id}/permissions
  // ---------------------------------------------------------------------------
  public async listUsers(): Promise<UserRead[]> {
    return this.request<UserRead[]>('/users', { method: 'GET' });
  }

  public async createUser(payload: UserCreateRequest): Promise<UserRead> {
    return this.request<UserRead>('/users', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async updateUserPermissions(
    userId: string,
    payload: UserPermissionUpdateRequest
  ): Promise<UserRead> {
    return this.request<UserRead>(
      `/users/${encodeURIComponent(userId)}/permissions`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }
    );
  }

  // ---------------------------------------------------------------------------
  // 4. Documents Endpoints:
  //    POST /api/v1/documents/upload
  //    GET /api/v1/documents
  //    GET /api/v1/documents/{doc_id}
  //    PATCH /api/v1/documents/{doc_id}/acl
  //    DELETE /api/v1/documents/{doc_id}
  // ---------------------------------------------------------------------------
  public async uploadDocument(
    payload: DocumentUploadPayload
  ): Promise<DocumentRead> {
    const formData = new FormData();
    formData.append('file', payload.file);
    if (payload.title && payload.title.trim()) {
      formData.append('title', payload.title.trim());
    }
    formData.append('department', payload.department);
    formData.append('access_level', payload.access_level);

    if (payload.allowed_roles) {
      const rolesStr = Array.isArray(payload.allowed_roles)
        ? payload.allowed_roles.join(',')
        : payload.allowed_roles;
      formData.append('allowed_roles', rolesStr);
    } else {
      formData.append('allowed_roles', 'admin,manager,employee');
    }

    if (payload.permissions && payload.permissions.length > 0) {
      formData.append('permissions_json', JSON.stringify(payload.permissions));
    }

    return this.request<DocumentRead>('/documents/upload', {
      method: 'POST',
      body: formData,
    });
  }

  public async listDocuments(filters?: {
    department?: DepartmentCode | '';
    access_level?: AccessLevel | '';
  }): Promise<DocumentRead[]> {
    const params = new URLSearchParams();
    if (filters?.department) {
      params.set('department', filters.department);
    }
    if (filters?.access_level) {
      params.set('access_level', filters.access_level);
    }
    const queryString = params.toString();
    const path = queryString ? `/documents?${queryString}` : '/documents';
    return this.request<DocumentRead[]>(path, { method: 'GET' });
  }

  public async getDocument(docId: string): Promise<DocumentRead> {
    return this.request<DocumentRead>(
      `/documents/${encodeURIComponent(docId)}`,
      { method: 'GET' }
    );
  }

  public async updateDocumentACL(
    docId: string,
    payload: DocumentACLUpdateRequest
  ): Promise<DocumentRead> {
    return this.request<DocumentRead>(
      `/documents/${encodeURIComponent(docId)}/acl`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }
    );
  }

  public async deleteDocument(docId: string): Promise<DocumentDeleteResponse> {
    return this.request<DocumentDeleteResponse>(
      `/documents/${encodeURIComponent(docId)}`,
      {
        method: 'DELETE',
      }
    );
  }

  // ---------------------------------------------------------------------------
  // 5. Chat & RAG Endpoints:
  //    POST /api/v1/chat/query
  //    GET /api/v1/chat/sessions
  //    GET /api/v1/chat/sessions/{session_id}
  // ---------------------------------------------------------------------------
  public async queryKnowledgeBase(
    payload: ChatQueryRequest
  ): Promise<ChatQueryResponse> {
    return this.request<ChatQueryResponse>('/chat/query', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async listChatSessions(): Promise<ChatSessionSummary[]> {
    return this.request<ChatSessionSummary[]>('/chat/sessions', {
      method: 'GET',
    });
  }

  public async getChatSessionDetail(
    sessionId: string
  ): Promise<ChatSessionDetail> {
    return this.request<ChatSessionDetail>(
      `/chat/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: 'GET',
      }
    );
  }
}

export const apiClient = new EnterpriseApiClient();
