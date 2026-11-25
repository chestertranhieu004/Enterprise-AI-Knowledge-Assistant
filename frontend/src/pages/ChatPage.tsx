import React, { useCallback, useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { CitationCard } from '../components/CitationCard';
import { useAuth } from '../context/AuthContext';
import {
  ABSTENTION_MESSAGE_VI,
  ChatMessageRead,
  ChatSessionSummary,
  CitationItem,
  HealthResponse,
} from '../types/api';

/**
 * Mandatory starter prompts specified in Phase 4 requirements + Scenario 3 RBAC probe prompt
 */
export const QUICK_STARTER_PROMPTS: readonly {
  label: string;
  query: string;
  scenarioHint: string;
}[] = [
  {
    label: 'Chính sách nghỉ phép (Public Doc)',
    query: 'Chính sách nghỉ phép của công ty như thế nào?',
    scenarioHint: 'Scenario 2: Trả lời kèm trích dẫn đã xác thực từ Quy_che_lam_viec_chung.pdf',
  },
  {
    label: 'Quy trình phê duyệt thanh toán',
    query: 'Quy trình xin phê duyệt thanh toán?',
    scenarioHint: 'Scenario 1 / 2: Kiểm tra tài liệu Phòng Tài chính hoặc Abstention nếu chưa có quyền',
  },
  {
    label: 'Hợp đồng năm 2025',
    query: 'Tìm tài liệu liên quan đến hợp đồng năm 2025.',
    scenarioHint: 'Scenario 1: Trả về found=false + gợi ý câu hỏi thay thế khi không có tài liệu khớp',
  },
  {
    label: 'Khung lương Giám đốc Khối (HR Restricted)',
    query: 'Khung lương Giám đốc Khối và quỹ thưởng điều hành cấp cao năm 2026 là bao nhiêu?',
    scenarioHint: 'Scenario 3: Chỉ Admin & HR Manager truy cập được; Employee/Guest nhận found=false (Zero-Leakage)',
  },
  {
    label: 'Quy trình ứng cứu sự cố P1 (Engineering)',
    query: 'Quy trình ứng cứu sự cố mức độ P1 và tiêu chuẩn CI/CD của khối Engineering là gì?',
    scenarioHint: 'Scenario 2 & 3: Nhân sự Engineering/Admin xem được; phòng ban khác bị lọc tại SQL WHERE',
  },
] as const;

export const ChatPage: React.FC = () => {
  const { user } = useAuth();
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageRead[]>([]);
  const [queryInput, setQueryInput] = useState<string>('');
  const [topK, setTopK] = useState<number>(5);
  const [isLoadingSessions, setIsLoadingSessions] = useState<boolean>(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
  const [isSendingQuery, setIsSendingQuery] = useState<boolean>(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [highlightedChunkId, setHighlightedChunkId] = useState<string | null>(
    null
  );
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchHealth = async () => {
      try {
        const res = await apiClient.getHealth();
        if (mounted) {
          setHealth(res);
        }
      } catch {
        // Keep subtle badge hidden/fallback if health endpoint is unreachable
      }
    };
    void fetchHealth();
    return () => {
      mounted = false;
    };
  }, []);

  const loadSessions = useCallback(async () => {
    setIsLoadingSessions(true);
    try {
      const list = await apiClient.listChatSessions();
      setSessions(list);
    } catch (err) {
      // Keep existing list on transient error
      console.error('Failed to load chat sessions:', err);
    } finally {
      setIsLoadingSessions(false);
    }
  }, []);

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    setIsLoadingHistory(true);
    setErrorBanner(null);
    try {
      const detail = await apiClient.getChatSessionDetail(sessionId);
      setActiveSessionId(detail.id);
      setMessages(detail.messages || []);
    } catch (err) {
      setErrorBanner(
        err instanceof Error
          ? err.message
          : 'Không thể tải lịch sử phiên hội thoại.'
      );
    } finally {
      setIsLoadingHistory(false);
    }
  }, []);

  // Reset active session whenever the authenticated user changes (Scenario 3 account switch)
  useEffect(() => {
    setActiveSessionId(null);
    setMessages([]);
    setErrorBanner(null);
    void loadSessions();
  }, [user?.id, loadSessions]);

  const handleStartNewChat = () => {
    setActiveSessionId(null);
    setMessages([]);
    setErrorBanner(null);
    setHighlightedChunkId(null);
  };

  const executeQuery = async (rawQuery: string) => {
    const trimmed = rawQuery.trim();
    if (!trimmed || isSendingQuery) return;

    setIsSendingQuery(true);
    setErrorBanner(null);
    setQueryInput('');

    const optimisticUserMsg: ChatMessageRead = {
      id: `temp-user-${Date.now()}`,
      session_id: activeSessionId || 'pending',
      role: 'user',
      content: trimmed,
      found: null,
      citations: [],
      suggested_queries: [],
      latency_ms: null,
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, optimisticUserMsg]);

    try {
      const response = await apiClient.queryKnowledgeBase({
        query: trimmed,
        session_id: activeSessionId,
        top_k: topK,
      });

      const assistantMsg: ChatMessageRead = {
        id: response.message_id,
        session_id: response.session_id,
        role: 'assistant',
        content: response.answer,
        found: response.found,
        citations: response.citations || [],
        suggested_queries: response.suggested_queries || [],
        latency_ms: response.latency_ms,
        created_at: new Date().toISOString(),
        match_level: response.match_level,
        related_documents: response.related_documents || [],
      };

      setActiveSessionId(response.session_id);
      setMessages((prev) => [
        ...prev.map((m) =>
          m.id === optimisticUserMsg.id
            ? { ...m, session_id: response.session_id }
            : m
        ),
        assistantMsg,
      ]);
      void loadSessions();
    } catch (err) {
      setErrorBanner(
        err instanceof Error
          ? err.message
          : 'Đã xảy ra lỗi khi truy vấn cơ sở tri thức.'
      );
      setMessages((prev) =>
        prev.filter((m) => m.id !== optimisticUserMsg.id)
      );
      setQueryInput(trimmed);
    } finally {
      setIsSendingQuery(false);
    }
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void executeQuery(queryInput);
  };

  /**
   * Renders answer text and turns inline `[chunk_id]` tags into interactive badges
   * that highlight the corresponding CitationCard below.
   */
  const renderGroundedAnswerWithChunkLinks = (
    answerText: string,
    citations: CitationItem[]
  ) => {
    const citationMap = new Map<string, number>();
    citations.forEach((c, idx) => {
      citationMap.set(c.chunk_id.toLowerCase(), idx + 1);
    });

    const parts = answerText.split(/(\[[0-9a-fA-F-]{8,36}\])/g);
    return parts.map((part, idx) => {
      const match = /^\[([0-9a-fA-F-]{8,36})\]$/.exec(part);
      if (match) {
        const chunkId = match[1];
        const citationNumber = citationMap.get(chunkId.toLowerCase());
        return (
          <button
            key={`${chunkId}-${idx}`}
            type="button"
            className="inline-chunk-ref"
            onClick={() => setHighlightedChunkId(chunkId)}
            title={`Nhấn để xem Nguồn trích dẫn Chunk ID: ${chunkId}`}
          >
            [Nguồn #{citationNumber ?? '✓'}: {chunkId.slice(0, 8)}&hellip;]
          </button>
        );
      }
      return <React.Fragment key={idx}>{part}</React.Fragment>;
    });
  };

  return (
    <div className="chat-layout">
      {/* Left Sidebar: Session History & Active RBAC Context */}
      <aside className="chat-sidebar">
        <div className="sidebar-header">
          <button
            type="button"
            className="btn btn-primary btn-block"
            onClick={handleStartNewChat}
            data-testid="new-chat-button"
          >
            + Cuộc hội thoại mới
          </button>
        </div>

        {/* Active RBAC Pre-Retrieval Context Card */}
        {user && (
          <div className="rbac-context-box">
            <div className="rbac-context-title">
              🔒 Ngữ cảnh Lọc RBAC Tiền Truy xuất
            </div>
            <div className="rbac-context-detail">
              <span>Vai trò:</span> <strong>{user.role.toUpperCase()}</strong>
            </div>
            <div className="rbac-context-detail">
              <span>Phòng ban:</span> <strong>{user.department}</strong>
            </div>
            <div className="rbac-context-note">
              Chỉ các tài liệu thỏa mãn điều kiện SQL <code>WHERE</code> cho{' '}
              <code>({user.role}, {user.department})</code> mới được phép tính
              vector similarity.
            </div>
          </div>
        )}

        {/* Top-K Retriever Control */}
        <div className="sidebar-control-box">
          <label htmlFor="top-k-select" className="sidebar-control-label">
            Số đoạn trích tối đa (<code>top_k</code>): <strong>{topK}</strong>
          </label>
          <input
            id="top-k-select"
            type="range"
            min={1}
            max={10}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="sidebar-range-input"
          />
        </div>

        {/* Session List */}
        <div className="session-list-header">
          <span>Lịch sử Hội thoại ({sessions.length})</span>
          <button
            type="button"
            className="btn-link"
            onClick={() => void loadSessions()}
            disabled={isLoadingSessions}
          >
            {isLoadingSessions ? '...' : 'Làm mới'}
          </button>
        </div>

        <div className="session-list" data-testid="chat-session-list">
          {sessions.length === 0 ? (
            <div className="session-empty">
              Chưa có phiên hội thoại nào cho tài khoản này.
            </div>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`session-item ${
                  activeSessionId === s.id ? 'session-item-active' : ''
                }`}
                onClick={() => void loadSessionDetail(s.id)}
                data-testid={`session-item-${s.id}`}
              >
                <div className="session-item-title">{s.title}</div>
                <div className="session-item-meta">
                  <span>{s.message_count ?? 0} tin nhắn</span>
                  <span>
                    {new Date(s.updated_at).toLocaleTimeString('vi-VN', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </div>
              </button>
            ))
          )}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="chat-main">
        {/* Starter Prompt Bar + Active AI Engine Indicator */}
        <div className="starter-prompts-bar">
          <div className="starter-prompts-top-row">
            <div className="starter-prompts-label">
              💡 Câu hỏi mẫu kiểm thử 3 Kịch bản (Nhấn để hỏi ngay):
            </div>

            {health && (
              <div
                className={`ai-engine-badge ${
                  health.llm_provider.toLowerCase() === 'gemini' ||
                  health.llm_provider.toLowerCase() === 'openai'
                    ? 'ai-engine-live'
                    : 'ai-engine-mock'
                }`}
                data-testid="ai-engine-badge"
                data-llm-provider={health.llm_provider.toLowerCase()}
                title={`LLM Provider: ${health.llm_provider} | Embedding Provider: ${health.embedding_provider} | DB: ${health.database}`}
              >
                <span className="ai-engine-dot" aria-hidden="true" />
                <span>
                  {health.llm_provider.toLowerCase() === 'gemini'
                    ? 'AI Engine: Gemini'
                    : health.llm_provider.toLowerCase() === 'openai'
                    ? 'AI Engine: OpenAI'
                    : health.llm_provider.toLowerCase() === 'mock'
                    ? 'Chế độ Demo Offline (Mock) — Cấu hình Gemini/OpenAI API key trong .env để chat AI tự do hoàn toàn'
                    : `AI Engine: ${health.llm_provider}`}
                </span>
              </div>
            )}
          </div>

          <div className="starter-prompts-list">
            {QUICK_STARTER_PROMPTS.map((item, idx) => (
              <button
                key={idx}
                type="button"
                className="starter-prompt-chip"
                disabled={isSendingQuery}
                onClick={() => void executeQuery(item.query)}
                title={item.scenarioHint}
                data-testid={`starter-prompt-${idx}`}
              >
                <span className="starter-chip-title">{item.query}</span>
              </button>
            ))}
          </div>
        </div>

        {errorBanner && (
          <div className="alert alert-error chat-error-banner" role="alert">
            <span>⚠️ {errorBanner}</span>
            <button
              type="button"
              className="btn-link"
              onClick={() => setErrorBanner(null)}
            >
              Đóng
            </button>
          </div>
        )}

        {/* Messages Feed */}
        <div className="chat-messages-container" data-testid="chat-messages-feed">
          {isLoadingHistory ? (
            <div className="chat-loading-state">
              Đang tải lịch sử hội thoại và trích dẫn...
            </div>
          ) : messages.length === 0 ? (
            <div className="chat-empty-state">
              <div className="empty-state-icon" aria-hidden="true">
                🏛️
              </div>
              <h2>Trợ lý Tri thức Doanh nghiệp (Conversational Grounded RAG)</h2>
              <p>
                Trò chuyện tự nhiên hoặc đặt câu hỏi bằng Tiếng Việt / Tiếng Anh
                về tài liệu nội bộ. Hệ thống hỗ trợ hội thoại nhiều lượt, tự động
                lọc phân quyền RBAC tiền truy xuất, trích xuất nguyên văn tài liệu
                và xác thực từng <code>[chunk_id]</code> trước khi hiển thị.
              </p>

              <div className="empty-state-cards">
                {QUICK_STARTER_PROMPTS.slice(0, 3).map((item, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="empty-starter-card"
                    onClick={() => void executeQuery(item.query)}
                    disabled={isSendingQuery}
                  >
                    <div className="empty-starter-query">
                      &ldquo;{item.query}&rdquo;
                    </div>
                    <div className="empty-starter-hint">{item.scenarioHint}</div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => {
              const isUser = msg.role === 'user';
              const citationsList = msg.citations || [];
              // Scenario 1 tier 2: no direct answer, but related authorized documents were suggested.
              // History messages carry no match_level, so detect by content (anything other than the exact abstention).
              const isRelatedSuggestion =
                !isUser &&
                msg.found === false &&
                (msg.match_level === 'related' ||
                  (msg.match_level === undefined &&
                    msg.content.trim() !== '' &&
                    msg.content.trim() !== ABSTENTION_MESSAGE_VI));
              const relatedDocs = msg.related_documents || [];
              const isAbstention =
                !isUser &&
                !isRelatedSuggestion &&
                (msg.found === false ||
                  msg.content.trim() === ABSTENTION_MESSAGE_VI);
              const isGroundedWithCitations =
                !isUser &&
                !isAbstention &&
                msg.found === true &&
                citationsList.length > 0;
              const isConversationalReply =
                !isUser &&
                !isAbstention &&
                msg.found === true &&
                citationsList.length === 0;

              return (
                <div
                  key={msg.id}
                  className={`chat-message-row ${
                    isUser
                      ? 'chat-message-user'
                      : isConversationalReply
                      ? 'chat-message-assistant chat-message-conversational'
                      : 'chat-message-assistant'
                  }`}
                  data-testid={`chat-message-${msg.role}`}
                  data-found={msg.found !== undefined && msg.found !== null ? String(msg.found) : undefined}
                  data-conversational={isConversationalReply ? 'true' : undefined}
                >
                  <div className="chat-message-avatar" aria-hidden="true">
                    {isUser ? '👤' : '🤖'}
                  </div>

                  <div className="chat-message-bubble">
                    <div className="chat-message-header">
                      <strong>
                        {isUser
                          ? `${user?.full_name || 'Bạn'} (${user?.role.toUpperCase()} - ${user?.department})`
                          : 'Enterprise Knowledge Assistant'}
                      </strong>

                      <div className="chat-message-badges">
                        {isGroundedWithCitations && (
                          <span
                            className="status-pill status-pill-verified"
                            data-testid="scenario2-verified-pill"
                          >
                            ✓ Scenario 2: Đã xác thực {citationsList.length} nguồn
                            trích dẫn
                          </span>
                        )}
                        {isConversationalReply && (
                          <span
                            className="status-pill status-pill-conversational"
                            data-testid="conversational-reply-pill"
                          >
                            💬 Trợ lý AI
                          </span>
                        )}
                        {isRelatedSuggestion && (
                          <span
                            className="status-pill status-pill-abstention"
                            data-testid="scenario1-related-pill"
                          >
                            🧭 Scenario 1: Gợi ý tài liệu liên quan (found: false)
                          </span>
                        )}
                        {!isUser && isAbstention && (
                          <span
                            className="status-pill status-pill-abstention"
                            data-testid="scenario1-abstention-pill"
                          >
                            🛡️ Scenario 1 / 3: Từ chối trả lời an toàn (found:
                            false)
                          </span>
                        )}
                        {!isUser &&
                          typeof msg.latency_ms === 'number' &&
                          msg.latency_ms > 0 && (
                            <span className="latency-badge">
                              ⚡ {msg.latency_ms} ms
                            </span>
                          )}
                      </div>
                    </div>

                    {/* SCENARIO 1 UI: When found === false, render clear abstention alert, ZERO fabricated citations, and clickable suggested_queries */}
                    {isRelatedSuggestion ? (
                      <div
                        className="abstention-alert-box related-docs-box"
                        role="status"
                        data-testid="scenario1-related-alert"
                      >
                        <div className="related-docs-text">{msg.content}</div>

                        {relatedDocs.length > 0 && (
                          <div className="related-docs-list">
                            {relatedDocs.map((doc) => (
                              <div
                                key={`${doc.document_id}-${doc.page_or_sheet}`}
                                className="related-doc-card"
                              >
                                <div className="related-doc-title">
                                  📄 {doc.title}
                                </div>
                                <div className="related-doc-meta">
                                  {doc.file_name} · {doc.page_or_sheet} · độ
                                  tương đồng {doc.score.toFixed(2)}
                                </div>
                                {doc.excerpt && (
                                  <div className="related-doc-excerpt">
                                    “{doc.excerpt}”
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}

                        {msg.suggested_queries &&
                          msg.suggested_queries.length > 0 && (
                            <div
                              className="suggested-queries-section"
                              data-testid="suggested-queries-container"
                            >
                              <div className="suggested-queries-title">
                                💡 Gợi ý cách đặt lại câu hỏi (Nhấn để hỏi lại
                                ngay):
                              </div>
                              <div className="suggested-queries-chips">
                                {msg.suggested_queries.map((suggestion, sIdx) => (
                                  <button
                                    key={sIdx}
                                    type="button"
                                    className="suggested-query-chip"
                                    disabled={isSendingQuery}
                                    onClick={() => void executeQuery(suggestion)}
                                  >
                                    ↗ {suggestion}
                                  </button>
                                ))}
                              </div>
                            </div>
                          )}
                      </div>
                    ) : isAbstention ? (
                      <div
                        className="abstention-alert-box"
                        role="status"
                        data-testid="scenario1-abstention-alert"
                      >
                        <div className="abstention-alert-title">
                          <span className="abstention-icon">🔍</span>
                          <strong>{ABSTENTION_MESSAGE_VI}</strong>
                        </div>
                        <p className="abstention-alert-desc">
                          Hệ thống chống ảo giác (Zero-Hallucination Guardrail) đã
                          chặn sinh câu trả lời tự suy diễn vì không có đoạn tài
                          liệu nào thuộc phạm vi quyền truy cập của bạn (
                          <code>{user?.role}</code> / <code>{user?.department}</code>
                          ) đủ liên quan đến câu hỏi. Không có
                          nguồn trích dẫn giả mạo nào được tạo (
                          <code>citations: []</code>).
                        </p>

                        {msg.suggested_queries &&
                          msg.suggested_queries.length > 0 && (
                            <div
                              className="suggested-queries-section"
                              data-testid="suggested-queries-container"
                            >
                              <div className="suggested-queries-title">
                                💡 Gợi ý cách đặt lại câu hỏi (Nhấn để hỏi lại
                                ngay):
                              </div>
                              <div className="suggested-queries-chips">
                                {msg.suggested_queries.map((suggestion, sIdx) => (
                                  <button
                                    key={sIdx}
                                    type="button"
                                    className="suggested-query-chip"
                                    disabled={isSendingQuery}
                                    onClick={() => void executeQuery(suggestion)}
                                    data-testid={`suggested-query-chip-${sIdx}`}
                                  >
                                    ↗ {suggestion}
                                  </button>
                                ))}
                              </div>
                            </div>
                          )}
                      </div>
                    ) : (
                      /* Natural Conversational Reply (found=true, citations=[]) OR SCENARIO 2 UI (found=true, citations>0) */
                      <>
                        <div className="chat-message-content">
                          {isUser || isConversationalReply
                            ? msg.content
                            : renderGroundedAnswerWithChunkLinks(
                                msg.content,
                                citationsList
                              )}
                        </div>

                        {isGroundedWithCitations && (
                            <div
                              className="citations-section"
                              data-testid="citations-section"
                            >
                              <div className="citations-section-header">
                                <span>
                                  📚 Nguồn trích dẫn đã kiểm chứng (
                                  {citationsList.length} tài liệu tham chiếu)
                                </span>
                                <span className="citations-section-sub">
                                  Post-Generation Citation Verifier: Whitelist
                                  Chunk ID &amp; Quote Overlap Verified
                                </span>
                              </div>
                              <div className="citations-grid">
                                {citationsList.map((cit, cIdx) => (
                                  <CitationCard
                                    key={`${cit.chunk_id}-${cIdx}`}
                                    citation={cit}
                                    index={cIdx}
                                    isHighlighted={
                                      highlightedChunkId?.toLowerCase() ===
                                      cit.chunk_id.toLowerCase()
                                    }
                                  />
                                ))}
                              </div>
                            </div>
                          )}

                        {!isUser &&
                          msg.suggested_queries &&
                          msg.suggested_queries.length > 0 && (
                            <div className="suggested-queries-section">
                              <div className="suggested-queries-title">
                                💬 Câu hỏi liên quan bạn có thể quan tâm:
                              </div>
                              <div className="suggested-queries-chips">
                                {msg.suggested_queries.map((suggestion, sIdx) => (
                                  <button
                                    key={sIdx}
                                    type="button"
                                    className="suggested-query-chip"
                                    disabled={isSendingQuery}
                                    onClick={() => void executeQuery(suggestion)}
                                  >
                                    ↗ {suggestion}
                                  </button>
                                ))}
                              </div>
                            </div>
                          )}
                      </>
                    )}
                  </div>
                </div>
              );
            })
          )}

          {isSendingQuery && (
            <div className="chat-loading-indicator">
              <span className="spinner-dot" />
              <span>
                Đang truy xuất pgvector (Pre-Retrieval RBAC SQL Filter) &amp;
                kiểm chứng trích dẫn nguồn...
              </span>
            </div>
          )}
        </div>

        {/* Bottom Query Input Form */}
        <form onSubmit={handleFormSubmit} className="chat-input-bar">
          <input
            type="text"
            className="chat-text-input"
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder="Trò chuyện tự nhiên hoặc đặt bất kỳ câu hỏi nào về tài liệu nội bộ..."
            disabled={isSendingQuery}
            maxLength={4000}
            data-testid="chat-query-input"
          />
          <button
            type="submit"
            className="btn btn-primary chat-send-btn"
            disabled={isSendingQuery || !queryInput.trim()}
            data-testid="chat-send-button"
          >
            {isSendingQuery ? 'Đang xử lý...' : 'Gửi câu hỏi'}
          </button>
        </form>
      </section>
    </div>
  );
};
