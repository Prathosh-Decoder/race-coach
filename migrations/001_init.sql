PRAGMA foreign_keys = ON;

CREATE TABLE runner (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  sex TEXT CHECK (sex IN ('female','male','other')),
  birth_year INTEGER,
  height_cm REAL, weight_kg REAL,
  level TEXT NOT NULL CHECK (level IN ('starter','beginner','recreational')),
  run_walk INTEGER NOT NULL DEFAULT 0,
  prior_injury INTEGER NOT NULL DEFAULT 0,
  diet TEXT NOT NULL DEFAULT 'none'
    CHECK (diet IN ('none','no_added_sugar','no_sugar','low_carb')),
  diet_confirmed INTEGER NOT NULL DEFAULT 1,  -- 0 = "Ask": diet still to be answered
  has_treadmill INTEGER NOT NULL DEFAULT 1,
  baseline_week_km REAL NOT NULL,      -- running km in the 7 days before setup
  baseline_longest_km REAL NOT NULL,   -- longest run in the 30 days before setup
  goal_time_s INTEGER,
  race_day TEXT NOT NULL DEFAULT '2026-12-05',
  first_week TEXT NOT NULL,            -- Monday of the first week in the app (Singapore date)
  guide_json TEXT,                     -- run-walk guide paces (jog/walk min/km), shown only as a guide
  created_at TEXT NOT NULL             -- UTC, ISO 8601
);

CREATE TABLE screening (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  answered_on TEXT NOT NULL,           -- Singapore date
  active_3x_week INTEGER NOT NULL,
  known_disease INTEGER NOT NULL,
  symptoms INTEGER NOT NULL,
  doctor_cleared_on TEXT
);

CREATE TABLE time_trial (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  day TEXT NOT NULL,
  distance_km REAL NOT NULL,
  time_s INTEGER NOT NULL,
  setting TEXT NOT NULL CHECK (setting IN
    ('treadmill_1pct','treadmill_flat','outdoor_heat','outdoor_cool'))
);

CREATE TABLE availability (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  start_day TEXT NOT NULL, end_day TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('travel','holiday','sick','busy')),
  can_run INTEGER NOT NULL,
  climate TEXT CHECK (climate IN ('hot','cool')),
  note TEXT
);

CREATE TABLE plan_day (
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  day TEXT NOT NULL,                   -- Singapore date YYYY-MM-DD
  session_type TEXT NOT NULL CHECK (session_type IN ('rest','walk','easy','long',
    'tempo','intervals','race_pace','strength','time_trial','race')),
  distance_km REAL NOT NULL DEFAULT 0,
  zone TEXT,
  location TEXT CHECK (location IN ('treadmill','outdoor','either','gym','none')),
  run_walk TEXT,                       -- continuous, 1:1, 90/60, 2:1, 3:1, 4:1
  steps_json TEXT,
  rule_ids TEXT,                       -- JSON array, e.g. ["R-04","R-06"]
  elevated_risk INTEGER NOT NULL DEFAULT 0,
  runner_ok_at TEXT,
  source TEXT NOT NULL CHECK (source IN ('seed','model','fallback','manual')),
  PRIMARY KEY (runner_id, day)
);

CREATE TABLE session_log (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  day TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('done','partial','missed')),
  distance_km REAL, minutes REAL,
  location TEXT,
  effort INTEGER CHECK (effort BETWEEN 1 AND 10),
  pain TEXT NOT NULL DEFAULT 'none' CHECK (pain IN ('none','soreness','sharp')),
  pain_where TEXT,
  pain_resolved_at TEXT,               -- UTC; set when the runner says the pain is gone (R-07)
  sleep TEXT CHECK (sleep IN ('good','ok','poor')),
  note TEXT,
  logged_at TEXT NOT NULL,
  CHECK (status = 'missed' OR (effort IS NOT NULL AND minutes IS NOT NULL))  -- R-28
);

CREATE TABLE proposal (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  created_at TEXT NOT NULL,
  trigger TEXT NOT NULL,
  model TEXT, attempt INTEGER NOT NULL,
  draft_json TEXT NOT NULL,
  checks_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
    ('failed_checks','awaiting_runner','accepted','declined','fallback'))
);

CREATE TABLE plan_history (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL, day TEXT NOT NULL,
  proposal_id INTEGER REFERENCES proposal(id),
  before_json TEXT, after_json TEXT NOT NULL,
  changed_at TEXT NOT NULL
);

CREATE TABLE coach_note (
  id INTEGER PRIMARY KEY,
  runner_id INTEGER NOT NULL REFERENCES runner(id),
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  record_json TEXT,
  reply TEXT
);

CREATE TABLE schema_version (version INTEGER NOT NULL);
