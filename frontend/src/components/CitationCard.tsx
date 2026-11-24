import React, { useState } from 'react';
import { CitationItem } from '../types/api';

interface CitationCardProps {
  citation: CitationItem;
  index: number;
  isHighlighted?: boolean;
}

export const CitationCard: React.FC<CitationCardProps> = ({
  citation,
  index,
  isHighlighted = false,
}) => {
  const [copiedChunkId, setCopiedChunkId] = useState(false);

  const similarityPercent = Math.min(
    100,
    Math.max(0, Number((citation.score * 100).toFixed(1)))
  );

  const handleCopyChunkId = async () => {
    try {
      await navigator.clipboard.writeText(citation.chunk_id);
      setCopiedChunkId(true);
      window.setTimeout(() => setCopiedChunkId(false), 2000);
    } catch {
      // Clipboard API may be unavailable in non-secure context
    }
  };

  const isVerified = citation.verification_status === 'verified';

  return (
    <article
      className={`citation-card ${isHighlighted ? 'citation-card-highlighted' : ''}`}
      data-testid={`citation-card-${index}`}
      data-chunk-id={citation.chunk_id}
    >
      <div className="citation-card-header">
        <div className="citation-file-info">
          <span className="citation-index-badge">#{index + 1}</span>
          <span className="citation-file-icon" aria-hidden="true">
            📑
          </span>
          <strong className="citation-file-name" title={citation.file_name}>
            {citation.file_name}
          </strong>
          <span
            className="citation-location-badge"
            title="Tọa độ trang / sheet / mục trong tài liệu gốc"
          >
            📍 {citation.page_or_sheet}
          </span>
        </div>

        <div className="citation-meta-badges">
          <span
            className="citation-score-badge"
            title={`Độ tương đồng ngữ nghĩa Cosine Similarity: ${citation.score.toFixed(4)}`}
          >
            Độ khớp: {similarityPercent}%
          </span>

          <span
            className={`verification-badge ${
              isVerified ? 'verification-verified' : 'verification-unverified'
            }`}
            title="Trạng thái kiểm chứng hậu sinh (Post-Generation Citation Verifier - Scenario 2)"
          >
            {isVerified ? '✓ VERIFIED' : citation.verification_status.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Similarity score visual bar */}
      <div className="citation-score-bar-track" aria-hidden="true">
        <div
          className="citation-score-bar-fill"
          style={{ width: `${similarityPercent}%` }}
        />
      </div>

      {/* Exact Verbatim Quote Block */}
      <blockquote className="citation-quote-block">
        <div className="citation-quote-label">
          Trích dẫn nguyên văn từ tài liệu (Verbatim Grounded Quote):
        </div>
        <p className="citation-quote-text">&ldquo;{citation.quote}&rdquo;</p>
      </blockquote>

      {/* Traceable Chunk & Document Metadata */}
      <div className="citation-card-footer">
        <div className="citation-id-group">
          <span className="citation-id-label">Chunk ID:</span>
          <code className="citation-chunk-id">{citation.chunk_id}</code>
          <button
            type="button"
            className="btn-copy-id"
            onClick={handleCopyChunkId}
            title="Sao chép mã định danh Chunk UUID"
          >
            {copiedChunkId ? 'Đã chép' : 'Sao chép'}
          </button>
        </div>

        <div className="citation-id-group">
          <span className="citation-id-label">Document ID:</span>
          <code className="citation-doc-id">
            {citation.document_id.slice(0, 8)}&hellip;
          </code>
        </div>
      </div>
    </article>
  );
};
