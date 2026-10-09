# Race Coach

A small local app that coaches two runners (Prathosh and Mom) to the adidas Half Marathon,
Sat 5 Dec 2026. It runs on one laptop: FastAPI on 127.0.0.1, SQLite, and an optional local
Qwen model through Ollama. The model only drafts changes; `app/validator.py` checks every
draft against the MUST rules in `rules/brief_v1.yaml`, and the runner approves before anything changes.

## Run

    python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    ollama pull hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M   # optional
    ./start.command                                                # or: .venv/bin/python -m app.main

The app opens at http://127.0.0.1:8765. On first start it imports both seed plans
(only if every MUST check passes). Without Ollama it still works and shows "Coach offline".

Data lives in `~/Library/Application Support/RaceCoach` (never in this folder), with a daily
backup kept for 30 days. Set `RACE_COACH_DATA=/some/folder` to use another place.

## Test

    .venv/bin/python -m pytest -q
    .venv/bin/python -m app.seed --check     # validate the seeds into a throwaway database

## Layout

    app/main.py        API and start-up        app/validator.py   the MUST rule checks
    app/engine.py      sessions, paces, locks  app/coach.py       Ollama client, fallback
    app/clock.py       Singapore time          app/prank/         display-only extras
    app/static/        the screens             rules/brief_v1.yaml  the 36 rules

## Preview a day

To see the app as it will look on another day (for example to try Big Day on a run day),
start a throwaway copy with its own data folder and a fixed time. Never set this on Mom's laptop.

    RACE_COACH_DATA=/tmp/rc-preview RACE_COACH_FAKE_NOW=2026-10-13T06:30+08:00 .venv/bin/python -m app.main
