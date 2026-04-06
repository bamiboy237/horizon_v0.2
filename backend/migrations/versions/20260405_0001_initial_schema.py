"""Create initial Horizon schema.

Revision ID: 20260405_0001
Revises:
Create Date: 2026-04-05 13:40:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260405_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

    op.execute(
        """
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
        """
    )

    op.execute(
        """
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
            embedding vector(1536),
            embedding_model VARCHAR(50) DEFAULT 'text-embedding-3-small',
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector(
                    'english',
                    coalesce(title, '') || ' ' ||
                    coalesce(description, '') || ' ' ||
                    coalesce(eligibility_text, '')
                )
            ) STORED,
            discovered_at TIMESTAMPTZ DEFAULT NOW(),
            last_verified TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT TRUE
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_opp_embedding ON opportunities USING hnsw (embedding vector_cosine_ops);"
    )
    op.execute("CREATE INDEX idx_opp_search ON opportunities USING gin(search_vector);")
    op.execute(
        "CREATE INDEX idx_opp_active_deadline ON opportunities(is_active, deadline);"
    )
    op.execute("CREATE INDEX idx_opp_type ON opportunities(opportunity_type);")
    op.execute("CREATE INDEX idx_opp_normalized_url ON opportunities(normalized_url);")

    op.execute(
        """
        CREATE TABLE sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_active_at TIMESTAMPTZ DEFAULT NOW(),
            metadata JSONB DEFAULT '{}'::jsonb
        );
        """
    )

    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT,
            tool_calls JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX idx_messages_session ON messages(session_id, created_at);")

    op.execute(
        """
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
        """
    )

    op.execute(
        """
        CREATE TABLE user_signals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
            opportunity_id UUID REFERENCES opportunities(id) ON DELETE CASCADE,
            signal_type VARCHAR(30) NOT NULL,
            strength FLOAT NOT NULL DEFAULT 1.0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, opportunity_id, signal_type)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_signals_user ON user_signals(user_id, created_at DESC);"
    )

    op.execute(
        """
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
        """
    )

    op.execute(
        """
        CREATE TABLE email_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id VARCHAR(255) REFERENCES profiles(id) ON DELETE CASCADE,
            email_type VARCHAR(50) NOT NULL,
            opportunity_ids UUID[],
            sent_at TIMESTAMPTZ DEFAULT NOW(),
            status VARCHAR(20) NOT NULL,
            resend_id VARCHAR(100)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_logs;")
    op.execute("DROP TABLE IF EXISTS research_sessions;")
    op.execute("DROP INDEX IF EXISTS idx_signals_user;")
    op.execute("DROP TABLE IF EXISTS user_signals;")
    op.execute("DROP TABLE IF EXISTS applications;")
    op.execute("DROP INDEX IF EXISTS idx_messages_session;")
    op.execute("DROP TABLE IF EXISTS messages;")
    op.execute("DROP TABLE IF EXISTS sessions;")
    op.execute("DROP INDEX IF EXISTS idx_opp_normalized_url;")
    op.execute("DROP INDEX IF EXISTS idx_opp_type;")
    op.execute("DROP INDEX IF EXISTS idx_opp_active_deadline;")
    op.execute("DROP INDEX IF EXISTS idx_opp_search;")
    op.execute("DROP INDEX IF EXISTS idx_opp_embedding;")
    op.execute("DROP TABLE IF EXISTS opportunities;")
    op.execute("DROP TABLE IF EXISTS profiles;")
