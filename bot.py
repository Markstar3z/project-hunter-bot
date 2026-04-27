"""Telegram bot entrypoint for project hunter scans."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from categories import is_valid_category, list_categories
from scanner import CoinGeckoScanner, ScanParams, ScannerError
from storage import Storage


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

ASK_COUNT, ASK_SCAN_TYPE, ASK_CATEGORY, ASK_SORT, ASK_CONFIRM, ASK_SEARCH = range(6)
CLEAR_CONFIRM = "clear_confirm"
BASE_DIR = Path(__file__).resolve().parent
COUNT_OPTIONS = [["10", "25", "50"], ["100", "150", "200"]]
SCAN_TYPE_OPTIONS = [["General Scan", "Specific Category"], ["Cancel"]]
SORT_PRIMARY_OPTIONS = [["Highest Market Cap", "Volume"], ["Recently Added"], ["Cancel"]]
CONFIRM_OPTIONS = [["Start Scan", "Cancel"]]


def get_data_dir() -> Path:
    value = os.getenv("DATA_DIR")
    return Path(value) if value else BASE_DIR


def reply_keyboard(options: List[List[str]], one_time: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(options, resize_keyboard=True, one_time_keyboard=one_time)


def normalize_choice(text: str) -> str:
    return text.strip().lower()


def build_app() -> Application:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")
    return Application.builder().token(token).build()


def get_storage() -> Storage:
    storage = Storage.from_base_dir(get_data_dir())
    storage.ensure_db()
    return storage


def get_scanner() -> CoinGeckoScanner:
    return CoinGeckoScanner(get_storage(), api_key=os.getenv("COINGECKO_API_KEY"))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Crypto Project Hunter Bot\n\n"
        "Finds crypto projects with both X and Telegram links.\n"
        "Market cap filter: $10k to $1B.\n\n"
        "Commands:\n"
        "/start - Show welcome message\n"
        "/scan - Start a new scan\n"
        "/list - Show last 10 saved projects\n"
        "/stats - Show collection stats\n"
        "/search - Search saved projects\n"
        "/export - Export the database\n"
        "/clear - Reset the database\n"
        "/help - Show commands"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["scan"] = {
        "target_count": 50,
        "scan_type": "general",
        "category_name": None,
        "sort_mode": "market_cap_desc",
    }
    await update.message.reply_text(
        "How many projects do you want to find? (Minimum: 10, Maximum: 200)",
        reply_markup=reply_keyboard(COUNT_OPTIONS),
    )
    return ASK_COUNT


async def scan_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    try:
        count = int(text)
    except ValueError:
        await update.message.reply_text(
            "Choose a count button or enter a whole number between 10 and 200.",
            reply_markup=reply_keyboard(COUNT_OPTIONS),
        )
        return ASK_COUNT

    if count < 10 or count > 200:
        await update.message.reply_text("Value must be between 10 and 200.", reply_markup=reply_keyboard(COUNT_OPTIONS))
        return ASK_COUNT

    context.user_data["scan"]["target_count"] = count
    await update.message.reply_text(
        "Select scan type:",
        reply_markup=reply_keyboard(SCAN_TYPE_OPTIONS),
    )
    return ASK_SCAN_TYPE


async def scan_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = normalize_choice(update.message.text or "")
    if text == "cancel":
        return await cancel_scan(update, context)

    if text in {"general scan", "general", "all"}:
        context.user_data["scan"]["scan_type"] = "general"
        context.user_data["scan"]["category_name"] = None
        await update.message.reply_text(
            "How should results be sorted?",
            reply_markup=reply_keyboard(SORT_PRIMARY_OPTIONS),
        )
        return ASK_SORT

    if text in {"specific category", "specific", "category"}:
        context.user_data["scan"]["scan_type"] = "specific"
        categories = list_categories()
        category_buttons = [categories[idx : idx + 2] for idx in range(0, len(categories), 2)]
        category_buttons.append(["Cancel"])
        await update.message.reply_text(
            "Select category:",
            reply_markup=reply_keyboard(category_buttons),
        )
        return ASK_CATEGORY

    await update.message.reply_text(
        'Choose "General Scan" or "Specific Category".',
        reply_markup=reply_keyboard(SCAN_TYPE_OPTIONS),
    )
    return ASK_SCAN_TYPE


async def scan_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    category_name = (update.message.text or "").strip()
    if normalize_choice(category_name) == "cancel":
        return await cancel_scan(update, context)

    if not is_valid_category(category_name):
        categories = list_categories()
        category_buttons = [categories[idx : idx + 2] for idx in range(0, len(categories), 2)]
        category_buttons.append(["Cancel"])
        await update.message.reply_text(
            f"Invalid category. Choose one of:\n{', '.join(categories)}",
            reply_markup=reply_keyboard(category_buttons),
        )
        return ASK_CATEGORY

    context.user_data["scan"]["category_name"] = category_name.title() if category_name.lower() != "ai" else "AI"
    await update.message.reply_text(
        "How should results be sorted?",
        reply_markup=reply_keyboard(SORT_PRIMARY_OPTIONS),
    )
    return ASK_SORT


async def scan_sort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = normalize_choice(update.message.text or "")
    if text == "cancel":
        return await cancel_scan(update, context)

    if text in {"yes", "y", "highest market cap", "market cap", "highest"}:
        context.user_data["scan"]["sort_mode"] = "market_cap_desc"
    elif text in {"recent", "recently added"}:
        context.user_data["scan"]["sort_mode"] = "id_desc"
    elif text in {"continue", "volume", "sort by volume"}:
        context.user_data["scan"]["sort_mode"] = "volume_desc"
    else:
        await update.message.reply_text(
            "Choose a sort option.",
            reply_markup=reply_keyboard(SORT_PRIMARY_OPTIONS),
        )
        return ASK_SORT

    scan_data = context.user_data["scan"]
    category_label = scan_data["category_name"] or "All"
    sort_label = {
        "market_cap_desc": "Highest market cap",
        "volume_desc": "Volume",
        "id_desc": "Recently added",
    }[scan_data["sort_mode"]]
    await update.message.reply_text(
        "Start scan?\n\n"
        f"Target: {scan_data['target_count']} projects\n"
        f"Scan type: {scan_data['scan_type'].title()}\n"
        f"Category: {category_label}\n"
        f"Sort: {sort_label}\n\n"
        "Tap Start Scan to continue.",
        reply_markup=reply_keyboard(CONFIRM_OPTIONS),
    )
    return ASK_CONFIRM


async def scan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = normalize_choice(update.message.text or "")
    if text in {"no", "n", "cancel"}:
        await update.message.reply_text("Scan cancelled.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("scan", None)
        return ConversationHandler.END
    if text not in {"yes", "y", "start scan", "start"}:
        await update.message.reply_text(
            "Tap Start Scan or Cancel.",
            reply_markup=reply_keyboard(CONFIRM_OPTIONS),
        )
        return ASK_CONFIRM

    scan_data = context.user_data["scan"]
    params = ScanParams(
        target_count=scan_data["target_count"],
        scan_type=scan_data["scan_type"],
        category_name=scan_data["category_name"],
        sort_mode=scan_data["sort_mode"],
    )

    progress_queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_progress(message: str) -> None:
        loop.call_soon_threadsafe(progress_queue.put_nowait, message)

    async def progress_worker() -> None:
        last_message = None
        while True:
            message = await progress_queue.get()
            if message is None:
                break
            if message != last_message:
                last_message = message
                await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())

    worker_task = asyncio.create_task(progress_worker())
    try:
        result = await asyncio.to_thread(get_scanner().scan, params, on_progress)
        progress_queue.put_nowait(None)
        await worker_task
    except ScannerError:
        progress_queue.put_nowait(None)
        await worker_task
        LOGGER.exception("Scan failed due to scanner error")
        await update.message.reply_text("Network error, please try again.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("scan", None)
        return ConversationHandler.END
    except Exception:
        progress_queue.put_nowait(None)
        await worker_task
        LOGGER.exception("Scan failed unexpectedly")
        await update.message.reply_text("Unexpected error during scan. Check logs and try again.", reply_markup=ReplyKeyboardRemove())
        context.user_data.pop("scan", None)
        return ConversationHandler.END

    if result["new_count"] >= 1:
        await update.message.reply_text(
            format_scan_results(result),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            "NO NEW PROJECTS FOUND\n\n"
            f"Parameters used:\n"
            f"- Target: {params.target_count} projects\n"
            f"- Market cap range: $10k - $1B\n"
            f"- Category: {params.category_name or 'All'}\n"
            f"- Scanned: {result['scanned_count']} coins\n\n"
            "Suggestions:\n"
            "- Try a different category\n"
            "- Lower your requested count\n"
            "- Run /scan again later",
            reply_markup=ReplyKeyboardRemove(),
        )

    context.user_data.pop("scan", None)
    return ConversationHandler.END


async def cancel_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("scan", None)
    await update.message.reply_text("Scan cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    projects = get_storage().recent_projects()
    if not projects:
        await update.message.reply_text("No projects found yet. Run /scan to start collecting.")
        return

    lines = ["Recently added projects:"]
    for project in projects:
        lines.append(
            f"- {project['name']} ({project['symbol']}) | {project.get('date_added', 'n/a')} | "
            f"X: @{project.get('twitter_handle', '')} | TG: @{project.get('telegram_handle', '')}"
        )
    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = get_storage().stats()
    await update.message.reply_text(
        "Collection stats:\n"
        f"- Total projects collected: {stats['total_projects']}\n"
        f"- Date of first project added: {stats['first_project_date'] or 'n/a'}\n"
        f"- Date of last project added: {stats['last_project_date'] or 'n/a'}\n"
        f"- Date of last scan: {stats['last_scan_date'] or 'n/a'}\n"
        f"- Total scans performed: {stats['total_scans']}\n"
        f"- Average projects per scan: {stats['average_projects_per_scan']}"
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Enter project name or symbol to search:")
    return ASK_SEARCH


async def search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = (update.message.text or "").strip()
    matches = get_storage().search_projects(query)
    if not matches:
        await update.message.reply_text(f"No project found matching '{query}'")
        return ConversationHandler.END

    lines = []
    for project in matches[:10]:
        lines.append(
            f"{project['name']} ({project['symbol']})\n"
            f"X: {project['twitter_url']}\n"
            f"TG: {project['telegram_url']}\n"
            f"Date added: {project.get('date_added', 'n/a')}"
        )
    await update.message.reply_text("\n\n".join(lines), disable_web_page_preview=True)
    return ConversationHandler.END


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage = get_storage()
    payload = storage.export_text()
    if not payload:
        await update.message.reply_text("No projects to export. Run /scan first.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / f"projects_export_{timestamp}.csv"
    file_path.write_text(payload, encoding="utf-8")
    with file_path.open("rb") as handle:
        await update.message.reply_document(document=handle, filename=file_path.name)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, delete all", callback_data=CLEAR_CONFIRM),
                InlineKeyboardButton("No, cancel", callback_data="clear_cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        "WARNING: This will delete ALL collected projects. Are you sure?",
        reply_markup=keyboard,
    )


async def clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == CLEAR_CONFIRM:
        get_storage().clear()
        await query.edit_message_text("Database cleared. Start fresh with /scan")
        return
    await query.edit_message_text("Clear cancelled.")


def format_scan_results(result: Dict[str, Any]) -> str:
    lines = [f"<b>NEW PROJECTS FOUND ({result['new_count']} projects)</b>", ""]
    for idx, project in enumerate(result["projects"], start=1):
        lines.extend(
            [
                "----------------------------------",
                f"{idx}. <b>{project['name']}</b> ({project['symbol']})",
                f"MCap: ${project['market_cap']:,}",
                "----------------------------------",
                f"X: {project['twitter_url']}",
                f"TG: {project['telegram_url']}",
                "",
            ]
        )
    lines.extend(
        [
            "----------------------------------",
            "Scan Summary",
            f"- Total scanned: {result['scanned_count']} coins",
            f"- Filtered (no links): {result['no_links_count']}",
            f"- Filtered (MCap out of range): {result['mcap_filtered_count']}",
            f"- Duplicates skipped: {result['duplicate_count']}",
            f"- New projects added: {result['new_count']}",
            "",
            f"Total in database: {result['total_db_count']}",
        ]
    )
    return "\n".join(lines)


def register_handlers(app: Application) -> None:
    scan_flow = ConversationHandler(
        entry_points=[CommandHandler("scan", scan_command)],
        states={
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, scan_count)],
            ASK_SCAN_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, scan_type)],
            ASK_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, scan_category)],
            ASK_SORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, scan_sort)],
            ASK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, scan_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_scan)],
        allow_reentry=True,
    )

    search_flow = ConversationHandler(
        entry_points=[CommandHandler("search", search_command)],
        states={ASK_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_query)]},
        fallbacks=[CommandHandler("cancel", cancel_scan)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CallbackQueryHandler(clear_callback, pattern="^clear_"))
    app.add_handler(scan_flow)
    app.add_handler(search_flow)


def main() -> None:
    app = build_app()
    register_handlers(app)
    app.run_polling()


if __name__ == "__main__":
    main()
