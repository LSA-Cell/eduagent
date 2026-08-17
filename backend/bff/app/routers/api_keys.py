"""用户 API Key 管理：密文存储，响应仅含掩码后 4 位，任何接口不回显明文。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError

from eduagent_shared.logging import get_logger
from eduagent_shared.security.crypto import DecryptError, decrypt_key, encrypt_key, mask_api_key

from ..deps import get_api_key_store, get_current_user
from ..errors import AppError
from ..schemas import ApiKeyCreate, ApiKeyOut
from ..services.store import ApiKeyStore, DuplicateKeyError

router = APIRouter(prefix="/users/me/api-keys", tags=["api-keys"])
log = get_logger("bff.api_keys")


def _masked_from_ciphertext(encrypted_key: str) -> str:
    """服务端解密后取掩码；密钥不出进程，失败时退化为全掩码。"""
    try:
        return mask_api_key(decrypt_key(encrypted_key))
    except DecryptError:
        return "****"


@router.post("", response_model=ApiKeyOut, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    user=Depends(get_current_user),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> ApiKeyOut:
    """绑定第三方 API Key（AES-256-GCM 加密落库）。"""
    encrypted = encrypt_key(payload.api_key)
    try:
        record = await store.create(
            user_id=user.id,
            provider=payload.provider,
            encrypted_key=encrypted,
            monthly_budget=payload.monthly_budget,
        )
    except (DuplicateKeyError, IntegrityError):
        raise AppError(409, "provider_key_exists", "该服务商已绑定 Key，请先删除再重新绑定") from None
    log.info("api_key_created", provider=record.provider, masked_key=mask_api_key(payload.api_key))
    return ApiKeyOut(
        id=record.id,
        provider=record.provider,
        masked_key=mask_api_key(payload.api_key),
        monthly_budget=record.monthly_budget,
        used_budget=record.used_budget,
        is_active=record.is_active,
        created_at=record.created_at,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    user=Depends(get_current_user),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> list[ApiKeyOut]:
    """列出当前用户的 Key（掩码展示）。"""
    records = await store.list_for_user(user.id)
    return [
        ApiKeyOut(
            id=r.id,
            provider=r.provider,
            masked_key=_masked_from_ciphertext(r.encrypted_key),
            monthly_budget=r.monthly_budget,
            used_budget=r.used_budget,
            is_active=r.is_active,
            created_at=r.created_at,
        )
        for r in records
    ]


@router.delete("/{key_id}", status_code=204)
async def delete_api_key(
    key_id: uuid.UUID,
    user=Depends(get_current_user),
    store: ApiKeyStore = Depends(get_api_key_store),
) -> None:
    """删除指定 Key（仅可操作自己的）。"""
    if not await store.delete(user.id, key_id):
        raise AppError(404, "api_key_not_found", "Key 不存在")
