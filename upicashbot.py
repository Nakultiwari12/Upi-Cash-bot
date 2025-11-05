# referral_bot_admin_panel.py
# Requirements: Python 3.9+, python-telegram-bot==20.5
# pip install python-telegram-bot==20.5
#
# Replace BOT_TOKEN and ADMIN_ID, then run:
# python referral_bot_admin_panel.py

import sqlite3
import logging
import secrets
import time
from typing import List

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, ChatMemberUpdated
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ChatMemberHandler
)

# ---------- CONFIG ----------
BOT_TOKEN = "8377663696:AAEDnlDil88yl3SLGPTeSGc5GKFg8u4DrKM"
ADMIN_ID = 5635549484  # <-- your Telegram user id (int)
DB_FILE = "bot_data.db"
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Defaults (can be changed by admin) ----------
DEFAULT_REFERRAL_BONUS = 10
DEFAULT_REFEREE_BONUS = 5
DEFAULT_MIN_WITHDRAW = 50
# ----------------------------

# ---------- DB helpers ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    vpa TEXT,
                    balance INTEGER DEFAULT 0,
                    referrer INTEGER DEFAULT NULL,
                    joined INTEGER DEFAULT 0,
                    joined_at INTEGER DEFAULT NULL
                )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS withdraws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    vpa TEXT,
                    status TEXT DEFAULT 'pending',
                    requested_at INTEGER,
                    processed_at INTEGER DEFAULT NULL,
                    admin_note TEXT DEFAULT NULL
                )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer INTEGER,
                    referee INTEGER,
                    credited INTEGER DEFAULT 0,
                    credited_at INTEGER DEFAULT NULL
                )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_text TEXT UNIQUE,
                    added_at INTEGER
                )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )""")
    # seed default settings if absent
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("referral_bonus", str(DEFAULT_REFERRAL_BONUS)))
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("referee_bonus", str(DEFAULT_REFEREE_BONUS)))
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("min_withdraw", str(DEFAULT_MIN_WITHDRAW)))
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(query, params)
    if fetch:
        rows = cur.fetchall()
        conn.commit()
        conn.close()
        return rows
    conn.commit()
    conn.close()

# channel helpers
def add_channel_to_db(ch_text: str):
    ts = int(time.time())
    try:
        db_execute("INSERT INTO channels (channel_text, added_at) VALUES (?, ?)", (ch_text, ts))
        return True
    except Exception as e:
        logger.warning("Channel insert failed: %s", e)
        return False

def remove_channel_from_db(ch_text: str):
    db_execute("DELETE FROM channels WHERE channel_text = ?", (ch_text,))

def list_channels_from_db():
    rows = db_execute("SELECT channel_text FROM channels ORDER BY id", (), fetch=True)
    return [r[0] for r in rows] if rows else []

# settings helpers
def get_setting(key: str, as_int=True, default=0):
    rows = db_execute("SELECT value FROM settings WHERE key = ?", (key,), fetch=True)
    if not rows:
        return default
    val = rows[0][0]
    return int(val) if as_int else val

def set_setting(key: str, value):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

# ---------- Business logic ----------
def ensure_user_record(user):
    if not user:
        return
    uid = user.id
    rows = db_execute("SELECT 1 FROM users WHERE user_id = ?", (uid,), fetch=True)
    if not rows:
        db_execute("INSERT INTO users (user_id, first_name, username) VALUES (?, ?, ?)",
                   (uid, user.first_name or "", user.username or ""))
    else:
        db_execute("UPDATE users SET first_name=?, username=? WHERE user_id=?",
                   (user.first_name or "", user.username or "", uid))

def credit_balance(user_id: int, amount: int):
    db_execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))

def set_vpa(user_id: int, vpa: str):
    db_execute("UPDATE users SET vpa = ? WHERE user_id = ?", (vpa, user_id))

def get_user(user_id: int):
    rows = db_execute("SELECT user_id, first_name, username, vpa, balance, referrer, joined, joined_at FROM users WHERE user_id = ?", (user_id,), fetch=True)
    return rows[0] if rows else None

def create_withdraw_request(user_id: int, amount: int, vpa: str):
    ts = int(time.time())
    db_execute("INSERT INTO withdraws (user_id, amount, vpa, requested_at) VALUES (?, ?, ?, ?)",
               (user_id, amount, vpa, ts))
    rows = db_execute("SELECT last_insert_rowid()", (), fetch=True)
    return rows[0][0] if rows else None

def get_pending_withdraws():
    return db_execute("SELECT id, user_id, amount, vpa, requested_at FROM withdraws WHERE status='pending'", (), fetch=True)

def set_withdraw_status(wid: int, status: str, admin_note=None):
    ts = int(time.time())
    db_execute("UPDATE withdraws SET status=?, processed_at=?, admin_note=? WHERE id=?", (status, ts, admin_note, wid))

def record_referral(referrer: int, referee: int):
    db_execute("INSERT INTO referrals (referrer, referee) VALUES (?, ?)", (referrer, referee))

def credit_referral_if_needed(referrer: int, referee: int):
    rows = db_execute("SELECT id, credited FROM referrals WHERE referrer=? AND referee=?", (referrer, referee), fetch=True)
    if not rows:
        return False
    rid, credited = rows[0]
    if credited:
        return False
    # read current settings
    REFERRAL_BONUS = get_setting("referral_bonus", as_int=True, default=DEFAULT_REFERRAL_BONUS)
    REFEREE_BONUS = get_setting("referee_bonus", as_int=True, default=DEFAULT_REFEREE_BONUS)
    credit_balance(referrer, REFERRAL_BONUS)
    credit_balance(referee, REFEREE_BONUS)
    db_execute("UPDATE referrals SET credited=1, credited_at=? WHERE id=?", (int(time.time()), rid))
    return True

def mark_user_joined(user_id: int):
    db_execute("UPDATE users SET joined=1, joined_at=? WHERE user_id=?", (int(time.time()), user_id))

def mark_user_left(user_id: int):
    db_execute("UPDATE users SET joined=0 WHERE user_id=?", (user_id,))
    db_execute("UPDATE users SET balance=0 WHERE user_id=?", (user_id,))

# ---------- Telegram handlers ----------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)

    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].split("_", 1)[1])
            if referrer_id != user.id:
                rows = db_execute("SELECT referrer FROM users WHERE user_id = ?", (user.id,), fetch=True)
                current_ref = rows[0][0] if rows else None
                if not current_ref:
                    db_execute("UPDATE users SET referrer = ? WHERE user_id = ?", (referrer_id, user.id))
                    record_referral(referrer_id, user.id)
        except Exception as e:
            logger.exception("Bad referral param: %s", e)

    # build keyboard with current channels from DB
    channels = list_channels_from_db()
    kb = []
    kb.append([InlineKeyboardButton("I Joined ✅", callback_data="verify_join")])
    for ch in channels:
        # ch may be like "@mychannel" or channel id - we'll create t.me link if startswith @
        if ch.startswith("@"):
            url = f"https://t.me/{ch.lstrip('@')}"
            kb.append([InlineKeyboardButton(f"Join {ch}", url=url)])
        else:
            kb.append([InlineKeyboardButton(f"Join {ch}", url=f"https://t.me/{ch}")])

    bot_username = context.bot.username or "thisbot"
    text = (f"Hey {user.first_name or 'there'}!\n\n"
            "Please join the channels listed below, then press 'I Joined' to verify.\n\n"
            f"Invite friends to earn: share your link:\n`https://t.me/{bot_username}?start=ref_{user.id}`\n\n"
            "Use /wallet to see your balance, /setvpa to set your UPI VPA, and /withdraw to request a payout.")
    await update.message.reply_markdown(text, reply_markup=InlineKeyboardMarkup(kb))

async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    ensure_user_record(user)
    channels = list_channels_from_db()
    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user.id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception as e:
            logger.warning("Error checking membership for %s in %s: %s", user.id, ch, e)
            not_joined.append(ch)

    if not_joined:
        text = "I couldn't verify you in these channels. Make sure you joined them and bot is admin in the channels:\n" + "\n".join(not_joined)
        await q.edit_message_text(text)
        return

    mark_user_joined(user.id)
    row = db_execute("SELECT referrer FROM users WHERE user_id = ?", (user.id,), fetch=True)
    ref = row[0][0] if row else None
    credited = False
    if ref:
        credited = credit_referral_if_needed(ref, user.id)
    await q.edit_message_text("✅ Verified — thanks for joining!\n" +
                              (f"You and your referrer got credited." if credited else "No referral to credit or already credited."))

async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Unknown command.\nType /help to see the list of available commands."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        "📘 *Bot Help Guide*\n\n"
        "💰 *Refer & Earn*\n"
        "Invite friends using your unique link — both of you get bonus credits when they join all required channels.\n\n"
        "👥 *Commands for Users*\n"
        "/start - Start the bot and see your referral link\n"
        "/wallet - Check your balance & VPA\n"
        "/setvpa <vpa@bank> - Set your UPI VPA for withdrawals\n"
        "/withdraw <amount> - Request withdrawal (manual approval)\n"
        "/help - Show this help message\n"
        "/myid - Show your Telegram user ID\n\n"
        "⚙️ *Admin Commands*\n"
        "Only for the admin:\n"
        "/admin_panel or /adminpanel - Open admin panel\n"
        "/add_channel or /addchannel @channel - Add required join channel\n"
        "/remove_channel or /removechannel @channel - Remove a join channel\n"
        "/list_channels or /listchannels - Show current channels\n"
        "/list_withdraws or /listwithdraws - View pending withdrawals\n"
        "/approve_withdraw or /approvewithdraw <id> - Approve payout\n"
        "/decline_withdraw or /declinewithdraw <id> [reason] - Decline payout\n"
        "/set_bonus or /setbonus ref|referee <amt> - Change referral bonuses\n"
        "/set_min_withdraw or /setminwithdraw <amt> - Change min withdraw limit\n"
        "/add_balance or /addbalance <user_id> <amt> - Add manual credits\n"
        "/broadcast <message> - Send message to all users\n"
        "/stats - View bot stats\n"
        "/whoami - Show your ID and admin status"
    )
    await update.message.reply_markdown(text)

async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    row = get_user(user.id)
    if not row:
        await update.message.reply_text("No data found; try /start.")
        return
    uid, first_name, username, vpa, balance, referrer, joined, joined_at = row
    text = f"Wallet for {first_name} (@{username or '—'}):\n\nBalance: {balance} credits\nVPA: {vpa or 'Not set'}\nJoined status: {'Joined' if joined else 'Not joined'}"
    kb = [
        [InlineKeyboardButton("Set VPA", callback_data="set_vpa")],
        [InlineKeyboardButton("Withdraw", callback_data="withdraw_start")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def setvpa_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setvpa your@vpa")
        return
    vpa = args[0][:80]
    set_vpa(user.id, vpa)
    await update.message.reply_text(f"Saved your VPA: {vpa}")

async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /withdraw <amount>")
        return
    try:
        amount = int(args[0])
        if amount <= 0:
            raise ValueError()
    except:
        await update.message.reply_text("Amount must be a positive integer.")
        return
    row = get_user(user.id)
    balance = row[4]
    vpa = row[3]
    if not vpa:
        await update.message.reply_text("You must set your VPA first: /setvpa your@vpa")
        return
    min_w = get_setting("min_withdraw", as_int=True, default=DEFAULT_MIN_WITHDRAW)
    if amount < min_w:
        await update.message.reply_text(f"Minimum withdrawal amount is {min_w}.")
        return
    if amount > balance:
        await update.message.reply_text("Insufficient balance.")
        return
    wid = create_withdraw_request(user.id, amount, vpa)
    await update.message.reply_text(f"Withdrawal request #{wid} created for {amount} credits. Admin will process it soon.")

async def withdraw_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("To request payout, use command:\n/withdraw <amount>\nMake sure your VPA is set with /setvpa")

async def set_vpa_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Set your VPA with command: /setvpa your@vpa")

async def profile_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user)
    channels = list_channels_from_db()
    try:
        ch = channels[0] if channels else None
        if ch:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user.id)
            if member.status in ("left", "kicked"):
                mark_user_left(user.id)
                await update.message.reply_text("We detected you left a required channel. Your balance has been reset to 0. Rejoin to become eligible again.")
                return
            else:
                rows = db_execute("SELECT joined FROM users WHERE user_id = ?", (user.id,), fetch=True)
                if rows and rows[0][0] == 0:
                    mark_user_joined(user.id)
                    row = db_execute("SELECT referrer FROM users WHERE user_id = ?", (user.id,), fetch=True)
                    ref = row[0][0] if row else None
                    credited = False
                    if ref:
                        credited = credit_referral_if_needed(ref, user.id)
                    await update.message.reply_text("Welcome back! Verified your join. " + ("Referral credited." if credited else ""))
    except Exception as e:
        logger.warning("Couldn't verify membership: %s", e)
    await update.message.reply_text("Nice — got your message. Use /wallet to see your balance.")

# ---------- Admin handlers ----------
def is_admin(user_id: int):
    return user_id == ADMIN_ID

async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    pending = get_pending_withdraws()
    channels = list_channels_from_db()
    text = f"Admin Panel\nPending withdrawals: {len(pending)}\nRequired channels: {len(channels)}"
    kb = [
        [InlineKeyboardButton("List Pending", callback_data="admin_list_pending")],
        [InlineKeyboardButton("List Channels", callback_data="admin_list_channels")],
        [InlineKeyboardButton("Add Channel", callback_data="admin_add_channel")],
        [InlineKeyboardButton("Remove Channel", callback_data="admin_remove_channel")],
        [InlineKeyboardButton("Broadcast", callback_data="admin_broadcast")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Seen user id: {user.id}\nYour username: @{user.username}\nIs admin according to config: {user.id == ADMIN_ID}")

async def list_withdraws_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    rows = get_pending_withdraws()
    if not rows:
        await update.message.reply_text("No pending withdrawals.")
        return
    text_lines = []
    for r in rows:
        wid, uid, amt, vpa, ts = r
        text_lines.append(f"#{wid} — user {uid} — {amt} — {vpa} — req at {time.ctime(ts)}")
    await update.message.reply_text("\n".join(text_lines))

async def approve_withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /approve_withdraw <withdraw_id>")
        return
    try:
        wid = int(args[0])
    except:
        await update.message.reply_text("Bad id.")
        return
    rows = db_execute("SELECT user_id, amount FROM withdraws WHERE id=? AND status='pending'", (wid,), fetch=True)
    if not rows:
        await update.message.reply_text("Withdraw not found or already processed.")
        return
    uid, amt = rows[0]
    db_execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, uid))
    set_withdraw_status(wid, "approved", admin_note=f"Approved by admin {user.id}")
    await update.message.reply_text(f"Approved withdraw #{wid}. Please transfer {amt} to user's VPA manually.")
    try:
        await context.bot.send_message(uid, f"Your withdrawal request #{wid} for {amt} credits was approved. Admin will process it.")
    except Exception:
        logger.warning("Could not notify user %s", uid)

async def decline_withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /decline_withdraw <withdraw_id> [reason]")
        return
    try:
        wid = int(args[0])
    except:
        await update.message.reply_text("Bad id.")
        return
    reason = " ".join(args[1:]) if len(args) > 1 else None
    rows = db_execute("SELECT user_id, amount FROM withdraws WHERE id=? AND status='pending'", (wid,), fetch=True)
    if not rows:
        await update.message.reply_text("Withdraw not found or already processed.")
        return
    uid, amt = rows[0]
    set_withdraw_status(wid, "declined", admin_note=reason)
    await update.message.reply_text(f"Declined withdraw #{wid}.")
    try:
        await context.bot.send_message(uid, f"Your withdrawal request #{wid} was declined. Reason: {reason or 'No reason provided.'}")
    except Exception:
        pass

async def add_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /add_balance <user_id> <amount>")
        return
    try:
        uid = int(args[0]); amt = int(args[1])
    except:
        await update.message.reply_text("Bad args.")
        return
    credit_balance(uid, amt)
    await update.message.reply_text(f"Added {amt} to {uid}.")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    rows = db_execute("SELECT user_id FROM users", (), fetch=True)
    sent = 0
    for r in rows:
        try:
            await context.bot.send_message(r[0], text)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"Broadcast sent to {sent} users (attempted).")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    total = db_execute("SELECT COUNT(*) FROM users", (), fetch=True)[0][0]
    joined = db_execute("SELECT COUNT(*) FROM users WHERE joined=1", (), fetch=True)[0][0]
    pending = db_execute("SELECT COUNT(*) FROM withdraws WHERE status='pending'", (), fetch=True)[0][0]
    await update.message.reply_text(f"Stats:\nUsers: {total}\nJoined: {joined}\nPending withdraws: {pending}")

# ---------- Admin channel control commands ----------
async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /add_channel @channelusername")
        return
    ch = args[0].strip()
    success = add_channel_to_db(ch)
    if success:
        await update.message.reply_text(f"Channel {ch} added to required list. Make sure bot is admin in that channel.")
    else:
        await update.message.reply_text(f"Could not add {ch}. It may already exist.")

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"Seen user id: {user.id}\nYour username: @{user.username}\nIs admin according to config: {user.id == ADMIN_ID}")

async def remove_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /remove_channel @channelusername")
        return
    ch = args[0].strip()
    remove_channel_from_db(ch)
    await update.message.reply_text(f"Channel {ch} removed from required list.")

async def list_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    channels = list_channels_from_db()
    if not channels:
        await update.message.reply_text("No required channels set.")
        return
    await update.message.reply_text("Required channels:\n" + "\n".join(channels))

# ---------- Admin settings control ----------
async def set_bonus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if len(args) != 2 or args[0] not in ("ref", "referee"):
        await update.message.reply_text("Usage: /set_bonus ref|referee <amount>")
        return
    key = "referral_bonus" if args[0] == "ref" else "referee_bonus"
    try:
        amt = int(args[1])
    except:
        await update.message.reply_text("Amount must be integer.")
        return
    set_setting(key, amt)
    await update.message.reply_text(f"Set {key} to {amt}.")

async def set_min_withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("You are not admin.")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /set_min_withdraw <amount>")
        return
    try:
        amt = int(args[0])
    except:
        await update.message.reply_text("Amount must be integer.")
        return
    set_setting("min_withdraw", amt)
    await update.message.reply_text(f"Minimum withdrawal set to {amt}.")

# ---------- ChatMember updates ----------
def extract_status_change(old: ChatMember, new: ChatMember):
    return old.status, new.status

async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result: ChatMemberUpdated = update.chat_member
    user = result.from_user
    old, new = result.old_chat_member, result.new_chat_member
    old_status, new_status = extract_status_change(old, new)
    logger.info("ChatMember update: user=%s old=%s new=%s chat=%s", user.id, old_status, new_status, update.effective_chat.id)

    # check if this chat is in our required channels list (match by username or id)
    channels = list_channels_from_db()
    chat_identifier = None
    if update.effective_chat and update.effective_chat.username:
        chat_identifier = "@" + update.effective_chat.username
    else:
        chat_identifier = str(update.effective_chat.id) if update.effective_chat else None

    if chat_identifier and chat_identifier in channels:
        if new_status in ("left", "kicked"):
            mark_user_left(user.id)
            try:
                await context.bot.send_message(user.id, "You left a required channel — your balance has been reset to 0. Rejoin to be eligible again.")
            except Exception:
                pass
        elif new_status in ("member", "administrator", "creator"):
            mark_user_joined(user.id)
            row = db_execute("SELECT referrer FROM users WHERE user_id = ?", (user.id,), fetch=True)
            ref = row[0][0] if row else None
            if ref:
                credit_referral_if_needed(ref, user.id)
            try:
                await context.bot.send_message(user.id, "Thanks for rejoining — you're verified again.")
            except Exception:
                pass

# ---------- CallbackQuery router ----------
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    if data == "verify_join":
        await verify_join_callback(update, context)
    elif data == "withdraw_start":
        await withdraw_start_cb(update, context)
    elif data == "set_vpa":
        await set_vpa_cb(update, context)
    elif data == "admin_list_pending":
        await list_withdraws_cmd(update, context)
    elif data == "admin_list_channels":
        await list_channels_cmd(update, context)
    elif data == "admin_add_channel":
        await q.edit_message_text("Use /add_channel @channelusername to add channel.")
    elif data == "admin_remove_channel":
        await q.edit_message_text("Use /remove_channel @channelusername to remove channel.")
    elif data == "admin_broadcast":
        await q.edit_message_text("Use /broadcast <message> to send broadcast to all users.")
    else:
        await q.answer("Unknown action.")

# ---------- Main ----------
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # user commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_handler(CommandHandler("setvpa", setvpa_cmd))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("whoami", whoami))
    # admin
 # ===== ADMIN COMMAND HANDLERS (fixed with aliases) =====
    app.add_handler(CommandHandler(["admin_panel", "adminpanel"], admin_panel_cmd))
    app.add_handler(CommandHandler(["list_withdraws", "listwithdraws"], list_withdraws_cmd))
    app.add_handler(CommandHandler(["approve_withdraw", "approvewithdraw"], approve_withdraw_cmd))
    app.add_handler(CommandHandler(["decline_withdraw", "declinewithdraw"], decline_withdraw_cmd))
    app.add_handler(CommandHandler(["add_balance", "addbalance"], add_balance_cmd))
    app.add_handler(CommandHandler(["broadcast"], broadcast_cmd))
    app.add_handler(CommandHandler(["stats"], stats_cmd))

    app.add_handler(CommandHandler(["add_channel", "addchannel"], add_channel_cmd))
    app.add_handler(CommandHandler(["remove_channel", "removechannel"], remove_channel_cmd))
    app.add_handler(CommandHandler(["list_channels", "listchannels"], list_channels_cmd))

    app.add_handler(CommandHandler(["set_bonus", "setbonus"], set_bonus_cmd))
    app.add_handler(CommandHandler(["set_min_withdraw", "setminwithdraw"], set_min_withdraw_cmd))
    # channel admin commands
    app.add_handler(CommandHandler("add_channel", add_channel_cmd))
    app.add_handler(CommandHandler("remove_channel", remove_channel_cmd))
    app.add_handler(CommandHandler("list_channels", list_channels_cmd))

    # settings
    app.add_handler(CommandHandler("set_bonus", set_bonus_cmd))
    app.add_handler(CommandHandler("set_min_withdraw", set_min_withdraw_cmd))

    # callbacks & chat member updates
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    # echo / normal messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, profile_echo))

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()