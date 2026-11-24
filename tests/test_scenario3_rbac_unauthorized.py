# File: tests/test_scenario3_rbac_unauthorized.py
"""
Mandatory Scenario 3 Verification Suite: Người dùng không có quyền (Pre-Retrieval RBAC Zero-Leakage).

Proves that:
1. SQL Pre-Retrieval Proof: Attaches a SQLAlchemy `before_cursor_execute` event listener during
   `PreRetrievalRBACRetriever.retrieve_authorized_chunks` and asserts that the executed SQL statement
   contains the `WHERE` filter on `access_level`, `department`, `allowed_roles`, and `document_permissions`
   BEFORE rows leave the database.
2. Cross-Department & Role Isolation:
   - `eng.employee` querying HR restricted salary data gets 0 chunks, `found=False`, `citations=[]`,
     and zero mention of the HR salary filename, sheet name, or salary numbers.
   - `hr.employee` (role `employee` in `HR`) querying HR `restricted` document (`allowed_roles=["admin", "manager"]`)
     gets 0 chunks, `found=False`, `citations=[]`.
   - `guest` querying `department`-scoped documents gets 0 chunks, `found=False`, `citations=[]`.
3. Document API Zero-Leakage Proof:
   - Unauthorized user calling `GET /api/v1/documents/{restricted_doc_id}`,
     `PATCH /api/v1/documents/{restricted_doc_id}/acl`, or `DELETE /api/v1/documents/{restricted_doc_id}`
     receives `404 Not Found` (identical response to a random non-existent UUID), never `403` and never
     leaking the filename or title.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Document
from app.rag.retriever import PreRetrievalRBACRetriever
from app.rag.service import RAGService
from app.schemas.chat import ChatQueryRequest
from app.schemas.common import ABSTENTION_MESSAGE_VI
from app.schemas.user import UserContext

HR_SALARY_QUERY = "Bảng khung lương Giám đốc Khối và quỹ thưởng điều hành cấp cao năm 2026?"
HR_CONFIDENTIAL_MARKERS: tuple[str, ...] = (
    "Bang_luong_mat_Ban_Giam_doc_HR_2026.xlsx",
    "Bang_Luong_Cap_Cao",
    "Bảng Lương Mật Ban Giám Đốc",
    "120.000.000",
    "180.000.000",
    "190.000.000",
    "250.000.000",
    "Executive Bonus Pool",
)


def test_scenario3_sql_pre_retrieval_before_cursor_execute_proof(
    db_session: Session,
    user_contexts: dict[str, UserContext],
) -> None:
    """
    Attach a SQLAlchemy `before_cursor_execute` listener and prove that RBAC + Department ABAC
    predicates (`access_level`, `department`, `allowed_roles`, `document_permissions`) are
    compiled directly into the SQL `WHERE` clause before rows leave the database.
    """
    captured_sql_statements: list[tuple[str, Any]] = []
    engine = db_session.get_bind()

    def _capture_sql(
        _conn: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "document_chunks" in statement.lower():
            captured_sql_statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture_sql)
    try:
        retriever = PreRetrievalRBACRetriever()
        chunks = retriever.retrieve_authorized_chunks(
            db=db_session,
            user=user_contexts["eng.employee"],
            query=HR_SALARY_QUERY,
            top_k=5,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture_sql)

    assert chunks == [], "Unauthorized eng.employee must retrieve 0 chunks from HR restricted salary doc"
    assert len(captured_sql_statements) == 1, (
        f"Expected exactly 1 pre-retrieval SQL query on document_chunks, got {len(captured_sql_statements)}"
    )

    executed_sql, _params = captured_sql_statements[0]
    sql_lower = executed_sql.lower()

    # Assert SQL WHERE clause contains RBAC and Department ABAC filters before retrieval
    assert "where" in sql_lower
    assert "documents.status" in sql_lower or "d.status" in sql_lower
    assert "access_level" in sql_lower
    assert "department" in sql_lower
    assert "allowed_roles" in sql_lower
    assert "document_permissions" in sql_lower
    assert "exists" in sql_lower


def test_scenario3_eng_employee_cannot_access_hr_restricted_salary(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
) -> None:
    """
    `eng.employee` querying HR restricted salary data gets 0 retrieved chunks,
    found=False, citations=[], and zero mention of the HR salary filename, sheet name, or numbers.
    """
    retriever = PreRetrievalRBACRetriever()
    eng_user = user_contexts["eng.employee"]

    retrieved = retriever.retrieve_authorized_chunks(
        db=db_session,
        user=eng_user,
        query=HR_SALARY_QUERY,
        top_k=5,
    )
    assert retrieved == []

    response = rag_service.query(
        db=db_session,
        user=eng_user,
        request=ChatQueryRequest(query=HR_SALARY_QUERY, top_k=5),
    )
    assert response.found is False
    assert response.answer == ABSTENTION_MESSAGE_VI
    assert response.citations == []
    assert 2 <= len(response.suggested_queries) <= 3

    full_payload_text = response.answer + " " + " ".join(response.suggested_queries)
    for marker in HR_CONFIDENTIAL_MARKERS:
        assert marker.lower() not in full_payload_text.lower(), (
            f"Confidential HR marker '{marker}' leaked to eng.employee!"
        )


def test_scenario3_hr_employee_blocked_from_hr_restricted_manager_only_doc(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
) -> None:
    """
    `hr.employee` (role='employee' in department='HR') querying an HR `restricted` document
    (`allowed_roles=['admin', 'manager']`) gets 0 chunks, found=False, citations=[].
    Meanwhile `hr.manager` and `admin` are authorized and receive found=True.
    """
    retriever = PreRetrievalRBACRetriever()
    hr_emp = user_contexts["hr.employee"]

    emp_chunks = retriever.retrieve_authorized_chunks(
        db=db_session,
        user=hr_emp,
        query=HR_SALARY_QUERY,
        top_k=5,
    )
    assert emp_chunks == []

    emp_response = rag_service.query(
        db=db_session,
        user=hr_emp,
        request=ChatQueryRequest(query=HR_SALARY_QUERY, top_k=5),
    )
    assert emp_response.found is False
    assert emp_response.answer == ABSTENTION_MESSAGE_VI
    assert emp_response.citations == []
    for marker in HR_CONFIDENTIAL_MARKERS:
        assert marker.lower() not in (
            emp_response.answer + " " + " ".join(emp_response.suggested_queries)
        ).lower()

    # Verify that hr.manager and admin CAN access the exact same query
    for privileged_persona in ("hr.manager", "admin"):
        priv_response = rag_service.query(
            db=db_session,
            user=user_contexts[privileged_persona],
            request=ChatQueryRequest(query=HR_SALARY_QUERY, top_k=5),
        )
        assert priv_response.found is True
        assert len(priv_response.citations) >= 1
        assert (
            priv_response.citations[0].file_name
            == "Bang_luong_mat_Ban_Giam_doc_HR_2026.xlsx"
        )


@pytest.mark.parametrize(
    "dept_query",
    [
        "Quy trình xin phê duyệt thanh toán?",
        "Tìm tài liệu liên quan đến hợp đồng năm 2025.",
        "Tiêu chuẩn triển khai hệ thống CI/CD khối Engineering và độ phủ pytest?",
    ],
)
def test_scenario3_guest_blocked_from_all_department_scoped_documents(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
    dept_query: str,
) -> None:
    """
    `guest` querying `department`-scoped documents (Finance, Legal, Engineering) gets
    0 chunks, found=False, citations=[], while still able to query `public` Leave Policy.
    """
    retriever = PreRetrievalRBACRetriever()
    guest_user = user_contexts["guest"]

    chunks = retriever.retrieve_authorized_chunks(
        db=db_session,
        user=guest_user,
        query=dept_query,
        top_k=5,
    )
    assert chunks == []

    resp = rag_service.query(
        db=db_session,
        user=guest_user,
        request=ChatQueryRequest(query=dept_query, top_k=5),
    )
    assert resp.found is False
    assert resp.answer == ABSTENTION_MESSAGE_VI
    assert resp.citations == []

    # Guest can still access public Leave Policy
    public_resp = rag_service.query(
        db=db_session,
        user=guest_user,
        request=ChatQueryRequest(
            query="Chính sách nghỉ phép của công ty như thế nào?",
            top_k=5,
        ),
    )
    assert public_resp.found is True
    assert len(public_resp.citations) >= 1
    assert public_resp.citations[0].file_name == "Chinh_sach_nghi_phep_2026.pdf"


def test_scenario3_cross_department_isolation_between_finance_legal_engineering(
    db_session: Session,
    rag_service: RAGService,
    user_contexts: dict[str, UserContext],
) -> None:
    """
    Verify strict cross-department isolation:
    - `eng.employee` cannot retrieve Finance Payment Approval or Legal Contract 2025.
    - `finance.manager` cannot retrieve Engineering CI/CD or Legal Contract 2025.
    - `legal.manager` cannot retrieve Finance Payment Approval or Engineering CI/CD.
    """
    cross_dept_cases = [
        ("eng.employee", "Quy trình xin phê duyệt thanh toán?"),
        ("eng.employee", "Tìm tài liệu liên quan đến hợp đồng năm 2025."),
        ("finance.manager", "Tìm tài liệu liên quan đến hợp đồng năm 2025."),
        ("finance.manager", "Tiêu chuẩn triển khai hệ thống CI/CD khối Engineering và độ phủ pytest?"),
        ("legal.manager", "Quy trình xin phê duyệt thanh toán?"),
        ("legal.manager", "Tiêu chuẩn triển khai hệ thống CI/CD khối Engineering và độ phủ pytest?"),
    ]
    retriever = PreRetrievalRBACRetriever()

    for persona, query_text in cross_dept_cases:
        user_ctx = user_contexts[persona]
        assert (
            retriever.retrieve_authorized_chunks(
                db=db_session, user=user_ctx, query=query_text, top_k=5
            )
            == []
        )
        res = rag_service.query(
            db=db_session,
            user=user_ctx,
            request=ChatQueryRequest(query=query_text, top_k=5),
        )
        assert res.found is False
        assert res.answer == ABSTENTION_MESSAGE_VI
        assert res.citations == []


def test_scenario3_document_api_zero_leakage_404_identical_to_nonexistent_uuid(
    client: TestClient,
    auth_headers: dict[str, dict[str, str]],
    seeded_documents: dict[str, Document],
) -> None:
    """
    Unauthorized users calling GET /api/v1/documents/{restricted_doc_id},
    PATCH /api/v1/documents/{restricted_doc_id}/acl, or DELETE /api/v1/documents/{restricted_doc_id}
    must receive 404 Not Found with a payload identical to a random non-existent UUID—never 403
    and never leaking the filename or title.
    """
    restricted_doc = seeded_documents["hr_restricted_salary_xlsx"]
    random_nonexistent_id = uuid.uuid4()

    for unauthorized_persona in ("eng.employee", "hr.employee", "finance.manager", "guest"):
        headers = auth_headers[unauthorized_persona]

        # 1. GET /api/v1/documents/{id}
        resp_restricted_get = client.get(
            f"/api/v1/documents/{restricted_doc.id}", headers=headers
        )
        resp_random_get = client.get(
            f"/api/v1/documents/{random_nonexistent_id}", headers=headers
        )
        assert resp_restricted_get.status_code == 404
        assert resp_restricted_get.status_code == resp_random_get.status_code
        assert resp_restricted_get.json() == resp_random_get.json()
        assert restricted_doc.file_name not in resp_restricted_get.text
        assert restricted_doc.title not in resp_restricted_get.text

        # 2. PATCH /api/v1/documents/{id}/acl
        acl_payload = {"access_level": "public"}
        resp_restricted_patch = client.patch(
            f"/api/v1/documents/{restricted_doc.id}/acl",
            headers=headers,
            json=acl_payload,
        )
        resp_random_patch = client.patch(
            f"/api/v1/documents/{random_nonexistent_id}/acl",
            headers=headers,
            json=acl_payload,
        )
        assert resp_restricted_patch.status_code == 404
        assert resp_restricted_patch.json() == resp_random_patch.json()
        assert restricted_doc.file_name not in resp_restricted_patch.text

        # 3. DELETE /api/v1/documents/{id}
        resp_restricted_del = client.delete(
            f"/api/v1/documents/{restricted_doc.id}", headers=headers
        )
        resp_random_del = client.delete(
            f"/api/v1/documents/{random_nonexistent_id}", headers=headers
        )
        assert resp_restricted_del.status_code == 404
        assert resp_restricted_del.json() == resp_random_del.json()
        assert restricted_doc.file_name not in resp_restricted_del.text

        # 4. GET /api/v1/documents list must never include the restricted HR document
        list_resp = client.get("/api/v1/documents", headers=headers)
        assert list_resp.status_code == 200
        listed_ids = {item["id"] for item in list_resp.json()}
        assert str(restricted_doc.id) not in listed_ids
        assert restricted_doc.file_name not in list_resp.text
