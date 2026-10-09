CREATE TABLE prank_state (
  runner_id INTEGER PRIMARY KEY REFERENCES runner(id),
  enabled INTEGER NOT NULL DEFAULT 0,
  flavour TEXT NOT NULL DEFAULT 'goose' CHECK (flavour IN
    ('goose','royal','commentator','trailer','son','surprise')),
  big_day_km REAL,          -- fake distance while Big Day is armed
  big_day_until TEXT,       -- UTC time Big Day switches off
  sound INTEGER NOT NULL DEFAULT 0,
  changed_at TEXT NOT NULL
);
