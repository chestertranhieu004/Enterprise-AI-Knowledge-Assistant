# File: tests/test_api_endpoints.py
"""
End-to-End FastAPI REST Endpoints Integration Test Suite (`backend/app/api/v1/`).

Covers:
1. `/api/v1/health` readiness check.
2. `/api/v1/auth/login` (by email and username) & `/api/v1/auth/me`.
3. `/api/v1/users` RBAC management (`GET`, `POST`, `PATCH /{user_id}/permissions`).
4. `/api/v1/documents/upload` (multipart PDF, DOCX, XLSX upload + automatic ingestion).
5. `/api/v1/documents` list, get, ACL update (`PATCH /{doc_id}/acl`), and soft delete (`DELETE /{doc_id}`).
6. `/api/v1/chat/query`, `/api/v1/chat/sessions`, and `/api/v1/chat/sessions/{session_id}`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import (
    TEST_PASSWORD,
    create_real_docx_file,
    create_real_pdf_file,
    create_real_xlsx_file,
)


def test_health_endpoint(client: TestClient) -> None:
    """GET /api/v1/health reports connected database, pgvector enabled, and active providers."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["pgvector"] == "enabled"
    assert data["llm_provider"] == "mock"
    assert data["embedding_provider"] == "mock"
    assert "timestamp" in data


def test_auth_login_and_me_flow(client: TestClient) -> None:
    """POST /api/v1/auth/login supports both email and username and issues valid JWT for /api/v1/auth/me."""
    # Login via email
    login_email_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "hr.manager@company.vn", "password": TEST_PASSWORD},
    )
    assert login_email_resp.status_code == 200
    token_data = login_email_resp.json()
    assert token_data["token_type"] == "bearer"
    assert token_data["user"]["username"] == "hr.manager"
    assert token_data["user"]["role"] == "manager"
    assert token_data["user"]["department"] == "HR"

    # Introspect /api/v1/auth/me
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "hr.manager@company.vn"

    # Login via username
    login_user_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "eng.employee", "password": TEST_PASSWORD},
    )
    assert login_user_resp.status_code == 200
    assert login_user_resp.json()["user"]["department"] == "Engineering"

    # Invalid password returns 401
    bad_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "WrongPassword999!"},
    )
    assert bad_resp.status_code == 401
    assert bad_resp.json()["error_code"] == "AUTH_INVALID_CREDENTIALS"


def test_users_rbac_management_lifecycle(
    client: TestClient,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    """Admin can list, create, and update user permissions; duplicate users return 409."""
    # 1. List users as Admin and Manager
    list_resp = client.get("/api/v1/users", headers=auth_headers["admin"])
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 7

    mgr_list_resp = client.get("/api/v1/users", headers=auth_headers["hr.manager"])
    assert mgr_list_resp.status_code == 200

    # 2. Create new user as Admin
    create_payload = {
        "email": "qa.auditor@company.vn",
        "username": "qa.auditor",
        "password": "StrongPassword123!",
        "full_name": "Hoàng Kiểm Toán QA",
        "role": "employee",
        "department": "Finance",
        "is_active": True,
    }
    create_resp = client.post(
        "/api/v1/users",
        headers=auth_headers["admin"],
        json=create_payload,
    )
    assert create_resp.status_code == 201
    created_user = create_resp.json()
    assert created_user["email"] == "qa.auditor@company.vn"
    assert created_user["role"] == "employee"
    assert created_user["department"] == "Finance"

    # 3. Duplicate creation returns 409 Conflict
    dup_resp = client.post(
        "/api/v1/users",
        headers=auth_headers["admin"],
        json=create_payload,
    )
    assert dup_resp.status_code == 409

    # 4. Update user role and department via PATCH /api/v1/users/{id}/permissions
    patch_resp = client.patch(
        f"/api/v1/users/{created_user['id']}/permissions",
        headers=auth_headers["admin"],
        json={"role": "manager", "department": "Legal", "is_active": True},
    )
    assert patch_resp.status_code == 200
    updated_user = patch_resp.json()
    assert updated_user["role"] == "manager"
    assert updated_user["department"] == "Legal"


def test_documents_multipart_upload_acl_update_and_delete(
    client: TestClient,
    auth_headers: dict[str, dict[str, str]],
    tmp_path: Path,
) -> None:
    """
    POST /api/v1/documents/upload ingests multipart PDF, DOCX, and XLSX documents,
    PATCH /api/v1/documents/{id}/acl updates visibility, and DELETE /api/v1/documents/{id} removes them.
    """
    pdf_file = create_real_pdf_file(
        tmp_path / "Upload_Test_Policy.pdf",
        pages=["Quy chế thử nghiệm upload PDF (Trang 1): Nhân viên được hỗ trợ phụ cấp ăn trưa 1,500,000 VNĐ/tháng."],
    )
    docx_file = create_real_docx_file(
        tmp_path / "Upload_Test_Guide.docx",
        sections=[
            (
                "Hướng dẫn Upload DOCX",
                ["Tài liệu DOCX kiểm thử tích hợp hệ thống quản lý tri thức."],
                [["Mục", "Giá trị"], ["Trạng thái", "Hoạt động"]],
            )
        ],
    )
    xlsx_file = create_real_xlsx_file(
        tmp_path / "Upload_Test_Budget.xlsx",
        sheets={
            "Ngan_Sach_2026": [
                ["Hang_Muc", "Chi_Phi_VND"],
                ["Hạ tầng Cloud", 500000000],
            ]
        },
    )

    uploaded_ids: list[str] = []
    for file_path, mime_type, dept, access_lvl in [
        (pdf_file, "application/pdf", "General", "public"),
        (
            docx_file,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Engineering",
            "department",
        ),
        (
            xlsx_file,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Finance",
            "department",
        ),
    ]:
        with file_path.open("rb") as fh:
            resp = client.post(
                "/api/v1/documents/upload",
                headers=auth_headers["admin"],
                files={"file": (file_path.name, fh, mime_type)},
                data={
                    "title": f"Uploaded {file_path.name}",
                    "department": dept,
                    "access_level": access_lvl,
                    "allowed_roles": "admin,manager,employee",
                },
            )
        assert resp.status_code == 201, resp.text
        doc_json = resp.json()
        assert doc_json["status"] == "ready"
        assert doc_json["chunk_count"] >= 1
        uploaded_ids.append(doc_json["id"])

    target_doc_id = uploaded_ids[1]

    # Update ACL on the uploaded Engineering DOCX to make it public
    acl_resp = client.patch(
        f"/api/v1/documents/{target_doc_id}/acl",
        headers=auth_headers["admin"],
        json={
            "access_level": "public",
            "allowed_roles": ["admin", "manager", "employee", "guest"],
        },
    )
    assert acl_resp.status_code == 200
    assert acl_resp.json()["access_level"] == "public"
    assert "guest" in acl_resp.json()["allowed_roles"]

    # Guest can now view the document
    guest_get = client.get(
        f"/api/v1/documents/{target_doc_id}",
        headers=auth_headers["guest"],
    )
    assert guest_get.status_code == 200

    # Delete the uploaded document
    del_resp = client.delete(
        f"/api/v1/documents/{target_doc_id}",
        headers=auth_headers["admin"],
    )
    assert del_resp.status_code == 200
    assert del_resp.json() == {"deleted": True, "document_id": target_doc_id}

    # Deleted document returns 404
    after_del = client.get(
        f"/api/v1/documents/{target_doc_id}",
        headers=auth_headers["admin"],
    )
    assert after_del.status_code == 404


def test_chat_query_and_sessions_history_lifecycle(
    client: TestClient,
    auth_headers: dict[str, dict[str, str]],
) -> None:
    """
    POST /api/v1/chat/query creates a session, appends multi-turn messages with citations,
    and isolates chat sessions strictly per user.
    """
    # Turn 1: Create new session via query
    q1_resp = client.post(
        "/api/v1/chat/query",
        headers=auth_headers["eng.employee"],
        json={"query": "Chính sách nghỉ phép của công ty như thế nào?", "top_k": 5},
    )
    assert q1_resp.status_code == 200
    q1_data = q1_resp.json()
    assert q1_data["found"] is True
    assert len(q1_data["citations"]) >= 1
    session_id = q1_data["session_id"]

    # Turn 2: Follow-up in the same session
    q2_resp = client.post(
        "/api/v1/chat/query",
        headers=auth_headers["eng.employee"],
        json={
            "query": "Tiêu chuẩn triển khai hệ thống CI/CD khối Engineering và độ phủ pytest?",
            "session_id": session_id,
            "top_k": 5,
        },
    )
    assert q2_resp.status_code == 200
    q2_data = q2_resp.json()
    assert q2_data["session_id"] == session_id
    assert q2_data["found"] is True
    assert any(
        c["file_name"] == "Tieu_chuan_CI_CD_Engineering.md"
        for c in q2_data["citations"]
    )

    # List sessions for eng.employee
    sessions_resp = client.get(
        "/api/v1/chat/sessions",
        headers=auth_headers["eng.employee"],
    )
    assert sessions_resp.status_code == 200
    sessions_list = sessions_resp.json()
    matching = [s for s in sessions_list if s["id"] == session_id]
    assert len(matching) == 1
    assert matching[0]["message_count"] == 4  # 2 user + 2 assistant messages

    # Fetch session detail
    detail_resp = client.get(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_headers["eng.employee"],
    )
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert len(detail_data["messages"]) == 4
    assert detail_data["messages"][1]["role"] == "assistant"
    assert detail_data["messages"][1]["found"] is True
    assert len(detail_data["messages"][1]["citations"]) >= 1

    # Another user (guest) attempting to read eng.employee's session receives 404
    other_user_resp = client.get(
        f"/api/v1/chat/sessions/{session_id}",
        headers=auth_headers["guest"],
    )
    assert other_user_resp.status_code == 404
