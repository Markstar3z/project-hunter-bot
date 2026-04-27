# Project Hunter Bot

Telegram bot for discovering crypto projects from CoinGecko, filtering them by market cap, requiring both X and Telegram links, and storing non-duplicate results in a JSON database.

## Features

- `/start` and `/help` for bot guidance
- `/scan` multi-step scan flow with confirmation
- `/list` for the 10 most recent projects
- `/stats` for collection statistics
- `/search` for case-insensitive name or symbol lookup
- `/export` to send a CSV export
- `/clear` with inline confirmation
- CoinGecko pagination, market-cap filtering, duplicate prevention, and resume state tracking

## Local Setup

1. Create a Telegram bot with `@BotFather` and copy the bot token.
2. Get a CoinGecko API key if you want higher rate limits.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Update `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
COINGECKO_API_KEY=your_api_key_here
```

5. Run the bot:

```bash
python bot.py
```

## Render Deployment

Deploy this bot as a `Background Worker`, not a web service.

### Recommended files

- `render.yaml` provisions the worker configuration
- `.python-version` pins Python to 3.13

### Required environment variables

- `TELEGRAM_BOT_TOKEN`
- `COINGECKO_API_KEY`

### Persistent data

Render services use an ephemeral filesystem by default, so the JSON database will be lost on restart or redeploy unless you attach a persistent disk.

If you attach a disk in Render, mount it at:

```text
/var/data
```

Then set:

```text
DATA_DIR=/var/data
```

The bot will store `projects_db.json` and CSV exports in that directory.

### Manual Render settings

- Service type: `Background Worker`
- Runtime: `Python`
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`

## Notes

- Database file: `projects_db.json`
- Export files are created in the same directory as `bot.py`
- CoinGecko free-tier limits still apply, so scans can take time
