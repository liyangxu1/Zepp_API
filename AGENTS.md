# Repository Guidelines

## Project Shape

- This repository is a Python refactor of `apgk/Zepp_API`.
- `index.php` is the original PHP implementation and remains the reference source for the legacy payload template. Do not delete or rewrite it casually; `app.py` currently parses it in `_build_payload_template()`.
- `app.py` is the main implementation. It supports CLI mode, an HTTP server, a simple browser UI, JSON APIs, local SQLite logging, token caching, and payload preview helpers.
- `requirements.txt` only lists the optional stable AES dependency. If `cryptography` is absent, `app.py` falls back to the system `openssl` command.
- `tool_records.sqlite3` is runtime state, not source. It can contain account identifiers, device bindings, token cache rows, logs, and message board data. Do not commit it or use it as a fixture without sanitizing it.

## Commands

- Install dependency: `python -m pip install -r requirements.txt`
- Syntax check: `python -m py_compile app.py`
- CLI run, submits to Zepp: `python app.py --user <account> --pwd <password> --step <number>`
- HTTP server: `python app.py --serve --host 127.0.0.1 --port 8000`
- HTTPS server: `python app.py --serve --host 0.0.0.0 --port 8443 --ssl-cert <cert> --ssl-key <key>`

## API Surface

- `GET /` or `/index.html` serves the local tool page.
- `GET|POST /api/step` is the legacy-compatible submit endpoint.
- `POST /api/tools/zepp-step` is the JSON submit endpoint and requires `api_key`, `X-Api-Key`, or `Authorization: Bearer ...`.
- `GET /api/payload-preview` builds an offline payload preview and does not submit to Zepp.
- `GET /api/logs` returns masked call logs.
- `GET|POST /api/messages` handles the local message board.

## Development Notes

- Treat Zepp endpoints as private, unstable upstream APIs. Source-level changes are not proof of live usability.
- Do not run live submission commands unless the user explicitly provides credentials and asks for a real test. A live run can modify Zepp/WeChat step data and can trigger account risk controls.
- Keep `index.php` and `app.py` behavior aligned when changing login, encryption, or submit payload logic.
- Preserve account masking in responses and logs. Do not add password logging.
- Prefer narrowly scoped edits; this repo may contain untracked local runtime artifacts from previous manual tests.
