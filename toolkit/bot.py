import asyncio
import logging
import os
from io import BytesIO

import img2pdf
import pymupdf
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .core import InputError, PasswordRequired, assemble, filename, permutation, ranges, reader, reorder, unlock
from .store import Store

log = logging.getLogger(__name__)
DOWNLOAD_LIMIT = 20 * 1024 * 1024
UPLOAD_LIMIT = 49 * 1024 * 1024
MENU = Markup([
    [Button("Merge", callback_data="merge"), Button("Remove pages", callback_data="remove")],
    [Button("Add pages", callback_data="add"), Button("Reorder", callback_data="reorder")],
    [Button("Rename", callback_data="rename"), Button("Preview", callback_data="preview:0")],
    [Button("Undo", callback_data="undo"), Button("Reset", callback_data="reset")],
    [Button("Done", callback_data="done")],
])


def store(context):
    return context.application.bot_data["store"]


async def menu(message, context, uid, prefix=""):
    state = store(context).get(uid)
    count = len(reader(store(context).read(uid, state["work"])).pages)
    await message.reply_text(f"{prefix}\n{state['name']} · {count} pages · {len(state['sources'])} source files\nChoose an action or send another PDF/photo.", reply_markup=MENU)


async def start(update, context):
    await update.message.reply_text(
        "Send PDFs or photos in this private chat. I can merge, remove, add, reorder and rename pages. "
        "No text editing, OCR, annotations or compression.\n\n"
        "Each upload must be under 20 MB. Sessions and working files expire after inactivity "
        f"({context.bot_data['timeout'] // 60} minutes). /cancel deletes your session. "
        "Telegram retains messages/files under its own policies. Passwords are used only to unlock a file.\n"
        "/undo · /reset · /preview · /order 2,1,3 · /cancel"
    )


def accept(uid, context, data, label):
    db = store(context)
    file = db.put(uid, data)
    state = db.get(uid)
    if state is None or "work" not in state:
        state = {"sources": [], "work": file, "original": file, "history": [], "name": filename(label), "mode": "menu"}
    state["sources"].append({"file": file, "name": filename(label)})
    state["mode"] = "menu"
    state.pop("pending", None)
    db.save(uid, state)
    return state


async def upload(update, context):
    uid = update.effective_user.id
    msg = update.message
    db = store(context)
    state = db.get(uid)
    if state and state.get("pending"):
        raise InputError("Unlock the pending PDF first, or /cancel to delete this session.")
    if state and len(state["sources"]) >= 30:
        raise InputError("A session can contain up to 30 source files. Finish or /cancel first.")
    media = msg.document or msg.photo[-1]
    if not media.file_size or media.file_size >= DOWNLOAD_LIMIT:
        raise InputError("Telegram downloads are limited to 20 MB. Send a file smaller than 20 MB.")
    label = getattr(media, "file_name", None) or "photo.pdf"
    is_image = bool(msg.photo) or (getattr(media, "mime_type", "") or "").startswith("image/")
    if not is_image and not label.lower().endswith(".pdf"):
        raise InputError("Please send a PDF or a photo.")
    data = bytes(await (await media.get_file()).download_as_bytearray())
    if is_image:
        try:
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > 40_000_000:
                    raise InputError("That image is too large. Use an image under 40 megapixels.")
            data = await asyncio.to_thread(img2pdf.convert, data)
        except InputError:
            raise
        except Exception as exc:
            raise InputError("That image could not be converted. Send a JPEG or PNG.") from exc
    try:
        data = await asyncio.to_thread(unlock, data)
    except PasswordRequired:
        # Store the encrypted upload separately; never persist the password.
        directory = db.directory(uid)
        used = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        if used + len(data) > db.max_bytes:
            raise InputError("Session storage limit reached.")
        (directory / "locked.pdf").write_bytes(data)
        state = state or {"sources": [], "history": [], "name": filename(label)}
        state.update(mode="password", pending=label)
        db.save(uid, state)
        await msg.reply_text("This PDF is password-protected. Send its password as your next message or /cancel. Password messages are deleted on a best-effort basis.")
        return
    accept(uid, context, data, label)
    await menu(msg, context, uid, "Source received. Use Merge or Add pages to include additional sources.")


async def preview(message, context, uid, offset):
    db = store(context)
    state = db.get(uid)
    data = db.read(uid, state["work"])

    def render():
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if offset < 0 or offset >= len(doc):
                raise InputError("That preview page is no longer available.")
            sheet = Image.new("RGB", (720, 460), "#e8edf2")
            draw = ImageDraw.Draw(sheet)
            for slot, index in enumerate(range(offset, min(offset + 8, len(doc)))):
                page = doc[index]
                scale = min(164 / max(1, page.rect.width), 190 / max(1, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                thumb = Image.open(BytesIO(pix.tobytes("png")))
                thumb.thumbnail((164, 190))
                x, y = (slot % 4) * 180, (slot // 4) * 230
                sheet.paste(thumb, (x + (180 - thumb.width) // 2, y + 8))
                draw.text((x + 65, y + 208), f"Page {index + 1}", fill="black")
            output = BytesIO()
            sheet.save(output, "PNG")
            return output.getvalue(), len(doc)

    png, count = await asyncio.to_thread(render)
    buttons = []
    if offset:
        buttons.append(Button("Previous", callback_data=f"preview:{max(0, offset-8)}"))
    if offset + 8 < count:
        buttons.append(Button("Next", callback_data=f"preview:{offset+8}"))
    await message.reply_photo(png, caption="Current working copy · page numbers", reply_markup=Markup([buttons]) if buttons else None)


async def action(update, context):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    db = store(context)
    state = db.get(uid)
    if not state or "work" not in state:
        raise InputError("Send a PDF to start, or unlock your pending PDF.")
    if state.get("pending"):
        raise InputError("Send the PDF password first or /cancel.")
    value = query.data
    db.save(uid, state)
    if value.startswith("preview:"):
        await preview(query.message, context, uid, int(value.split(":")[1]))
        return
    if value in {"undo", "reset"}:
        await restore(query.message, context, uid, value)
        return
    if value == "merge":
        state.update(mode="merge", selection=[])
        db.save(uid, state)
        await merge_choices(query.message, state)
        return
    if value.startswith("pick:"):
        if state["mode"] != "merge":
            raise InputError("Open Merge again to choose a file order.")
        index = int(value.split(":")[1])
        if index not in range(len(state["sources"])) or index in state["selection"]:
            raise InputError("That source was already selected or is unavailable.")
        state["selection"].append(index)
        db.save(uid, state)
        if len(state["selection"]) == len(state["sources"]):
            await merge(query.message, context, uid, state, state["selection"])
        else:
            await merge_choices(query.message, state)
        return
    if value == "add":
        state["mode"] = "source"
        prompt = "Choose source number:\n" + "\n".join(f"{i+1}. {s['name']}" for i, s in enumerate(state["sources"]))
    elif value in {"remove", "reorder", "rename", "done"}:
        state["mode"] = value
        prompt = {"remove": "Pages to remove? Example: 3, 5-7, 10", "reorder": "Send every page in its new order, or reverse, or move 5 to 1.", "rename": "What should the file be called? Templates: Invoice_{date}", "done": f"Final filename? Send a name or '.' to use {state['name']}."}[value]
    else:
        raise InputError("That action is no longer available.")
    db.save(uid, state)
    await query.message.reply_text(prompt)


async def merge_choices(message, state):
    choices = [[Button(f"{i+1}. {s['name'][:45]}", callback_data=f"pick:{i}")] for i, s in enumerate(state["sources"]) if i not in state["selection"]]
    selected = ", ".join(str(i + 1) for i in state["selection"]) or "none"
    await message.reply_text(f"Tap all sources in order, or /order 2,1,3. Selected: {selected}\nMerge rebuilds the working copy from these sources.", reply_markup=Markup(choices))


async def merge(message, context, uid, state, order):
    db = store(context)
    parts = [(db.read(uid, state["sources"][i]["file"]), None) for i in order]
    if sum(len(reader(data).pages) for data, _ in parts) > db.max_pages:
        raise InputError(f"The merged result would exceed {db.max_pages} pages.")
    data = await asyncio.to_thread(assemble, parts)
    db.commit(uid, state, data)
    await menu(message, context, uid, "Merged. Undo is available.")


async def restore(message, context, uid, action_name):
    db = store(context)
    state = db.get(uid)
    if not state or "work" not in state:
        raise InputError("Send a PDF first.")
    if state.get("pending"):
        raise InputError("Unlock the pending PDF or /cancel first.")
    if action_name == "undo":
        if not state["history"]:
            raise InputError("There is nothing to undo yet.")
        state["work"], state["name"] = state["history"].pop()
    else:
        state["history"].append([state["work"], state["name"]])
        state["work"] = state["original"]
        state["name"] = state["sources"][0]["name"]
    state["mode"] = "menu"
    db.save(uid, state)
    await menu(message, context, uid, "Working copy restored.")


async def text(update, context):
    uid = update.effective_user.id
    db = store(context)
    state = db.get(uid)
    if not state:
        raise InputError("Send a PDF or photo to start.")
    value = update.message.text.strip()
    mode = state["mode"]
    db.save(uid, state)
    if mode == "password":
        try:
            data = await asyncio.to_thread(unlock, (db.directory(uid) / "locked.pdf").read_bytes(), value)
        finally:
            try:
                await update.message.delete()
            except Exception:
                pass
        accept(uid, context, data, state["pending"])
        (db.directory(uid) / "locked.pdf").unlink(missing_ok=True)
        await menu(update.message, context, uid, "PDF unlocked.")
        return
    current = db.read(uid, state["work"])
    count = len(reader(current).pages)
    if mode == "merge":
        await merge(update.message, context, uid, state, permutation(value, len(state["sources"])))
        return
    if mode in {"rename", "done"}:
        name = state["name"] if value == "." else filename(value)
        if mode == "rename":
            db.commit(uid, state, name=name)
        else:
            if len(current) > UPLOAD_LIMIT:
                raise InputError("The result exceeds the 49 MB delivery limit. Remove pages and try Done again.")
            await update.message.reply_document(current, filename=name, caption="Your final PDF. Session files are now deleted.")
            db.delete(uid)
            return
    elif mode == "remove":
        removed = set(ranges(value, count))
        keep = [i for i in range(count) if i not in removed]
        if not keep:
            raise InputError("Keep at least one page.")
        data = await asyncio.to_thread(assemble, [(current, keep)])
        db.commit(uid, state, data)
        await menu(update.message, context, uid, f"{count} pages → {len(keep)} pages")
        return
    elif mode == "reorder":
        data = await asyncio.to_thread(assemble, [(current, reorder(value, count))])
        db.commit(uid, state, data)
    elif mode == "source":
        if not value.isdecimal() or not 1 <= int(value) <= len(state["sources"]):
            raise InputError("Choose a source number from the list.")
        state.update(source=int(value)-1, mode="pages")
        db.save(uid, state)
        source_count = len(reader(db.read(uid, state["sources"][state["source"]]["file"])).pages)
        await update.message.reply_text(f"Which source pages? 1-{source_count} available. Example: 1-3")
        return
    elif mode == "pages":
        source = db.read(uid, state["sources"][state["source"]]["file"])
        state.update(pages=ranges(value, len(reader(source).pages)), mode="position")
        db.save(uid, state)
        await update.message.reply_text(f"Insert at start, at end, or after page N (1-{count})?")
        return
    elif mode == "position":
        import re
        if count + len(state["pages"]) > db.max_pages:
            raise InputError(f"The result would exceed {db.max_pages} pages. Choose Add pages again and select fewer pages.")
        normalized = value.lower().strip()
        match = re.fullmatch(r"after(?: page)? (\d+)", normalized)
        if normalized in {"start", "at start"}:
            position = 0
        elif normalized in {"end", "at end"}:
            position = count
        elif match and 1 <= int(match[1]) <= count:
            position = int(match[1])
        else:
            raise InputError(f"Use 'at start', 'at end', or 'after page 4' (1-{count}).")
        source = db.read(uid, state["sources"][state["source"]]["file"])
        parts = []
        if position:
            parts.append((current, list(range(position))))
        parts.append((source, state["pages"]))
        if position < count:
            parts.append((current, list(range(position, count))))
        data = await asyncio.to_thread(assemble, parts)
        db.commit(uid, state, data)
    else:
        raise InputError("Choose an action from the menu first. /cancel deletes the session.")
    await menu(update.message, context, uid, "Updated. Undo is available.")


async def command(update, context):
    uid = update.effective_user.id
    cmd = update.message.text.split()[0].split("@")[0][1:]
    if cmd == "cancel":
        store(context).delete(uid)
        await update.message.reply_text("Session and temporary files deleted.")
    elif cmd in {"undo", "reset"}:
        await restore(update.message, context, uid, cmd)
    elif cmd == "preview":
        state = store(context).get(uid)
        if not state or "work" not in state:
            raise InputError("Send a PDF first.")
        store(context).save(uid, state)
        await preview(update.message, context, uid, 0)
    elif cmd == "order":
        state = store(context).get(uid)
        if not state or "work" not in state or state.get("pending"):
            raise InputError("Send and unlock your PDFs first.")
        await merge(update.message, context, uid, state, permutation(" ".join(context.args), len(state["sources"])))


async def error(update, context):
    message = update.effective_message if isinstance(update, Update) else None
    if isinstance(context.error, InputError):
        if message:
            await message.reply_text(str(context.error))
    else:
        # Never log incoming updates, passwords, file URLs, or bot tokens.
        log.error("Request failed (%s)", type(context.error).__name__)
        if message:
            await message.reply_text("That operation failed. Your working copy is kept; try again or /cancel.")


async def cleanup(context):
    store(context).expire(context.bot_data["timeout"])


def build_app(token, db, timeout):
    app = Application.builder().token(token).concurrent_updates(False).build()
    app.bot_data.update(store=db, timeout=timeout)
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["start", "help"], start, filters=private))
    app.add_handler(CommandHandler(["cancel", "undo", "reset", "preview", "order"], command, filters=private))
    app.add_handler(CallbackQueryHandler(action))
    app.add_handler(MessageHandler(private & (filters.Document.ALL | filters.PHOTO), upload))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, text))
    app.add_error_handler(error)
    app.job_queue.run_repeating(cleanup, interval=60, first=1)
    return app


def main():
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN in .env or your environment.")
    timeout = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")) * 60
    db = Store(os.getenv("DATA_DIR", "data"), int(os.getenv("MAX_PAGES", "500")), int(os.getenv("MAX_SESSION_MB", "100")))
    db.expire(timeout)
    app = build_app(token, db, timeout)
    url = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
    if url:
        secret = os.getenv("WEBHOOK_SECRET", "")
        if not secret:
            raise SystemExit("Set WEBHOOK_SECRET for authenticated webhook delivery.")
        app.run_webhook(listen="0.0.0.0", port=int(os.getenv("PORT", "10000")), url_path="telegram", webhook_url=url + "/telegram", secret_token=secret)
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
