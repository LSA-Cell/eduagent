"""聊天媒体直传契约：鉴权、归属、类型/大小校验及私有下载签名。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from minio.credentials import Credentials
from minio.error import S3Error

from app.main import create_app
from app.routers.uploads import MAX_FILE_SIZE, MAX_REPORT_SIZE, POST_FORM_OVERHEAD
from app.services import storage_service
from tests.conftest import TEST_USER_ID, auth_headers

FILE_NAME = "a" * 32
KEY = f"u/{TEST_USER_ID}/{FILE_NAME}.jpg"
SIGNED_PUT = "http://minio/uploads/image?X-Amz-Credential=test%2Fscope&X-Amz-Signature=put"
SIGNED_POST = "http://localhost:9000/eduagent-uploads"
SIGNED_GET = "http://minio/uploads/image?X-Amz-Credential=test%2Fscope&X-Amz-Signature=get"


@pytest.fixture
def storage(monkeypatch):
    calls = []

    def post(key, content_type, **kwargs):
        calls.append(("post", key, {"content_type": content_type, **kwargs}))
        return SIGNED_POST, {
            "key": key,
            "Content-Type": content_type,
            "policy": "signed-policy",
            "x-amz-signature": "signed-post",
        }

    def get(key, **kwargs):
        calls.append(("get", key, kwargs))
        return SIGNED_GET

    info = SimpleNamespace(size=1024, content_type="image/jpeg")

    def stat(key, **kwargs):
        calls.append(("stat", key, kwargs))
        return info

    monkeypatch.setattr(storage_service, "create_presigned_post", post)
    monkeypatch.setattr(storage_service, "presigned_get", get)
    monkeypatch.setattr(storage_service, "stat_object", stat)
    return info, calls


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as value:
        yield value


def presign_body(**changes):
    return {
        "file_name": "作业.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 1024,
        "purpose": "chat_image",
        **changes,
    }


@pytest.mark.parametrize(
    "extension,content_type,purpose",
    [
        ("jpg", "image/jpeg", "chat_image"),
        ("jpeg", "image/jpeg", "chat_image"),
        ("png", "image/png", "chat_image"),
        ("webp", "image/webp", "chat_image"),
        ("m4a", "audio/mp4", "chat_audio"),
        ("wav", "audio/wav", "chat_audio"),
        ("mp3", "audio/mpeg", "chat_audio"),
        ("ogg", "audio/ogg", "chat_audio"),
        ("csv", "text/csv", "report_file"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "report_file"),
        ("pdf", "application/pdf", "certification_file"),
        ("jpg", "image/jpeg", "certification_file"),
        ("png", "image/png", "certification_file"),
    ],
)
async def test_presign_keeps_signature_and_scopes_key(
    client, storage, extension, content_type, purpose
):
    response = await client.post(
        "/uploads/presign", headers=auth_headers(),
        json=presign_body(file_name=f"a.{extension}", content_type=content_type, purpose=purpose),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["upload_url"] == SIGNED_POST
    assert body["object_key"].startswith(f"u/{TEST_USER_ID}/")
    assert body["object_key"].endswith(f".{extension}")
    assert body["method"] == "POST"
    assert body["fields"]["key"] == body["object_key"]
    assert body["fields"]["Content-Type"] == content_type
    assert body["fields"]["x-amz-signature"] == "signed-post"
    assert 0 < body["expires_in"] <= 3600
    assert storage[1][0][2]["expires"] == body["expires_in"]
    expected_limit = MAX_REPORT_SIZE if purpose == "report_file" else MAX_FILE_SIZE
    assert storage[1][0][2]["max_size"] == expected_limit + POST_FORM_OVERHEAD


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"file_name": "a.exe"}, 400),
        ({"file_name": "a.pdf", "content_type": "application/pdf"}, 400),
        ({"file_name": "a.pdf", "content_type": "application/pdf", "purpose": "report_file"}, 400),
        ({"file_name": "a.xls", "content_type": "application/vnd.ms-excel", "purpose": "report_file"}, 400),
        ({"file_name": "a.gif", "content_type": "image/gif"}, 400),
        ({"file_name": "a.jpg", "content_type": "audio/mpeg"}, 400),
        ({"purpose": "chat_audio"}, 400),
        ({"purpose": "report_file"}, 400),
        ({"purpose": "knowledge_doc"}, 422),
        ({"purpose": "avatar"}, 422),
        ({"size_bytes": MAX_FILE_SIZE + 1}, 422),
        ({"size_bytes": 0}, 422),
        ({"size_bytes": -1}, 422),
    ],
)
async def test_presign_rejects_invalid_media_before_storage(client, storage, changes, status):
    response = await client.post(
        "/uploads/presign", headers=auth_headers(), json=presign_body(**changes)
    )
    assert response.status_code == status
    assert storage[1] == []


async def test_confirm_verifies_then_returns_full_signed_get(client, storage):
    response = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": KEY, "purpose": "chat_image"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["status"] == "ready"
    assert body["document_id"] is None
    assert body["object_key"] == KEY
    assert body["download_url"] == SIGNED_GET
    assert body["file_url"] == SIGNED_GET
    assert [call[0] for call in storage[1]] == ["stat", "get"]


async def test_audio_confirm_and_download_url_refresh(client, storage):
    storage[0].content_type = "audio/mp4"
    key = KEY.replace(".jpg", ".m4a")
    confirmed = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "chat_audio"},
    )
    assert confirmed.status_code == 200
    refreshed = await client.get(f"/files/{key}/url", headers=auth_headers())
    assert refreshed.status_code == 200
    assert refreshed.json()["download_url"] == SIGNED_GET
    assert 0 < refreshed.json()["expires_in"] <= 3600


async def test_certification_pdf_confirm_and_download_url(client, storage):
    storage[0].content_type = "application/pdf"
    key = KEY.replace(".jpg", ".pdf")
    confirmed = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "certification_file"},
    )
    assert confirmed.status_code == 200
    refreshed = await client.get(f"/files/{key}/url", headers=auth_headers())
    assert refreshed.status_code == 200


@pytest.mark.parametrize("extension,content_type", [
    ("jpg", "image/jpeg"),
    ("png", "image/png"),
])
async def test_certification_image_confirm(client, storage, extension, content_type):
    storage[0].content_type = content_type
    key = KEY.replace(".jpg", f".{extension}")
    confirmed = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "certification_file"},
    )
    assert confirmed.status_code == 200


@pytest.mark.parametrize("size", [0, -1, MAX_FILE_SIZE + 1])
async def test_confirm_checks_actual_uploaded_size(client, storage, size):
    storage[0].size = size
    response = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": KEY, "purpose": "chat_image"},
    )
    assert response.status_code == 400
    assert [call[0] for call in storage[1]] == ["stat"]


async def test_report_rejects_claimed_and_actual_oversize(client, storage):
    body = presign_body(
        file_name="report.csv", content_type="text/csv", purpose="report_file",
        size_bytes=MAX_REPORT_SIZE + 1,
    )
    presign = await client.post("/uploads/presign", headers=auth_headers(), json=body)
    assert presign.status_code == 400
    assert storage[1] == []

    storage[0].content_type = "text/csv"
    storage[0].size = MAX_REPORT_SIZE + 1
    key = KEY.replace(".jpg", ".csv")
    confirm = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "report_file"},
    )
    assert confirm.status_code == 400
    assert [call[0] for call in storage[1]] == ["stat"]


async def test_report_size_boundary_is_accepted(client, storage):
    presign = await client.post(
        "/uploads/presign", headers=auth_headers(),
        json=presign_body(
            file_name="report.csv", content_type="text/csv",
            purpose="report_file", size_bytes=MAX_REPORT_SIZE,
        ),
    )
    assert presign.status_code == 200
    assert storage[1][0][2]["max_size"] == MAX_REPORT_SIZE + POST_FORM_OVERHEAD
    storage[0].content_type = "text/csv"
    storage[0].size = MAX_REPORT_SIZE
    key = KEY.replace(".jpg", ".csv")
    confirm = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "report_file"},
    )
    assert confirm.status_code == 200


async def test_confirm_rejects_actual_content_type_mismatch(client, storage):
    storage[0].content_type = "application/octet-stream"
    response = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": KEY, "purpose": "chat_image"},
    )
    assert response.status_code == 400
    assert [call[0] for call in storage[1]] == ["stat"]


@pytest.mark.parametrize("key", [
    f"u/other/{FILE_NAME}.jpg",
    f"u/{TEST_USER_ID}/nested/{FILE_NAME}.jpg",
    f"u/{TEST_USER_ID}/../{FILE_NAME}.jpg",
    f"kb/{TEST_USER_ID}/{FILE_NAME}.pdf",
    f"u/{TEST_USER_ID}/{FILE_NAME}.exe",
    f"u/{TEST_USER_ID}/unsigned.jpg",
])
async def test_confirm_and_url_reject_out_of_scope_keys(client, storage, key):
    confirm = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": key, "purpose": "chat_image"},
    )
    assert confirm.status_code == 404
    refreshed = await client.get(f"/files/{key}/url", headers=auth_headers())
    assert refreshed.status_code == 404
    assert storage[1] == []


async def test_confirm_rejects_wrong_purpose_before_stat(client, storage):
    response = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": KEY, "purpose": "chat_audio"},
    )
    assert response.status_code == 400
    assert storage[1] == []


@pytest.mark.parametrize("path,method,body", [
    ("/uploads/presign", "POST", presign_body()),
    ("/uploads/confirm", "POST", {"object_key": KEY, "purpose": "chat_image"}),
    (f"/files/{KEY}/url", "GET", None),
])
async def test_media_routes_require_internal_token_and_user(client, storage, path, method, body):
    for headers in [{"X-User-Id": TEST_USER_ID}, auth_headers(token="invalid")]:
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 401
    response = await client.request(
        method, path, json=body, headers={"X-Internal-Token": "test-internal-token"}
    )
    assert response.status_code == 400
    assert storage[1] == []


async def test_missing_object_does_not_return_download_url(client, storage, monkeypatch):
    def missing(*args, **kwargs):
        raise S3Error(
            response=None, code="NoSuchKey", message="missing", resource="private-path",
            request_id="request", host_id="host",
        )

    monkeypatch.setattr(storage_service, "stat_object", missing)
    response = await client.post(
        "/uploads/confirm", headers=auth_headers(),
        json={"object_key": KEY, "purpose": "chat_image"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "file not found"}
    assert storage[1] == []


async def test_storage_failure_is_sanitized(client, storage, monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("private-minio-address secret-access-key")

    monkeypatch.setattr(storage_service, "create_presigned_post", failed)
    response = await client.post(
        "/uploads/presign", headers=auth_headers(), json=presign_body()
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "file storage unavailable"}


def test_post_policy_enforces_exact_key_type_and_actual_size():
    policies = []

    class Client:
        def bucket_exists(self, bucket):
            return True

        def presigned_post_policy(self, policy):
            policies.append(policy)
            return policy.form_data(Credentials("access", "secret"), "us-east-1")

    settings = SimpleNamespace(
        minio_bucket_uploads="private-uploads",
        minio_endpoint="minio:9000",
        minio_use_ssl=False,
        minio_public_endpoint="files.example.com",
        minio_public_use_ssl=True,
    )
    key = "u/user/" + "a" * 32 + ".jpg"
    url, fields = storage_service.create_presigned_post(
        key, "image/jpeg", settings=settings,
        max_size=MAX_FILE_SIZE + POST_FORM_OVERHEAD,
        client=Client(), expires=900,
    )
    assert url == "https://files.example.com/private-uploads"
    assert fields["key"] == key
    assert fields["Content-Type"] == "image/jpeg"
    policy = json.loads(base64.b64decode(fields["policy"]))
    assert ["eq", "$key", key] in policy["conditions"]
    assert ["eq", "$Content-Type", "image/jpeg"] in policy["conditions"]
    assert ["content-length-range", 1, MAX_FILE_SIZE + POST_FORM_OVERHEAD] in policy["conditions"]
    assert len(policies) == 1


def test_legacy_put_and_get_sign_with_public_host(monkeypatch):
    calls = []

    class InternalClient:
        def bucket_exists(self, bucket):
            calls.append(("bucket", bucket))
            return True

    class PublicClient:
        def __init__(self, endpoint, **kwargs):
            assert endpoint == "files.example.com"
            assert kwargs["secure"] is True
            calls.append(("public_client", endpoint))

        def presigned_put_object(self, bucket, key, **kwargs):
            calls.append(("put", bucket, key))
            return f"https://files.example.com/{bucket}/{key}?signed=put"

        def presigned_get_object(self, bucket, key, **kwargs):
            calls.append(("get", bucket, key))
            return f"https://files.example.com/{bucket}/{key}?signed=get"

    internal = InternalClient()
    monkeypatch.setattr(storage_service, "get_minio_client", lambda settings: internal)
    monkeypatch.setattr(storage_service, "Minio", PublicClient)
    settings = SimpleNamespace(
        minio_bucket_uploads="private-uploads",
        minio_endpoint="minio:9000",
        minio_use_ssl=False,
        minio_public_endpoint="files.example.com",
        minio_public_use_ssl=True,
        minio_access_key="access",
        minio_secret_key="secret",
    )
    put_url = storage_service.create_presigned_put(KEY, settings=settings)
    get_url = storage_service.presigned_get(KEY, settings=settings)
    assert put_url.startswith("https://files.example.com/")
    assert get_url.startswith("https://files.example.com/")
    assert ("put", "private-uploads", KEY) in calls
    assert ("get", "private-uploads", KEY) in calls
    assert calls.count(("bucket", "private-uploads")) == 2


def test_stat_helper_uses_existing_private_upload_bucket():
    info = SimpleNamespace(size=1, content_type="image/jpeg")
    calls = []

    class Client:
        def stat_object(self, bucket, key):
            calls.append((bucket, key))
            return info

    settings = SimpleNamespace(minio_bucket_uploads="private-uploads")
    assert storage_service.stat_object(KEY, settings=settings, client=Client()) is info
    assert calls == [("private-uploads", KEY)]
