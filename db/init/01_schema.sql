-- ReadIssue: schema for the read-load target table.
-- Loaded automatically by the official postgres image on first init.

CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    category    TEXT        NOT NULL,
    price       NUMERIC(10, 2) NOT NULL,
    stock       INTEGER     NOT NULL,
    description TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index used by the "normal" read path so we can contrast it with the
-- deliberately un-indexed "slow query" scenario later.
CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);
