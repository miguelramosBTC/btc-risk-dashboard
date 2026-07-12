-- ===========================================================================
-- Phase 3 — Stripe billing
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).
-- Requires the Phase 2 schema (api_keys) to exist already.
-- ===========================================================================

-- Track the Stripe subscription attached to a key (customer id column already
-- exists from Phase 2). Used by the webhook to upgrade/downgrade precisely.
alter table api_keys add column if not exists stripe_subscription_id text;

-- Webhook idempotency + audit trail: one row per processed Stripe event.
-- The UNIQUE constraint on stripe_event_id is what makes retries harmless.
create table if not exists billing_events (
  id               uuid primary key default gen_random_uuid(),
  stripe_event_id  text not null unique,
  type             text not null,
  email            text,
  created_at       timestamptz not null default now()
);

alter table billing_events enable row level security;
-- No policies: accessed only with the service-role key (bypasses RLS), same as
-- every other table in this project. The anon key sees nothing.
