# Horizon v0.2 — Backend Technical Spec

## Context

Horizon is an academic opportunity discovery platform. Students create profiles, search a curated database of scholarships/fellowships/research programs, and trigger AI-powered deep research to find new opportunities on the web.

v0.1 (at `~/Desktop/horizon_v0.1`) works but has fundamental architectural problems: conversation history stored in Redis with fragile optimistic locking, ML models loaded in-process (500MB SentenceTransformer), two incompatible agent frameworks (PydanticAI + LangGraph), no human-in-the-loop for research, and Supabase coupling for auth + database.

**This spec is the rebuild blueprint.** Backend only. Creates a new project at `~/Desktop/horizon_v0.2/`.

---

## Decisions (locked in)

| Decision | Choice | Why |
|----------|--------|-----|
| Database | Neon (managed PostgreSQL + pgvector) | pgvector built-in, branching, free tier, connection pooling included |
| Auth | Clerk | Handles JWT/MFA/social login; backend just verifies RS256 tokens |
| Query layer | asyncpg (raw SQL) | Fastest async Postgres driver for Python; no ORM overhead |
| Migrations | Alembic | Standard, versionable, works with asyncpg |
| Chat agent | PydanticAI | Structured output, clean tool calling, provider-agnostic model abstraction |
| Research agent | LangGraph | interrupt() for human-in-the-loop, PostgresSaver for durable checkpoints |
| LLM | Provider-agnostic | PydanticAI model string (swap `google-gla:gemini-2.5-flash` ↔ `anthropic:claude-sonnet-4-6` without code changes) |
| Embeddings | Configurable | Inject embed function as dependency; default to `text-embedding-004` (768d) |
| Background jobs | ARQ (async Redis queue) | Native asyncio, separate worker process, cron support |
| Streaming | SSE (text/event-stream) | No WebSocket needed; works with Next.js fetch |
| Package manager | uv | Keep from v0.1 |

---

## 1. Database Schema

### Core Tables

```sql
-- Clerk user_id is the primary key (e.g. "user_2abc...")
CREATE TABLE profiles (
    id VARCHAR(255) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255),
    institution VARCHAR(255),
    institution_type VARCHAR(50),
    major VARCHAR(255),
    cip_code VARCHAR(10),
    gpa DECIMAL(3,2),
    graduation_year INTEGER,
    citizenship VARCHAR(100),
    state_residence VARCHAR(50),
    first_generation BOOLEAN DEFAULT FALSE,
    ethnicity TEXT[],
    goals TEXT[],
    interests TEXT[],
    career_aspirations TEXT[],
    onboarding_complete BOOLEAN DEFAULT FALSE,
    profile_embedding vector(768),
    interaction_embedding vector(768),
    embedding_model VARCHAR(50) DEFAULT 'text-embedding-004',
    email_digest_enabled BOOLEAN DEFAULT TRUE,
    email_digest_frequency VARCHAR(20) DEFAULT 'weekly',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_url TEXT,
    normalized_url TEXT UNIQUE,
    title VARCHAR(500) NOT NULL,
    organization VARCHAR(255) NOT NULL,
    opportunity_type VARCHAR(50) NOT NULL,
    location VARCHAR(255),
    funding_amount VARCHAR(100),
    funding_type VARCHAR(50),
    citizenship_required TEXT[],
    gpa_minimum DECIMAL(3,2),
    major_requirements TEXT[],
    major_cip_requirements TEXT[],
    institution_types TEXT[],
    demographic_requirements JSONB,
    eligibility_text TEXT,
    deadline TIMESTAMPTZ,
    application_url TEXT,
    required_materials TEXT[],
    estimated_prep_hours INTEGER,
    description TEXT,
    embedding vector(768),
    embedding_model VARCHAR(50) DEFAULT 'text-embedding-004',
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(title,'') || ' ' ||
            coalesce(description,'') || ' ' ||
            coalesce(eligibility_text,''))
    ) STORED,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    last_verified TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_opp_embedding ON opportunities USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_opp_search ON opportunities USING gin(search_vector);
CREATE INDEX idx_opp_active_deadline ON opportunities(is_active, deadline);
CREATE INDEX idx_opp_type ON opportunities(opportunity_type);
CREATE INDEX idx_opp_normalized_url ON opportunities(normalized_url);
```

### Conversation Tables

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,  -- user, assistant, tool_call, tool_result
    content TEXT,
    tool_calls JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_messages_session ON messages(session_id, created_at);
```

### Application Tracking + Signals

```sql
CREATE TABLE applications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
    opportunity_id UUID REFERENCES opportunities(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'interested',
    outcome VARCHAR(50),
    outcome_date TIMESTAMPTZ,
    user_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, opportunity_id)
);

CREATE TABLE user_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
    opportunity_id UUID REFERENCES opportunities(id) ON DELETE CASCADE,
    signal_type VARCHAR(30) NOT NULL,
    strength FLOAT NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, opportunity_id, signal_type)
);
CREATE INDEX idx_signals_user ON user_signals(user_id, created_at DESC);
```

### Research Sessions

```sql
CREATE TABLE research_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) REFERENCES profiles(id),
    session_id UUID REFERENCES sessions(id),
    status VARCHAR(20) DEFAULT 'running',
    checkpoint JSONB,
    queries_executed TEXT[],
    opportunities_found INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Email Logs

```sql
CREATE TABLE email_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
    email_type VARCHAR(50) NOT NULL,
    opportunity_ids UUID[],
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    status VARCHAR(20) NOT NULL,
    resend_id VARCHAR(100)
);
```

---

## 2. Auth (Clerk)

- Clerk owns identity (email, password, sessions, MFA, social login)
- Backend verifies Clerk JWTs with RS256 public key — no Clerk SDK on backend
- Profile created via eager webhook (`user.created` → POST /webhooks/clerk)
- Webhook verified with svix library (signature check)
- `onboarding_complete` gate: endpoints needing personalization return 403 until true

**Dependency chain:**
```
get_current_user(token) → user_id
get_profile(user_id)    → Profile (with onboarding check)
require_onboarding()    → raises 403 if not complete
```

---

## 3. Chat Agent (PydanticAI)

### Agent setup
- Provider-agnostic: model string from env var (`CHAT_MODEL=google-gla:gemini-2.5-flash`)
- Structured output: `ChatMessage | ClarifyingQuestion | OpportunitiesResult` (discriminated union)
- Streaming: `agent.run_stream()` over SSE endpoint `GET /api/chat/stream`

### Tools

| Tool | Purpose |
|------|---------|
| `search_opportunities(query, filters?)` | pgvector similarity + eligibility SQL |
| `get_more_results()` | Cursor from `session.metadata` JSONB |
| `get_opportunity_details(id)` | Full record + eligibility explanation |
| `start_research(query)` | Creates research_session, fires LangGraph in background |

### Conversation History
- Stored in Postgres `messages` table (not Redis)
- Load last 20 messages for agent context
- After 40+ messages: summarize oldest 20 with one LLM call, store summary in `sessions.metadata.history_summary`, delete old rows
- Tool call + result always stored as a pair in `tool_calls JSONB`

### Search Query (replaces entire matching engine)
```sql
SELECT o.*,
    1 - (o.embedding <=> $1) AS semantic_score,
    (o.citizenship_required IS NULL OR $2 = ANY(o.citizenship_required)) AND
    (o.gpa_minimum IS NULL OR $3 >= o.gpa_minimum) AS is_eligible
FROM opportunities o
WHERE o.is_active = TRUE
ORDER BY is_eligible DESC, 1 - (o.embedding <=> $1) DESC
LIMIT $4;
```

---

## 4. Deep Research Agent (LangGraph)

### Architecture
```
decompose_query → [interrupt: approve queries]
    → search_node (Tavily + Exa parallel)
    → extract_node (Firecrawl + Jina fallback)
    → parse_node (PydanticAI structured extraction)
    → reflect_node → [interrupt: approve/redirect/stop]
    → loop or complete
```

### Key design choices

- **Checkpointer:** `AsyncPostgresSaver` (Neon) — survives restarts
- **Thread ID:** `research_session.id` — enables time travel
- **Transport:** SSE at `GET /api/research/{id}/stream` for progress events
- **Human input:** `POST /api/research/{id}/respond` resumes via `Command(resume=...)`

### Interrupt points

**After decompose:** "I'll search for [X, Y, Z]. Edit, add, or approve."
**After reflect:** "Found N. Gaps: [X]. Next: [Y]. Continue / redirect / stop?"

### Search layer

| Provider | Role |
|----------|------|
| Tavily | Keyword-precise searches (known program names, deadlines) |
| Exa | Semantic/neural search (similar programs, broad discovery) |

Run both in parallel per query. Deduplicate by URL.

### Extraction layer

1. Firecrawl (primary): better markdown, 15s timeout per page
2. Jina Reader (fallback): free (`r.jina.ai/{url}`), 10s timeout
3. PydanticAI structured extraction: `Agent(output_type=ExtractedOpportunities)` — no manual JSON parsing

### Deduplication (two layers)

1. URL normalization before insert (strip UTM params, trailing slashes)
2. Embedding similarity check: `embedding <=> candidate < 0.06` = duplicate → merge instead of insert

### Data quality

Validate extracted data before insert:
- Reject titles < 5 chars or starting with "Apply"
- Normalize citizenship strings ("us citizen" → "US Citizen")
- Parse messy deadlines to ISO format or NULL

---

## 5. Recommendation System

### Multi-signal scoring

```
final_score = (
    0.40 * semantic_score     # profile/taste embedding <-> opportunity embedding
  + 0.25 * eligibility_score  # 1.0 eligible, 0.5 unknown, 0.0 ineligible
  + 0.20 * temporal_score     # deadline buffer quality
  + 0.10 * social_score       # what similar students saved/applied
  + 0.05 * freshness_score    # recently discovered
)
```

### Cold start → warm user transition

- **0 interactions:** use `profile_embedding` (built at onboarding)
- **3+ interactions:** blend profile (60%) + interaction (40%)
- **10+ interactions:** blend profile (30%) + interaction (70%)

### Interaction embedding

Updated as background task on every save/apply:
- Weighted average of embeddings from positively-interacted opportunities
- Weights: viewed=0.1, saved=0.5, applied=1.0, outcome_accepted=2.0, dismissed=-0.3

### Social score (lightweight collaborative filtering)

```sql
SELECT opportunity_id, SUM(strength) AS social_score
FROM user_signals
WHERE user_id IN (
    SELECT id FROM profiles
    ORDER BY profile_embedding <=> $1
    LIMIT 50
)
GROUP BY opportunity_id;
```

Works from day 1 (returns empty). Becomes useful at 500+ users with signal history.

### Temporal score

Optimal window = 8-45 days of buffer (days until deadline minus estimated prep days).
Under 3 days buffer = too late (0.1). Over 120 days = far future (0.4). No deadline = neutral (0.5).

---

## 6. Background Jobs (ARQ)

Separate worker process: `uv run arq worker.WorkerSettings`

| Job | Trigger | Frequency |
|-----|---------|-----------|
| Expire past-deadline opportunities | Cron | Every 6 hours |
| Verify opportunity URLs (HEAD check) | Cron | Weekly |
| Sweep unverified 90-day records | Cron | Daily |
| Send email digest (per-user parallelized) | Cron | Monday 8am |
| Update interaction embedding | Event (save/apply) | Immediate |
| Re-embed all on model upgrade | Migration | One-time |
| Clean expired research sessions | Cron | Daily |

---

## 7. Rate Limiting & API Budget

### Per-user limits
- Research: 3 sessions/day (Redis counter, 24h TTL)
- Chat: 60 messages/hour
- SSE connections: max 3 open streams per user

### API budget protection (global daily caps)
```
tavily: 500 calls/day
exa: 300 calls/day
firecrawl: 200 calls/day
```
Redis counter per API per day. Research pipeline checks before each call. Prevents runaway costs from bugs or abuse.

---

## 8. Security Checklist

- [ ] Clerk webhook: verify svix signature on every POST /webhooks/clerk
- [ ] Research session ownership: always `WHERE id=$1 AND user_id=$2`
- [ ] SSE connection limit: prevent file descriptor exhaustion
- [ ] Parameterized queries only (asyncpg enforces this)
- [ ] No secrets in code: all via env vars, `.env` in .gitignore
- [ ] embedding_model tracked per row: prevents cross-model cosine bugs on upgrade

---

## 9. Observability

- **Structured logging:** structlog (keep from v0.1)
- **Agent tracing:** LangSmith for research pipeline
- **Metrics to track:** research success rate (≥10 valid opps per session), search click-through rate, recommendation engagement, embedding backfill progress

---

## 10. Project Structure

```
horizon_v0.2/
├── backend/
│   ├── main.py                    # FastAPI app, lifespan (pool init)
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py            # POST /webhooks/clerk
│   │   │   ├── profile.py         # GET/PUT /api/profile
│   │   │   ├── chat.py            # GET /api/chat/stream (SSE)
│   │   │   ├── search.py          # GET /api/search, /api/opportunities/{id}
│   │   │   ├── research.py        # GET /api/research/{id}/stream, POST /respond
│   │   │   └── email.py           # email preferences
│   │   ├── core/
│   │   │   ├── config.py          # pydantic-settings
│   │   │   ├── security.py        # get_current_user (Clerk JWT verify)
│   │   │   ├── database.py        # asyncpg pool management
│   │   │   ├── logging.py         # structlog setup
│   │   │   ├── exceptions.py      # custom errors
│   │   │   └── embeddings.py      # embed_text() — injectable, provider-agnostic
│   │   ├── agents/
│   │   │   ├── chat_agent.py      # PydanticAI agent + tools
│   │   │   └── research/
│   │   │       ├── graph.py       # LangGraph main + supervisor
│   │   │       ├── nodes.py       # decompose, search, extract, parse, reflect
│   │   │       ├── state.py       # TypedDict states
│   │   │       └── tools.py       # ConductSearch, SearchComplete schemas
│   │   ├── services/
│   │   │   ├── search.py          # pgvector queries
│   │   │   ├── recommendations.py # multi-signal scorer
│   │   │   ├── deduplication.py   # URL normalize + embedding similarity
│   │   │   ├── validation.py      # opportunity data quality checks
│   │   │   └── email_service.py   # Resend integration
│   │   └── models/
│   │       └── schemas.py         # Pydantic request/response models
│   ├── worker.py                  # ARQ worker settings + job functions
│   ├── migrations/                # Alembic
│   ├── pyproject.toml
│   └── .env.example
└── SPEC.md                        # this document
```

---

## 11. Build Order

Build and test in this order. Each step has a clear "done when" checkpoint.

| Step | What | Done when |
|------|------|-----------|
| 1 | Neon DB + Alembic migrations + all tables created | `alembic upgrade head` succeeds |
| 2 | FastAPI skeleton + asyncpg pool + health endpoint | `GET /health` returns 200 |
| 3 | Clerk auth: webhook + JWT verify + get_current_user | Sign up on frontend → profile row created |
| 4 | Profile CRUD: GET/PUT /api/profile + onboarding gate | Complete onboarding → `onboarding_complete=TRUE` |
| 5 | Opportunity seed: embed + insert 50 test opportunities | Opportunities visible in DB with embeddings |
| 6 | Search: pgvector query + eligibility filter | `GET /api/search?q=CS+scholarships` returns ranked results |
| 7 | Chat agent: PydanticAI + search_tool + SSE streaming | Chat returns streaming results from DB |
| 8 | Conversation history: messages table + sliding window | Multi-turn chat works, history persists across requests |
| 9 | Research pipeline: LangGraph + Tavily + Exa + Firecrawl | `start_research("NSF REU CS")` discovers new opportunities |
| 10 | Human-in-the-loop: interrupt() + SSE events + POST respond | User can approve/redirect/stop research mid-flow |
| 11 | Recommendations: multi-signal scorer + dashboard feed | Homepage shows personalized opportunities |
| 12 | Signals: user_signals table + interaction embedding updates | Save/apply updates taste, recommendations shift |
| 13 | ARQ worker: deadline expiry, URL verification, email digest | Background jobs run on schedule |
| 14 | Rate limiting + API budget protection | Research endpoint limited to 3/day/user |

---

## What's NOT in this spec

- Frontend (Next.js rebuild) — separate spec
- Payment integration — not needed for Horizon v0.2
- Mobile app — future
- Admin dashboard — future
- Deployment / CI/CD — separate doc when ready for prod
