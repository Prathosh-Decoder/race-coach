#!/bin/bash
# Race Coach: double-click to start. Opens the app in the browser.
cd "$(dirname "$0")" || exit 1
if ! curl -s -o /dev/null --max-time 2 http://localhost:11434/api/tags; then
  open -a Ollama 2>/dev/null   # start the coach model if Ollama is installed; the app works without it
fi
exec .venv/bin/python -m app.main
