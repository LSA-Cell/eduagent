"""GET /proxy/usage/{user_id}：各 provider 用量查询（不含任何 Key 信息）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from ..deps import get_key_store, require_internal_token
from ..schemas import ProviderUsageOut
from ..services.store import ApiKeyStore

router = APIRouter(prefix="/proxy", tags=["proxy"], dependencies=[Depends(require_internal_token)])


@router.get("/usage/{user_id}", response_model=list[ProviderUsageOut])
async def get_usage(
    user_id: uuid.UUID,
    store: ApiKeyStore = Depends(get_key_store),
) -> list[ProviderUsageOut]:
    records = await store.list_for_user(user_id)
    return [
        ProviderUsageOut(
            provider=r.provider,
            monthly_budget=r.monthly_budget,
            used_budget=r.used_budget,
            remaining_budget=(
                None if r.monthly_budget is None else max(0, r.monthly_budget - r.used_budget)
            ),
            is_active=r.is_active,
        )
        for r in records
    ]
