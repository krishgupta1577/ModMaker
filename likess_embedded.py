import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import threading
import datetime
import time
import os
import re
import json
import base64
import zlib
import sqlite3

# ---------------------------------------------------------------------------
# Wispbyte .env loader
# Wispbyte Environment tab may write variables to /.env rather than exporting
# them into the process environment. Load both common locations explicitly.
# Existing non-empty hosting environment variables take precedence.
# ---------------------------------------------------------------------------
def _load_hosting_env():
    import os
    candidates = [
        "/.env",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    loaded_from = []
    for env_path in dict.fromkeys(candidates):
        try:
            if not os.path.isfile(env_path):
                continue
            with open(env_path, "r", encoding="utf-8-sig") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        continue
                    # Remove matching single/double quotes and optional trailing comments.
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    elif " #" in value:
                        value = value.split(" #", 1)[0].rstrip()
                    # Prefer a non-empty process variable; otherwise use .env value.
                    if value and not os.environ.get(key, "").strip():
                        os.environ[key] = value
            loaded_from.append(env_path)
        except (OSError, UnicodeError) as exc:
            print(f"[ENV] Could not read {env_path}: {type(exc).__name__}: {exc}", flush=True)
    return loaded_from

_ENV_FILES_LOADED = _load_hosting_env()
print(f"[ENV] Environment file(s) loaded: {len(_ENV_FILES_LOADED)}", flush=True)


# ==========================================
# 🔐 CONFIGURATION
# ==========================================
# Set BOT_TOKEN in your hosting 
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required to start likess.py")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

OWNER_ID = 8975516546

# ADMINS structure: {user_id: {"expires": timestamp or None, "autolike_limit": int or None, "autolike_used": int}}
# None for expires = permanent, None for autolike_limit = unlimited
ADMINS = {
    OWNER_ID: {
        "expires": None,          # Permanent
        "autolike_limit": None,   # Unlimited
        "autolike_used": 0
    }
}

def is_admin(user_id):
    """Check if user is admin and not expired."""
    if user_id not in ADMINS:
        return False
    data = ADMINS[user_id]
    expires = data.get("expires")
    if expires is not None and time.time() > expires:
        # Auto remove expired admin (except owner)
        if user_id != OWNER_ID:
            del ADMINS[user_id]
        return False
    return True

def get_admin_data(user_id):
    return ADMINS.get(user_id)

# Both groups are mandatory for force-join verification.
REQUIRED_GROUPS = [
    {
        "id": -1003946894376,
        "name": "Fʀᴇᴇ Fɪʀᴇ AᴜᴛᴏLɪᴋᴇ",
        "link": "https://t.me/AutoLike_220Daily",
    },
]
# Keep the legacy notification target pointed at the second group.
REQUIRED_GROUP_ID = -1004448538957

MANUAL_LIKE_API = "https://freeuser20.vercel.app"
AUTO_LIKE_API = "https://220likes-api.vercel.app"
IMAGE_URL = "https://myimgs.org/storage/images/74851/35174.jpg"

# ==========================================
# 🎨 PREMIUM EMOJI MAPPING 
# ==========================================
_EMOJI_MAP_B64 = "eJyNVUtuHDkMvUqgdRb8i8xVEi/sru4bZDWYu+fJLnWXhJnB2HChYDwWRb6P/mq/fh+pB57394/249vP5mIkxiIaKuWG9/b924p7+/yHdPKvEsMrVWfr5s49qNoLMcF2+wSHpKQLibNXsSZFeyHeZqsbno+s8/sahFMVO3WxHqptx71dz3jI82Ck7L0zOll1pd523FJ4p/tXoXZl48T5hKOKMtqOWwuTzo7mklaJdWQIxls7DtxciORZ0tHDsltmpAXatRdi6XI7zuNZdNcSKYvsperedtzsEvbr9+NOj3m8cnL8SWIZCcLajjsLufq1UCQqw6qkMJqDkLbj1oUcfG6yKvGrXDnelbTtuJU7pudalBINnc3CyLLtuDmj61kCXWF9aqgLY46ntIBYuzzel7WwJymFdE2H1Hq1f0afHfXx7MhdxZVwvIRv8iTiE7FydzvFrMGgOgKck4Jitbbj1sKwpyaJZTAIJasTU9txi32OsukCidQYbiOIhX21z8CtvqPjyp1ESIAOZ6K247YZ36dhh+tgA42xWtFsO24trGMK2yW0mAz2Kwcrbcc9C+/j7F3ncig7yGcGe9qTX76buLWQYxZKJUQcSYiZ6Je4m7iT9Hin1UrwzGA9EZHJ6Np23OoI89fmHjNskRJlCE/8wPrYdvuvmlXDdDtH4JFueMAxPbzTurSBmyNUXUdQ485gVjMR9fjONPULt9J0l68MJ6gC9wPyQCrUOVdTD9yqqBtPozE8SuSKrKPgS3BN3NrRnsKojtsEUk6kVvq2qIFb+VXEEjodYxqTJcoQKrCtlyiY69d1/Xvl+vGDr7RM+DzLx8e5JdKhC9gm4Ifwi9P/31dWumeijitm6NQ8YCywvtG9J+rtPu08RofcECGB4CGttuM2O58qhT0gCcyRPQvhaNJ23BYg/sy6GMmq6G2By3sPENy/f/8BLa8zYA=="   # <-- paste here
_BUTTON_MAP_B64 = "eJyFzk9Lw0AQBfCvMu7Zw+zu/PUmphQEuyE2ikfBFQslKQEVEb+7SQNePczp/XhvvsN108DdBvYFnkrfwbYrfRuuIDBpikKmZCZGIu7hEsJ937al269ClLOn5CSmnjPzIsrjbtOtOTkjz5csoxopLfnteBjg5u15GOrxzFISNyH35Ohzo+Af207j++kfVF8u4KFOh9cv2I2f63BSnFWcF5mjCp5fb+rHcTzVaSGCmC2iRkTJkYUzhZ9fAlU/Ug==" # <-- paste here

def _get_emoji_map():
    if not hasattr(_get_emoji_map, "cache"):
        data = zlib.decompress(base64.b64decode(_EMOJI_MAP_B64))
        _get_emoji_map.cache = json.loads(data.decode('utf-8'))
    return _get_emoji_map.cache

def _get_button_map():
    if not hasattr(_get_button_map, "cache"):
        data = zlib.decompress(base64.b64decode(_BUTTON_MAP_B64))
        _get_button_map.cache = json.loads(data.decode('utf-8'))
    return _get_button_map.cache

def convert_normal_emojis_to_premium(text: str) -> str:
    if not text:
        return text
    emoji_map = _get_emoji_map()
    for emoji_char in sorted(emoji_map.keys(), key=len, reverse=True):
        if emoji_char in text:
            eid, fallback = emoji_map[emoji_char]
            tag = f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
            text = text.replace(emoji_char, tag)
    return text

def strip_emojis(text: str) -> str:
    """Remove all emoji characters from text."""
    emoji_pattern = re.compile(
        r'['
        r'\U0001F1E6-\U0001F1FF\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF'
        r'\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF'
        r'\u2600-\u26FF\u2700-\u27BF\u2300-\u23FF\uFE0F\u200D'
        r']+',
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()

def make_premium_button(text: str, callback_data: str = None, url: str = None, icon_id: str = None, style: str = "primary"):
    """Create button with premium icon and color style."""
    clean_text = strip_emojis(text)
    kwargs = {"text": clean_text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if icon_id:
        kwargs["icon_custom_emoji_id"] = icon_id
    kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

# ==========================================
# 💾 RAM DATABASE (UNCHANGED)
# ==========================================
GLOBAL_LIMIT = 60
REGIONS = ["IND", "BR", "US", "NA", "SAC", "SG", "RU", "ID", "TW", "VN", "TH", "ME", "PK", "CIS", "BD", "EUROPE", "EU"]
total_likes_used = 0
total_requests_used = 0
last_request_time = 0
REQUEST_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
users = set()
ALLOWED_GROUPS = [-1003946894376]
user_success_logs = {}
user_custom_limits = {}
autolike_list = {}
BOT_ENABLED = True  # /on /off se control hota hai
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "likes_state.json")

def save_data():
    """Atomically persist bot state so restarts do not erase approvals/limits."""
    with STATE_LOCK:
        state = {
            "autolike_list": autolike_list,
            "admins": {str(k): v for k, v in ADMINS.items()},
            "allowed_groups": ALLOWED_GROUPS,
            "users": sorted(users),
            "user_success_logs": user_success_logs,
            "user_custom_limits": user_custom_limits,
            "bot_enabled": BOT_ENABLED,
            "global_limit": GLOBAL_LIMIT,
            "total_likes_used": total_likes_used,
            "total_requests_used": total_requests_used,
            "likes_approvals": {str(k): v for k, v in LIKES_APPROVALS.items()},
            "likes_allowed_groups": {str(k): v for k, v in LIKES_ALLOWED_GROUPS.items()},
            "manual_like_api": MANUAL_LIKE_API,
            "auto_like_api": AUTO_LIKE_API,
        }
        tmp_path = STATE_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, STATE_FILE)
        except Exception as exc:
            print(f"[State Save Error] {exc}")
            try:
                if os.path.exists(tmp_path): os.remove(tmp_path)
            except OSError:
                pass

def load_data():
    """Restore persisted bot state; malformed state is ignored safely."""
    global autolike_list, ADMINS, ALLOWED_GROUPS, BOT_ENABLED, GLOBAL_LIMIT
    global total_likes_used, total_requests_used, MANUAL_LIKE_API, AUTO_LIKE_API
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        autolike_list = state.get("autolike_list", {}) if isinstance(state.get("autolike_list", {}), dict) else {}
        ADMINS = {int(k): v for k, v in state.get("admins", {}).items()}
        ADMINS.setdefault(OWNER_ID, {"expires": None, "autolike_limit": None, "autolike_used": 0})
        ALLOWED_GROUPS = [int(x) for x in state.get("allowed_groups", ALLOWED_GROUPS)]
        users.update(int(x) for x in state.get("users", []))
        user_success_logs.update(state.get("user_success_logs", {}))
        user_custom_limits.update(state.get("user_custom_limits", {}))
        BOT_ENABLED = bool(state.get("bot_enabled", BOT_ENABLED))
        GLOBAL_LIMIT = int(state.get("global_limit", GLOBAL_LIMIT))
        total_likes_used = int(state.get("total_likes_used", total_likes_used))
        total_requests_used = int(state.get("total_requests_used", total_requests_used))
        LIKES_APPROVALS.update({int(k): v for k, v in state.get("likes_approvals", {}).items()})
        LIKES_ALLOWED_GROUPS.update({int(k): str(v) for k, v in state.get("likes_allowed_groups", {}).items()})
        MANUAL_LIKE_API = state.get("manual_like_api", MANUAL_LIKE_API)
        AUTO_LIKE_API = state.get("auto_like_api", AUTO_LIKE_API)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[State Load Error] {exc}; starting with defaults")

DATABASE_FILE = os.path.abspath(os.getenv("AUTOLIKE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")))


def ensure_autolike_table():
    """Create the shared task table if the website has not created it yet."""
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autolike_task (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_id INTEGER UNIQUE,
                user_id INTEGER,
                uid VARCHAR(40) NOT NULL,
                region VARCHAR(20) NOT NULL,
                target_likes INTEGER NOT NULL,
                remaining_likes INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'queued',
                source VARCHAR(20) NOT NULL DEFAULT 'manual',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def load_db():
    """Read active tasks from the same SQLite database used by the website."""
    ensure_autolike_table()
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM autolike_task WHERE status IN ('queued','running') AND remaining_likes > 0 ORDER BY id"
        ).fetchall()
    tasks = {}
    for row in rows:
        tasks[str(row['id'])] = {
            'uid': str(row['uid']),
            'region': str(row['region']),
            'target_likes': int(row['target_likes']),
            'remaining_likes': int(row['remaining_likes']),
            'purchase_id': row['purchase_id'],
            'user_id': row['user_id'],
            'source': row['source'],
        }
    return {'autolike_list': tasks}


def add_autolike_task(uid, region, target, source='manual', purchase_id=None, user_id=None):
    ensure_autolike_table()
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        cur = conn.execute(
            """INSERT INTO autolike_task
               (purchase_id, user_id, uid, region, target_likes, remaining_likes, status, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (purchase_id, user_id, str(uid), str(region).upper(), int(target), int(target), source)
        )
        conn.commit()
        return cur.lastrowid


def remove_autolike_tasks(uid):
    ensure_autolike_table()
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        cur = conn.execute("DELETE FROM autolike_task WHERE uid = ? AND status != 'completed'", (str(uid),))
        conn.commit()
        return cur.rowcount


def extend_autolike_task(uid, region, add_likes):
    """Increase the target of the newest active UID+region task without resetting its remaining count.

    If no active task exists, create a fresh task with add_likes remaining.
    For website-linked tasks, also credit the linked dashboard wallet by add_likes.
    """
    ensure_autolike_table()
    uid = str(uid)
    region = str(region).upper()
    add_likes = int(add_likes)
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT id, target_likes, remaining_likes, user_id FROM autolike_task "
            "WHERE uid=? AND region=? "
            "ORDER BY CASE WHEN status IN ('queued','running') AND remaining_likes > 0 THEN 0 ELSE 1 END, id DESC LIMIT 1",
            (uid, region)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO autolike_task (uid, region, target_likes, remaining_likes, status, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', 'telegram_extend', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (uid, region, add_likes, add_likes)
            )
            task_id = cur.lastrowid
            new_target, new_remaining, user_id = add_likes, add_likes, None
            action = 'created'
        else:
            task_id = int(row['id'])
            new_target = int(row['target_likes'] or 0) + add_likes
            new_remaining = int(row['remaining_likes'] or 0) + add_likes
            user_id = row['user_id']
            conn.execute(
                "UPDATE autolike_task SET target_likes=?, remaining_likes=?, status='queued', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_target, new_remaining, task_id)
            )
            if user_id is not None:
                conn.execute(
                    "UPDATE user SET likes_remaining=COALESCE(likes_remaining,0)+?, "
                    "plan_total_likes=COALESCE(plan_total_likes,0)+? WHERE id=?",
                    (add_likes, add_likes, int(user_id))
                )
            action = 'extended'
        conn.commit()
    return task_id, action, new_remaining, new_target, user_id


def update_task_progress(task_id, likes_given):
    """Apply actual API likes to this purchase and the linked website wallet atomically."""
    ensure_autolike_table()
    with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            "SELECT remaining_likes, user_id FROM autolike_task WHERE id = ?", (int(task_id),)
        ).fetchone()
        if row is None:
            conn.rollback()
            return 0, 0
        task_remaining = max(0, int(row['remaining_likes'] or 0))
        applied = min(max(0, int(likes_given)), task_remaining)
        user_id = row['user_id']
        if user_id is not None:
            user = conn.execute("SELECT likes_remaining FROM user WHERE id = ?", (int(user_id),)).fetchone()
            if user is not None:
                wallet_remaining = max(0, int(user['likes_remaining'] or 0))
                applied = min(applied, wallet_remaining)
                conn.execute(
                    "UPDATE user SET likes_remaining = MAX(0, COALESCE(likes_remaining,0) - ?) WHERE id = ?",
                    (applied, int(user_id))
                )
        new_remaining = max(0, task_remaining - applied)
        new_status = 'completed' if new_remaining <= 0 else 'queued'
        conn.execute(
            "UPDATE autolike_task SET remaining_likes = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_remaining, new_status, int(task_id))
        )
        conn.commit()
        return new_remaining, applied


def migrate_legacy_autolike_tasks():
    """Preserve tasks from the older likes_state.json format without UID collisions."""
    ensure_autolike_table()
    if not isinstance(autolike_list, dict):
        return
    for old_uid, info in list(autolike_list.items()):
        try:
            uid = str(old_uid)
            region = str(info.get('region', 'IND')).upper()
            target = int(info.get('target_likes', 0))
            remaining = int(info.get('remaining_likes', target))
            if uid.isdigit() and region in REGIONS and target > 0:
                with sqlite3.connect(DATABASE_FILE, timeout=30) as conn:
                    conn.execute(
                        """INSERT INTO autolike_task
                           (uid, region, target_likes, remaining_likes, status, source, created_at, updated_at)
                           SELECT ?, ?, ?, ?, 'queued', 'legacy', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                           WHERE NOT EXISTS (
                               SELECT 1 FROM autolike_task WHERE uid=? AND region=? AND target_likes=? AND remaining_likes=?
                           )""",
                        (uid, region, target, remaining, uid, region, target, remaining)
                    )
                    conn.commit()
        except Exception as exc:
            print(f'[Legacy AutoLike Migration] {exc}')

# ==========================================
# 🛠 HELPER FUNCTIONS
# ==========================================
def now_india():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

def get_current_day_str():
    now = now_india()
    if now.hour < 4:
        effective_date = now - datetime.timedelta(days=1)
    else:
        effective_date = now
    return effective_date.strftime("%Y-%m-%d")

def make_request(url):
    global last_request_time
    try:
        with REQUEST_LOCK:
            wait = max(0, 1 - (time.time() - last_request_time))
            if wait:
                time.sleep(wait)
            last_request_time = time.time()
            return requests.get(url, timeout=30)
    except requests.RequestException as e:
        print(f"[API Error] {e}")
        return None

def get_remaining_likes():
    """Return a consistent, thread-safe snapshot of the remaining global likes."""
    with STATE_LOCK:
        return max(0, int(GLOBAL_LIMIT) - int(total_requests_used))


def is_member(user_id):
    """User must join all required groups."""
    try:
        for required_group in REQUIRED_GROUPS:
            member = bot.get_chat_member(required_group["id"], user_id)
            if member.status in ["left", "kicked"]:
                return False
        return True
    except Exception as e:
        print(f"🚫 Could not check membership for user {user_id}: {e}")
        return False


def send_force_join_prompt(message):
    """Show join buttons for all required groups and a verification button."""
    markup = InlineKeyboardMarkup()
    for required_group in REQUIRED_GROUPS:
        btn = make_premium_button(
            "Join " + required_group["name"],
            url=required_group["link"],
            icon_id=_get_button_map().get("Join Group"),
            style="primary"
        )
        markup.add(btn)

    btn_ver = make_premium_button(
        "Joined! Verify Now",
        callback_data="verify_join",
        icon_id=_get_button_map().get("Joined! Verify Now"),
        style="success"
    )
    markup.add(btn_ver)
    warn_text = (
        "<b>🛑 Stop! To use this command, you must join all three required groups.</b>"
        + chr(10) + chr(10)
        + "Please join all three groups and then click the 'Verify Now' button below."
    )
    warn_text = convert_normal_emojis_to_premium(warn_text)
    bot.reply_to(message, warn_text, reply_markup=markup)


# ==========================================
# 🔐 FORCE JOIN CALLBACK (likes.py style)
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data == "verify_join")
def handle_verification_callback(call):
    user_id = call.from_user.id
    if is_member(user_id):
        bot.answer_callback_query(call.id, "✅ Verification successful!")
        msg_text = convert_normal_emojis_to_premium(
            "<b>✅ Verification successful!</b>" + chr(10) +
            "You can now send your <code>/like</code> command again."
        )
        try:
            bot.edit_message_text(
                msg_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception:
            bot.send_message(call.message.chat.id, msg_text)
    else:
        bot.answer_callback_query(
            call.id,
            "❌ You haven't joined the channel and group yet.",
            show_alert=True
        )


# ==========================================
# 🎮 MANUAL LIKEBOT COMMANDS
# ==========================================
@bot.message_handler(commands=['start'])
def handle_start(message):
    if message.chat.type != 'private':
        return  
    user_id = message.from_user.id
    if user_id not in users:
        users.add(user_id)
        save_data()
    user_name = message.from_user.first_name
    start_text = (f"🎉 WELCOME {user_name} 🎉\n"
                  "━━━✦✦━━━\n"
                  "🔥 Ready to Fire, Ready to Win 🔥\n"
                  "😎 Enjoy the Game with our BOT 🚀\n"
                  "━━━━━━━━━━━━━━━\n"
                  "<b>Command -</b>\n"
                  "<code>/like {region} {uid}</code>")
    start_text = convert_normal_emojis_to_premium(start_text)
    markup = InlineKeyboardMarkup()
    bot_username = bot.get_me().username
    add_to_group_link = f"https://t.me/{bot_username}?startgroup=true"
    
    btn1 = make_premium_button("ADD ME TO YOUR GROUP", url=add_to_group_link, 
                               icon_id=_get_button_map().get("ADD ME TO YOUR GROUP"), style="primary")
    btn2 = make_premium_button("SUPPORT", url="https://t.me/TITANLIKESGROUP",
                               icon_id=_get_button_map().get("SUPPORT"), style="success")
    btn3 = make_premium_button("OWNER", url="https://t.me/princegmr",
                               icon_id=_get_button_map().get("OWNER"), style="danger")
    markup.add(btn1)
    markup.add(btn2, btn3)
    bot.reply_to(message, start_text, reply_markup=markup)

@bot.message_handler(commands=['help'])
def handle_help(message):
    user_id = message.from_user.id
    
    if is_admin(user_id):
        # Admin / Owner help
        help_text = (
            "👑 <b>ADMIN / OWNER COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🆔 <code>/getid</code> - Current chat ID\n"
            "📢 <code>/broadcast &lt;message&gt;</code> - Broadcast to users/groups\n"
            "👤 <code>/uselimit &lt;user_id&gt; &lt;limit&gt;</code> - Set user daily limit\n"
            "📊 <code>/setlimit &lt;number&gt;</code> - Set global daily limit\n"
            "✅ <code>/approve &lt;group_id&gt;</code> - Approve a group\n"
            "❌ <code>/disapprove &lt;group_id&gt;</code> - Disapprove a group\n"
            "📋 <code>/listgroups</code> - List approved groups\n"
            "🔗 <code>/setlikeapi &lt;url&gt;</code> - Set manual like API\n"
            "🔗 <code>/setautoapi &lt;url&gt;</code> - Set auto like API\n"
            "🚀 <code>/autolike &lt;region&gt; &lt;uid&gt; &lt;target&gt;</code> - Add auto like task\n"
            "➕ <code>/extend &lt;region&gt; &lt;uid&gt; &lt;addlike&gt;</code> - Extend existing target\n"
            "🗑 <code>/remove &lt;uid&gt;</code> - Remove auto like task\n"
            "📜 <code>/listuid</code> - Show all auto like UIDs\n"
            "▶️ <code>/run</code> - Run autolike process manually\n"
            "👑 <code>/addadmin &lt;user_id&gt; &lt;days&gt; [max_autolike]</code> - Add temp/permanent admin\n"
            "🗑 <code>/removeadmin &lt;user_id&gt;</code> - Remove an admin (Owner only)\n"
            "🟢 <code>/on</code> - Turn bot ON (Owner only)\n"
            "🔴 <code>/off</code> - Turn bot OFF (Owner only)\n"
            "❤️ <code>/likes &lt;region&gt; &lt;uid&gt;</code> - Instant AutoLike\n<code>/likes &lt;user_id&gt; &lt;uses&gt; &lt;days&gt;</code> - Approve /likes (Owner)\n<code>/likesremove &lt;user_id&gt;</code> - Remove approval (Owner)\n"
            "🔓 <code>/likesallow</code> - Allow /likes in this group (Owner only)\n"
            "🚫 <code>/likesdisallow</code> - Disable /likes in this group (Owner only)\n"
            "📋 <code>/likesgroups</code> - Show all /likes allowed groups (Owner only)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>Also available (User commands):</b>\n"
            "🎉 <code>/start</code> - Welcome message\n"
            "❤️ <code>/like &lt;region&gt; &lt;uid&gt;</code> - Send likes\n"
            "📊 <code>/remain</code> - Global remaining requests"
        )
    else:
        # Normal user help
        help_text = (
            "📚 <b>USER COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🎉 <code>/start</code> - Welcome message\n"
            "❤️ <code>/like &lt;region&gt; &lt;uid&gt;</code> - Send likes\n"
            "   Example: <code>/like IND 1234567890</code>\n"
            "📊 <code>/remain</code> - Check global remaining requests\n"
            "❌ <code>/help</code> - Show this help message\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Bot is free to use."
        )
    
    help_text = convert_normal_emojis_to_premium(help_text)
    bot.reply_to(message, help_text)

@bot.message_handler(commands=["like"])
def handle_like(message):
    user_id = message.from_user.id
    user_str_id = str(user_id)
    
    # Bot OFF check (admins can still use)
    if not BOT_ENABLED and not is_admin(user_id):
        bot.reply_to(message, convert_normal_emojis_to_premium("🛑 <b>Bot is currently OFF.</b>\nPlease wait until the Owner turns it ON."))
        return

    # Force join (likes.py style) - Owner always allowed
    if user_id != OWNER_ID and not is_member(user_id):
        send_force_join_prompt(message)
        return

    if message.chat.id not in ALLOWED_GROUPS:
        deny_text = "🔐 <b>This group is not approved to use the bot.\nPlease contact: @princegmr</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return

    current_day = get_current_day_str()
    
    if not is_admin(user_id):
        user_daily_limit = user_custom_limits.get(user_str_id, 1)
        user_log = user_success_logs.get(user_str_id, {})
        if user_log.get("date") == current_day:
            current_usage = user_log.get("count", 0)
        else:
            current_usage = 0
        if current_usage >= user_daily_limit:
            limit_text = (f"🚫 <b>Daily Limit Reached!</b>\n\n"
                          f"You have used: <b>{current_usage}/{user_daily_limit}</b> successful likes today.\n"
                          "Please try again tomorrow after <b>4:00 AM IST</b>.")
            limit_text = convert_normal_emojis_to_premium(limit_text)
            bot.reply_to(message, limit_text)
            return

    global total_likes_used, total_requests_used
    args = message.text.split()
    if len(args) != 3:
        format_text = "<b>❌ Incorrect format!</b>\nCorrect usage: <code>/like &lt;region&gt; &lt;uid&gt;</code>\nExample: <code>/like IND 1234567890</code>"
        format_text = convert_normal_emojis_to_premium(format_text)
        bot.reply_to(message, format_text)
        return
    region, uid = args[1].upper(), args[2]
    if not uid.isdigit():
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>UID sirf numbers mein hona chahiye.</b>"
        ))
    if region not in REGIONS:
        region_text = f"🗺️ Invalid region: `{region}`"
        region_text = convert_normal_emojis_to_premium(region_text)
        bot.reply_to(message, region_text)
        return
        
    remaining = get_remaining_likes()
    if remaining <= 0 and not is_admin(user_id):
        global_limit_text = "🚫 <b>Global Limit reached! Please try again later.</b>"
        global_limit_text = convert_normal_emojis_to_premium(global_limit_text)
        bot.reply_to(message, global_limit_text)
        return
    
    processing_msg = bot.reply_to(message, convert_normal_emojis_to_premium("⏳ <b>Processing your request...</b>"))
    
    try:
        # Let the API decide how many likes it can send.
        # Do not force a fixed `likes=20` request parameter.
        url = f"{MANUAL_LIKE_API.rstrip('/')}/like?uid={uid}&server_name={region}"
        response = make_request(url)

        if response and response.status_code == 200:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("API response is not a JSON object")
            status = result.get("status")
            # Prefer the player's real before/after counter difference.
            # Some API builds report LikesGivenByAPI for only one account
            # even when the backend has sent likes through several accounts.
            # The counter delta is the best available total for the command.
            likes_given = None
            try:
                likes_before = int(str(result.get("LikesbeforeCommand")).replace(",", "").strip())
                likes_after = int(str(result.get("LikesafterCommand")).replace(",", "").strip())
                counter_delta = likes_after - likes_before
                if counter_delta >= 0:
                    likes_given = counter_delta
            except (TypeError, ValueError):
                pass

            # Fall back to the API field only when counters are unavailable
            # or invalid. Never impose a fixed 20-like cap in the bot.
            if likes_given is None:
                try:
                    likes_given = max(0, int(str(result.get("LikesGivenByAPI")).replace(",", "").strip()))
                except (TypeError, ValueError):
                    likes_given = 0

            if str(status) == "1":
                # Global /setlimit is measured in actual likes, not API calls.
                # Update the global counter and per-user daily usage atomically.
                # This prevents concurrent requests from losing counter/log updates.
                with STATE_LOCK:
                    total_likes_used += likes_given
                    total_requests_used += 1
                    user_log = user_success_logs.get(user_str_id, {})
                    if user_log.get("date") == current_day:
                        current_usage = int(user_log.get("count", 0))
                    else:
                        current_usage = 0
                    user_success_logs[user_str_id] = {
                        "date": current_day,
                        "count": current_usage + 1
                    }
                save_data()

                nickname = str(result.get('PlayerNickname', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
                
                if is_admin(user_id):
                    limit_msg = "📉 <b>Your Limit: Unlimited (Owner)</b>"
                else:
                    user_daily_limit = user_custom_limits.get(user_str_id, 1)
                    limit_msg = f"📉 <b>Your Limit: {current_usage + 1}/{user_daily_limit}</b>"

                text = (
                    f"<blockquote>"
                    f"✅ <b>Lɪᴋᴇꜱ Sᴇɴᴛ Sᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ</b> 🥳\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Pʟᴀʏᴇʀ:</b> {nickname}\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    f"🌍 <b>Rᴇɢɪᴏɴ:</b> {region}\n"
                    f"📊 <b>Lᴇᴠᴇʟ:</b> {result.get('PlayerLevel', 'N/A')}\n"
                    f"❤️ <b>Bᴇꜰᴏʀᴇ:</b> {result.get('LikesbeforeCommand', 'N/A')}\n"
                    f"💝 <b>Aꜰᴛᴇʀ:</b> {result.get('LikesafterCommand', 'N/A')}\n"
                    f"🤖 <b>Gɪᴠᴇɴ Bʏ Bᴏᴛ:</b> {likes_given}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{limit_msg}"
                    f"</blockquote>"
                )
            elif status == 2:
                nickname = str(result.get('PlayerNickname', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
                text = (
                    f"<blockquote>"
                    f"⚠️ <b>Fᴀɪʟᴇᴅ ᴛᴏ Sᴇɴᴅ Lɪᴋᴇꜱ</b>\n"
                    f"👤 <b>Pʟᴀʏᴇʀ:</b> {nickname}\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    f"💝 <b>Total Lɪᴋᴇ</b> {result.get('LikesafterCommand', 'N/A')}\n"
                    f"⚠️ <b>Message:</b> LIMIT_REACHED"
                    f"</blockquote>"
                )
            else:
                nickname = str(result.get('PlayerNickname', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
                text = (
                    f"<blockquote>"
                    f"⚠️ <b>Pʟᴀʏᴇʀ Nᴏᴛ Fᴏᴜɴᴅ</b>\n"
                    f"👤 <b>Pʟᴀʏᴇʀ:</b> {nickname}\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    f"🙅🏻 <b>Message:</b> player_not_found"
                    f"</blockquote>"
                )
        else:
            text = "<blockquote>🔌 <b>Failed to connect to the API. Please try again later.</b></blockquote>"

        text = convert_normal_emojis_to_premium(text)
        markup = InlineKeyboardMarkup()
        dev_btn = make_premium_button("Devloper", url="https://t.me/princegmr",
                                      icon_id=_get_button_map().get("Devloper"), style="primary")
        markup.add(dev_btn)
        bot.edit_message_text(text, message.chat.id, processing_msg.message_id, reply_markup=markup)

    except Exception as e:
        error_text = f"<blockquote>⚙️ <b>Error:</b> {str(e).replace('<', '&lt;').replace('>', '&gt;')}</blockquote>"
        error_text = convert_normal_emojis_to_premium(error_text)
        markup = InlineKeyboardMarkup()
        dev_btn = make_premium_button("Devloper", url="https://t.me/princegmr",
                                      icon_id=_get_button_map().get("Devloper"), style="primary")
        markup.add(dev_btn)
        bot.edit_message_text(error_text, message.chat.id, processing_msg.message_id, reply_markup=markup)

@bot.message_handler(commands=["remain"])
def handle_remain(message):
    if not BOT_ENABLED and not is_admin(message.from_user.id):
        bot.reply_to(message, convert_normal_emojis_to_premium("🛑 <b>Bot is currently OFF.</b>\nPlease wait until the Owner turns it ON."))
        return
    if message.chat.id not in ALLOWED_GROUPS:
        deny_text = "🔐 This group is not approved to use the bot.\nPlease contact: @princegmr"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    remaining = get_remaining_likes()
    reply_text = f"📊 <b>Remaining Requests: {remaining}/{GLOBAL_LIMIT}</b>"
    reply_text = convert_normal_emojis_to_premium(reply_text)
    bot.reply_to(message, reply_text)

@bot.message_handler(commands=['getid'])
def get_chat_id(message):
    if is_admin(message.from_user.id):
        bot.reply_to(message, f"🆔 This chat's ID is: `{message.chat.id}`", parse_mode="Markdown")

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    if not is_admin(message.from_user.id):
        deny_text = "🚫 <b>Access Denied! This command is for the admin only.</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        err_text = "❌ <b>Incorrect format!</b>\nCorrect usage: <code>/broadcast &lt;message&gt;</code>"
        err_text = convert_normal_emojis_to_premium(err_text)
        bot.reply_to(message, err_text)
        return
    broadcast_message = args[1]
    broadcast_message = convert_normal_emojis_to_premium(broadcast_message)
    target_chats = set(ALLOWED_GROUPS).union(users)
    if not target_chats:
        bot.reply_to(message, "📭 No users or groups to broadcast to.")
        return
    success_count = 0
    fail_count = 0
    status_msg = bot.reply_to(message, convert_normal_emojis_to_premium(f"📢 <b>Broadcast started...</b>\nSending to {len(target_chats)} chats."))
    for chat_id in target_chats:
        try:
            bot.send_message(chat_id, broadcast_message)
            success_count += 1
        except Exception as e:
            fail_count += 1
        time.sleep(0.1)
    final_report = (
        f"🏁 <b>Broadcast Finished!</b>\n\n"
        f"✅ Sent successfully: {success_count}\n"
        f"❌ Failed to send: {fail_count}"
    )
    final_report = convert_normal_emojis_to_premium(final_report)
    bot.edit_message_text(final_report, message.chat.id, status_msg.message_id)

@bot.message_handler(commands=["uselimit"])
def handle_set_user_limit(message):
    if not is_admin(message.from_user.id):
        deny_text = "🚫 <b>Access Denied! This command is for the admin only.</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    args = message.text.split()
    if len(args) != 3:
        err_text = "❌ <b>Incorrect format!</b>\nCorrect usage: <code>/uselimit {user_id} {uses}</code>\nExample: <code>/uselimit 123456789 2</code>"
        err_text = convert_normal_emojis_to_premium(err_text)
        bot.reply_to(message, err_text)
        return
    try:
        target_user_id = str(args[1])
        new_limit = int(args[2])
        if not target_user_id.isdigit() or new_limit < 1 or new_limit > 1000:
            return bot.reply_to(message, "❌ User ID valid hona chahiye aur limit 1–1000 ke beech honi chahiye.")
        user_custom_limits[target_user_id] = new_limit
        save_data()
        reply_text = f"✅ <b>Limit Updated!</b>\n\nUser ID: <code>{target_user_id}</code>\nNew Daily Limit: <b>{new_limit}</b> likes per day."
        reply_text = convert_normal_emojis_to_premium(reply_text)
        bot.reply_to(message, reply_text)
    except ValueError:
        bot.reply_to(message, "❌ <b>Error:</b> User ID and Limit must be numbers.")

@bot.message_handler(commands=["setlimit", "approve", "disapprove", "listgroups"])
def handle_admin_commands(message):
    if not is_admin(message.from_user.id):
        deny_text = "🚫 <b>Access Denied! This command is for the admin only.</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    command, *args = message.text.split()
    usage_text = ""
    if command == "/approve" and not args: usage_text = "/approve <group_id>"
    elif command == "/disapprove" and not args: usage_text = "/disapprove <group_id>"
    elif command == "/setlimit" and not args: usage_text = "/setlimit <number>"
    if usage_text:
        bot.reply_to(message, f"❌ Incorrect format!\nCorrect usage: `{usage_text}`", parse_mode="Markdown")
        return
    try:
        if command == "/approve":
            group_id = int(args[0])
            if group_id in ALLOWED_GROUPS:
                bot.reply_to(message, "👍 This group is already approved.")
            else:
                ALLOWED_GROUPS.append(group_id)
                save_data()
                bot.reply_to(message, f"✅ Group Approved!\nID: `{group_id}`", parse_mode="Markdown")
        elif command == "/disapprove":
            group_id = int(args[0])
            if group_id in ALLOWED_GROUPS:
                ALLOWED_GROUPS.remove(group_id)
                save_data()
                bot.reply_to(message, f"❌ Group Disapproved!\nID: `{group_id}`", parse_mode="Markdown")
            else:
                bot.reply_to(message, "🤷‍♂️ This group is not in the approved list.")
        elif command == "/listgroups":
            if not ALLOWED_GROUPS:
                bot.reply_to(message, "📭 No groups are currently approved.")
            else:
                text = "✅ Approved Group IDs:\n" + "\n".join([f"- `{gid}`" for gid in ALLOWED_GROUPS])
                bot.reply_to(message, text, parse_mode="Markdown")
        elif command == "/setlimit":
            global GLOBAL_LIMIT
            new_global_limit = int(args[0])
            if new_global_limit < 1 or new_global_limit > 1000000:
                return bot.reply_to(message, "❌ Global limit 1–1,000,000 ke beech honi chahiye.")
            with STATE_LOCK:
                GLOBAL_LIMIT = new_global_limit
            save_data()
            reply_text = f"✅ Global limit is now set to {GLOBAL_LIMIT}."
            reply_text = convert_normal_emojis_to_premium(reply_text)
            bot.reply_to(message, reply_text)
    except (ValueError, IndexError):
        bot.reply_to(message, "⚙️ Invalid command format or ID.")
    except Exception as e:
        bot.reply_to(message, f"⚙️ An error occurred: {e}")

# ==========================================
# 🆕 API SETTER COMMANDS
# ==========================================
@bot.message_handler(commands=["setlikeapi"])
def handle_setlikeapi(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "🚫 <b>Access Denied! This command is for the admin only.</b>")
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        bot.reply_to(message, "❌ <b>Usage:</b> <code>/setlikeapi {api_url}</code>")
        return
    global MANUAL_LIKE_API
    if not args[1].strip().startswith(("https://", "http://")):
        return bot.reply_to(message, "❌ API URL http:// ya https:// se start hona chahiye.")
    MANUAL_LIKE_API = args[1].strip().rstrip("/")
    save_data()
    reply_text = f"✅ <b>Manual Like API Updated!</b>\nNew URL: <code>{MANUAL_LIKE_API}</code>"
    reply_text = convert_normal_emojis_to_premium(reply_text)
    bot.reply_to(message, reply_text)

@bot.message_handler(commands=["setautoapi"])
def handle_setautoapi(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "🚫 <b>Access Denied! This command is for the admin only.</b>")
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        bot.reply_to(message, "❌ <b>Usage:</b> <code>/setautoapi {api_url}</code>")
        return
    global AUTO_LIKE_API
    if not args[1].strip().startswith(("https://", "http://")):
        return bot.reply_to(message, "❌ API URL http:// ya https:// se start hona chahiye.")
    AUTO_LIKE_API = args[1].strip().rstrip("/")
    save_data()
    reply_text = f"✅ <b>Auto Like API Updated!</b>\nNew URL: <code>{AUTO_LIKE_API}</code>"
    reply_text = convert_normal_emojis_to_premium(reply_text)
    bot.reply_to(message, reply_text)

@bot.message_handler(commands=["addadmin"])
def handle_addadmin(message):
    if not is_admin(message.from_user.id):
        deny_text = "🚫 <b>Access Denied! This command is for admins only.</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    
    # Only original owner can add new admins
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Only the Owner can add new admins.</b>"))
        return
    
    args = message.text.split()
    # /addadmin <user_id> <days> [max_autolike]
    if len(args) < 3:
        err_text = (
            "❌ <b>Incorrect format!</b>\n"
            "Correct usage: <code>/addadmin &lt;user_id&gt; &lt;days&gt; [max_autolike]</code>\n"
            "Example: <code>/addadmin 123456789 7 5</code>\n"
            "• days = kitne din ke liye admin rahega (0 = permanent)\n"
            "• max_autolike = optional, kitni baar /autolike use kar sakta hai (blank = unlimited)"
        )
        err_text = convert_normal_emojis_to_premium(err_text)
        bot.reply_to(message, err_text)
        return
    
    try:
        new_admin_id = int(args[1])
        days = int(args[2])
        max_autolike = int(args[3]) if len(args) >= 4 else None
        
        if new_admin_id == OWNER_ID:
            bot.reply_to(message, convert_normal_emojis_to_premium("🚫 Owner already permanent admin hai."))
            return
        
        if days < 0:
            bot.reply_to(message, convert_normal_emojis_to_premium("❌ Days 0 ya usse zyada hona chahiye."))
            return
        
        expires = None if days == 0 else time.time() + (days * 24 * 60 * 60)
        
        if max_autolike is not None and max_autolike < 0:
            return bot.reply_to(message, "❌ max_autolike 0 ya usse zyada hona chahiye.")
        ADMINS[new_admin_id] = {
            "expires": expires,
            "autolike_limit": max_autolike,
            "autolike_used": 0
        }
        save_data()
        
        days_text = "Permanent" if days == 0 else f"{days} days"
        limit_text = "Unlimited" if max_autolike is None else str(max_autolike)
        
        reply_text = (
            f"✅ <b>New Admin Added!</b>\n\n"
            f"👤 User ID: <code>{new_admin_id}</code>\n"
            f"⏳ Duration: <b>{days_text}</b>\n"
            f"🚀 Autolike Limit: <b>{limit_text}</b>\n"
            f"📊 Total Admins: <b>{len(ADMINS)}</b>"
        )
        reply_text = convert_normal_emojis_to_premium(reply_text)
        bot.reply_to(message, reply_text)
        
        # Notify new admin
        try:
            notify = (
                f"🎉 <b>You have been made an Admin!</b>\n\n"
                f"⏳ Duration: <b>{days_text}</b>\n"
                f"🚀 Autolike uses allowed: <b>{limit_text}</b>\n"
                f"Type /help to see all admin commands."
            )
            bot.send_message(new_admin_id, convert_normal_emojis_to_premium(notify))
        except:
            pass
            
    except ValueError:
        bot.reply_to(message, convert_normal_emojis_to_premium("❌ <b>Error:</b> User ID, days and max_autolike numbers hone chahiye."))

@bot.message_handler(commands=["removeadmin"])
def handle_removeadmin(message):
    if not is_admin(message.from_user.id):
        deny_text = "🚫 <b>Access Denied! This command is for admins only.</b>"
        deny_text = convert_normal_emojis_to_premium(deny_text)
        bot.reply_to(message, deny_text)
        return
    
    # Only Owner can remove admins
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Only the Owner can remove admins.</b>"))
        return
    
    args = message.text.split()
    if len(args) != 2:
        err_text = "❌ <b>Incorrect format!</b>\nCorrect usage: <code>/removeadmin &lt;user_id&gt;</code>\nExample: <code>/removeadmin 123456789</code>"
        err_text = convert_normal_emojis_to_premium(err_text)
        bot.reply_to(message, err_text)
        return
    
    try:
        target_id = int(args[1])
        
        if target_id == OWNER_ID:
            bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Owner ko kabhi remove nahi kiya ja sakta!</b>"))
            return
        
        if target_id not in ADMINS:
            bot.reply_to(message, convert_normal_emojis_to_premium(f"❌ User <code>{target_id}</code> admin nahi hai."))
            return
        
        del ADMINS[target_id]
        save_data()
        reply_text = f"✅ <b>Admin Removed!</b>\nUser ID: <code>{target_id}</code>\nRemaining Admins: <b>{len(ADMINS)}</b>"
        reply_text = convert_normal_emojis_to_premium(reply_text)
        bot.reply_to(message, reply_text)
        
        try:
            bot.send_message(target_id, convert_normal_emojis_to_premium("⚠️ <b>You have been removed as Admin.</b>"))
        except:
            pass
            
    except ValueError:
        bot.reply_to(message, convert_normal_emojis_to_premium("❌ <b>Error:</b> User ID must be a number."))

@bot.message_handler(commands=["on"])
def handle_on(message):
    global BOT_ENABLED
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Access Denied!</b>"))
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Only Owner can turn the bot ON.</b>"))
    
    BOT_ENABLED = True
    bot.reply_to(message, convert_normal_emojis_to_premium("✅ <b>Bot is now ON!</b>\nUsers can use the bot again."))

@bot.message_handler(commands=["off"])
def handle_off(message):
    global BOT_ENABLED
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Access Denied!</b>"))
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, convert_normal_emojis_to_premium("🚫 <b>Only Owner can turn the bot OFF.</b>"))
    
    BOT_ENABLED = False
# 🔐 GROUP-BASED /likes ACCESS
# ==========================================
# Only OWNER can allow/disallow /likes for groups.
# Approved groups are stored in memory: {chat_id: group_title}
LIKES_ALLOWED_GROUPS = {}

def is_likes_group_allowed(chat_id):
    return chat_id in LIKES_ALLOWED_GROUPS

def likes_group_title(message):
    title = getattr(message.chat, "title", None)
    return title or str(message.chat.id)

@bot.message_handler(commands=["likesallow"])
def handle_likesallow(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Only owner can use this command.</b>"
        ))
    chat = message.chat
    if chat.type == "private":
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Ye command group mein use karo.</b>"
        ))
    if chat.id in LIKES_ALLOWED_GROUPS:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "ℹ️ <b>Is group mein /likes already allowed hai.</b>"
        ))
    LIKES_ALLOWED_GROUPS[chat.id] = likes_group_title(message)
    save_data()
    return bot.reply_to(message, convert_normal_emojis_to_premium(
        "✅ <b>/likes enabled for this group.</b>\n"
        f"🏷️ <b>Group:</b> {likes_group_title(message)}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"📊 <b>Allowed Groups:</b> {len(LIKES_ALLOWED_GROUPS)}"
    ))

@bot.message_handler(commands=["likesdisallow"])
def handle_likesdisallow(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Only owner can use this command.</b>"
        ))
    chat = message.chat
    if chat.type == "private":
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Ye command group mein use karo.</b>"
        ))
    if chat.id not in LIKES_ALLOWED_GROUPS:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "ℹ️ <b>Is group mein /likes already disabled hai.</b>"
        ))
    del LIKES_ALLOWED_GROUPS[chat.id]
    save_data()
    return bot.reply_to(message, convert_normal_emojis_to_premium(
        "🚫 <b>/likes disabled for this group.</b>\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"📊 <b>Allowed Groups:</b> {len(LIKES_ALLOWED_GROUPS)}"
    ))

@bot.message_handler(commands=["likesgroups"])
def handle_likesgroups(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Only owner can use this command.</b>"
        ))
    if not LIKES_ALLOWED_GROUPS:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "📭 <b>Koi group /likes ke liye allowed nahi hai.</b>"
        ))
    lines = ["📋 <b>/likes Allowed Groups</b>", "━━━━━━━━━━━━━━━━━"]
    for i, (chat_id, title) in enumerate(LIKES_ALLOWED_GROUPS.items(), 1):
        lines.append(f"{i}. 🏷️ <b>{title}</b>\n   🆔 <code>{chat_id}</code>")
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 <b>Total:</b> {len(LIKES_ALLOWED_GROUPS)} groups")
    return bot.reply_to(message, convert_normal_emojis_to_premium("\n".join(lines)))

# ==========================================
# ❤️ INSTANT AUTO-LIKE COMMAND (/likes)
# ==========================================
# Owner can approve any user/admin with a fixed number of /likes uses.
# Owner himself has unlimited /likes use.
LIKES_APPROVALS = {}  # {user_id: {"limit": int, "used": int}}

def has_likes_access(user_id):
    if user_id == OWNER_ID:
        return True
    data = LIKES_APPROVALS.get(user_id)
    if data is None:
        return False
    expires = data.get("expires")
    if expires is not None and time.time() >= float(expires):
        return False
    return int(data.get("used", 0)) < int(data.get("limit", 0))


@bot.message_handler(commands=["likesremove"])
def handle_likesremove(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "❌ <b>Only owner can remove /likes approval.</b>")
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        return bot.reply_to(message, "❌ <b>Usage:</b> <code>/likesremove &lt;user_id&gt;</code>")
    target_user_id = int(args[1])
    if target_user_id == OWNER_ID:
        return bot.reply_to(message, "👑 <b>Owner approval cannot be removed.</b>")
    if LIKES_APPROVALS.pop(target_user_id, None) is None:
        return bot.reply_to(message, f"ℹ️ <b>User <code>{target_user_id}</code> has no /likes approval.</b>")
    save_data()
    return bot.reply_to(message, f"🗑 <b>/likes approval removed.</b>\n🆔 User ID: <code>{target_user_id}</code>")

@bot.message_handler(commands=["likes"])
def handle_likes(message):
    user_id = message.from_user.id
    args = message.text.split()

    # Owner-approved users can use /likes even outside an allowed group.
    # Other users still need the current group to be owner-approved.
    if (user_id != OWNER_ID
            and not has_likes_access(user_id)
            and not is_likes_group_allowed(message.chat.id)):
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>You don't have permission to use this command!</b>\n"
            "Contact <b>@princegmr</b> to request"
        ))

    # Force join (likes.py style) - Owner always allowed
    if user_id != OWNER_ID and not is_member(user_id):
        send_force_join_prompt(message)
        return

    # Owner approval: /likes <user_id> <uses> <days>
    # Normal /likes <region> <uid> requests continue to use three arguments.
    if user_id == OWNER_ID and len(args) == 4:
        try:
            target_user_id = int(args[1])
            uses = int(args[2])
            days = int(args[3])
            if target_user_id <= 0 or uses < 1 or days < 1:
                return bot.reply_to(message, "❌ <b>User ID positive, uses aur days kam se kam 1 hone chahiye.</b>")
            if target_user_id == OWNER_ID:
                return bot.reply_to(message, "👑 <b>Owner ko approval ki zarurat nahi hai.</b>")
            expires = time.time() + (days * 24 * 60 * 60)
            LIKES_APPROVALS[target_user_id] = {"limit": uses, "used": 0, "expires": expires}
            save_data()
            reply_text = (
                "✅ <b>/likes Approved!</b>\n\n"
                f"🆔 User ID: <code>{target_user_id}</code>\n"
                f"🔢 Allowed Uses: <b>{uses}</b>\n"
                f"⏳ Validity: <b>{days} days</b>\n"
                "❤️ Command: <code>/likes &lt;region&gt; &lt;uid&gt;</code>"
            )
            return bot.reply_to(message, convert_normal_emojis_to_premium(reply_text))
        except ValueError:
            return bot.reply_to(message, "❌ <b>Usage:</b> <code>/likes &lt;user_id&gt; &lt;uses&gt; &lt;days&gt;</code>\nExample: <code>/likes 123456789 5 7</code>")

    # User/Admin: /likes <region> <uid>
    if len(args) != 3:
        if user_id == OWNER_ID:
            return bot.reply_to(message, convert_normal_emojis_to_premium(
                "❌ <b>Usage:</b>\n"
                "<code>/likes &lt;region&gt; &lt;uid&gt;</code> - Instant AutoLike\n"
                "<code>/likes &lt;user_id&gt; &lt;uses&gt; &lt;days&gt;</code> - Approve user (Owner)\n<code>/likesremove &lt;user_id&gt;</code> - Remove approval (Owner)"
            ))
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            "❌ <b>Usage:</b> <code>/likes &lt;region&gt; &lt;uid&gt;</code>"
        ))

    region, uid = args[1].upper(), args[2]
    if not uid.isdigit():
        return bot.reply_to(message, convert_normal_emojis_to_premium("❌ UID must contain digits only."))
    if region not in REGIONS:
        return bot.reply_to(message, convert_normal_emojis_to_premium(
            f"🗺️ <b>Invalid region:</b> <code>{region}</code>"
        ))

    processing_msg = bot.reply_to(
        message,
        convert_normal_emojis_to_premium("⏳ <b>Instant Lɪᴋᴇ processing...</b>")
    )

    try:
        if not AUTO_LIKE_API:
            return bot.edit_message_text(
                convert_normal_emojis_to_premium(
                    "❌ <b>Auto Like API set nahi hai.</b>\nOwner <code>/setautoapi &lt;url&gt;</code> use kare."
                ),
                message.chat.id,
                processing_msg.message_id
            )

        url = f"{AUTO_LIKE_API.rstrip('/')}/like?uid={uid}&server_name={region}"
        response = make_request(url)

        if not response:
            text = "<blockquote>🔌 <b>Auto Like API se response nahi mila. Please try again later.</b></blockquote>"
        elif response.status_code != 200:
            text = (
                f"<blockquote>🔌 <b>Auto Like API Error</b>\n"
                f"HTTP Status: <code>{response.status_code}</code></blockquote>"
            )
        else:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("API response is not a JSON object")
            status = result.get("status", 0)

            if status == 1:
                # Count a personal approval only when access comes from that
                # approval (users in an owner-allowed group remain unlimited).
                if user_id == OWNER_ID:
                    usage_text = "Owner - Unlimited"
                elif not is_likes_group_allowed(message.chat.id):
                    approval = LIKES_APPROVALS.get(user_id)
                    if approval is not None:
                        approval["used"] = int(approval.get("used", 0)) + 1
                        save_data()
                        usage_text = f"{approval['used']}/{approval.get('limit', 0)}"
                    else:
                        usage_text = "Allowed group - Unlimited"
                else:
                    usage_text = "Allowed group - Unlimited"

                nickname = str(result.get("PlayerNickname", "N/A")).replace("<", "&lt;").replace(">", "&gt;")
                likes_given = result.get("LikesGivenByAPI", 0)

                text = (
                    "<blockquote>"
                    "✅ <b>Iɴsᴛᴀɴᴛ Lɪᴋᴇ Sᴇɴᴛ Sᴜᴄᴄᴇssғᴜʟʟʏ</b> 🥳\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Pʟᴀʏᴇʀ:</b> {nickname}\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    f"🌍 <b>Rᴇɢɪᴏɴ:</b> {result.get('PlayerRegion', region)}\n"
                    f"📊 <b>Lᴇᴠᴇʟ:</b> {result.get('PlayerLevel', 'N/A')}\n"
                    f"❤️ <b>Bᴇғᴏʀᴇ:</b> {result.get('LikesbeforeCommand', 'N/A')}\n"
                    f"💝 <b>Aғᴛᴇʀ:</b> {result.get('LikesafterCommand', 'N/A')}\n"
                    f"🤖 <b>Gɪᴠᴇɴ Bʏ Aᴜᴛᴏ API:</b> {likes_given}\n"
                    "━━━━━━━━━━━━━━━━━\n"
                    f"📉 <b>/likes Uꜱᴇ:</b> {usage_text}"
                    "</blockquote>"
                )
            elif status == 2:
                text = (
                    "<blockquote>"
                    "⚠️ <b>Lɪᴋᴇs Aʟʀᴇᴀᴅʏ Sᴇɴᴛ</b>\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    "📊 <b>Message:</b> LIMIT_REACHED"
                    "</blockquote>"
                )
            else:
                text = (
                    "<blockquote>"
                    "❌ <b>Pʟᴀʏᴇʀ Nᴏᴛ Fᴏᴜɴᴅ / API Error</b>\n"
                    f"🆔 <b>UID:</b> {uid}\n"
                    f"⚠️ <b>Message:</b> {result.get('message', 'player_not_found')}"
                    "</blockquote>"
                )

        text = convert_normal_emojis_to_premium(text)
        markup = InlineKeyboardMarkup()
        dev_btn = make_premium_button(
            "Devloper",
            url="https://t.me/princegmr",
            icon_id=_get_button_map().get("Devloper"),
            style="primary"
        )
        markup.add(dev_btn)
        bot.edit_message_text(
            text,
            message.chat.id,
            processing_msg.message_id,
            reply_markup=markup
        )

    except Exception as e:
        # Keep technical details in the bot log; show a safe message to group users.
        print(f"[likes] Error for user_id={user_id}, uid={uid}, region={region}: {type(e).__name__}: {e}")
        error_text = (
            "<blockquote>⚠️ <b>Request process nahi ho payi.</b> "
            "Please thodi der baad dobara try karein. Agar issue continue ho, "
            "Owner ko inform karein.</blockquote>"
        )
        try:
            bot.edit_message_text(
                convert_normal_emojis_to_premium(error_text),
                message.chat.id,
                processing_msg.message_id
            )
        except Exception as edit_error:
            print(f"[likes] Could not edit processing message: {type(edit_error).__name__}: {edit_error}")
            try:
                bot.send_message(
                    message.chat.id,
                    convert_normal_emojis_to_premium(error_text),
                    reply_to_message_id=message.message_id
                )
            except Exception as send_error:
                print(f"[likes] Could not send fallback error: {type(send_error).__name__}: {send_error}")

# ==========================================
# 🚀 AUTOLIKE COMMANDS
# ==========================================
@bot.message_handler(commands=["autolike"])
def handle_autolike(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return bot.reply_to(message, "❌ <b>Only owner/admin can use this command.</b>")

    admin_data = get_admin_data(user_id)
    if admin_data is not None:
        limit = admin_data.get("autolike_limit")
        used = admin_data.get("autolike_used", 0)
        if limit is not None and used >= limit:
            return bot.reply_to(message, convert_normal_emojis_to_premium(
                f"🚫 <b>Autolike Limit Reached!</b>\\nYou have used {used}/{limit} times.\\nContact Owner for more."
            ))

    args = message.text.split()
    if len(args) != 4:
        return bot.reply_to(message, "Usage: /autolike {region} {uid} {Target Likes}")
    region, uid, target = args[1].upper(), args[2], args[3]
    if region not in REGIONS:
        return bot.reply_to(message, convert_normal_emojis_to_premium(f"❌ Invalid region: <code>{region}</code>"))
    if not uid.isdigit():
        return bot.reply_to(message, convert_normal_emojis_to_premium("❌ UID must contain digits only."))
    try:
        target = int(target)
        if target <= 0 or target > 1000000:
            return bot.reply_to(message, "❌ Target Likes must be between 1 and 1,000,000.")
    except ValueError:
        return bot.reply_to(message, "❌ Target Likes must be a number.")

    task_id = add_autolike_task(uid, region, target, source='manual')
    if admin_data is not None and admin_data.get("autolike_limit") is not None:
        ADMINS[user_id]["autolike_used"] = admin_data.get("autolike_used", 0) + 1
        save_data()
    reply_text = f"✅ <b>Added to AutoLike:</b>\\n🧾 Task: #{task_id}\\n🆔 UID: {uid}\\n🌍 Region: {region}\\n🎯 Target Likes: {target}"
    bot.reply_to(message, convert_normal_emojis_to_premium(reply_text))

@bot.message_handler(commands=["extend"])
def handle_extend(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ <b>Only owner/admin can use this command.</b>")
    args = message.text.split()
    if len(args) != 4:
        return bot.reply_to(message, "Usage: /extend {region} {uid} {addlike}")
    region, uid, amount_text = args[1].upper(), args[2], args[3]
    if region not in REGIONS:
        return bot.reply_to(message, f"❌ Invalid region: <code>{region}</code>")
    if not uid.isdigit():
        return bot.reply_to(message, "❌ UID must contain digits only.")
    try:
        add_likes = int(amount_text)
    except ValueError:
        return bot.reply_to(message, "❌ Extend likes must be a number.")
    if add_likes <= 0 or add_likes > 1000000:
        return bot.reply_to(message, "❌ Extend likes must be between 1 and 1,000,000.")

    try:
        task_id, action, remaining, target, user_id = extend_autolike_task(uid, region, add_likes)
        action_text = "New AutoLike set" if action == 'created' else "AutoLike target extended"
        reply = (
            f"✅ <b>{action_text} successfully!</b>\n"
            f"🧾 Task: <code>#{task_id}</code>\n"
            f"🆔 UID: <code>{uid}</code>\n"
            f"🌍 Region: <b>{region}</b>\n"
            f"➕ Added target: <b>{add_likes}</b>\n"
            f"🎯 Remaining / Target: <b>{remaining}/{target}</b>"
        )
        if user_id is not None:
            reply += "\n🌐 Website wallet credited with the extension."
        bot.reply_to(message, convert_normal_emojis_to_premium(reply))
    except Exception as exc:
        bot.reply_to(message, f"❌ Could not extend AutoLike: <code>{str(exc)}</code>")


@bot.message_handler(commands=["remove"])
def handle_remove(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ <b>Only owner can use this command.</b>")
    args = message.text.split()
    if len(args) != 2:
        return bot.reply_to(message, "Usage: /remove {uid}")
    uid = args[1]
    if not uid.isdigit():
        return bot.reply_to(message, "❌ UID must contain digits only.")
    removed = remove_autolike_tasks(uid)
    if removed:
        bot.reply_to(message, convert_normal_emojis_to_premium(
            f"🗑 Removed {removed} active task(s) for UID {uid}."
        ))
    else:
        bot.reply_to(message, f"❌ No active tasks found for UID {uid}.")

@bot.message_handler(commands=["listuid"])
def handle_listuid(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ <b>Only owner can use this command.</b>")
    data = load_db().get("autolike_list", {})
    if not data:
        return bot.reply_to(message, "📭 <b>Database is empty.</b>")
    msg = f"📋 <b>AutoLike List (Total: {len(data)}):</b>\n"
    for task_id, info in data.items():
        remaining = info.get("remaining_likes", 0)
        target = info.get("target_likes", 0)
        msg += (
            f"🧾 <b>Task #{task_id}</b> | 🆔 <code>\n{info['uid']}\n</code> | "
            f"🌍 {info['region']} | 🎯 {remaining}/{target} ❤️\n"
        )
    bot.reply_to(message, convert_normal_emojis_to_premium(msg))

@bot.message_handler(commands=["run"])
def handle_run(message):
    if not is_admin(message.from_user.id):
        return bot.reply_to(message, "❌ Only owner.")
    threading.Thread(target=run_autolike_process, args=(True, message.chat.id), daemon=True).start()
    bot.reply_to(message, "🚀 <b>AutoLike process started manually. Result will be reported here.</b>")

def run_autolike_process(manual=False, report_chat_id=None):
    db = load_db()
    autolike_data = db.get("autolike_list", {})
    if not autolike_data:
        if manual:
            bot.send_message(report_chat_id or REQUIRED_GROUP_ID, convert_normal_emojis_to_premium("📭 <b>No active website/bot tasks found in the shared database.</b>"))
        return
    if manual:
        bot.send_message(report_chat_id or REQUIRED_GROUP_ID, convert_normal_emojis_to_premium(f"🚀 <b>AutoLike Started...</b>\n📋 Active tasks: {len(autolike_data)}"))

    for task_id, info in list(autolike_data.items()):
        uid = str(info["uid"])
        region = info["region"]
        target = int(info.get("target_likes", 0))
        remaining = int(info.get("remaining_likes", target))
        url = f"{AUTO_LIKE_API.rstrip('/')}/like?uid={uid}&server_name={region}"
        try:
            res = make_request(url)
            if not res or res.status_code != 200:
                error_msg = f"❌ No response for UID {uid} (Task #{task_id})"
                bot.send_message(report_chat_id or REQUIRED_GROUP_ID, convert_normal_emojis_to_premium(error_msg), parse_mode="HTML")
                continue
            result = res.json()
            if not isinstance(result, dict):
                raise ValueError("API response is not a JSON object")
            status = result.get("status", 0)
            markup = InlineKeyboardMarkup()
            dev_btn = make_premium_button("Devloper", url="https://t.me/princegmr",
                                          icon_id=_get_button_map().get("Devloper"), style="primary")
            markup.add(dev_btn)

            if status == 1:
                nickname = str(result.get('PlayerNickname', 'N/A')).replace('<', '&lt;').replace('>', '&gt;')
                likes_given = int(result.get('LikesGivenByAPI', 0) or 0)
                if likes_given < 0:
                    raise ValueError("API returned a negative LikesGivenByAPI value")

                remaining, credited_likes = update_task_progress(int(task_id), likes_given)
                msg = (
                    f"<blockquote>"
                    f"✅ <b>Aᴜᴛᴏʟɪᴋᴇꜱ Sᴇɴᴛ Sᴜᴄᴄᴇꜱꜰᴜʟʟʏ</b> 🥳\\n"
                    f"━━━━━━━━━━━━━━━━━\\n"
                    f"👤 <b>Pʟᴀʏᴇʀ:</b> {nickname}\\n"
                    f"📊 <b>Lᴇᴠᴇʟ:</b> {result.get('PlayerLevel','N/A')}\\n"
                    f"🌍 <b>Rᴇɢɪᴏɴ:</b> {result.get('PlayerRegion', region)}\\n"
                    f"🆔 <b>UID:</b> {result.get('UID', uid)}\\n"
                    f"📶 <b>API Uꜱᴇᴅ:</b> API 1\\n"
                    f"❤️ <b>Bᴇꜰᴏʀᴇ:</b> {result.get('LikesbeforeCommand','N/A')}\\n"
                    f"💝 <b>Aꜰᴛᴇʀ:</b> {result.get('LikesafterCommand','N/A')}\\n"
                    f"🤖 <b>Gɪᴠᴇɴ Bʏ Bᴏᴛ:</b> {likes_given}\\n"
                    f"━━━━━━━━━━━━━━━━━\\n"
                    f"📉 <b>Rᴇᴍᴀɪɴɪɴɢ Tᴀʀɢᴇᴛ Lɪᴋᴇꜱ:</b> {remaining}/{target} 💝\\n"
                    f"🧾 <b>Purchase Task:</b> #{task_id}"
                    f"</blockquote>"
                )
                bot.send_photo(report_chat_id or REQUIRED_GROUP_ID, IMAGE_URL, caption=convert_normal_emojis_to_premium(msg),
                               reply_markup=markup, parse_mode="HTML")

                if remaining <= 0:
                    done_msg = f"<blockquote>🏁 <b>UID {uid} TASK #{task_id} TARGET COMPLETED.</b></blockquote>"
                    bot.send_message(REQUIRED_GROUP_ID, convert_normal_emojis_to_premium(done_msg), parse_mode="HTML")

            elif status == 2:
                msg = (
                    f"<blockquote>"
                    f"⚠️ UID {uid}: <b>Likes already sent today.</b>\\n"
                    f"📊 Remaining Target: {remaining}/{target}\\n"
                    f"🧾 Task: #{task_id}"
                    f"</blockquote>"
                )
                bot.send_photo(report_chat_id or REQUIRED_GROUP_ID, IMAGE_URL, caption=convert_normal_emojis_to_premium(msg),
                               reply_markup=markup, parse_mode="HTML")
            else:
                msg = (
                    f"<blockquote>"
                    f"❌ UID {uid}: <b>Player not found or Error.</b>\\n"
                    f"📊 Remaining Target: {remaining}/{target}\\n"
                    f"🧾 Task: #{task_id}"
                    f"</blockquote>"
                )
                bot.send_photo(report_chat_id or REQUIRED_GROUP_ID, IMAGE_URL, caption=convert_normal_emojis_to_premium(msg),
                               reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            error_msg = f"<blockquote>❌ Error processing UID {uid} (Task #{task_id}): {str(e)}</blockquote>"
            bot.send_photo(report_chat_id or REQUIRED_GROUP_ID, IMAGE_URL, caption=convert_normal_emojis_to_premium(error_msg), parse_mode="HTML")
        time.sleep(5)

    if manual:
        bot.send_message(REQUIRED_GROUP_ID, convert_normal_emojis_to_premium("🌅 <b>AutoLike Process Completed.</b>"))

# ==========================================
# ⏰ SCHEDULER
# ==========================================
def daily_scheduler():
    global total_likes_used, total_requests_used, user_success_logs
    while True:
        now = now_india()
        next_run = (now + datetime.timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
        if now.hour < 4:
            next_run = now.replace(hour=4, minute=0, second=0, microsecond=0)
        wait_seconds = (next_run - now).total_seconds()
        if wait_seconds < 0:
            time.sleep(10)
            continue
        print(f"😴 Scheduler sleeping for {wait_seconds} seconds.")
        time.sleep(wait_seconds)
        with STATE_LOCK:
            total_likes_used = 0
            total_requests_used = 0
            user_success_logs.clear()
        save_data()
        print(f"🔄 Daily like limit and user logs reset at {now_india()}")
        run_autolike_process(manual=False)
        time.sleep(60)

# Restore saved state only after all state containers and handlers are defined.
load_data()
ensure_autolike_table()
migrate_legacy_autolike_tasks()

# ==========================================
# 🚀 MAIN LOOP
# ==========================================
def start_bot_loop():
    while True:
        try:
            print("🚀 Bot polling started...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except telebot.apihelper.ApiTelegramException as e:
            if e.result.status_code == 409:
                print("Conflict error (409) detected. Restarting in 5 seconds...")
                time.sleep(5)
            else:
                print(f"API error: {e}. Restarting in 10s...")
                time.sleep(10)
        except Exception as e:
            print(f"⚠️ Bot crashed: {e}")
            time.sleep(10)
        except KeyboardInterrupt:
            print("🛑 Bot stopped manually.")
            break

