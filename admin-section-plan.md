# Admin Section — Plan

A platform-level admin area so an owner (like Glenn) can see and control the
whole BuildUpQuote instance: every organization, user, quote and client —
plus the ability to promote people to admin, change any client account's
subscription tier, and export client lists.

Status: **Phase 1 implemented** (commit pending review). This document is the
design reference for the admin section; §7–§11 are the implementation guide.
Phase 2 ideas remain in §9.

---

## 1. Goals

1. **See everything going on** — a single page that shows platform-wide
   totals (organizations, users, quotes, clients, revenue, subscriptions) and
   the recent activity behind them.
2. **Manage admins** — promote or demote any account to platform admin.
3. **Manage client accounts** — change any organization's subscription tier
   and status directly (manual override, not via Stripe).
4. **Export client lists** — download all clients (or one org's clients) as a
   CSV.
5. **Admins only** — every admin capability is gated on the server. A
   non-admin cannot see or change anything admin-related, even by calling the
   API directly.

## 2. Current state (what already exists)

| Thing | Where | Notes |
|---|---|---|
| `User.role` | `app/models.py` | Org-scoped RBAC (`owner`/`admin`/`estimator`/`viewer`) but **never enforced** — every user registers as `owner`. |
| `User.is_active` | `app/models.py` | Exists, default `True`, not exposed/used for login blocking yet. |
| Org subscription fields | `Organization` | `subscription_tier`, `subscription_status`, `trial_ends_at`, Stripe ids — added for billing. |
| Per-org scoping | all routers | Every listing endpoint filters by `current_user.organization_id`. |
| No platform admin | — | No `is_admin`/superuser concept, no admin routes, no role checks anywhere. |
| Page architecture | `pages.py` + `base.html` | Thin Jinja shells; auth via `localStorage bq_token`; base.html guards non-login pages. |

Key implication: there is **no** platform-level "admin" today. The admin
section introduces that concept cleanly, *alongside* the existing org-level
`role` column (which can stay for future team features).

## 3. Design decisions

### 3.1 Platform admin = a flag on the user, not a role value
- Add **`User.is_admin: Boolean, default False`** (`app/models.py`).
- The existing `role` column stays org-scoped for future team/estimator
  features. Platform admin is a *different axis*: it grants access to the
  whole instance, regardless of which org the user belongs to.
- Only admins can set it (via the new admin endpoints). The first admin is
  bootstrapped with the `ADMIN_EMAILS` env var (see §6) or `scripts/make_admin.py`.

### 3.2 One dependency gates everything: `get_current_admin`
- In `app/auth.py`: `get_current_admin` = `get_current_user` + `user.is_admin`
  check → **403** otherwise.
- Every `/api/admin/*` endpoint and the `/admin` page depend on it. This is
  the "only admins can change/see" guarantee, enforced server-side (the page
  also hides itself client-side, but the server is the real gate).

### 3.3 Manual tier overrides vs Stripe
- The admin tier change writes `subscription_tier`/`subscription_status`
  directly. It does **not** create a Stripe subscription (that stays the
  tenant's own job in Settings → Billing).
- The override is applied on top of whatever Stripe state exists; the next
  real Stripe webhook event for that org will overwrite it again. That's
  expected behaviour — document it in the UI.

## 4. Data model changes

- **`app/models.py`** — `User.is_admin = Column(Boolean, nullable=False, default=False)`.
- **`app/database.py`** — add the column to `ensure_legacy_columns()` (same
  idempotent `ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE`
  pattern, SQLite-safe).
- **`app/schemas.py`** — expose `is_admin: bool = False` on `UserOut` and
  `UserProfileOut` so the frontend knows whether to show the Admin nav item.

## 5. Backend API — `app/routers/admin.py`

New router, prefix `/api/admin`, every route uses `get_current_admin`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/admin/stats` | GET | Platform overview: orgs, users, admins, clients, quotes, pipeline total, won revenue, avg margin, subscription breakdown (trialing/active/past_due/canceled), MRR estimate by tier, recent signups (last 5), recent quotes (last 8, across all orgs). |
| `/api/admin/organizations` | GET | All orgs: name, slug, contact email, tier, status, trial end, user count, quote count, client count, created_at. Optional `?search=` (name/slug/email), `?tier=`, `?status=`. |
| `/api/admin/organizations/{id}` | GET | One org: full profile + its users + its recent quotes + client count. |
| `/api/admin/organizations/{id}/subscription` | PATCH | Body `{tier, status}` → manual override. Validates tier ∈ starter/pro/enterprise, status ∈ trialing/active/past_due/canceled. |
| `/api/admin/users` | GET | All users: email, name, job_title, role, is_admin, is_active, org name, created_at. Optional `?search=`. |
| `/api/admin/users/{id}/admin` | PATCH | Body `{is_admin: true|false}` → promote/demote. Guards: cannot demote yourself. |
| `/api/admin/users/{id}/active` | PATCH | Body `{is_active: true|false}` → enable/disable an account. Guards: cannot deactivate yourself. |
| `/api/admin/clients/export` | GET | CSV of all clients (or `?organization_id=` to scope to one org). Columns: org, client name, site address, phone, email, created_at, quote count, total quoted. `Content-Disposition: attachment`. |
| `/api/admin/activity` | GET | *(Phase 2)* Recent cross-org activity feed. See §9. |

All responses keep hashed passwords out (never serialize `hashed_password`).

## 6. First admin bootstrap

Two ways (both documented):

1. **Env var (recommended):** `ADMIN_EMAILS=glenn@example.com,other@example.com`
   read in `fastapi_app.py`'s `lifespan` after table creation — idempotently
   sets `is_admin=True` for matching emails. Add to `docker-compose.yml` as
   `${ADMIN_EMAILS:-}`.
2. **One-off script:** `scripts/make_admin.py <email>` — sets the flag and
   prints confirmation.

## 7. Admin page — `GET /admin` + `app/templates/admin.html`

Thin Jinja shell like the other pages (extends `base.html`). A script checks
`/api/users/me`; if `is_admin` is false it redirects to `/dashboard` (the
server would 403 the APIs anyway — this just avoids showing a broken page).

Tabs (Alpine `tab` state, same pattern as Settings):

1. **Overview**
   - KPI cards: Organizations, Users, Clients, Quotes, Pipeline value, Won
     revenue, Avg margin.
   - Subscription breakdown: trialing / active / past_due / canceled chips
     with counts + a rough MRR figure (sum of tier prices × active/trialing
     orgs).
   - Recent signups + recent quotes tables (who just joined, what's moving).

2. **Organizations**
   - Search box + tier/status filters.
   - Table rows: name, contact, tier badge, status badge, trial end, counts.
   - Row action: **Change tier/status** (inline select + Save) → calls
     `PATCH /api/admin/organizations/{id}/subscription`.
   - "Export clients" button per row → `?organization_id=` CSV.

3. **Users & Admins**
   - Table: email, name, role, org, active, admin badge, joined.
   - Row actions: **Make Admin / Remove Admin**, **Deactivate / Reactivate**
     (with confirm dialogs; self-demotion and self-deactivation blocked).

4. **Clients**
   - Full client list across all orgs + **Export CSV** button (global export).

**Nav:** `base.html` shows an "Admin" link only when the logged-in user is an
admin (populated from `/api/users/me` after load). Non-admins never see it.

## 8. Security checklist

- [ ] Every `/api/admin/*` endpoint requires `get_current_admin` → 403 for non-admins.
- [ ] `GET /admin` page: server renders it, but data calls 403 for non-admins and the page self-redirects.
- [ ] No `hashed_password`, Google tokens, or Stripe secrets in any admin response.
- [ ] Self-demotion (`PATCH /users/{me}/admin false`) and self-deactivation rejected.
- [ ] CSV export uses the standard library `csv` module; values quoted.
- [ ] All admin endpoints covered by tests that assert 403 for ordinary users.

## 9. Phases

**Phase 1 (this plan, as specced above)**
- `User.is_admin` + migration + schemas.
- `get_current_admin` dependency.
- `app/routers/admin.py` endpoints (stats, orgs, subscription override, users,
  admin promotion, active toggle, client CSV export).
- `/admin` page + nav item + bootstrap (`ADMIN_EMAILS`, script).
- `tests/test_admin.py`.

**Phase 2 (natural follow-ups, not in scope now)**
- `/api/admin/activity` — an `AuditLog` table recording admin actions
  (who changed what, when) so "everything that's going on" is auditable.
- Stripe-synced tier changes (actually creating/updating the Stripe
  subscription from the admin panel).
- Enforcement of org-level `role` (`admin`/`estimator`/`viewer`) for future
  team accounts.

## 10. Files touched (Phase 1)

```
app/models.py                 + User.is_admin
app/database.py               + is_admin migration
app/schemas.py                + is_admin on UserOut / UserProfileOut
app/auth.py                   + get_current_admin
app/routers/admin.py          NEW  (all /api/admin endpoints)
app/routers/pages.py          + GET /admin
app/templates/admin.html      NEW  (4-tab admin console)
app/templates/base.html       + admin nav item (admin-only, client-side)
fastapi_app.py                + include admin router, ADMIN_EMAILS bootstrap
docker-compose.yml            + ADMIN_EMAILS pass-through
scripts/make_admin.py         NEW  (one-off promote helper)
tests/test_admin.py           NEW
requirements.txt              (unchanged — stdlib csv)
```

## 11. Open questions

1. **Who is the first admin?** I'll plan for `ADMIN_EMAILS` (you set your
   email in the VPS `.env`). Confirm the email to use.
2. **Should non-admin users keep working while deactivated?** Deactivation
   should block login and invalidate sessions. I'll add a check in
   `get_current_user` (or leave it cosmetic for Phase 1) — recommend blocking login.
3. **MRR figure** on Overview — I'll compute a rough monthly number from tier
   prices (Starter $29 / Pro $69 / Enterprise $149) for trialing+active orgs.
   Fine, or leave the number out?
4. **Tier changes and Stripe** — manual override only (no Stripe sync) for
   Phase 1. Confirm that's what you want.



