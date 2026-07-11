-- ===========================================================================
-- Phase 2 — Public API + API key system
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).
--
-- Deviations from the columns named in the task, and why (all additive):
--   • key  ->  key_hash + key_prefix
--       Keys are stored ONLY as a SHA-256 hash. A DB leak never leaks a usable
--       key. key_prefix ("brk_" + 8 hex) is the safe-to-show handle in listings.
--   • user_id (uuid, references auth.users)
--       Kept, but NULLABLE — the site has no login yet, so keys are anchored to
--       a verified email instead. The FK/column is here so Supabase Auth can
--       attach later with no migration.
--   • email
--       The real owner handle in Phase 2 (proven by delivering the key to it),
--       mirroring the double-opt-in subscriptions flow.
--   • active + revoked_at
--       "Revoke" is a soft delete (active=false), so usage history survives for
--       Phase 3 billing and a revoked key can never be silently re-issued.
--   • day + day_count
--       Per-key daily rate-limit window, kept on the row itself (one table, one
--       write per request) instead of a separate usage table.
--   • request_count            lifetime counter (as requested).
--   • stripe_customer_id       nullable hook for Phase 3 (Stripe) — no-op today.
--
-- Anonymous callers are metered per IP via the api_usage(day, ip, count) table
-- already created in Phase 1; no change needed here.
-- ===========================================================================

create extension if not exists pgcrypto;   -- gen_random_uuid()

create table if not exists api_keys (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid references auth.users(id) on delete set null,  -- nullable; Auth not used yet
  stripe_customer_id text,                                               -- Phase 3 hook (nullable)
  email              text not null,
  key_hash           text not null unique,                              -- SHA-256 hex of the raw key
  key_prefix         text not null,                                     -- e.g. "brk_1a2b3c4d" (display only)
  tier               text not null default 'free' check (tier in ('free','pro','power')),
  active             boolean not null default true,
  request_count      integer not null default 0,                        -- lifetime
  day                date,                                              -- current rate-limit window
  day_count          integer not null default 0,                        -- requests used in `day`
  created_at         timestamptz not null default now(),
  last_used_at       timestamptz,
  revoked_at         timestamptz
);

-- fast active-key lookups by email (cap check + listing)
create index if not exists api_keys_email_idx on api_keys (email) where active;
-- key_hash already has a unique index from the UNIQUE constraint above.

alter table api_keys enable row level security;
-- No policies: the API talks to Supabase only with the SERVICE ROLE key, which
-- bypasses RLS. Enabling RLS just makes the anon key see nothing (matches the
-- rest of the project). Never expose the service-role key to the browser.

-- ---------------------------------------------------------------------------
-- Phase 1 table (reused for anonymous + keygen metering). Included here only so
-- a fresh project has it; harmless if it already exists.
-- ---------------------------------------------------------------------------
create table if not exists api_usage (
  day   date not null,
  ip    text not null,
  count integer not null default 0,
  primary key (day, ip)
);
alter table api_usage enable row level security;
