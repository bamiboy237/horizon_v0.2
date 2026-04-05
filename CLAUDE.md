# Horizon v0.2 — Agent Rules

## Project Overview

Academic opportunity discovery platform. Students create profiles, search a curated DB of scholarships/fellowships/research programs, and trigger AI-powered deep research to find new opportunities on the web.

**v0.2 is a backend-only rebuild** of v0.1 (`~/Desktop/horizon_v0.1`). Full spec at `SPEC.md`.

---

## Tech Stack (Locked)

| Layer | Choice |
|-------|--------|
| Database | Neon (managed PostgreSQL + pgvector) |
| Auth | Clerk — backend verifies RS256 JWTs only |
| Query layer | asyncpg (raw SQL — NO ORM) |
| Migrations | Alembic |
| Chat agent | PydanticAI |
| Research agent | LangGraph (interrupt() for human-in-the-loop) |
| LLM | Provider-agnostic (`google-gla:gemini-2.5-flash` default) |
| Background jobs | ARQ (async Redis queue) |
| Streaming | SSE (text/event-stream) |
| Package manager | uv |
| Logging | structlog |

**Frontend (separate spec, not started):** Next.js 14, TypeScript, Tailwind CSS 4, shadcn/ui

---

## Project Structure

```
backend/
├── main.py                    # FastAPI app, lifespan (pool init)
├── app/
│   ├── api/
│   │   ├── auth.py            # POST /webhooks/clerk
│   │   ├── profile.py         # GET/PUT /api/profile
│   │   ├── chat.py            # GET /api/chat/stream (SSE)
│   │   ├── search.py          # GET /api/search, /api/opportunities/{id}
│   │   ├── research.py        # GET /api/research/{id}/stream, POST /respond
│   │   └── email.py
│   ├── core/
│   │   ├── config.py          # pydantic-settings
│   │   ├── security.py        # get_current_user (Clerk JWT verify)
│   │   ├── database.py        # asyncpg pool management
│   │   ├── logging.py         # structlog setup
│   │   ├── exceptions.py
│   │   └── embeddings.py      # embed_text() — injectable, provider-agnostic
│   ├── agents/
│   │   ├── chat_agent.py      # PydanticAI agent + tools
│   │   └── research/
│   │       ├── graph.py       # LangGraph main graph
│   │       ├── nodes.py       # decompose, search, extract, reflect
│   │       ├── state.py       # TypedDict states
│   │       └── tools.py       # ConductSearch, SearchComplete schemas
│   ├── services/
│   │   ├── search.py          # pgvector queries
│   │   ├── recommendations.py # multi-signal scorer
│   │   ├── deduplication.py   # URL normalize + embedding similarity
│   │   ├── validation.py      # data quality checks
│   │   └── email_service.py   # Resend integration
│   └── models/
│       └── schemas.py         # Pydantic request/response models
├── worker.py                  # ARQ worker
├── migrations/                # Alembic
├── pyproject.toml
└── .env.example
```

---

## Development Commands

```bash
cd backend

# Install
uv sync

# Dev server
uv run fastapi dev main.py

# Tests
uv run pytest
uv run pytest --cov=app --cov-report=term-missing

# Type check
uv run mypy .

# Lint/format
uv run ruff check .
uv run ruff format .

# Migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

---

## Build Order

| Step | What | Done when |
|------|------|-----------|
| 1 | Neon DB + Alembic migrations | `alembic upgrade head` succeeds |
| 2 | FastAPI skeleton + asyncpg pool + health endpoint | `GET /health` returns 200 |
| 3 | Clerk auth: webhook + JWT verify | Sign up → profile row created |
| 4 | Profile CRUD + onboarding gate | `onboarding_complete=TRUE` |
| 5 | Opportunity seed: embed + insert 50 test records | Opportunities in DB with embeddings |
| 6 | Search: pgvector + eligibility filter | Ranked results from `GET /api/search` |
| 7 | Chat agent: PydanticAI + search_tool + SSE | Streaming chat from DB |
| 8 | Conversation history: sliding window | Multi-turn chat persists |
| 9 | Research pipeline: LangGraph + Tavily + Firecrawl | Discovers new opportunities |
| 10 | Human-in-the-loop: interrupt() + SSE + POST respond | User can steer research |
| 11 | Recommendations: multi-signal scorer | Personalized homepage feed |
| 12 | User signals + interaction embedding updates | Interactions shift recommendations |
| 13 | ARQ worker: deadline expiry + email digest | Background jobs on schedule |
| 14 | Rate limiting + API budget protection | Research: 3/day/user |

---

## Coding Standards

### Universal Rules
- **Functions < 50 lines** — extract helpers early
- **Files < 800 lines** — organize by feature/domain
- **No deep nesting** (> 4 levels) — use early returns
- **Immutable patterns** — never mutate, always return new objects
- **No hardcoded secrets** — use environment variables
- **Explicit error handling** — no bare `except:`, no swallowed exceptions
- **Input validation** at every system boundary
- **No `print()`** — use structlog

### Python (PEP 8 + extras)
- Type annotations on **all** function signatures (public and internal)
- Use `dataclass(frozen=True)` or `NamedTuple` for immutable data
- Prefer list comprehensions over loops for transformations
- `"".join()` not string concatenation in loops
- `value is None` not `value == None`
- **asyncpg**: always use parameterized queries (`$1, $2`), never f-strings in SQL
- **FastAPI**: validate all request bodies with Pydantic, use response models
- **PydanticAI**: structured output, inject deps, no global state
- **LangGraph**: keep state as TypedDict, nodes as pure functions

### TypeScript (when frontend starts)
- Explicit return types on exported functions
- `interface` for extendable shapes, `type` for unions/utilities
- `unknown` over `any` — always narrow safely
- Zod for all external/user input validation
- `const` by default, `let` only when reassignment needed

### Security Checklist (before every commit)
- [ ] No hardcoded secrets (API keys, tokens, connection strings)
- [ ] SQL uses parameterized queries (`$1` placeholders)
- [ ] All user inputs validated with Pydantic
- [ ] Auth checked on every protected route
- [ ] Rate limiting on all public endpoints
- [ ] Error messages don't leak internals
- [ ] Logs don't contain PII or secrets

### Testing Requirements
- Minimum **80% coverage**
- Write tests **before** implementation (TDD)
- Unit tests: individual functions in isolation
- Integration tests: API endpoints + DB operations
- Mock all external services (Clerk, Tavily, Firecrawl, LLM)
- Test error paths, not just happy path

---

## Agent Usage Guide

Agents are in `.zed/agents/` — @-mention them in context when needed.

| Task | Use |
|------|-----|
| Planning a new feature | `@planner` — creates phased implementation plan |
| System/architecture design | `@architect` — designs components and trade-offs |
| After writing any code | `@code-reviewer` — MUST be used before moving on |
| Security-sensitive code | `@security-reviewer` — auth, API endpoints, DB queries |
| Starting any new feature | `@tdd-guide` — enforces write-tests-first |
| Python code changes | `@python-reviewer` — PEP8, type hints, asyncio patterns |
| TypeScript/Next.js code | `@typescript-reviewer` — type safety, React patterns |
| DB migrations/queries | `@database-reviewer` — query optimization, indexes, RLS |
| Performance issues | `@performance-optimizer` — profiles bottlenecks |

### Development Workflow (from everything-claude-code)
1. **Research first** — search Context7 for library docs before implementing
2. **Plan** → use `@planner` to create implementation plan
3. **TDD** → use `@tdd-guide` to write tests first (RED)
4. **Implement** → write minimal code to pass tests (GREEN)
5. **Review** → use `@code-reviewer` and `@python-reviewer` (IMPROVE)
6. **Security** → use `@security-reviewer` for any auth/API/DB changes
7. **Commit** → conventional commits format (`feat:`, `fix:`, `refactor:`)

---

## Environment Variables

Backend `.env`:
```
# Neon
DATABASE_URL=postgresql+asyncpg://user:pass@host/db
DATABASE_URL_POOLED=postgresql+asyncpg://user:pass@host/db?pgbouncer=true

# Clerk
CLERK_SECRET_KEY=sk_...
CLERK_WEBHOOK_SIGNING_SECRET=whsec_...

# LLM
GEMINI_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...  # optional fallback

# Search
TAVILY_API_KEY=tvly-...
FIRECRAWL_API_KEY=fc-...

# Embeddings
EMBEDDING_MODEL=text-embedding-004

# Background jobs
REDIS_URL=redis://localhost:6379/0

# Email
RESEND_API_KEY=re_...
FROM_EMAIL=noreply@horizon.app

# Observability
LOG_LEVEL=INFO
ENVIRONMENT=development
LANGSMITH_API_KEY=lsv2_...   # optional
```

---

## Key Architectural Constraints

1. **Raw asyncpg only** — no SQLAlchemy ORM, no Tortoise, no SQLModel
2. **Clerk for auth** — backend only verifies JWT, never manages passwords
3. **Provider-agnostic LLM** — swap model string, don't change code
4. **inject embeddings** — `embed_text()` is a dep, never call provider SDK directly
5. **SSE not WebSocket** — use `text/event-stream` for all streaming
6. **LangGraph interrupts** — research agent must support human-in-the-loop via `interrupt()`
7. **ARQ for background jobs** — never block the FastAPI event loop with heavy work

---

## MCP Tools Available (Zed)

**Global:** context7 (library docs), supabase, shadcn  
**Project:** sequential-thinking (complex reasoning), memory (session notes), playwright (E2E tests), firecrawl (scraping tests)

Use Context7 to look up: `asyncpg`, `PydanticAI`, `LangGraph`, `Alembic`, `Clerk`, `ARQ`, `Neon pgvector`
