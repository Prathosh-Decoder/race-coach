#!/bin/bash
# Race Coach: double-click to update. Your data lives outside this folder and is kept.
cd "$(dirname "$0")" || exit 1
git pull --ff-only && .venv/bin/pip install -q -r requirements.txt && echo "Updated. Start Race Coach again."
read -r -p "Press Return to close."
