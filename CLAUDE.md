# CLAUDE.md — Orbit: complete project context for coding agents

> Read this whole file at the start of every session. It is the single source of truth for **what Orbit is, how it is designed, what phase we are in, and how to work**.
> `ORBIT_ROADMAP.md` is the phase summary. `PROJECT.md` is the long-term product vision (much of it is out of scope for v1).
> **If this file and PROJECT.md disagree, this file wins.**

---

## 0. Session protocol (follow every time)

1. Read **§2 Current status** to know the active phase and ticket.
2. Work on **exactly one ticket** (§17). State its ID in your first message: "Working on T-xxx".
3. Never start a ticket from a later phase than the current one.
4. Check **§3 Ownership** — some files are human-written; for those you explain and review only.
5. **Propose a plan first and wait for approval** before writing code.
6. Tests first (from the ticket's acceptance criteria) → confirm they fail → implement → `make check`.
7. End with a summary: what changed, how it was tested, open questions, and whether an ADR is needed (§6).
8. If you are unsure how a library works, **read the installed package source or official docs. Never invent APIs.**
9. If scope must change, propose the edit to this file / ORBIT_ROADMAP.md first.

---

## 1. Product

### 1.1 One line
**Orbit lets a small engineering team scale its output by delegating tickets to AI agents, while humans keep control through approvals, budgets and a full audit trail.**

### 1.2 What it does (user view)
- **Track work:** tickets with status, priority, assignee, comments, @mentions, history — like Linear/Jira, but thin.
- **Delegate to agents:** assign a ticket to `@coder-agent` like to a person → agent posts a plan → human approves → agent codes in a sandbox → opens a PR → reviewer agent comments → human merges → ticket moves to Done automatically.
- **Control agents:** every step, token and rupee is visible; policies decide what agents may do; budgets hard-stop runaway runs; any agent can be disabled instantly.

### 1.3 The core of the product
**The core is the agent control plane, not the agents.** An agent (LLM + prompt + tools) is commodity. What is hard and valuable:
policy engine outside the model · human approvals · durable crash-safe runs · sandboxing · budgets & metering · audit trail · context building.
The tracker exists to give agents identity, work units, context and a place to report. **Build the tracker thin; build the control plane deep.**

### 1.4 Non-goals (v1)
- Not a Git host, CI runner, or deploy platform. Code lives in GitHub.
- No realtime sync / offline mode, no cycles/projects/roadmaps, no billing, no SSO, no Slack, no importers, no docs editor.
- **Agents never merge PRs, never push to default branches, never deploy.** Humans do.

---

## 2. Current status  ← update this section as work progresses

| Field | Value |
|---|---|
| Active phase | **P0 — Foundations** |
| Active ticket | T-001 |
| Completed tickets | — |
| Open ADRs | ADR-0001 (Python backend), ADR-0002 (Tenancy via RLS) |
| Last updated | 2026-09-29 |

---

## 3. Ownership split (important)

The human owner writes **security- and correctness-critical code himself** so he can defend every line in interviews.
For these paths: **explain, review, suggest tests, point out bugs — do NOT write the implementation unless the owner explicitly asks in that session.**

| Path | Why it is owner-written |
|---|---|
| `src/orbit/store/tenant.py` + RLS policy migrations | Tenant isolation |
| `src/orbit/agents/policy/` | Policy engine — core IP |
| `src/orbit/agents/tools/router.py` | Enforcement point for every agent action |
| `src/orbit/agents/workflows/` | Run state machine, approvals, crash-resume |
| `src/orbit/jobs/outbox.py` | Transactional outbox worker |
| `src/orbit/llm/budget.py` | Cost enforcement |
| `docs/adr/*` | Decisions must be the owner's reasoning |

Everything else (boilerplate, CRUD, schemas, CI, infra, test scaffolding, UI) you may implement. Every diff is reviewed line by line — keep diffs small and explain non-obvious choices.

---

## 4. Architecture

### 4.1 Principles (do not violate)
1. **Single write path.** Every mutation — from humans, agents, integrations — goes through `domain/` functions. Same validation, same audit, same side effects. Agents are just clients with a different identity.
2. **Transactional outbox.** Domain row + history row + outbox row are written in **one** DB transaction. No dual writes (never "commit, then call external service").
3. **Bulkheads.** Agent workers and sandboxes run as separate processes. An LLM or sandbox outage must never slow down or break the tracker.
4. **Policy outside the model.** The model proposes; deterministic code decides. Prompt injection cannot grant permissions.
5. **Fail closed.** No tenant set ⇒ zero rows. Missing approval ⇒ no action. Unknown tool ⇒ rejected. Budget unknown ⇒ stop.
6. **Everything behind an interface** where a vendor may change: LLM providers, sandbox provider, code host.

### 4.2 Components

```
┌────────────────────────────────────────────────────────────────────┐
│ WEB CLIENT (React + TS, later)   list view · issue page · run UI   │
└───────────────┬────────────────────────────────────────────────────┘
                │ HTTPS (JSON)
┌───────────────▼────────────────────┐
│ API SERVICE (FastAPI)              │   api/ = thin routers
│ auth · authz · idempotency         │   domain/ = ONLY writer
│ domain handlers (single write path)│   store/ = DB + with_tenant
│ MCP endpoint (P5)                  │
└───────┬────────────────────────────┘
        │ one transaction
┌───────▼─────────────────────────────────────────────────────────────┐
│ POSTGRES 16 (+pgvector)                                             │
│ tenant tables (RLS) · issue_history · outbox · agent_run_step · ... │
└───────┬─────────────────────────────────────────────────────────────┘
        │ outbox rows (SKIP LOCKED)
┌───────▼───────────────┐   start workflow / signal   ┌───────────────┐
│ OUTBOX WORKER         │ ──────────────────────────▶ │ TEMPORAL      │
│ notifications ·       │                             │ SERVER        │
│ agent triggers ·      │                             └──────┬────────┘
│ webhooks (later)      │                                    │
└───────────────────────┘                                    │
┌────────────────────────────────────────────────────────────▼────────┐
│ AGENT WORKER (Python, Temporal worker)  = the control plane         │
│  run workflow: context → plan → approval → execute loop → finish    │
│  context builder · LLM gateway (metering, budgets, fallback)        │
│  tool router + policy engine (checked BEFORE every tool call)       │
└──────┬───────────────────┬─────────────────────┬────────────────────┘
       ▼                   ▼                     ▼
  Orbit domain       SANDBOX RUNNER          GITHUB APP (P4)
  (as agent actor,   Docker container per    short-lived scoped tokens
   same write path)  run; no secrets;        branch · push · open PR
                     restricted egress       (never merge)
                                  LLM PROVIDERS ◀── only via LLM gateway
Redis: rate limits, concurrency semaphores · MinIO/S3: run logs, artifacts
GitHub webhooks ─▶ API ─▶ domain (PR opened ⇒ In Review, merged ⇒ Done)
```

### 4.3 Processes (one codebase, several entrypoints)
| Process | Entrypoint | Phase |
|---|---|---|
| API | `orbit.main:app` (uvicorn) | P0 |
| Outbox worker | `python -m orbit.jobs.worker` | P1 |
| Agent worker (Temporal) | `python -m orbit.agents.worker` | P2 |
| Sandbox runner | `python -m orbit.sandbox.runner` (or library in agent worker initially) | P4 |

This is a **modular monolith**: separate processes for failure isolation, one codebase, no network contracts between domain modules.

### 4.4 Flow A — human edits an issue
1. Client `PATCH /v1/issues/{id}` with `Idempotency-Key`.
2. API authenticates session → resolves actor + workspace.
3. `with_tenant(workspace_id)` opens a transaction and sets RLS context.
4. `domain.issues.update(...)`: authorize → validate → update row → write `issue_history` rows (one per changed field) → write `outbox` rows (e.g. `issue.assigned`) → store idempotency result.
5. Commit. Return new state.
6. Outbox worker picks up events → notifications / agent triggers.

### 4.5 Flow B — agent fixes a bug (the core flow)
```
 1. Human assigns ENG-142 to @coder-agent          → Flow A; outbox event issue.assigned
 2. Outbox worker: assignee is an agent            → start Temporal workflow AgentRun(run_id)
 3. Activity: build context (issue, comments, repo map, similar issues)
 4. Activity: LLM plan (budget pre-check → call → meter → record step)
 5. Tool orbit.add_comment(plan)                   → policy: ALLOW → domain write as agent actor
 6. Policy: execution requires approval            → create approval row; workflow waits on signal
 7. Human approves in UI                           → API → domain → Temporal signal → resume
 8. Execute loop: LLM → tool → result → LLM ...    (every step recorded with cost + latency)
    tools: sandbox.exec, sandbox.write_file, repo.read_file, ...
 9. github.push + github.open_pr on orbit-agent/*   → policy: ALLOW (agent branches only)
10. GitHub webhook PR opened                        → issue → In Review; reviewer agent triggered
11. Human merges on GitHub                          → webhook → issue → Done
    github.merge_pr does not exist as an agent tool. Merge is human-only.
```
Crash at any step ⇒ Temporal replays the workflow and resumes at the last completed activity. Activities are idempotent (keyed by `run_id + step_seq`), so no duplicate comments, commits or PRs.

---

## 5. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | Managed with **uv** |
| API | FastAPI + Pydantic v2 | Lifespan handler for startup/shutdown |
| Config | pydantic-settings | One `Settings` object; fail fast on missing vars |
| DB | PostgreSQL 16 + pgvector | Single store for rows, audit, outbox, embeddings |
| DB access | SQLAlchemy 2.0 **Core** (async) + asyncpg driver | **No ORM models.** Explicit SQL-shaped queries |
| Migrations | Alembic | Hand-written; no autogenerate for RLS/policies/roles |
| Pooling (prod) | PgBouncer, transaction mode | `set_config(..., true)` is transaction-scoped so RLS still works |
| Jobs | Own outbox table + worker, `FOR UPDATE SKIP LOCKED` | Enqueue in same txn as the write |
| Durable runs | Temporal (Python SDK) | Confirm in ADR-0003 before P2 |
| Cache/limits | Redis | Rate limits, per-workspace concurrency semaphores |
| Object storage | MinIO locally, S3 in prod | Run logs, large LLM outputs, artifacts |
| Sandbox | Docker via Python Docker SDK, behind `SandboxProvider` interface | P4 |
| LLM | Own gateway module over provider SDKs/HTTP | Metering, budgets, fallback |
| Embeddings | pgvector, side table keyed by `(entity, model)` | P3 |
| Logging | structlog (JSON) | Include `request_id`, `workspace_id`, `actor_id`, `run_id` |
| Tracing | OpenTelemetry | API → workflow → LLM/tool spans |
| Lint/format | ruff | |
| Types | mypy --strict | |
| Tests | pytest, pytest-asyncio, httpx (ASGI client), testcontainers | Real Postgres in tests, never SQLite |
| Frontend | TypeScript + React (later) | Under `web/` |
| CI | GitHub Actions | `make check` |

---

## 6. Decisions (ADR index)

ADRs live in `docs/adr/NNNN-title.md`. **The owner writes them.** You may flag that one is needed.

| ADR | Title | Status |
|---|---|---|
| 0001 | Python backend (not Go/TS) — core is an agent control plane; Python owns the LLM/eval/workflow ecosystem; tracker is thin so client/server logic sharing matters little | proposed |
| 0002 | Multi-tenancy via Postgres RLS + app-layer authz (defense in depth) | proposed |
| 0003 | Durable runs: Temporal vs own state machine (lean Temporal) | open — before P2 |
| 0004 | Policy model: allow / deny / require_approval, evaluated outside the model | open — P2 |
| 0005 | LLM gateway: own module vs proxy | open — P2 |
| 0006 | Sandbox isolation model (Docker first, provider interface) | open — P4 |
| 0007 | Agent GitHub identity: GitHub App installation tokens | open — P4 |

Template:
```md
# ADR-NNNN: Title
Status: proposed | accepted | superseded by ADR-XXXX
Date: YYYY-MM-DD
## Context
## Options considered (with tradeoffs)
## Decision
## Consequences (positive, negative, follow-ups)
```

---

## 7. Repository layout

```
orbit/
├─ src/orbit/
│  ├─ main.py              # FastAPI app factory + lifespan
│  ├─ api/                 # routers only: parse → call domain → map errors
│  ├─ domain/              # business logic; ONLY writer of domain tables
│  │  ├─ errors.py         # ForbiddenError, ValidationError, ConflictError, NotFoundError
│  │  ├─ authz.py          # can(actor, action, resource)
│  │  ├─ issues.py  comments.py  teams.py  workspaces.py  agents.py
│  ├─ store/
│  │  ├─ db.py             # engine, connection helpers
│  │  ├─ tenant.py         # with_tenant()  (OWNER-WRITTEN)
│  │  └─ tables.py         # SQLAlchemy Core Table definitions
│  ├─ auth/                # oauth, sessions, invites, api keys
│  ├─ platform/            # config.py, logging.py, telemetry.py, ids.py
│  ├─ jobs/                # outbox.py (OWNER-WRITTEN), worker.py, handlers/
│  ├─ agents/              # P2+
│  │  ├─ workflows/        # Temporal workflows (OWNER-WRITTEN)
│  │  ├─ activities/       # Temporal activities
│  │  ├─ policy/           # policy engine (OWNER-WRITTEN)
│  │  ├─ tools/            # registry.py, router.py (OWNER-WRITTEN), impl/
│  │  ├─ context/          # context builder
│  │  ├─ templates/        # agent prompts + configs (versioned)
│  │  └─ worker.py
│  ├─ llm/                 # gateway.py, providers/, metering.py, budget.py (OWNER-WRITTEN)
│  ├─ sandbox/             # provider interface + docker provider (P4)
│  ├─ integrations/github/ # P4
│  └─ mcp/                 # P5
├─ migrations/             # alembic (created by `alembic init`)
├─ tests/                  # mirrors src/orbit; tests/security/ for cross-tenant + agent-safety suites
├─ evals/                  # P3: agent eval harness, fixtures, reports
├─ web/                    # later
├─ infra/docker-compose.yml
├─ docs/adr/  docs/deep-dives/  docs/benchmarks.md
├─ .github/workflows/ci.yml
├─ Makefile  pyproject.toml  .env.example
└─ CLAUDE.md  ORBIT_ROADMAP.md  PROJECT.md  README.md
```
Create a module only when a ticket needs it. No `utils/`, `helpers/`, `common/` or `models/` catch-alls.

---

## 8. Data model (v1)

Conventions: `id uuid` (UUIDv7, generated in app) · `created_at/updated_at timestamptz` (UTC) · soft delete via `archived_at` · **every tenant table has `workspace_id uuid NOT NULL` + RLS**.

### 8.1 Global (no RLS)
```sql
user_account(id, email UNIQUE, name, avatar_url, github_user_id UNIQUE, created_at)
session(id, user_id, token_hash UNIQUE, expires_at, revoked_at, created_at, last_seen_at, user_agent)
```

### 8.2 Tenancy & identity (P0)
```sql
workspace(id, name, url_key UNIQUE, created_at)
actor(id, workspace_id, kind ENUM('user','agent','integration'), display_name, avatar_url, created_at)
membership(workspace_id, user_id, actor_id, role ENUM('owner','admin','member','guest'),
           created_at, PRIMARY KEY(workspace_id, user_id))
team(id, workspace_id, key VARCHAR(6), name, issue_counter bigint DEFAULT 0, created_at,
     UNIQUE(workspace_id, key))
invite(id, workspace_id, email NULL, role, token_hash UNIQUE, expires_at, used_at,
       created_by_actor_id, created_at)
```
`actor` is the unified identity for audit and assignment: humans, agents and integrations are all actors.

### 8.3 Tracker (P1)
```sql
workflow_state(id, workspace_id, team_id, name,
               type ENUM('backlog','unstarted','started','completed','canceled'),
               position float8)
issue(id, workspace_id, team_id, number int, title, description_md,
      state_id, priority smallint /* 0 none, 1 urgent, 2 high, 3 medium, 4 low */,
      assignee_actor_id NULL, creator_actor_id, parent_id NULL,
      started_at, completed_at, canceled_at, archived_at, created_at, updated_at,
      UNIQUE(team_id, number))
comment(id, workspace_id, issue_id, author_actor_id, body_md, edited_at, created_at)
issue_history(id, workspace_id, issue_id, actor_id, field, from_value jsonb, to_value jsonb, created_at)
issue_subscriber(workspace_id, issue_id, actor_id, PRIMARY KEY(issue_id, actor_id))
idempotency_key(workspace_id, key, actor_id, request_hash, response jsonb, created_at,
                PRIMARY KEY(workspace_id, key))   -- TTL cleanup job deletes rows > 24h
outbox(id bigserial, workspace_id, event_type, payload jsonb, created_at,
       available_at, attempts int DEFAULT 0, last_error, processed_at NULL)
```
- Issue number: `UPDATE team SET issue_counter = issue_counter + 1 WHERE id = :team RETURNING issue_counter` inside the create transaction. Automations key off `workflow_state.type`, never the name.
- Identifier shown to users: `{team.key}-{number}`, e.g. `ENG-142`.

### 8.4 Agent platform (P2)
```sql
agent(id, workspace_id, actor_id, slug, name,
      kind ENUM('triage','spec','coder','reviewer','custom'),
      template_version, model_config jsonb, tool_allowlist text[],
      autonomy ENUM('suggest','act_with_approval','autonomous'),
      budget_monthly_usd numeric, enabled bool, created_at)
agent_run(id, workspace_id, agent_id, issue_id NULL,
          trigger ENUM('assignment','mention','event','manual'),
          status ENUM('queued','planning','awaiting_approval','running','blocked',
                      'succeeded','failed','canceled','budget_exceeded'),
          temporal_workflow_id, input jsonb, output jsonb, error jsonb,
          cost_usd numeric DEFAULT 0, tokens_in bigint DEFAULT 0, tokens_out bigint DEFAULT 0,
          created_by_actor_id, started_at, finished_at, created_at)
agent_run_step(id, workspace_id, run_id, seq int,
               kind ENUM('llm_call','tool_call','tool_result','message',
                         'approval_request','approval_decision','checkpoint','policy_denial'),
               payload jsonb /* small; large bodies go to object storage, store the key */,
               cost_usd numeric, latency_ms int, created_at, UNIQUE(run_id, seq))
approval(id, workspace_id, run_id, action_type, action_payload jsonb,
         status ENUM('pending','approved','rejected','expired'),
         decided_by_actor_id, decided_at, note, expires_at, created_at)
agent_policy(id, workspace_id, scope ENUM('workspace','agent'), scope_id NULL, rules jsonb, created_at)
llm_usage(id, workspace_id, run_id NULL, provider, model, tokens_in, tokens_out, cost_usd, created_at)
```
`agent_run_step` grows fastest (~60 rows/run). Plan monthly partitioning before it matters.

### 8.5 Agents & integrations (P3–P5)
```sql
embedding(workspace_id, entity_type, entity_id, model, dims int, vector vector, updated_at,
          PRIMARY KEY(entity_type, entity_id, model))
github_installation(id, workspace_id, installation_id, account_login, created_at)
github_link(id, workspace_id, issue_id, repo_full_name, kind ENUM('branch','pr','commit'),
            external_id, url, state, metadata jsonb, created_at)
sandbox_session(id, workspace_id, run_id, provider, external_id, image, status,
                started_at, ended_at, cpu_seconds)
api_key(id, workspace_id, actor_id, prefix, key_hash, scopes text[], last_used_at, revoked_at, created_at)
```

---

## 9. Tenancy & RLS (read fully before touching the DB)

- **Two roles.** `orbit_migrator` owns all tables and runs migrations. `orbit_app` is used by the running app: no superuser, no BYPASSRLS, owns nothing, has only needed grants. **Superusers and table owners bypass RLS** — if the app used the migrator role, every policy would be silently skipped and tests would pass for the wrong reason.
- Every tenant table: `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY`.
- Policy shape (per table):
  - `USING (workspace_id = current_setting('app.workspace_id', true)::uuid)`
  - `WITH CHECK (workspace_id = current_setting('app.workspace_id', true)::uuid)`
  - `current_setting(..., true)` returns NULL when unset ⇒ comparison is NULL ⇒ **zero rows** (fail closed).
- Setting context: inside the transaction, `SELECT set_config('app.workspace_id', :workspace_id, true)`. The third arg `true` = transaction-local. **`SET LOCAL` cannot take bind parameters; never string-format SQL.**
- `with_tenant(workspace_id)` = open transaction → set_config → yield connection → commit/rollback. All tenant access goes through it.
- **Bootstrapping problem:** at login you need "which workspaces is this user in?" before any tenant is set. `membership` therefore also needs a policy keyed on `app.user_id` (set the same way) so a user can list only their own memberships.
- **Defense in depth:** RLS is the safety net. `domain/authz.py` still checks role permissions on every action.
- **Required tests (`tests/security/test_cross_tenant.py`)** for every tenant table:
  1. Under tenant A, selecting B's rows returns 0 rows.
  2. Under tenant A, inserting/updating a row with B's `workspace_id` fails.
  3. With no tenant set, selects return 0 rows.
  4. The app role is not superuser and does not own the table (assert via catalog query).

---

## 10. Auth

- **GitHub OAuth** only in v1. Flow: `/auth/github/login` generates random `state`, stores it (signed cookie or short-lived row) → GitHub → `/auth/github/callback` verifies `state` (CSRF protection), exchanges code, upserts `user_account` by `github_user_id`.
- **Sessions:** random 32-byte token → cookie; DB stores only `sha256(token)`. Cookie: `HttpOnly`, `Secure` (prod), `SameSite=Lax`, path `/`. Expiry + sliding refresh. Logout = set `revoked_at`.
- **CSRF for cookie auth:** SameSite=Lax + verify `Origin`/`Referer` header on state-changing requests.
- **Invites:** random token, store hash, expiry (e.g. 7 days), single use (`used_at`). Accepting creates `membership` + `actor(kind=user)`.
- **API keys (P5):** format `orb_<prefix>_<secret>`; store prefix (for lookup) + hash; shown once; scoped; revocable.
- **Agents** act as their `actor` with permissions derived from agent config + policy, never from the human who triggered them.

---

## 11. Domain & API conventions

### 11.1 Domain layer
- Signature style: `async def update_issue(conn, actor: Actor, issue_id: UUID, patch: IssuePatch, idem_key: str | None) -> Issue`.
- Order inside every mutation: **authorize → validate → load current → apply → write history → write outbox → save idempotency result**. All in the caller's transaction.
- Typed errors in `domain/errors.py`; mapped to HTTP only in `api/`:
  | Error | HTTP |
  |---|---|
  | `NotFoundError` | 404 |
  | `ForbiddenError` | 403 |
  | `ValidationError` | 422 |
  | `ConflictError` | 409 |
  | `UnauthenticatedError` | 401 |
- No HTTP concepts (Request, status codes) inside `domain/`.

### 11.2 Idempotency
- All POST/PATCH/DELETE accept `Idempotency-Key`. Same key + same request hash ⇒ return stored response. Same key + different hash ⇒ 409.
- Stored in `idempotency_key` inside the same transaction as the mutation. Cleanup job removes rows older than 24h.

### 11.3 Outbox
- `outbox` rows are written in the domain transaction.
- Worker loop: `SELECT ... WHERE processed_at IS NULL AND available_at <= now() ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED` → dispatch by `event_type` → mark processed, or increment `attempts` and set `available_at` with exponential backoff → dead-letter after N attempts.
- Handlers must be **idempotent** (at-least-once delivery).
- Event names: `issue.created`, `issue.updated`, `issue.assigned`, `comment.created`, `agent_run.requested`, `approval.decided`.

### 11.4 HTTP API
- Prefix `/v1`. JSON. snake_case fields.
- Resource routes: `/v1/workspaces/{ws}/issues`, `/v1/issues/{id}`, `/v1/issues/{id}/comments`, `/v1/agent-runs/{id}`, `/v1/approvals/{id}/decide`.
- Issue lookup accepts UUID or identifier (`ENG-142`).
- Pagination: cursor-based (`?cursor=&limit=`, max 100).
- Error body: `{"error": {"code": "forbidden", "message": "...", "details": {...}}}`.
- Health: `/healthz` (process alive) and `/readyz` (DB reachable).

---

## 12. Agent control plane (P2 — the core)

### 12.1 Run state machine
```
queued → planning → awaiting_approval ─(approved)→ running → succeeded
            │               │                        │   ↘ failed
            │               └─(rejected/expired)→ canceled
            │                                        ├→ blocked (needs human input) → running
            └────────────────────────────────────────└→ budget_exceeded
Any non-terminal state → canceled (kill switch / user cancel)
```
Transitions are validated in one place; illegal transitions raise.

### 12.2 Workflow (Temporal) outline
1. `load_context` activity → context bundle (stored in object storage; workflow holds the key).
2. `llm_plan` activity → plan text + proposed actions.
3. `post_comment` tool via router → plan visible on the issue.
4. If policy requires approval: `create_approval` activity → **wait for signal** `approval_decided` with timeout → on timeout mark `blocked`/`expired`.
5. Execute loop (bounded by max steps and budget): `llm_step` → for each tool call: `route_tool` activity (policy + budget + execute + record) → feed result back.
6. `finalize` activity: summary comment, artifacts, final status, costs.

Rules: workflow code is **deterministic** (no I/O, no `datetime.now()`, no randomness — use activities and Temporal's time APIs); every activity is **idempotent** keyed by `(run_id, step_seq)`; activities have explicit timeouts and retry policies; large payloads go to object storage.

### 12.3 Policy engine (OWNER-WRITTEN)
- Pure function: `evaluate(policy, action, context) -> Decision` where `Decision = allow | deny | require_approval` + `reason`.
- Inputs: tool name, risk level, arguments summary, agent config (allowlist, autonomy), workspace/agent policy rules, run cost so far.
- Deny by default. Unknown tool ⇒ deny. Missing policy ⇒ most restrictive.
- 100% table-driven tests.

### 12.4 Tools
A tool = name + description + Pydantic input model (→ JSON schema for the LLM) + risk level + required scope + implementation.

| Tool | Risk | Default policy | Phase |
|---|---|---|---|
| `orbit.get_issue`, `orbit.search_issues`, `orbit.list_comments` | read | allow | P2 |
| `orbit.add_comment` | low | allow | P2 |
| `orbit.update_issue_fields` (labels, priority, estimate) | low | allow | P2 |
| `orbit.create_sub_issues` | medium | allow ≤ 5, else approval | P3 |
| `orbit.close_issue` | medium | approval | P2 |
| `repo.read_file`, `repo.search_code`, `repo.list_tree` | read | allow | P4 |
| `sandbox.exec`, `sandbox.write_file`, `sandbox.read_file` | medium (contained) | allow inside sandbox | P4 |
| `github.create_branch`, `github.push`, `github.open_pr` | medium | allow on `orbit-agent/*` only | P4 |
| `github.comment_pr` | low | allow | P4 |
| `github.merge_pr` | — | **does not exist as an agent tool** | — |

### 12.5 Tool router (OWNER-WRITTEN)
For every tool call, in order: tool exists? → in agent allowlist? → validate args against schema → policy decision → budget check → rate limit → execute (as agent actor via domain layer / provider) → record `tool_call` + `tool_result` steps (or `policy_denial`). Rejections return a **structured, model-readable error** so the model can adapt, and are audited.

### 12.6 Approvals
- Row in `approval` with exact action preview (e.g. comment text, list of sub-issues, PR diff summary).
- Surfaced on the issue page and the notifications list.
- Decision → domain write → outbox `approval.decided` → worker sends Temporal signal.
- Expiry → run `blocked`.

### 12.7 Budgets & metering (budget.py OWNER-WRITTEN)
- Levels: per run (hard cap), per agent per month, per workspace per month.
- **Pre-check before every LLM call** using an estimate of max cost; post-call record actual usage in `llm_usage` and increment run totals.
- Exceeded ⇒ run stops cleanly with `budget_exceeded`, summary comment posted, sandbox destroyed.
- Per-workspace concurrency limit (Redis semaphore) to prevent noisy neighbours.

### 12.8 LLM gateway
- Interface: `complete(request) -> response` and `stream(request) -> events`; provider adapters behind it.
- Retries with jitter on 429/5xx; fallback model chain from config; timeouts.
- Metering on every call; structured output validated with Pydantic; retry-with-feedback on validation failure (bounded).
- Prompt caching where the provider supports it.
- Never log full prompts containing user content at info level.

### 12.9 Context builder
- Sources: issue + comments + parent/sub-issues + linked PRs + team conventions (repo `AGENTS.md`/`CLAUDE.md`) + similar issues (pgvector) + repo map (coder).
- Token budget allocator trims lowest-priority sources first.
- Untrusted content (issue text, comments, PR bodies) is **clearly delimited** in the prompt and labelled as data, not instructions.
- Log which sources and how many tokens were used per run.

### 12.10 Sandbox (P4)
- `SandboxProvider` interface: `create`, `exec`, `write_file`, `read_file`, `destroy`. First impl: Docker.
- Fresh sandbox per run; repo cloned with a short-lived, repo-scoped GitHub App installation token; token never logged or written to disk inside the sandbox beyond git credential use.
- Limits: CPU, memory, wall time, output size. Network egress restricted to package registries + GitHub.
- Destroyed on run end or TTL; destroyed during long approval waits (recreate from checkpoint).
- Must not reach internal services (tested with a canary endpoint).

### 12.11 Kill switch
Disabling an agent: new runs rejected; in-flight runs receive cancel → sandbox destroyed → status `canceled` → audit entry.

---

## 13. Agents (templates)

Prompts are code: versioned in `agents/templates/`, reviewed, and evaluated before rollout.

| Agent | Trigger | Does | Output | Autonomy | Phase |
|---|---|---|---|---|---|
| Hello-world | manual | Reads issue, comments a plan, requests approval, adds a label | Proves the control plane | act_with_approval | P2 |
| **Triage** | issue created | Similar-issue search (dedupe), suggests labels/priority/assignee, asks a clarifying question if vague | One-click suggestions | suggest | P3 |
| **Spec** | mention / action | Rewrites issue: problem, scope, non-goals, acceptance criteria (Given/When/Then), edge cases, open questions | Proposed description diff | suggest | P3 |
| **Coder** | assignment | Plan → approval → sandbox: clone, read conventions, implement, test/lint until green or limits → push `orbit-agent/{identifier}-{slug}` → draft PR → summary | PR linked to issue | act_with_approval | P4 |
| **Reviewer** | PR opened/updated | Reviews diff vs acceptance criteria + conventions; inline comments; flags missing tests/security smells | Review comments; never approves merge | autonomous (comments only) | P4 |

Coder guardrails: max iterations, max diff size, forbidden paths (`infra/`, secrets, CI config), never touches default branch, never merges.

---

## 14. Evals (P3+)

- `evals/`: fixture issues (and fixture repos for coder) + expected outcomes + scoring.
- Runs agents headless through the real API; reports score, cost, latency per agent.
- Metrics: triage suggestion acceptance proxy, duplicate precision/recall; spec AC completeness; coder tests-pass rate, diff size, cost per success; reviewer true-issue rate.
- Run on every prompt/model change and nightly. **A score drop beyond threshold blocks rollout.**
- Reports committed to `docs/evals/` — they go in the README.

---

## 15. Non-functional requirements

| # | Area | Target | Enforced by |
|---|---|---|---|
| 1 | Tenant isolation | Cross-tenant suite green for every table | RLS + FORCE RLS + app role + tests |
| 2 | Agent safety | No agent merge/deploy; 100% side effects audited | Policy engine, tool router, no merge tool |
| 3 | Durability of runs | Crash-resume with zero duplicate side effects | Temporal + idempotent activities; explicit test |
| 4 | Idempotency | Retries are no-ops | `idempotency_key` table |
| 5 | Consistency | Server authoritative; one write path | Domain layer only writer |
| 6 | API latency | p95 < 300 ms | Heavy work via outbox; indexes; benchmarks |
| 7 | Failure isolation | LLM/sandbox outage never degrades tracker | Separate processes, timeouts, circuit breaking |
| 8 | Cost control | Hard budgets per run/agent/workspace | Budget pre-check before every LLM call |
| 9 | Security | No secrets in logs or agent context; hashed tokens | Code review + tests |
| 10 | Observability | Every request and run traceable | structlog + OTel with correlation IDs |
| 11 | DX | `make dev` one command; `make check` green | Makefile + CI |

---

## 16. Scalability guardrails (cheap now, essential later)

- `workspace_id` on every tenant row → sharding by workspace stays possible.
- API processes stateless → horizontal scale.
- Every vendor behind an interface (LLM, sandbox, code host).
- High-growth tables (`agent_run_step`, `issue_history`, `llm_usage`, `outbox`) designed for monthly partitioning + archival.
- Large blobs in object storage, not Postgres or Temporal history.
- Per-workspace concurrency limits and fair queueing for agent runs.
- Known bottleneck order at scale: LLM provider rate limits → sandbox capacity/cost → audit table growth → realtime fan-out → Postgres writes.

---

## 17. Phases & tickets

Every phase ends with a **demo** = its exit criterion. Tickets marked **(O)** are owner-written (§3).

### P0 — Foundations (≈2 weeks)
Exit: sign up with GitHub → create workspace → invite teammate who joins; cross-tenant suite green in CI.

| ID | Ticket | Acceptance |
|---|---|---|
| T-001 | Scaffold: uv project, FastAPI app factory + lifespan, `/healthz`, ruff, mypy strict, pytest + httpx test, Makefile | `make check` green; `/healthz` 200; clean shutdown on Ctrl+C |
| T-002 | Infra: docker-compose Postgres 16 + pgvector, Redis, healthchecks, `make infra-up/down` | Services healthy |
| T-003 | Config: pydantic-settings `Settings`, `.env.example` | Missing required var ⇒ startup fails with clear message |
| T-004 | Logging: structlog JSON, request-ID middleware, context binding | One structured log line per request with request ID |
| T-005 | DB: SQLAlchemy Core async engine, Alembic, two roles, tables §8.1–8.2, UUIDv7 ids | upgrade + downgrade in CI; app connects as `orbit_app` |
| T-006 **(O)** | RLS policies + `with_tenant()` + `tests/security/test_cross_tenant.py` | All 4 checks in §9 pass |
| T-007 | Auth: GitHub OAuth with state, hashed sessions, cookie, logout, Origin check | Login/logout/revoke tests pass |
| T-008 | Workspace create (+ first team, default states later) + invites | Invite → second user joins; expired/used token rejected |
| T-009 | CI: GitHub Actions running `make check` with real Postgres | Green on main |
| — **(O)** | ADR-0001, ADR-0002 | Merged |

### P1 — Thin tracker (≈2 weeks)
Exit: Orbit's own backlog is tracked in Orbit.

| ID | Ticket | Acceptance |
|---|---|---|
| T-101 | Workflow states: table, defaults seeded on team create | Unit + integration tests |
| T-102 | Authz: `can(actor, action, resource)` matrix for all roles | Table-driven tests for every role × action |
| T-103 | Issue schema + atomic numbering | Concurrent creates yield unique sequential numbers |
| T-104 | Issue domain handlers: create/update/archive + history + outbox | History row per changed field; outbox row per event |
| T-105 | Idempotency key middleware + table + cleanup job | Duplicate key = same response; different body = 409 |
| T-106 | Comments + @mentions + subscribers | Mention creates subscriber + outbox event |
| T-107 **(O)** | Outbox worker with SKIP LOCKED, backoff, dead-letter | Two workers never process the same row; failed jobs retried |
| T-108 | Issue API routes (list with filter/sort + cursor, get by UUID or identifier) | API tests incl. pagination |
| T-109 | Minimal web UI: issue list, issue page, comment box (or HTMX/Jinja if faster — decide in plan) | Playwright/HTTP smoke test |

### P2 — Agent control plane (≈4 weeks) ★
Exit: hello-world agent assigned → plan comment → approval → tool calls → done; crash-resume, budget-cutoff and disallowed-tool tests green.

| ID | Ticket | Acceptance |
|---|---|---|
| T-200 **(O)** | ADR-0003 (Temporal vs own), ADR-0004 (policy model), ADR-0005 (gateway) | Merged |
| T-201 | Agent entity + actor + templates + enable/disable | Agent assignable; disabled agent rejects new runs |
| T-202 | Temporal in docker-compose + agent worker process skeleton | Worker connects; sample workflow runs |
| T-203 | LLM gateway: provider interface, one provider, retries, fallback, metering, structured output | Cost recorded per call; provider swap is config |
| T-204 **(O)** | Budget enforcement (run/agent/workspace) + concurrency semaphore | Budget exceeded ⇒ `budget_exceeded`, clean stop |
| T-205 **(O)** | Policy engine | Table-driven tests: allow/deny/require_approval incl. unknown tool |
| T-206 | Tool registry + Orbit tools (get_issue, search, add_comment, update_fields, close_issue) | Schemas generated from Pydantic models |
| T-207 **(O)** | Tool router | Disallowed tool rejected with model-readable error + audited |
| T-208 **(O)** | Agent run workflow + state machine + approval signal + timeouts | Crash-resume test: kill worker mid-run ⇒ resumes, no duplicate comments |
| T-209 | Triggers: assignment + mention → outbox → start workflow | Each trigger starts exactly one run |
| T-210 | Approvals: model, API, decision → signal, expiry | Approve resumes run; reject cancels |
| T-211 | Context builder v1 + composition logging | Sources and token counts logged per run |
| T-212 | Run timeline API + UI (steps, costs, latency, cancel) | Every step visible with cost |
| T-213 | Kill switch | Disabling agent cancels in-flight runs cleanly |

### P3 — First agents + evals (≈3 weeks)
Exit: new issue gets one-click triage suggestions; eval report with scores committed.

| ID | Ticket | Acceptance |
|---|---|---|
| T-301 | Embedding side table + async embedding job + similar-issues query | Similar issues returned for a fixture |
| T-302 | Eval harness skeleton (`evals/`), fixtures format, runner, report | Report generated locally and in CI (nightly) |
| T-303 | Triage agent + suggestion chips (accept/reject) | Meets eval threshold on fixture set |
| T-304 | Spec agent + description diff accept/edit | Meets eval threshold |
| T-305 | `orbit.create_sub_issues` tool with approval above limit | Policy test + integration test |

### P4 — Code agents (≈4.5 weeks)
Exit: issue → coder agent PR on a real repo → reviewer comments → human merges → issue Done.

| ID | Ticket | Acceptance |
|---|---|---|
| T-400 **(O)** | ADR-0006 (sandbox), ADR-0007 (GitHub identity) | Merged |
| T-401 | GitHub App: installation flow, webhook endpoint with signature verification, events → outbox | Signature tests; replayed delivery is idempotent |
| T-402 | Git automations: branch/PR/merge ↔ issue links and state changes | E2E on a test repo within seconds of webhook |
| T-403 | Sandbox provider interface + Docker provider + limits + egress rules | Egress canary test: internal endpoint unreachable |
| T-404 | Repo + sandbox + GitHub tools | Tools restricted to `orbit-agent/*` branches |
| T-405 | Coder agent template + guardrails | Opens PR on fixture repo; forbidden paths rejected |
| T-406 | Reviewer agent | Posts inline comments; cannot approve/merge |
| T-407 | Coder/reviewer evals on fixture repos | Report committed |

### P5 — External access (≈1.5 weeks)
Exit: Claude Code works an Orbit ticket via MCP.

| ID | Ticket | Acceptance |
|---|---|---|
| T-501 | API keys: create/revoke, hashed, scopes, last_used | Scope enforcement tests |
| T-502 | MCP server: list_my_issues, get_issue, create_issue, update_issue, add_comment, search_issues | External agent CLI updates an issue |
| T-503 | Rate limits per key (Redis token bucket) | 429 with Retry-After |

### P6 — Portfolio polish (≈1.5 weeks)
Exit: a senior engineer understands what Orbit does, why it's designed this way, and that it works — in 2 minutes.

| ID | Ticket | Acceptance |
|---|---|---|
| T-601 | README: pitch, demo GIF of Flow B, architecture diagram, key decisions → ADR links, quickstart | Reviewed by owner |
| T-602 | Benchmarks (`docs/benchmarks.md`): API p95, outbox throughput, cost per run | Numbers reproducible via script |
| T-603 **(O)** | Deep dives: tenancy with RLS; crash-safe agent runs; policy outside the model | Published in `docs/deep-dives/` |
| T-604 | Small live deployment with seeded demo workspace | Public URL works |

### Later (do not start before P6 exit)
Realtime sync + offline · projects/cycles/board/triage inbox · notifications inbox · webhooks out · v2 delivery pipeline (PROJECT.md §14A).

---

## 18. Testing strategy

| Level | Scope | Notes |
|---|---|---|
| Unit | domain logic, authz, policy engine, state machine, budget math | Table-driven (`pytest.mark.parametrize`) |
| Integration | API + real Postgres (testcontainers or CI service), outbox worker, RLS | Never SQLite |
| Security | `tests/security/`: cross-tenant, disallowed tools, no merge tool, token hashing, sandbox egress | Must never be skipped |
| Workflow | Temporal test environment: signals, timeouts, replay, crash-resume | Deterministic time |
| E2E | Core journeys (login → issue → agent run) | Later: Playwright |
| Evals | Agent quality | `evals/`, nightly |

Rules: test names describe behaviour (`test_select_other_tenant_returns_no_rows`); fixtures create workspaces via domain functions, not raw inserts (except in RLS tests); no network calls to real LLMs in unit/integration tests — use a fake provider.

---

## 19. Python conventions

- Full type hints; mypy strict passes. `Any` requires a comment explaining why.
- Async all the way in request and worker paths; no blocking I/O inside `async def` (wrap unavoidable blocking calls in a thread).
- Pydantic models for all external input/output (API bodies, tool args, LLM structured outputs, config).
- Dependencies passed explicitly (FastAPI dependencies / constructor args). No module-level mutable globals except the settings instance.
- Small modules organised by domain. Functions do one thing.
- Errors: raise typed domain errors; never catch `Exception` without re-raising or logging with context.
- Time: always timezone-aware UTC (`datetime.now(UTC)` outside workflows).
- IDs: `platform/ids.py` provides UUIDv7 generation (use stdlib if the Python version supports it, else one small library — note which in the plan).
- Docstrings on public domain functions: what, why, invariants.

---

## 20. Security rules

- Never log secrets, tokens, cookies, API keys, or full prompts with user content at info level.
- Store only hashes of session tokens, invite tokens and API keys.
- Secrets come from environment/secrets manager, never committed. `.env` is gitignored; `.env.example` has names only.
- All user-supplied markdown is sanitized at render time.
- Outbound HTTP to user-supplied URLs (later: webhooks, link unfurls) blocks private IP ranges (SSRF).
- Agent context never contains secrets; issue/comment/PR text is untrusted data.
- GitHub tokens: short-lived installation tokens, repo-scoped, per run.
- Dependency audit (`pip-audit`) in CI once dependencies stabilise.

---

## 21. Observability

- Log fields: `request_id`, `workspace_id`, `actor_id`, `run_id`, `step_seq`, `event_type`.
- Spans: HTTP request → domain call → DB; outbox dispatch; workflow → activity → LLM call / tool call.
- Metrics (as they become relevant): request rate/errors/latency per route; outbox depth and oldest-age; runs by status; cost per run/agent/workspace; tool error rate; approval wait time; sandbox start latency.

---

## 22. Commands

| Command | Does |
|---|---|
| `make infra-up` / `make infra-down` | Start/stop Postgres, Redis (Temporal from P2, MinIO when needed) |
| `make dev` | Run API with reload |
| `make worker` | Run outbox worker (P1+) |
| `make agent-worker` | Run Temporal agent worker (P2+) |
| `make migrate` | Apply migrations as `orbit_migrator` |
| `make lint` / `make typecheck` / `make test` | ruff / mypy strict / pytest |
| `make check` | lint + typecheck + test — **must be green before every commit** |
| `make evals` | Run agent evals (P3+) |

---

## 23. Git conventions

- Branch per ticket: `t-006-rls-tenant`.
- Conventional Commits referencing the ticket: `feat(store): with_tenant helper (T-006)`.
- Small commits. One ticket per PR. PR description: what, why, how tested, follow-ups.
- Tag a release at each phase exit: `v0.1.0` (P0), `v0.2.0` (P1), ...

---

## 24. Definition of done (every ticket)

- [ ] Acceptance criteria met and covered by tests
- [ ] `make check` green (ruff, mypy strict, pytest)
- [ ] New tenant tables have RLS + cross-tenant tests
- [ ] New mutations go through `domain/`, write history + outbox, support idempotency
- [ ] Logs/spans added for new server paths
- [ ] No secrets in logs; no new dependency without justification
- [ ] ADR flagged if a non-obvious decision was made
- [ ] §2 Current status updated

---

## 25. Known pitfalls

1. App connecting as table owner/superuser ⇒ RLS silently bypassed.
2. Using `SET` (session) instead of transaction-local config ⇒ tenant leaks across pooled connections.
3. Forgetting `WITH CHECK` ⇒ writes into another tenant allowed.
4. Writing side effects after commit (dual write) instead of via outbox.
5. Non-idempotent outbox handlers or activities ⇒ duplicate comments/PRs on retry.
6. I/O or `datetime.now()` inside Temporal workflow code ⇒ non-determinism errors on replay.
7. Large LLM outputs stored in workflow history ⇒ history bloat; store in object storage.
8. Tests passing against SQLite or against the migrator role ⇒ false confidence.
9. Trusting LLM output as instructions or valid JSON without validation.
10. Blocking calls (sync HTTP, `time.sleep`) inside async code.
11. OAuth callback without `state` verification ⇒ login CSRF.
12. Letting prompt text decide permissions instead of the policy engine.