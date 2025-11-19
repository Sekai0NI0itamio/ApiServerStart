# README.md
## Overview
This project exposes a tiny FastAPI-powered endpoint that anyone can call to trigger the “start server” workflow:

1. Executes the curl command stored in `initsend.txt`.
2. Extracts `last_active_token.jwt` from the response.
3. Injects that JWT into the template stored in `serverstart-orig.txt` (without modifying the file or creating backups).
4. Executes the resulting curl command to call `worlds.v1.WorldService/StartSession`.
5. Logs the entire exchange to `logs/startserver-<timestamp>.log`.

A CLI helper (`update_and_start.py`) is provided for local/manual runs, and the API layer (`app.py`) is ready to deploy on Render (or any other platform that can run `uvicorn`).

## File layout
- `app.py` – FastAPI app exposing `/trigger`.
- `server_runner.py` – shared workflow logic.
- `update_and_start.py` – CLI helper invoking the same logic as the API.
- `serverstart-orig.txt` – StartSession curl template; the script replaces the Bearer token at runtime.
- `initsend.txt` – Provided Clerk session curl (must remain alongside the app).
- `requirements.txt` – Python dependencies.
- `Procfile` – Process definition for Render (runs `uvicorn`).
- `logs/` – Created at runtime; contains detailed per-run logs.

## Local setup
1. Ensure Python 3.11+ is available and `curl` exists on PATH.
2. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Keep `initsend.txt` and `serverstart-orig.txt` in the project root. Update `serverstart-orig.txt` to match your latest StartSession curl; keep the placeholder `{{JWT}}` where the token should be injected.
4. Start the API:
   ```bash
   uvicorn app:app --reload
   ```

## API usage
- Endpoint: `POST /trigger`
- Body: `{"call": "startserver"}`
- Example:
  ```bash
  curl -X POST http://localhost:8000/trigger \
       -H 'content-type: application/json' \
       --data '{"call":"startserver"}'
  ```
- Response includes:
  - `status`: `"ok"` or `"error"` depending on the StartSession curl exit code.
  - `token_preview`: masked JWT for quick verification.
  - `token`: `null` by default. Set environment variable `STARTSERVER_EXPOSE_FULL_JWT=true` to return the full token (discouraged for public deployments).
  - `log_path`: location of the stored log.
  - `initsend` / `startserver`: stdout/stderr plus exit codes.

Auxiliary endpoints:
- `GET /` – basic instructions.
- `GET /healthz` – liveness probe.

## CLI helper
To trigger the flow without HTTP: