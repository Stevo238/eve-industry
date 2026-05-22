# EVE Industry Manager

A local, self-hosted industry management tool for EVE Online. Tracks assets, blueprints, manufacturing jobs, market orders and wallet balance across multiple characters and accounts.

Built with **FastAPI + SQLite + HTMX + Jinja2** — no Node.js, no cloud dependency, runs entirely on your machine.

## Features

- **Multi-character / multi-account** support via EVE SSO OAuth2
- **Assets** — full asset list with location resolution (NPC stations, Upwell structures, fitted ships/cargo)
- **Blueprints** — BPO/BPC inventory with ME/TE tracking
- **Manufacturing** — material requirements with ME formula, skill checks, cost analysis
- **Industry jobs** — active and completed job tracking
- **Market orders** — open character orders
- **Wallet** — ISK balance per character
- **SDE import** — CCP's official Static Data Export for type names and blueprint data

## Requirements

- Python 3.11+
- EVE Online developer application ([register here](https://developers.eveonline.com/applications))

## Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/Stevo238/eve-industry.git
   cd eve-industry
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your EVE application credentials:
   - `EVE_CLIENT_ID` and `EVE_CLIENT_SECRET` from [developers.eveonline.com](https://developers.eveonline.com/applications)
   - `EVE_CALLBACK_URL` must be set to `http://localhost:9555/auth/callback` in your EVE app settings
   - `SECRET_KEY` — generate one with: `python -c "import secrets; print(secrets.token_hex(32))"`

4. **Required ESI scopes** — enable these on your EVE developer application:
   ```
   esi-assets.read_assets.v1
   esi-characters.read_blueprints.v1
   esi-skills.read_skills.v1
   esi-industry.read_character_jobs.v1
   esi-markets.read_character_orders.v1
   esi-wallet.read_character_wallet.v1
   esi-location.read_location.v1
   esi-universe.read_structures.v1
   ```

5. **Launch**
   ```bash
   # Windows — double-click start.bat
   # Or manually:
   python run.py
   ```
   Open [http://localhost:9555](http://localhost:9555)

6. **Import SDE** — go to the SDE page and click Import. This downloads and imports CCP's Static Data Export (~500 MB, one-time).

7. **Add characters** — click "Add Character" and authenticate via EVE SSO.

## Notes

- The SQLite database (`eve_industry.db`) is created locally and excluded from git — your data stays on your machine.
- Upwell structure names require `esi-universe.read_structures.v1` scope and docking access to the structure.
- Runs on port **9555** to avoid conflicts with Windows Hyper-V (which reserves 8000–8001).

## Tech Stack

| Layer | Library |
|---|---|
| API | FastAPI + uvicorn |
| Database | SQLAlchemy 2.0 async + aiosqlite |
| Auth | EVE SSO OAuth2 + PyJWT |
| ESI client | httpx |
| Templates | Jinja2 + HTMX |
| SDE parsing | PyYAML + aiofiles |
