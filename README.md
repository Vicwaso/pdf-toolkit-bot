# PDF Toolkit Bot

A private-chat Telegram bot for structural PDF editing. Upload PDFs or photos, chain edits, preview page thumbnails, undo/reset, then choose a filename and download the result. PDF pages are copied with pypdf; the bot does not edit page content, OCR, annotate, or compress PDFs. Image uploads become new pages with img2pdf. Previews are separate rendered images and never replace PDF pages.

## Run locally

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Set BOT_TOKEN from Telegram @BotFather in .env
python -m toolkit.bot
```

Open your bot in Telegram and send `/start`. No token is bundled with this repository.

## Workflow

- The first upload becomes the original and working copy. Later uploads enter the source library without changing the working copy.
- **Merge**: tap every source in order or use `/order 2,1,3`. This rebuilds the working copy from the source files; undo restores the previous edits.
- **Remove pages**: enter `3, 5-7, 10`. At least one page must remain.
- **Add pages**: choose a source number, select pages such as `1-3`, then enter `after page 4`, `at start`, or `at end`. Upload a photo first to use it as a source.
- **Reorder**: enter every page exactly once, `reverse`, or `move 5 to 1`.
- **Rename**: enter a name or a template such as `Invoice_{date}`. Dates use the server date; invalid characters and reserved names are cleaned.
- **Preview**: browse numbered sheets of eight thumbnails from the current working copy.
- **Undo** restores one edit, including a rename. **Reset** restores the first upload; source files remain available. Reset itself can be undone.
- **Done** asks for a filename (`.` accepts the current name). Files are removed only after Telegram accepts delivery. `/cancel` removes the session immediately.

Passwords are requested for encrypted uploads, used to create an unlocked working source, and never saved. Password messages are deleted when Telegram permits. The downloaded final PDF is unencrypted.

## Storage, limits and privacy

SQLite stores per-user session state; PDFs live under `DATA_DIR/<telegram-user-id>`. State survives process restarts. All edits are validated before the working-copy pointer changes. Keep one bot process and one replica; SQLite/local files are intentionally not a distributed store.

Default limits: uploads smaller than 20 MiB, 500 pages per result/source, 100 MiB total storage per session (including undo history), and 49 MiB final delivery. Configure `MAX_PAGES`, `MAX_SESSION_MB`, and `SESSION_TIMEOUT_MINUTES` in the environment. Sessions expire after 30 minutes of inactivity; cleanup runs every minute and at startup. A stopped service cannot run cleanup; startup removes expired files. Storage must be protected by the host's filesystem permissions and disk encryption policy. Deletion is ordinary filesystem deletion, not forensic secure erasure. Telegram retains chat uploads and downloads separately.

The bot handles private chats only. It does not log incoming messages, passwords or file URLs. For hostile public traffic, place the service in a resource-limited container: PDF parsing/rendering runs in threads, not a security sandbox. Global quotas and payment billing are not included.

## Render deployment

Deploy this repository using `render.yaml` through Render's **New > Blueprint** flow. It uses a paid Starter service with a persistent disk for SQLite and session files. Enter `BOT_TOKEN` when prompted. The bot automatically uses Render's `RENDER_EXTERNAL_URL` for its webhook; you can override this with `WEBHOOK_URL` for a custom domain. Render generates `WEBHOOK_SECRET`; the webhook endpoint validates Telegram's secret header. Keep a single instance. The app binds to `PORT` and registers `/telegram` automatically. For local polling leave `WEBHOOK_URL` empty. Stop your local bot with Ctrl+C before deploying so it does not compete with the hosted instance.

The webhook listener returns a response at `/`; no separate dashboard is provided. Deployment and live Telegram delivery require your own bot token and hosting account.

## Tests

```powershell
pip install pytest
python -m pytest -q
```

Tests cover page validation, structural copying, encrypted files, renaming, session persistence, undo state and expiry. Optional split, rotate, saved presets and M-Pesa premium billing are intentionally outside this implementation.

API references: [Telegram file downloads](https://core.telegram.org/bots/api#getfile), [python-telegram-bot application](https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html), [pypdf writer](https://pypdf.readthedocs.io/en/stable/modules/PdfWriter.html).
