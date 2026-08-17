-- Postgres schema for the ticket intake flow.
-- Apply this when the database is stood up, then implement bot.storage.base.Repository
-- against it and swap InMemoryRepository in main.py.

CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id           BIGINT PRIMARY KEY,
    enabled            BOOLEAN     NOT NULL DEFAULT TRUE,
    intake_title       TEXT        NOT NULL DEFAULT 'Before we begin',
    intake_description TEXT        NOT NULL DEFAULT '',
    prompt_template    TEXT        NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which categories Ticket Tool drops new tickets into. Many per guild so a server
-- can run several panels into separate categories.
CREATE TABLE IF NOT EXISTS guild_ticket_categories (
    guild_id    BIGINT NOT NULL REFERENCES guild_configs(guild_id) ON DELETE CASCADE,
    category_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, category_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT  NOT NULL REFERENCES guild_configs(guild_id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    -- 'short' | 'paragraph' | 'choice'
    style       TEXT    NOT NULL DEFAULT 'short',
    -- Dropdown options, ordered. Empty for the text styles.
    choices     JSONB   NOT NULL DEFAULT '[]'::jsonb,
    placeholder TEXT,
    required    BOOLEAN NOT NULL DEFAULT TRUE,
    -- SQL rows have no inherent order, so modal order is stored explicitly.
    -- In Python the GuildConfig.questions list order is authoritative: write with
    -- enumerate(), read back with ORDER BY position.
    position    INT     NOT NULL,
    UNIQUE (guild_id, key)
);

CREATE INDEX IF NOT EXISTS questions_guild_position_idx
    ON questions (guild_id, position);

-- A ticket channel that is locked, waiting on its opener.
CREATE TABLE IF NOT EXISTS pending_intakes (
    channel_id             BIGINT PRIMARY KEY,
    guild_id               BIGINT      NOT NULL,
    user_id                BIGINT      NOT NULL,
    -- The opener's send_messages overwrite before we locked them.
    -- NULL means "inherit", which is distinct from FALSE.
    original_send_messages BOOLEAN,
    prompt_message_id      BIGINT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS submissions (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        BIGINT      NOT NULL,
    channel_id      BIGINT      NOT NULL,
    user_id         BIGINT      NOT NULL,
    answers         JSONB       NOT NULL,
    -- Stored rather than re-rendered: the template may change later, and this is
    -- what the agent actually received.
    rendered_prompt TEXT        NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS submissions_guild_submitted_idx
    ON submissions (guild_id, submitted_at DESC);
