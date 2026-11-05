-- 10,000 users with fixed values, so every run and every API sees the same rows.
-- created_at = 2026-01-01T00:00:00Z plus id minutes.
CREATE TABLE users (
    id         integer     PRIMARY KEY,
    name       text        NOT NULL,
    email      text        NOT NULL,
    created_at timestamptz NOT NULL
);

INSERT INTO users (id, name, email, created_at)
SELECT g,
       'User ' || g,
       'user' || g || '@example.com',
       timestamptz '2026-01-01 00:00:00+00' + make_interval(mins => g)
FROM generate_series(1, 10000) AS g;

ANALYZE users;
