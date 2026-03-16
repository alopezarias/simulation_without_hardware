!#/bin/bash
source .venv/bin/activate && python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
source .venv/bin/activate && python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws