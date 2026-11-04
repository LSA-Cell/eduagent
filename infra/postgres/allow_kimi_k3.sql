-- Existing databases: allow Kimi K3 for the free plan without changing
-- other models, quotas, prices, subscriptions, or open-access plan semantics.
-- Safe to rerun: an existing kimi-k3 entry is left untouched.
BEGIN;

UPDATE plans
SET allowed_models = allowed_models || '["kimi-k3"]'::jsonb
WHERE id = 'free'
  AND jsonb_typeof(allowed_models) = 'array'
  AND allowed_models <> '[]'::jsonb
  AND NOT allowed_models @> '["kimi-k3"]'::jsonb;

COMMIT;
