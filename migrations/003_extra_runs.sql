-- Runs done outside the plan. They count towards weekly and longest-run totals,
-- but never stand in for the planned session of that day.
ALTER TABLE session_log ADD COLUMN extra INTEGER NOT NULL DEFAULT 0;
-- Breaks taken during the run: none, as planned (run-walk), a few extra, or many.
ALTER TABLE session_log ADD COLUMN breaks TEXT CHECK (breaks IN ('none','planned','some','many'))
