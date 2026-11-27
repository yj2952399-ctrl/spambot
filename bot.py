print("✅ ✅ ✅ プログラム開始！一番最初の行まで到達！")

# ============================================================
# 外部モード対応版（seed手動 / ユーザートークン自動 を切替）
#  - 自動モード: ユーザートークンで直送。seedも🧬も不要・5通制限なし
#  - 手動モード: seed + 🧬（5通/本）で送る従来動作
#  - 自動モードの対象は seed があればそのch、無ければ送信可能ch全部
#  - ユーザートークンは名前付きで複数登録・ローテーション
#  - バケットは アプリ共有 / webhook / ユーザー×チャンネル で分離
# ============================================================

import io
import os
import json
import random
import time
import asyncio
import inspect
import logging
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFont
import discord
from discord import ui, app_commands
from discord.ext import commands

logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.ERROR)

API_BASE = "https://discord.com/api/v10"
USER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Discord/1.0"

# ==============================================
# ✅ GIF生成（メモリ保持）
# ==============================================
def generate_gif() -> Optional[bytes]:
    print("[GIF生成] 🚀 === 開始 ===")
    try:
        WIDTH, HEIGHT = 800, 600
        TEXT = "トイ神の集い"
        COLORS = [(220, 30, 30), (30, 60, 200), (255, 255, 255)]
        TEXT_COLORS = [(255, 255, 255), (255, 255, 255), (0, 0, 0)]
        FRAME_DURATION = 100
        FONT_PATHS = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttf",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.otf",
        ]

        font = None
        for path in FONT_PATHS:
            if not os.path.exists(path):
                continue
            try:
                if path.endswith(".ttc"):
                    font = ImageFont.truetype(path, 110, index=0)
                else:
                    font = ImageFont.truetype(path, 110)
                print(f"[GIF生成] ✅ フォント発見: {path}")
                break
            except Exception as e:
                print(f"[GIF生成] ⚠️ {path} 読み込み失敗: {type(e).__name__}: {e}")

        if font is None:
            print("[GIF生成] ❌ フォントなし → GIFなしで続行")
            return None

        frames = []
        for bg, fg in zip(COLORS, TEXT_COLORS):
            img = Image.new("RGB", (WIDTH, HEIGHT), color=bg)
            draw = ImageDraw.Draw(img)
            bbox = draw.textbbox((0, 0), TEXT, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (WIDTH - tw) / 2 - bbox[0]
            y = (HEIGHT - th) / 2 - bbox[1]
            draw.text((x + 4, y + 4), TEXT, font=font, fill=(0, 0, 0, 80))
            draw.text((x, y), TEXT, font=font, fill=fg)
            frames.append(img)

        buf = io.BytesIO()
        frames[0].save(
            buf, format="GIF", save_all=True, append_images=frames[1:],
            loop=0, duration=FRAME_DURATION, optimize=False, disposal=2,
        )
        data = buf.getvalue()
        print(f"[GIF生成] ✅ 成功！ {len(data)} bytes（メモリ保持）")
        return data
    except Exception as e:
        print(f"[GIF生成] ❌ 致命的エラー: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None

GIF_BYTES = generate_gif()

def make_gif_file():
    if not GIF_BYTES:
        return None
    return discord.File(io.BytesIO(GIF_BYTES), filename="toykami.gif")

# ==============================
# 定数
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN 未設定")

RAID_HEADER = "# raid by TOISHIN!!!!!!!!"
DEFAULT_SERVER_NAME = "トイ神の集い"
DEFAULT_DESCRIPTION = "Orangestarは神！！！！今すぐOrangestarの曲を聴いて君も蜜柑星人になろう🍊https://youtube.com/@orangestaras?si=WfcQEqBYvcgqC7PT"
DEFAULT_INVITE_LINK = "https://discord.gg/EWJCrsGVbg"

TOKEN_CAPACITY = 5
RETRY_MAX = 6

MAX_SEEDS_PER_CHANNEL = 40

# アプリ経由（インタラクション）はアプリ単位で共有される
BUCKET_BURST = 4.0
BUCKET_PER = 1.8
# ユーザートークンはチャンネル単位で 5通/5秒
USER_BUCKET_BURST = 5.0
USER_BUCKET_PER = 5.0

SEED_TTL = 14 * 60

DEFAULT_SEND_COUNT = 5
DEFAULT_POWER = "normal"
DEFAULT_INTERVAL = 0.0
DEFAULT_MENTION = "auto"
DEFAULT_AUTO = False

POWER_CHOICES = [
    app_commands.Choice(name="lite（本文のみ・最速）", value="lite"),
    app_commands.Choice(name="normal（GIF初弾）", value="normal"),
    app_commands.Choice(name="heavy（embed10連）", value="heavy"),
]
MENTION_CHOICES = [
    app_commands.Choice(name="自動判定", value="auto"),
    app_commands.Choice(name="つける", value="on"),
    app_commands.Choice(name="つけない", value="off"),
]

# ==============================
# 利用者別設定ストア
# ==============================
CFG_PATH = os.environ.get(
    "CFG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
)
_CFG: dict[str, dict] = {}

def _blank_cfg() -> dict:
    return {
        "count": DEFAULT_SEND_COUNT,
        "power": DEFAULT_POWER,
        "interval": DEFAULT_INTERVAL,
        "mention": DEFAULT_MENTION,
        "auto": DEFAULT_AUTO,
        "tokens": [],
        "usertokens": [],
        "roles": [],
        "ignore": [],
    }

def load_cfg():
    global _CFG
    raw = {}
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"[cfg] 読込失敗: {type(e).__name__}: {e}")
    _CFG = {}
    for uid, c in (raw or {}).items():
        base = _blank_cfg()
        if isinstance(c, dict):
            base.update({k: v for k, v in c.items() if k in base})
        _CFG[str(uid)] = base
    print(f"[cfg] 読込: {len(_CFG)}人分")

def save_cfg():
    try:
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_CFG, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)
    except Exception as e:
        print(f"[cfg] 保存失敗: {type(e).__name__}: {e}")

def cfg(user_id) -> dict:
    key = str(user_id)
    c = _CFG.get(key)
    if c is None:
        c = _blank_cfg()
        _CFG[key] = c
    return c

def seed_from_env():
    if not _CFG:
        return
    tokens = [t.strip() for t in os.environ.get("BOT_TOKENS", "").split(",") if t.strip()]
    roles = [int(x) for x in os.environ.get("RAID_ROLE_IDS", "").split(",") if x.strip().isdigit()]
    if not tokens and not roles:
        return
    for c in _CFG.values():
        if tokens and not c["tokens"]:
            c["tokens"] = [{"token": t, "label": "env"} for t in tokens]
        if roles and not c["roles"]:
            c["roles"] = roles
    save_cfg()
    print("[cfg] env値をシードしました")

load_cfg()
seed_from_env()

# ==============================
# 外部モード用シード
# ==============================
SEED_PATH = os.environ.get(
    "SEED_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds.json")
)
SEEDS: dict[int, list[dict]] = {}

def _seed_ok(s: dict) -> bool:
    return bool(s.get("token")) and s.get("channel_id") is not None

def load_seeds():
    global SEEDS
    SEEDS = {}
    if not os.path.exists(SEED_PATH):
        return
    try:
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"[seed] 読込失敗: {type(e).__name__}: {e}")
        return
    now = time.time()
    for gid, lst in (raw or {}).items():
        try:
            gid_i = int(gid)
        except Exception:
            continue
        keep = []
        for s in lst or []:
            try:
                if float(s.get("expires", 0)) <= now:
                    continue
                item = {
                    "token": s["token"],
                    "webhook_id": s.get("webhook_id"),
                    "webhook_token": s.get("webhook_token"),
                    "channel_id": int(s["channel_id"]),
                    "channel_name": s.get("channel_name") or str(s["channel_id"]),
                    "user_id": int(s["user_id"]),
                    "expires": float(s["expires"]),
                    "used": int(s.get("used", 0)),
                    "msg_id": int(s["msg_id"]) if s.get("msg_id") else None,
                }
                if _seed_ok(item):
                    keep.append(item)
            except Exception:
                continue
        if keep:
            SEEDS[gid_i] = keep
    print(f"[seed] 読込: {sum(len(v) for v in SEEDS.values())}本（有効分のみ）")

def save_seeds():
    try:
        data = {}
        for gid, lst in SEEDS.items():
            rows = []
            for s in lst:
                rows.append({
                    "token": s["token"],
                    "webhook_id": s.get("webhook_id"),
                    "webhook_token": s.get("webhook_token"),
                    "channel_id": s["channel_id"],
                    "channel_name": s.get("channel_name"),
                    "user_id": s["user_id"],
                    "expires": s["expires"],
                    "used": int(s.get("used", 0)),
                    "msg_id": s.get("msg_id"),
                })
            data[str(gid)] = rows
        tmp = SEED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SEED_PATH)
    except Exception as e:
        print(f"[seed] 保存失敗: {type(e).__name__}: {e}")

def _gc_seeds(guild_id: int, persist: bool = True):
    now = time.time()
    lst = SEEDS.get(guild_id)
    if not lst:
        return
    before = len(lst)
    lst[:] = [s for s in lst if s["expires"] > now]
    if len(lst) != before and persist:
        save_seeds()
    if not lst:
        SEEDS.pop(guild_id, None)

def user_seeds(guild_id: int, user_id: int) -> list[dict]:
    if guild_id is None:
        return []
    _gc_seeds(guild_id)
    return [s for s in (SEEDS.get(guild_id) or []) if s["user_id"] == user_id]

def seeds_of_channel(guild_id: int, user_id: int, channel_id: int) -> list[dict]:
    return [s for s in user_seeds(guild_id, user_id) if s["channel_id"] == channel_id]

def seed_remaining(seed: dict) -> int:
    if seed.get("webhook_id") and seed.get("webhook_token"):
        return 10 ** 6
    return max(0, TOKEN_CAPACITY - int(seed.get("used", 0)))

def channel_capacity(guild_id: int, user_id: int, channel_id: int) -> int:
    return sum(seed_remaining(s) for s in seeds_of_channel(guild_id, user_id, channel_id))

def add_seed(gid: int, cid: int, uid: int, token: str, name: str,
             webhook=None, msg_id: Optional[int] = None) -> dict:
    seed = {
        "token": token,
        "webhook_id": getattr(webhook, "id", None),
        "webhook_token": getattr(webhook, "token", None),
        "channel_id": cid,
        "channel_name": name,
        "user_id": uid,
        "expires": time.time() + SEED_TTL,
        "used": 0,
        "msg_id": msg_id,
    }
    SEEDS.setdefault(gid, []).append(seed)
    save_seeds()
    return seed

load_seeds()

# ==============================
# 実行中カウンタ
# ==============================
ACTIVE: dict[int, int] = {}

def active_of(user_id: int) -> int:
    return ACTIVE.get(user_id, 0)

# ==============================
# ✅ 送信バケット
# ==============================
class Bucket:
    def __init__(self, burst: float = BUCKET_BURST, per: float = BUCKET_PER):
        self.capacity = burst
        self.per = per
        self.tokens = burst
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self.updated) * (self.capacity / self.per),
                )
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) * (self.per / self.capacity)
            await asyncio.sleep(wait)

_buckets: dict[tuple, Bucket] = {}

def bucket_for(key: tuple, burst: float = BUCKET_BURST,
               per: float = BUCKET_PER) -> Bucket:
    b = _buckets.get(key)
    if b is None:
        b = Bucket(burst, per)
        _buckets[key] = b
    return b

def gate_for_seed(seed: dict) -> Bucket:
    if seed.get("webhook_id"):
        return bucket_for(("seedwh", seed["webhook_id"]))
    return bucket_for(("seedapp", bot.application_id))

# ==============================
# キープアライブ
# ==============================
class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args):
        pass

def start_keep_alive():
    port = int(os.environ.get("BOT_PORT", 8099))
    server = HTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"キープアライブサーバー起動: ポート {port}")

# ==============================
# Bot設定
# ==============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ==============================
# ✅ HTTP直送（Bot・バージョン差を吸収）
# ==============================
def _accepts(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False

async def http_send(http, ch_id: int, content: str, file, allowed, embeds=None):
    kw = {}
    if _accepts(http.send_message, "allowed_mentions"):
        kw["allowed_mentions"] = allowed
    if embeds:
        if _accepts(http.send_message, "embeds"):
            kw["embeds"] = embeds
        elif _accepts(http.send_message, "embed"):
            kw["embed"] = embeds[0]

    if file is not None:
        if hasattr(http, "send_files"):
            await http.send_files(ch_id, files=[file], content=content, **kw)
            return
        if hasattr(http, "send_file"):
            try:
                file.fp.seek(0)
            except Exception:
                pass
            await http.send_file(ch_id, file.fp, filename=file.filename,
                                 content=content, **kw)
            return

    if _accepts(http.send_message, "content"):
        await http.send_message(ch_id, content=content, **kw)
    else:
        await http.send_message(ch_id, content, **kw)

# ==============================
# ✅ ユーザートークン直送
# ==============================
_session: Optional[aiohttp.ClientSession] = None

def http_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def _user_result(r) -> str:
    if r.status in (200, 201):
        return "ok"
    if r.status == 429:
        ra = 1.0
        try:
            d = await r.json()
            ra = float(d.get("retry_after", 1.0))
        except Exception:
            pass
        await asyncio.sleep(min(ra + 0.1, 10.0))
        return "retry"
    if r.status in (401, 403, 404):
        return "dead"
    try:
        body = (await r.text())[:200]
    except Exception:
        body = ""
    print(f"[user] HTTP {r.status} {body}")
    return "retry"

async def user_send(token: str, ch_id: int, content: str, file=None,
                    allowed=None, embeds=None) -> str:
    headers = {"Authorization": token, "User-Agent": USER_UA}
    url = f"{API_BASE}/channels/{ch_id}/messages"
    payload: dict = {"content": content}
    if embeds:
        payload["embeds"] = [e.to_dict() for e in embeds]
    if allowed:
        payload["allowed_mentions"] = allowed.to_dict()
    try:
        if file is not None:
            try:
                file.fp.seek(0)
            except Exception:
                pass
            form = aiohttp.FormData()
            form.add_field("payload_json", json.dumps(payload),
                           content_type="application/json")
            form.add_field("files[0]", file.fp, filename=file.filename,
                           content_type="image/gif")
            async with http_session().post(url, data=form, headers=headers) as r:
                return await _user_result(r)
        async with http_session().post(url, json=payload, headers=headers) as r:
            return await _user_result(r)
    except Exception as e:
        print(f"[user] {type(e).__name__}: {e}")
        return "retry"

async def user_me(token: str) -> Optional[dict]:
    try:
        async with http_session().get(
            f"{API_BASE}/users/@me",
            headers={"Authorization": token, "User-Agent": USER_UA},
        ) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception:
        return None

class UserSender:
    def __init__(self, label: str, token: str):
        self.label = label
        self.token = token
        self.ok = True

USERPOOLS: dict[int, list[UserSender]] = {}

def get_usersenders(user_id: int) -> list[UserSender]:
    return USERPOOLS.get(user_id, [])

def alive_usersenders(user_id: int) -> list[UserSender]:
    return [s for s in USERPOOLS.get(user_id, []) if s.ok]

def load_userpool(user_id: int):
    lst = []
    for item in cfg(user_id)["usertokens"]:
        lst.append(UserSender(item.get("label") or "user", item["token"]))
    USERPOOLS[user_id] = lst

# ==============================
# トークンプール（Bot）
# ==============================
class Sender:
    def __init__(self, label: str, token: str = None, http=None):
        self.label = label
        self.token = token
        self.http = http
        self.ready = http is not None

    async def login(self):
        try:
            await self.http.static_login(self.token, bot=True)
            self.ready = True
            print(f"[pool] {self.label} 参戦")
        except Exception as e:
            print(f"[pool] {self.label} ログイン失敗: {type(e).__name__}: {e}")

class TokenPool:
    def __init__(self):
        self.senders: list[Sender] = []
        self._i = 0
        self._lock = asyncio.Lock()

    def attach_main(self, http):
        self.senders.append(Sender("main", http=http))

    async def add(self, token: str, label: str):
        s = Sender(label, token=token, http=discord.http.HTTPClient(token=token))
        await s.login()
        if s.ready:
            self.senders.append(s)
        return s

    async def pick(self):
        if not self.senders:
            return None
        async with self._lock:
            s = self.senders[self._i % len(self.senders)]
            self._i += 1
        return s

    async def send(self, channel_id, content, file, allowed, embeds=None):
        s = await self.pick()
        if s is None:
            raise RuntimeError("sender exhausted")
        await bucket_for((s.label, channel_id)).acquire()
        await http_send(s.http, channel_id, content, file, allowed, embeds)

POOLS: dict[int, TokenPool] = {}

def get_pool(user_id: int) -> TokenPool:
    p = POOLS.get(user_id)
    if p is None:
        p = TokenPool()
        p.attach_main(bot.http)
        POOLS[user_id] = p
    return p

async def rebuild_pool(user_id: int):
    old = POOLS.get(user_id)
    if old is not None:
        for s in old.senders:
            if s.http is not bot.http:
                try:
                    await s.http.close()
                except Exception:
                    pass
    p = TokenPool()
    p.attach_main(bot.http)
    POOLS[user_id] = p
    for item in cfg(user_id)["tokens"]:
        await p.add(item["token"], item.get("label") or "sub")

def mask(token: str) -> str:
    if len(token) <= 12:
        return token[:4] + "…"
    return f"{token[:6]}…{token[-4:]}"

# ==============================
# ウェブフック（Botが鯖にいる時だけ使う）
# ==============================
_wh_cache: dict[int, discord.Webhook] = {}
_wh_dead: set[int] = set()
_wh_unsupported = False

async def get_webhook(channel):
    cid = getattr(channel, "id", None)
    if cid is None or cid in _wh_dead or _wh_unsupported:
        return None
    guild = getattr(channel, "guild", None)
    if guild is None or getattr(guild, "me", None) is None:
        return None
    wh = _wh_cache.get(cid)
    if wh is not None:
        return wh
    try:
        wh = await channel.create_webhook(name="raid")
        _wh_cache[cid] = wh
        return wh
    except discord.Forbidden:
        _wh_dead.add(cid)
        return None
    except Exception:
        _wh_dead.add(cid)
        return None

async def cleanup_webhooks(guild):
    n = 0
    for ch in getattr(guild, "text_channels", []) or []:
        try:
            for wh in await ch.webhooks():
                if wh.name == "raid" and wh.token:
                    await wh.delete()
                    n += 1
                    _wh_cache.pop(ch.id, None)
        except Exception:
            continue
    _wh_dead.clear()
    return n

# ==============================
# 🧬 増殖ボタン（/seed 用）
# ==============================
def breed_view(owner_id: int, channel_id: int) -> ui.View:
    v = ui.View(timeout=None)
    v.add_item(ui.Button(
        label="+5通", emoji="🧬", style=discord.ButtonStyle.success,
        custom_id=f"breed:{owner_id}:{channel_id}",
    ))
    return v

async def edit_seed_message(gid: int, cid: int, uid: int, msg_id: int, content: str) -> bool:
    origin = None
    for s in (SEEDS.get(gid) or []):
        if (s["user_id"] == uid and s["channel_id"] == cid
                and s.get("msg_id") == msg_id):
            origin = s
            break
    if origin is None:
        return False
    try:
        wh = discord.Webhook.partial(bot.application_id, origin["token"], client=bot)
        await wh.edit_message(msg_id, content=content)
        return True
    except Exception as e:
        print(f"[繁殖] 元メッセージ更新失敗: {type(e).__name__}: {e}")
        return False

# ==============================
# ユーティリティ
# ==============================
def note(state, key: str, msg: str):
    if key in state.setdefault("said", set()):
        return
    state["said"].add(key)
    print(msg)

async def get_bot_member(guild):
    if guild is None:
        return None
    if guild.me:
        return guild.me
    if bot.user:
        try:
            return await guild.fetch_member(bot.user.id)
        except Exception:
            return None
    return None

def _is_adminish(guild, subject) -> bool:
    if guild is None or subject is None:
        return False
    sid = getattr(subject, "id", None)
    if sid is not None and guild.owner_id == sid:
        return True
    gp = getattr(subject, "guild_permissions", None)
    return bool(gp and gp.administrator)

def channel_caps(channel, me, user=None) -> dict:
    caps = {"send": False, "attach": False}
    subject = me
    if subject is None and isinstance(user, discord.Member):
        subject = user
    if subject is None or channel is None:
        return {"send": True, "attach": True}
    guild = getattr(channel, "guild", None)
    if _is_adminish(guild, subject):
        return {"send": True, "attach": True}
    try:
        p = channel.permissions_for(subject)
        caps["send"] = p.send_messages
        caps["attach"] = p.attach_files
    except Exception:
        return {"send": True, "attach": True}
    return caps

def _passes_ignore(user_id: int, ch) -> bool:
    pats = cfg(user_id)["ignore"]
    if not pats:
        return True
    name = getattr(ch, "name", "").lower()
    return not any(name.startswith(p.lower()) for p in pats)

async def get_sendable_text_channels(guild, me, user=None, user_id: int = 0):
    if guild is None:
        return []
    subject = me if me is not None else (user if isinstance(user, discord.Member) else None)
    if subject is None:
        return []
    chans = list(guild.text_channels)
    if not chans:
        try:
            chans = [c for c in await guild.fetch_channels()
                     if isinstance(c, discord.TextChannel)]
        except Exception as e:
            print(f"[ch取得] 失敗: {type(e).__name__}: {e}")
            chans = []
    out = []
    for ch in chans:
        if not _passes_ignore(user_id, ch):
            continue
        try:
            p = ch.permissions_for(subject)
        except Exception:
            continue
        if _is_adminish(guild, subject) or (p.view_channel and p.send_messages):
            out.append(ch)
    return out

async def _resolve_mention_channel(interaction):
    ch = interaction.channel
    if ch is not None and hasattr(ch, "permissions_for"):
        return ch
    guild = interaction.guild
    cid = getattr(interaction, "channel_id", None)
    if guild is None or cid is None:
        return ch
    ch = guild.get_channel(cid) or guild.get_thread(cid)
    if ch is None:
        try:
            ch = await guild.fetch_channel(cid)
        except Exception:
            return None
    return ch

async def _resolve_member(guild, user):
    if isinstance(user, discord.Member):
        return user
    uid = getattr(user, "id", None)
    if guild is None or uid is None:
        return None
    m = guild.get_member(uid)
    if m is not None:
        return m
    try:
        return await guild.fetch_member(uid)
    except Exception:
        return None

def _perm_true(perms) -> Optional[bool]:
    if perms is None:
        return None
    try:
        if perms.value == 0:
            return None
        return bool(perms.administrator or perms.mention_everyone)
    except Exception:
        return None

def _mention_verdict(channel, subject) -> Optional[bool]:
    if channel is None or subject is None:
        return None
    guild = getattr(channel, "guild", None)
    if guild is None:
        return None
    if _is_adminish(guild, subject):
        return True
    try:
        p = channel.permissions_for(subject)
    except Exception as e:
        print(f"[権限判定] 計算失敗: {type(e).__name__}: {e}")
        return None
    return bool(p.administrator or p.mention_everyone)

def _guild_verdict(guild, subject) -> Optional[bool]:
    if guild is None or not isinstance(subject, discord.Member):
        return None
    if _is_adminish(guild, subject):
        return True
    try:
        p = guild.permissions_for(subject)
    except Exception:
        return None
    return bool(p.administrator or p.mention_everyone)

def judge_mention_for(channel, subject, payload=None) -> Optional[bool]:
    guild = getattr(channel, "guild", None)
    results: list[Optional[bool]] = [_perm_true(payload)]
    if channel is not None and subject is not None:
        results.append(_mention_verdict(channel, subject))
        if isinstance(channel, discord.Thread) and channel.parent is not None:
            results.append(_mention_verdict(channel.parent, subject))
        results.append(_guild_verdict(guild, subject))
    if any(r is True for r in results):
        return True
    if all(r is False for r in results):
        return False
    return None

def decide_mention(user_ok: Optional[bool], bot_ok: Optional[bool]) -> bool:
    if user_ok is True:
        return True
    if bot_ok is True:
        return True
    if bot_ok is False:
        return False
    return True

async def resolve_mention(interaction, mode: str) -> tuple[bool, str]:
    if mode == "on":
        return True, "✅ 手動指定: つける"
    if mode == "off":
        return False, "❌ 手動指定: つけない"

    guild = interaction.guild
    if guild is None:
        return True, "✅ 外部モード: つける（/seteveryone off で切替）"

    channel = await _resolve_mention_channel(interaction)
    if channel is None:
        return True, "✅ チャンネル不明→つける"

    user = await _resolve_member(guild, interaction.user)
    user_ok = judge_mention_for(channel, user, getattr(interaction, "permissions", None))

    me = await get_bot_member(guild)
    bot_ok = judge_mention_for(channel, me, getattr(interaction, "app_permissions", None))

    print(
        f"[権限判定] ch={getattr(channel, 'id', None)} user={getattr(user, 'id', None)} "
        f"user_ok={user_ok} bot_ok={bot_ok} "
        f"payload={getattr(getattr(interaction, 'permissions', None), 'value', None)}"
    )

    ok = decide_mention(user_ok, bot_ok)
    if not ok:
        if user_ok is False:
            return False, "❌ 自動判定: 実行者・Botとも権限なし（/seteveryone つける で強制可）"
        return False, "❌ 自動判定: Botに権限なし（/seteveryone つける で強制可）"
    if user_ok is True and bot_ok is False:
        return True, "⚠️ 実行者OK / Bot権限なし（pingは飛ばないかも）"
    if user_ok is True:
        return True, "✅ 自動判定: 実行者に権限あり"
    if bot_ok is True:
        return True, "✅ 自動判定: Botに権限あり"
    return True, "✅ 自動判定: 不明→つける"

def build_advertisement_text(guild) -> str:
    lines = [
        RAID_HEADER,
        "",
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
        f"# **{DEFAULT_DESCRIPTION}**",
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
    ]
    if guild and guild.member_count:
        lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")
    return "\n".join(lines)

def build_body(guild, mention: bool, roles: list[int]) -> str:
    head = ""
    if mention:
        head = "@everyone\n"
    if roles:
        head += f"<@&{random.choice(roles)}>\n"
    body = build_advertisement_text(guild)
    return head + body + "\n​\n" + body

def build_embeds(guild) -> list[discord.Embed]:
    out = []
    for i in range(10):
        em = discord.Embed(
            title="🎉 トイ神の集い" if i == 0 else None,
            description=DEFAULT_DESCRIPTION,
            color=0xDC1E1E if i % 2 == 0 else 0x1E3CC8,
        )
        em.add_field(name="🔗 招待", value=DEFAULT_INVITE_LINK, inline=False)
        em.set_footer(text=f"raid by TOISHIN {i + 1}/10")
        out.append(em)
    return out

def new_state(native: bool, attach: bool, power: str, allow_followup: bool = True,
              webhook: bool = False, interval: float = 0.0) -> dict:
    return {
        "native": native,
        "attach": attach and power != "lite",
        "attach_used": False,
        "allow_followup": allow_followup,
        "webhook": webhook,
        "power": power,
        "interval": interval,
        "said": set(),
    }

def take_file(state):
    if GIF_BYTES is None or not state.get("attach", False) or state.get("attach_used"):
        return None
    state["attach_used"] = True
    return discord.File(io.BytesIO(GIF_BYTES), filename="toykami.gif")

# ==============================
# ✅ 1通配信（/spam 用・直送→wh→followup）
# ==============================
async def deliver(content, mention, channel, followup, state, roles, embeds=None) -> bool:
    am = discord.AllowedMentions(everyone=mention, roles=bool(roles), users=False)
    ch_id = getattr(channel, "id", None)
    file = take_file(state)

    pool = state.get("pool")
    if state.get("native") and ch_id is not None and pool is not None and pool.senders:
        try:
            await pool.send(ch_id, content, file, am, embeds)
            return True
        except discord.Forbidden:
            state["native"] = False
            note(state, "no-native", "[送信] ⚠️ チャンネル直送は不可")
        except (AttributeError, TypeError) as e:
            state["native"] = False
            note(state, "bad-native", f"[送信] ⚠️ 直送API不一致 → followupへ: {type(e).__name__}: {e}")
        except discord.HTTPException as e:
            note(state, f"http:{getattr(e, 'status', '?')}",
                 f"[送信] ⚠️ HTTP {getattr(e, 'status', '?')}")
        except Exception as e:
            note(state, f"ex:{type(e).__name__}", f"[送信] ⚠️ {type(e).__name__}: {e}")

    if state.get("webhook") and ch_id is not None:
        wh = await get_webhook(channel)
        if wh is not None:
            try:
                await bucket_for(("wh", ch_id)).acquire()
                await wh.send(
                    content=content, embeds=embeds or [], allowed_mentions=am,
                    username="TOISHIN", wait=False,
                )
                return True
            except Exception as e:
                note(state, f"wh:{type(e).__name__}", f"[送信] ⚠️ wh {type(e).__name__}: {e}")

    if followup is not None and state.get("allow_followup", True):
        try:
            if ch_id is not None:
                await bucket_for(("fu", ch_id)).acquire()
            await followup.send(
                content=content, file=file, embeds=embeds or [], allowed_mentions=am
            )
            return True
        except discord.Forbidden:
            state["allow_followup"] = False
            note(state, "no-followup", "[送信] ❌ followupも拒否")
        except discord.HTTPException as e:
            note(state, f"fuhttp:{getattr(e, 'status', '?')}",
                 f"[送信] ⚠️ followup HTTP {getattr(e, 'status', '?')}")
        except Exception as e:
            note(state, f"fu:{type(e).__name__}", f"[送信] ❌ {type(e).__name__}: {e}")

    return False

# ==============================
# ✅ バースト送信（/spam）
# ==============================
async def run_spam(channel, followup, guild, count, mention, state, roles):
    body = build_body(guild, mention, roles)
    embeds = build_embeds(guild) if state.get("power") == "heavy" else None
    interval = state.get("interval", 0.0)

    ok = 0
    fail = 0

    async def one(i: int):
        nonlocal ok, fail
        if interval > 0:
            await asyncio.sleep(interval * i + random.random() * 0.03)
        for attempt in range(RETRY_MAX):
            try:
                if await deliver(body, mention, channel, followup, state, roles, embeds):
                    ok += 1
                    return
            except discord.HTTPException as e:
                if getattr(e, "status", 0) == 429:
                    ra = getattr(e, "retry_after", None) or 0.5
                    await asyncio.sleep(float(ra) + 0.05)
                    continue
                print(f"[送信] HTTP {getattr(e, 'status', '?')}: {e}")
            except Exception as e:
                print(f"[送信] {type(e).__name__}: {e}")
            await asyncio.sleep(0.15 * (attempt + 1))
        fail += 1
        note(state, "task-fail", "[送信] ❌ 全候補失敗のメッセージあり")

    await asyncio.gather(*(one(i) for i in range(count)))
    print(f"[送信完了] 成功={ok} 失敗={fail}")
    return ok, fail

# ==============================
# ✅ ユーザートークン送信
# ==============================
async def run_user(senders: list[UserSender], channel, guild, count, mention,
                   state, roles):
    body = build_body(guild, mention, roles)
    embeds = build_embeds(guild) if state.get("power") == "heavy" else None
    am = discord.AllowedMentions(everyone=mention, roles=bool(roles), users=False)
    ch_id = getattr(channel, "id", None)
    interval = state.get("interval", 0.0)

    ok = 0
    fail = 0
    idx = 0
    lock = asyncio.Lock()

    async def one(i: int):
        nonlocal ok, fail, idx
        if interval > 0:
            await asyncio.sleep(interval * i + random.random() * 0.03)
        file = take_file(state)
        for attempt in range(RETRY_MAX):
            async with lock:
                alive = [s for s in senders if s.ok]
                if not alive:
                    fail += 1
                    return
                s = alive[idx % len(alive)]
                idx += 1
            if ch_id is None:
                fail += 1
                return
            await bucket_for((f"u:{s.label}", ch_id),
                             USER_BUCKET_BURST, USER_BUCKET_PER).acquire()
            if file is not None:
                try:
                    file.fp.seek(0)
                except Exception:
                    pass
            r = await user_send(s.token, ch_id, body, file, am, embeds)
            if r == "ok":
                ok += 1
                return
            if r == "dead":
                s.ok = False
                note(state, f"user-dead:{s.label}",
                     f"[user] ⚠️ {s.label} が使えません → 他へ切替")
                continue
            await asyncio.sleep(0.4 * (attempt + 1))
        fail += 1

    await asyncio.gather(*(one(i) for i in range(count)))
    print(f"[user送信完了] 成功={ok} 失敗={fail}")
    return ok, fail

# ==============================
# ✅ シード送信（seedを順に消費・枯渇で次へ・直送フォールバック）
# ==============================
async def _seed_webhook(seed: dict, channel):
    if seed.get("webhook_id") and seed.get("webhook_token"):
        return discord.Webhook.partial(seed["webhook_id"], seed["webhook_token"], client=bot)
    guild = getattr(channel, "guild", None) if channel is not None else None
    if channel is not None and guild is not None and getattr(guild, "me", None) is not None:
        try:
            wh = await channel.create_webhook(name="raid")
            seed["webhook_id"], seed["webhook_token"] = wh.id, wh.token
            save_seeds()
            return discord.Webhook.partial(wh.id, wh.token, client=bot)
        except Exception as e:
            print(f"[シード] webhook作成失敗 → トークンで継続: {type(e).__name__}: {e}")
    return discord.Webhook.partial(bot.application_id, seed["token"], client=bot)

async def _send_via_seed(seed, body, embeds, am, file, channel) -> str:
    kw = {"content": body, "embeds": embeds or [], "allowed_mentions": am, "wait": False}
    if file is not None:
        kw["file"] = file

    gate = gate_for_seed(seed)
    wh = await _seed_webhook(seed, channel)
    try:
        await gate.acquire()
        await wh.send(**kw)
        seed["used"] = int(seed.get("used", 0)) + 1
        return "ok"
    except discord.NotFound:
        if seed.get("webhook_id"):
            seed["webhook_id"] = seed["webhook_token"] = None
            wh = await _seed_webhook(seed, channel)
            try:
                await gate_for_seed(seed).acquire()
                await wh.send(**kw)
                seed["used"] = int(seed.get("used", 0)) + 1
                return "ok"
            except discord.HTTPException as e:
                if getattr(e, "code", 0) == 40094:
                    return "exhausted"
                return "retry"
            except Exception:
                return "retry"
        return "dead"
    except discord.Forbidden:
        return "dead"
    except discord.HTTPException as e:
        if getattr(e, "code", 0) == 40094:
            return "exhausted"
        if getattr(e, "status", 0) == 429:
            await asyncio.sleep(float(getattr(e, "retry_after", None) or 0.7) + 0.05)
            return "retry"
        return "retry"
    except Exception as e:
        print(f"[シード] {type(e).__name__}: {e}")
        return "retry"

async def run_spam_seed(seeds: list[dict], channel, guild, count, mention, state, roles, pool):
    body = build_body(guild, mention, roles)
    embeds = build_embeds(guild) if state.get("power") == "heavy" else None
    am = discord.AllowedMentions(everyone=mention, roles=bool(roles), users=False)
    interval = state.get("interval", 0.0)
    ch_id = getattr(channel, "id", None)
    can_fb = channel is not None and pool is not None and bool(pool.senders)

    lock = asyncio.Lock()
    ok = 0
    fail = 0
    dead = None

    async def next_seed():
        async with lock:
            for s in seeds:
                if seed_remaining(s) > 0:
                    return s
            return None

    async def one(i: int):
        nonlocal ok, fail, dead
        if interval > 0:
            await asyncio.sleep(interval * i + random.random() * 0.03)
        if dead == "forbidden":
            fail += 1
            return

        file = take_file(state)
        for attempt in range(RETRY_MAX):
            seed = await next_seed()
            if seed is None:
                dead = "exhausted"
                break
            if file is not None:
                try:
                    file.fp.seek(0)
                except Exception:
                    pass
            r = await _send_via_seed(seed, body, embeds, am, file, channel)
            if r == "ok":
                ok += 1
                return
            if r == "exhausted":
                seed["used"] = TOKEN_CAPACITY
                save_seeds()
                continue
            if r == "dead":
                dead = "forbidden"
                break
            await asyncio.sleep(0.15 * (attempt + 1))

        if dead != "forbidden" and can_fb:
            try:
                await pool.send(ch_id, body, file, am, embeds)
                ok += 1
                return
            except Exception as e:
                print(f"[シード] 直送も失敗 {type(e).__name__}: {e}")
        if dead is None:
            dead = "failed"
        fail += 1

    await asyncio.gather(*(one(i) for i in range(count)))
    return ok, fail, dead

# ==============================
# 🧬 増殖ハンドラ（/seed のボタン）
# ==============================
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type is not discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id") or ""
    if not custom_id.startswith("breed:"):
        return

    try:
        _, uid_s, cid_s = custom_id.split(":")
        owner = int(uid_s)
        cid = int(cid_s)
    except Exception:
        return

    if interaction.user.id != owner:
        try:
            await interaction.response.send_message(
                "## ❌ **このボタンは実行者専用です。**", ephemeral=True
            )
        except Exception:
            pass
        return

    gid = interaction.guild_id
    if gid is None:
        return

    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        print(f"[繁殖] defer失敗: {type(e).__name__}: {e}")
        return

    _gc_seeds(gid)
    mine = [s for s in (SEEDS.get(gid) or [])
            if s["user_id"] == owner and s["channel_id"] == cid]

    if len(mine) >= MAX_SEEDS_PER_CHANNEL:
        await interaction.edit_original_response(
            content=f"## ⚠️ **上限 {MAX_SEEDS_PER_CHANNEL}本に達しました。**\n"
                    f"-# `/seedclear` で解除できます。"
        )
        return

    msg_id = getattr(interaction.message, "id", None)
    origin = next((s for s in mine if msg_id and s.get("msg_id") == msg_id), None)

    name = getattr(interaction.channel, "name", None) or (mine[0]["channel_name"] if mine else str(cid))
    wh = None
    try:
        wh = await get_webhook(interaction.channel)
    except Exception:
        wh = None

    add_seed(gid, cid, owner, interaction.token, name, wh,
             msg_id=origin.get("msg_id") if origin else None)

    total = sum(seed_remaining(s) for s in
                [s for s in (SEEDS.get(gid) or [])
                 if s["user_id"] == owner and s["channel_id"] == cid])
    cap_txt = "無制限" if total >= 10 ** 6 else f"{total}通"

    if origin is not None and msg_id:
        await edit_seed_message(
            gid, cid, owner, msg_id,
            f"## 🧬 **繁殖中 {len(mine) + 1}本**\n"
            f"このチャンネルの残り送信可能数: **{cap_txt}**\n"
            f"🧬 を押すたびに **+{TOKEN_CAPACITY}通** 増えます（連打可）\n"
            f"-# 残り {int(SEED_TTL / 60)} 分有効。増やしたら `/spamall` を実行。"
        )

    await interaction.edit_original_response(
        content=f"🧬 **+{TOKEN_CAPACITY}通** → 残り **{cap_txt}**"
    )

# ==============================
# View（/spam・/spamall）
# ==============================
class SpamView(ui.View):
    def __init__(self, mention: bool, reason: str, count: int, owner_id: int):
        super().__init__(timeout=None)
        self.mention = mention
        self.reason = reason
        self.count = count
        self.owner_id = owner_id

    @ui.button(label="開始", style=discord.ButtonStyle.danger, emoji="📢")
    async def start_handler(self, interaction: discord.Interaction, btn: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "## ❌ **このボタンは実行者専用です。**", ephemeral=True
            )
            return

        guild = interaction.guild
        channel = await _resolve_mention_channel(interaction) if guild else interaction.channel
        count = self.count
        c = cfg(self.owner_id)
        power = c["power"]
        roles = c["roles"]

        me = await get_bot_member(guild) if guild else None
        caps = channel_caps(channel, me, interaction.user) if guild else {"send": True, "attach": True}

        pool = get_pool(self.owner_id)
        native = bool(me) and channel is not None and caps["send"]
        state = new_state(native, caps["attach"], power,
                          allow_followup=True, interval=c["interval"])
        state["pool"] = pool

        senders = alive_usersenders(self.owner_id)
        use_auto = bool(c["auto"]) and bool(senders)

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            print(f"[ボタン] defer失敗: {type(e).__name__}: {e}")
            return

        if use_auto:
            print(f"[開始] 回数={count} 経路=user{len(senders)} 火力={power}")
            try:
                await run_user(senders, channel, guild, count, self.mention, state, roles)
            except Exception as e:
                print(f"[ボタン] 送信例外: {type(e).__name__}: {e}")
                traceback.print_exc()
            return

        if not native:
            count = min(count, TOKEN_CAPACITY)

        lanes = f"直送{len(pool.senders)}レーン" if native else f"followup(最大{TOKEN_CAPACITY}通)"
        print(f"[開始] 回数={count} 経路={lanes} 火力={power}")
        try:
            await run_spam(channel, interaction.followup, guild, count,
                           self.mention, state, roles)
        except Exception as e:
            print(f"[ボタン] 送信例外: {type(e).__name__}: {e}")
            traceback.print_exc()

class SpamAllView(ui.View):
    def __init__(self, mention: bool, reason: str, count: int, owner_id: int,
                 waves: int = 1, full: bool = True):
        super().__init__(timeout=None)
        self.mention = mention
        self.reason = reason
        self.count = count
        self.owner_id = owner_id
        self.waves = max(1, waves)
        self.full = full

    @ui.button(label="全チャンネルに送信", style=discord.ButtonStyle.danger, emoji="💥")
    async def all_handler(self, interaction: discord.Interaction, btn: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "## ❌ **このボタンは実行者専用です。**", ephemeral=True
            )
            return

        if interaction.guild_id is None:
            await interaction.response.send_message(
                "## ❌ **サーバー内で実行してください。**", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[spamall] defer失敗: {type(e).__name__}: {e}")
            return

        ACTIVE[self.owner_id] = active_of(self.owner_id) + 1
        try:
            await self._run(interaction)
        except Exception as e:
            print(f"[spamall] 例外: {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            ACTIVE[self.owner_id] = max(0, active_of(self.owner_id) - 1)

    async def _run(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        guild = interaction.guild
        c = cfg(self.owner_id)
        power = c["power"]
        roles = c["roles"]
        pool = get_pool(self.owner_id)
        me = await get_bot_member(guild) if guild else None
        user = await _resolve_member(guild, interaction.user) if guild else None

        senders = alive_usersenders(self.owner_id)
        use_auto = bool(c["auto"]) and bool(senders)

        seeds_all = user_seeds(gid, self.owner_id)
        now = time.time()
        seeds = [s for s in seeds_all if s["expires"] > now]
        expired = [s for s in seeds_all if s["expires"] <= now]

        by_ch: dict[int, list[dict]] = {}
        names: dict[int, str] = {}
        for s in seeds:
            by_ch.setdefault(s["channel_id"], []).append(s)
            names[s["channel_id"]] = s["channel_name"]

        # 対象決定
        targets: dict[int, str] = {}
        if use_auto:
            if by_ch:
                targets = {cid: names[cid] for cid in by_ch}
            else:
                chans = await get_sendable_text_channels(guild, me, user, self.owner_id)
                targets = {ch.id: getattr(ch, "name", str(ch.id)) for ch in chans}
            if not targets:
                await interaction.followup.send(
                    "## ❌ **送信できるチャンネルがありません。**\n"
                    "`/setignore` を確認するか、`/seed` で対象を絞ってください。",
                    ephemeral=True,
                )
                return
        else:
            if not seeds:
                msg = (
                    "## ❌ **設置がありません。**\n"
                    "自動モードでなければ `/seed` が必要です（🧬で増やせます）。\n"
                    "-# `/setauto on` にすると seed 不要で全chに撃てます。"
                )
                if expired:
                    msg += f"\n⚠️ 期限切れ {len(expired)}本。`/seed` をやり直してください。"
                await interaction.followup.send(msg, ephemeral=True)
                return
            targets = {cid: names[cid] for cid in by_ch}

        total_cap = sum(channel_capacity(gid, self.owner_id, cid) for cid in by_ch)

        print(f"[spamall] 開始 ch={len(targets)} 希望={self.count}周={self.waves} "
              f"火力={power} 経路={'user'+str(len(senders)) if use_auto else 'seed'} "
              f"総容量={total_cap} full={self.full} 期限切れ={len(expired)} "
              f"同時={active_of(self.owner_id)}")

        ok_total = 0
        fail_total = 0
        results: list[str] = []

        async def worker(cid: int, ch_name: str):
            nonlocal ok_total, fail_total
            ch = None
            try:
                ch = guild.get_channel(cid) if guild else None
            except Exception:
                ch = None
            state = new_state(True, True, power, allow_followup=False,
                              webhook=False, interval=c["interval"])
            state["pool"] = pool
            ok_sum = fail_sum = 0
            reason = None

            if use_auto:
                for _ in range(self.waves):
                    if not alive_usersenders(self.owner_id):
                        reason = "user枯渇"
                        break
                    ok, fail = await run_user(
                        senders, ch, guild, self.count, self.mention, state, roles
                    )
                    ok_sum += ok
                    fail_sum += fail
                    if ok == 0 and fail > 0:
                        reason = "失敗"
                        break
            else:
                ch_seeds = by_ch[cid]
                for _ in range(self.waves):
                    cap = sum(seed_remaining(s) for s in ch_seeds)
                    if cap <= 0:
                        reason = "枯渇"
                        break
                    n = cap if self.full else min(self.count, cap)
                    ok, fail, reason = await run_spam_seed(
                        ch_seeds, ch, guild, n, self.mention, state, roles, pool
                    )
                    ok_sum += ok
                    fail_sum += fail
                    if reason in ("forbidden", "exhausted", "failed"):
                        if reason == "exhausted":
                            reason = "枯渇"
                        break

            ok_total += ok_sum
            fail_total += fail_sum
            tail = f" ({reason})" if reason else ""
            results.append(f"・#{ch_name}: {ok_sum}ok/{fail_sum}ng{tail}")

        for s in expired:
            results.append(f"・#{s['channel_name']}: 期限切れ")

        await asyncio.gather(*(worker(cid, nm) for cid, nm in targets.items()))

        print(f"[spamall] 完了 ch={len(targets)} 成功={ok_total} 失敗={fail_total} "
              f"期限切れ={len(expired)}")
        for line in results:
            print(line)

# ==============================
# /spam
# ==============================
@tree.command(name="spam", description="宣伝メッセージを送信します（ボタンを押して実行）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(everyone="@everyone の扱い（未指定＝自分の設定）")
@app_commands.choices(everyone=MENTION_CHOICES)
async def cmd_spam(interaction: discord.Interaction, everyone: Optional[str] = None):
    c = cfg(interaction.user.id)
    mode = everyone or c["mention"]
    mention, reason = await resolve_mention(interaction, mode)

    warn = ""
    me = await get_bot_member(interaction.guild) if interaction.guild else None
    if interaction.guild and me is None and not c["auto"]:
        warn = f"\n## ⚠️ **外部モード（followupのみ・最大{TOKEN_CAPACITY}通）**"

    lanes = len(get_pool(interaction.user.id).senders)
    users = len(alive_usersenders(interaction.user.id))
    route = f"🧑 自動 {users}アカウント" if c["auto"] and users else f"🛰 {lanes}レーン"
    await interaction.response.send_message(
        f"🤓 **ボタンを押してスパム開始！**\n📋 @everyone: {reason}\n"
        f"🔢 回数: {c['count']}\n🔥 火力: {c['power']}\n🚚 経路: {route}{warn}",
        view=SpamView(mention=mention, reason=reason, count=c["count"],
                      owner_id=interaction.user.id),
        ephemeral=True,
    )

# ==============================
# /seed
# ==============================
@tree.command(name="seed", description="このチャンネルを対象にします（🧬で5通ずつ増やせます）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_seed(interaction: discord.Interaction):
    gid = interaction.guild_id
    cid = interaction.channel_id
    if gid is None or cid is None:
        await interaction.response.send_message(
            "## ❌ **サーバー内で実行してください。**", ephemeral=True
        )
        return

    _gc_seeds(gid)
    mine = [s for s in (SEEDS.get(gid) or [])
            if s["user_id"] == interaction.user.id and s["channel_id"] == cid]

    if len(mine) >= MAX_SEEDS_PER_CHANNEL:
        await interaction.response.send_message(
            f"## ⚠️ **このチャンネルの設置は上限 {MAX_SEEDS_PER_CHANNEL}本です。**\n"
            f"`/seedclear` で解除してください。",
            ephemeral=True,
        )
        return

    wh = None
    try:
        wh = await get_webhook(interaction.channel)
    except Exception:
        wh = None

    name = getattr(interaction.channel, "name", None) or str(cid)
    seed = add_seed(gid, cid, interaction.user.id, interaction.token, name, wh)

    total = sum(seed_remaining(s) for s in
                [s for s in (SEEDS.get(gid) or [])
                 if s["user_id"] == interaction.user.id and s["channel_id"] == cid])
    cap_txt = "無制限（webhook）" if total >= 10 ** 6 else f"{total}通"

    msg = await interaction.response.send_message(
        f"## ✅ **設置 {len(mine) + 1}本**\n"
        f"このチャンネルの残り送信可能数: **{cap_txt}**\n"
        f"🧬 を押すたびに **+{TOKEN_CAPACITY}通** 増えます（連打可）\n"
        f"-# 残り {int(SEED_TTL / 60)} 分有効。増やしたら `/spamall` を実行。",
        view=breed_view(interaction.user.id, cid),
        ephemeral=True,
    )
    try:
        seed["msg_id"] = msg.id
        save_seeds()
    except Exception:
        pass

@tree.command(name="seedlist", description="設置済みチャンネルを表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_seedlist(interaction: discord.Interaction):
    seeds = user_seeds(interaction.guild_id, interaction.user.id)
    if not seeds:
        await interaction.response.send_message(
            "# ℹ️ **設置なし**\n各チャンネルで `/seed` を実行してください。", ephemeral=True
        )
        return
    now = time.time()
    by_ch: dict[int, list[dict]] = {}
    for s in seeds:
        by_ch.setdefault(s["channel_id"], []).append(s)
    lines = []
    for i, (cid, group) in enumerate(by_ch.items(), 1):
        cap = sum(seed_remaining(s) for s in group)
        cap_txt = "無制限" if cap >= 10 ** 6 else f"{cap}通"
        left = int((min(s["expires"] for s in group) - now) / 60)
        lines.append(f"{i}. #{group[0]['channel_name']} — 残り{cap_txt} / {left}分")
    await interaction.response.send_message(
        f"# 📌 設置 {len(by_ch)}ch\n" + "\n".join(lines), ephemeral=True
    )

@tree.command(name="seedclear", description="設置をすべて解除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_seedclear(interaction: discord.Interaction):
    gid = interaction.guild_id
    lst = SEEDS.get(gid) or []
    before = len(lst)
    lst[:] = [s for s in lst if s["user_id"] != interaction.user.id]
    if not lst:
        SEEDS.pop(gid, None)
    save_seeds()
    await interaction.response.send_message(
        f"# ✅ **{before - len(lst)}本 の設置を解除しました。**", ephemeral=True
    )

# ==============================
# /spamall
# ==============================
@tree.command(name="spamall", description="設置済みの全チャンネルに一括送信します（先に /seed）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(
    everyone="@everyone の扱い（未指定＝自分の設定）",
    waves="全体を何周するか",
    count="各chの送信数（省略＝設定値）",
    full="容量を使い切る（手動モードのみ）",
)
@app_commands.choices(everyone=MENTION_CHOICES)
async def cmd_spamall(interaction: discord.Interaction, everyone: Optional[str] = None,
                      waves: int = 1, count: Optional[int] = None,
                      full: bool = True):
    c = cfg(interaction.user.id)
    mode = everyone or c["mention"]
    mention, reason = await resolve_mention(interaction, mode)
    waves = max(1, waves)
    want = count if (count and count > 0) else c["count"]

    senders = alive_usersenders(interaction.user.id)
    use_auto = bool(c["auto"]) and bool(senders)

    seeds = user_seeds(interaction.guild_id, interaction.user.id)
    by_ch: dict[int, list[dict]] = {}
    for s in seeds:
        by_ch.setdefault(s["channel_id"], []).append(s)
    cap_total = sum(channel_capacity(interaction.guild_id, interaction.user.id, cid)
                    for cid in by_ch)

    if use_auto:
        route = f"🧑 ユーザートークン {len(senders)}件（seed不要）"
        per_ch = f"各ch {want}回 × {waves}周"
    else:
        route = "🛰 seed（手動・🧬で増やす）"
        per_ch = f"各ch {want}回 × {waves}周{'（容量上限まで）' if full else ''}"

    head = (
        f"💥 **一括送信**\n"
        f"📋 @everyone: {reason}\n"
        f"🚚 経路: {route}\n"
        f"🔢 {per_ch} / 火力: {c['power']}\n"
        f"🔋 seed容量 {cap_total if cap_total < 10 ** 6 else '無制限'}通 / "
        f"設置 {len(by_ch)}ch\n"
        f"-# 連打OK・実行中 {active_of(interaction.user.id)}件"
    )
    if use_auto:
        head += "\n-# 対象: 設置ch（無ければ送信可能ch全部）"
    elif not seeds:
        head += "\n## ⚠️ **設置なし。`/seed` → 🧬 で増やすか `/setauto on`。**"

    await interaction.response.send_message(
        head,
        view=SpamAllView(mention=mention, reason=reason, count=want,
                         owner_id=interaction.user.id, waves=waves, full=full),
        ephemeral=True,
    )

# ==============================
# /setauto
# ==============================
@tree.command(name="setauto", description="ユーザートークンでの自動送信を切り替えます")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.choices(mode=[
    app_commands.Choice(name="自動（ユーザートークンで送る）", value="on"),
    app_commands.Choice(name="手動（seed + 🧬）", value="off"),
])
async def cmd_setauto(interaction: discord.Interaction, mode: str):
    c = cfg(interaction.user.id)
    on = mode == "on"
    if on and not c["usertokens"]:
        await interaction.response.send_message(
            "## ❌ **ユーザートークンが未登録です。**\n"
            "`/addusertoken` で登録してください。", ephemeral=True
        )
        return
    c["auto"] = on
    save_cfg()
    if on:
        msg = (f"# ✅ **自動モード ON**\n"
               f"🧑 {len(c['usertokens'])}件で直送します。seedは不要です。\n"
               f"-# seedを置いたchがある場合、対象はそのchだけになります。")
    else:
        msg = "# ✅ **手動モード（seed + 🧬）**\n-# `/seed` で対象を設置してください。"
    await interaction.response.send_message(msg, ephemeral=True)

# ==============================
# /addusertoken
# ==============================
@tree.command(name="addusertoken", description="ユーザートークンを名前付きで登録します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(token="ユーザートークン", label="名前（識別用）")
async def cmd_addusertoken(interaction: discord.Interaction, token: str, label: str):
    await interaction.response.defer(ephemeral=True)
    token = token.strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    label = label.strip() or "user"

    c = cfg(interaction.user.id)
    if any(t["token"] == token for t in c["usertokens"]):
        await interaction.followup.send("# ⚠️ **既に登録済みのトークンです。**", ephemeral=True)
        return

    me = await user_me(token)
    if me is None:
        await interaction.followup.send(
            "# ❌ **トークンが無効です。**\n"
            "Discordの開発者ツール等から取り直してください。", ephemeral=True
        )
        return

    c["usertokens"].append({"token": token, "label": label})
    save_cfg()
    if interaction.user.id not in USERPOOLS:
        load_userpool(interaction.user.id)
    else:
        USERPOOLS[interaction.user.id].append(UserSender(label, token))

    await interaction.followup.send(
        f"# ✅ **登録: {label}**\n"
        f"👤 アカウント: {me.get('username')}\n"
        f"🧑 登録数 {len(c['usertokens'])}件\n"
        f"-# {mask(token)}\n"
        f"-# 使うには `/setauto on`",
        ephemeral=True,
    )

# ==============================
# /delusertoken
# ==============================
@tree.command(name="delusertoken", description="登録したユーザートークンを削除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(number="番号（/myusertokens で確認）")
async def cmd_delusertoken(interaction: discord.Interaction, number: int):
    c = cfg(interaction.user.id)
    if not 1 <= number <= len(c["usertokens"]):
        await interaction.response.send_message(
            f"# ❌ **1〜{len(c['usertokens'])} で指定してください。**", ephemeral=True
        )
        return
    item = c["usertokens"].pop(number - 1)
    if not c["usertokens"]:
        c["auto"] = False
    save_cfg()

    lst = USERPOOLS.get(interaction.user.id) or []
    for s in list(lst):
        if s.token == item["token"]:
            lst.remove(s)
            break
    await interaction.response.send_message(
        f"# ✅ **削除: {item.get('label') or 'user'}**\n"
        f"🧑 残り {len(c['usertokens'])}件"
        + ("\n-# 登録0件のため自動モードOFF" if not c["usertokens"] else ""),
        ephemeral=True,
    )

# ==============================
# /myusertokens
# ==============================
@tree.command(name="myusertokens", description="登録したユーザートークンを一覧表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_myusertokens(interaction: discord.Interaction):
    c = cfg(interaction.user.id)
    if not c["usertokens"]:
        await interaction.response.send_message(
            "# ℹ️ **登録なし**\n`/addusertoken` で登録できます。", ephemeral=True
        )
        return
    lines = [f"{i}. {t.get('label') or 'user'} — `{mask(t['token'])}`"
             for i, t in enumerate(c["usertokens"], 1)]
    await interaction.response.send_message(
        f"# 🧑 登録ユーザー {len(c['usertokens'])}件\n" + "\n".join(lines)
        + f"\n\n自動モード: **{'ON' if c['auto'] else 'OFF'}**",
        ephemeral=True,
    )

# ==============================
# /setcount
# ==============================
@tree.command(name="setcount", description="自分の送信回数を変更します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(count="送信回数")
async def cmd_setcount(interaction: discord.Interaction, count: int):
    if count < 1:
        await interaction.response.send_message("# ❌ **1以上で指定してください。**", ephemeral=True)
        return
    c = cfg(interaction.user.id)
    c["count"] = count
    save_cfg()
    need = -(-count // TOKEN_CAPACITY)
    warn = ""
    if count > TOKEN_CAPACITY:
        warn = (f"\n## ℹ️ **手動モードなら 1ch あたり 🧬×{need} 本必要です。**"
                f"\n-# 自動モードなら seed 不要。")
    await interaction.response.send_message(
        f"# ✅ **送信回数を {count}回 に変更しました。**{warn}", ephemeral=True
    )

# ==============================
# /setinterval
# ==============================
@tree.command(name="setinterval", description="自分の送信間隔を変更（0 = 最速・バケット任せ）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(interval="送信間隔（秒）。0で最速")
async def cmd_setinterval(interaction: discord.Interaction, interval: float):
    if interval < 0:
        await interaction.response.send_message("# ❌ **0以上で指定してください。**", ephemeral=True)
        return
    c = cfg(interaction.user.id)
    c["interval"] = interval
    save_cfg()
    note_txt = "（バケット上限で自動制御）" if interval == 0 else ""
    await interaction.response.send_message(
        f"# ✅ **送信間隔を {interval}秒 に変更しました。**{note_txt}", ephemeral=True
    )

@bot.command(name="setinterval")
async def prefix_setinterval(ctx, interval: float = 0.0):
    if interval < 0:
        await ctx.send("# ❌ **0以上で指定してください。**")
        return
    c = cfg(ctx.author.id)
    c["interval"] = interval
    save_cfg()
    await ctx.send(f"# ✅ **送信間隔を {interval}秒 に変更しました。**")

# ==============================
# /seteveryone
# ==============================
@tree.command(name="seteveryone", description="自分の @everyone の扱いを設定します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.choices(mode=MENTION_CHOICES)
async def cmd_seteveryone(interaction: discord.Interaction, mode: str):
    c = cfg(interaction.user.id)
    c["mention"] = mode
    save_cfg()
    label = {"auto": "自動判定", "on": "常につける", "off": "常につけない"}[mode]
    await interaction.response.send_message(
        f"# ✅ **@everyone の設定を「{label}」に変更しました。**", ephemeral=True
    )

# ==============================
# /setpower
# ==============================
@tree.command(name="setpower", description="自分の1発の重さを変更します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.choices(mode=POWER_CHOICES)
async def cmd_setpower(interaction: discord.Interaction, mode: str):
    c = cfg(interaction.user.id)
    c["power"] = mode
    save_cfg()
    await interaction.response.send_message(
        f"# ✅ **火力モードを「{mode}」に変更しました。**", ephemeral=True
    )

# ==============================
# /setignore
# ==============================
@tree.command(name="setignore", description="自分の除外チャンネル名（前方一致・カンマ区切り）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(names="除外名（空で解除）")
async def cmd_setignore(interaction: discord.Interaction, names: str = ""):
    c = cfg(interaction.user.id)
    c["ignore"] = [n.strip() for n in names.split(",") if n.strip()]
    save_cfg()
    await interaction.response.send_message(
        f"# ✅ **除外設定: {', '.join(c['ignore']) or 'なし'}**", ephemeral=True
    )

# ==============================
# /addtoken
# ==============================
@tree.command(name="addtoken", description="サブBotのトークンを自分のレーンに登録します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(token="Botトークン", label="表示名（任意）")
async def cmd_addtoken(interaction: discord.Interaction, token: str, label: str = ""):
    await interaction.response.defer(ephemeral=True)
    token = token.strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()

    c = cfg(interaction.user.id)
    if any(t["token"] == token for t in c["tokens"]):
        await interaction.followup.send("# ⚠️ **既に登録済みのトークンです。**", ephemeral=True)
        return

    s = Sender(label or "sub", token=token, http=discord.http.HTTPClient(token=token))
    await s.login()
    if not s.ready:
        try:
            await s.http.close()
        except Exception:
            pass
        await interaction.followup.send(
            "# ❌ **ログイン失敗**\nトークンが不正か、Botが無効化されています。", ephemeral=True
        )
        return

    name = label
    try:
        me = await s.http.get_user("@me")
        name = name or me.name
    except Exception:
        name = name or "sub"

    c["tokens"].append({"token": token, "label": name})
    save_cfg()
    get_pool(interaction.user.id).senders.append(s)
    await interaction.followup.send(
        f"# ✅ **登録: {name}**\n🛰 現在 {len(c['tokens']) + 1}レーン"
        f"（メイン込み）\n-# {mask(token)}",
        ephemeral=True,
    )

# ==============================
# /deltoken
# ==============================
@tree.command(name="deltoken", description="登録したトークンを削除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(number="番号（/mytokens で確認）")
async def cmd_deltoken(interaction: discord.Interaction, number: int):
    c = cfg(interaction.user.id)
    if not 1 <= number <= len(c["tokens"]):
        await interaction.response.send_message(
            f"# ❌ **1〜{len(c['tokens'])} で指定してください。**", ephemeral=True
        )
        return
    item = c["tokens"].pop(number - 1)
    save_cfg()

    pool = get_pool(interaction.user.id)
    for s in list(pool.senders):
        if s.token == item["token"]:
            pool.senders.remove(s)
            try:
                await s.http.close()
            except Exception:
                pass
            break
    await interaction.response.send_message(
        f"# ✅ **削除: {item.get('label') or 'sub'}**\n🛰 残り {len(c['tokens'])}本", ephemeral=True
    )

# ==============================
# /mytokens
# ==============================
@tree.command(name="mytokens", description="登録したトークンを一覧表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_mytokens(interaction: discord.Interaction):
    c = cfg(interaction.user.id)
    if not c["tokens"]:
        await interaction.response.send_message(
            "# ℹ️ **登録トークンなし（メインのみで稼働）**", ephemeral=True
        )
        return
    lines = [f"{i}. {t.get('label') or 'sub'} — `{mask(t['token'])}`"
             for i, t in enumerate(c["tokens"], 1)]
    await interaction.response.send_message(
        "# 🛰 登録レーン\n" + "\n".join(lines)
        + f"\n\n合計 {len(c['tokens']) + 1}レーン（メイン込み）",
        ephemeral=True,
    )

# ==============================
# /addrole / /delrole / /myroles
# ==============================
@tree.command(name="addrole", description="踏みつけ用ロールIDを登録します（1通ごとに巡回）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(role="ロール（サーバーのロールから選択 or ID）")
async def cmd_addrole(interaction: discord.Interaction, role: str):
    rid = None
    txt = role.strip()
    if txt.startswith("<@&") and txt.endswith(">"):
        txt = txt[3:-1]
    if txt.isdigit():
        rid = int(txt)
    if rid is None:
        await interaction.response.send_message(
            "# ❌ **ロールIDが読めません。**\n`@ロール` を選択するか、IDを貼ってください。",
            ephemeral=True,
        )
        return
    c = cfg(interaction.user.id)
    if rid in c["roles"]:
        await interaction.response.send_message("# ⚠️ **登録済みです。**", ephemeral=True)
        return
    c["roles"].append(rid)
    save_cfg()
    await interaction.response.send_message(
        f"# ✅ **登録: <@&{rid}>**\n現在 {len(c['roles'])}件", ephemeral=True
    )

@tree.command(name="delrole", description="登録したロールを削除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(number="番号（/myroles で確認）")
async def cmd_delrole(interaction: discord.Interaction, number: int):
    c = cfg(interaction.user.id)
    if not 1 <= number <= len(c["roles"]):
        await interaction.response.send_message(
            f"# ❌ **1〜{len(c['roles'])} で指定してください。**", ephemeral=True
        )
        return
    rid = c["roles"].pop(number - 1)
    save_cfg()
    await interaction.response.send_message(
        f"# ✅ **削除: <@&{rid}>**\n残り {len(c['roles'])}件", ephemeral=True
    )

@tree.command(name="myroles", description="登録したロールを一覧表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_myroles(interaction: discord.Interaction):
    c = cfg(interaction.user.id)
    if not c["roles"]:
        await interaction.response.send_message("# ℹ️ **登録ロールなし**", ephemeral=True)
        return
    lines = [f"{i}. <@&{r}> (`{r}`)" for i, r in enumerate(c["roles"], 1)]
    await interaction.response.send_message("# 🎯 登録ロール\n" + "\n".join(lines), ephemeral=True)

# ==============================
# /cleanwh
# ==============================
@tree.command(name="cleanwh", description="生成したウェブフック(raid)を一括削除します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_cleanwh(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        await interaction.followup.send("## ❌ サーバー内で実行してください。", ephemeral=True)
        return
    n = await cleanup_webhooks(interaction.guild)
    for lst in SEEDS.values():
        for s in lst:
            s["webhook_id"] = s["webhook_token"] = None
    save_seeds()
    await interaction.followup.send(f"# ✅ **{n}件のウェブフックを削除しました。**", ephemeral=True)

# ==============================
# /myconfig
# ==============================
@tree.command(name="myconfig", description="自分の全設定を表示します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_myconfig(interaction: discord.Interaction):
    c = cfg(interaction.user.id)
    await interaction.response.send_message(
        f"# ⚙️ 自分の設定\n"
        f"モード: **{'🧑 自動' if c['auto'] else '🛰 手動(seed)'}**\n"
        f"火力: **{c['power']}**\n"
        f"回数: **{c['count']}**\n"
        f"間隔: **{c['interval']}秒**\n"
        f"@everyone: **{c['mention']}**\n"
        f"🧑 ユーザートークン: **{len(c['usertokens'])}件**\n"
        f"🛰 サブBot: **{len(c['tokens'])}本**\n"
        f"🎯 ロール: **{len(c['roles'])}件**\n"
        f"🚫 除外: **{', '.join(c['ignore']) or 'なし'}**",
        ephemeral=True,
    )

# ==============================
# /resetconfig
# ==============================
@tree.command(name="resetconfig", description="自分の設定を初期値に戻します（トークンは残す）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def cmd_resetconfig(interaction: discord.Interaction):
    c = cfg(interaction.user.id)
    tokens = c["tokens"]
    usertokens = c["usertokens"]
    c.clear()
    c.update(_blank_cfg())
    c["tokens"] = tokens
    c["usertokens"] = usertokens
    save_cfg()
    await interaction.response.send_message("# ✅ **設定をリセットしました。**", ephemeral=True)

# ==============================
# /permcheck
# ==============================
@tree.command(name="permcheck", description="権限判定の生データを表示します（デバッグ用）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_permcheck(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("DMです（判定は常に「つける」）", ephemeral=True)
        return

    ch = await _resolve_mention_channel(interaction)
    user = await _resolve_member(guild, interaction.user)
    me = await get_bot_member(guild)

    def fmt(label, fn):
        try:
            return f"{label}: {fn()}"
        except Exception as e:
            return f"{label}: ERR {type(e).__name__}: {e}"

    c = cfg(interaction.user.id)
    lines = [
        f"channel: {ch} ({type(ch).__name__}) id={getattr(ch, 'id', None)}",
        f"user: {user} ({type(user).__name__})",
        f"bot: {me} ({type(me).__name__})",
        f"guild.me: {getattr(guild, 'me', None)}",
        f"モード: {'自動' if c['auto'] else '手動'} / ユーザートークン {len(c['usertokens'])}件",
        f"owner一致: {guild.owner_id == getattr(user, 'id', None)}",
        fmt("payload(user)", lambda: interaction.permissions.value),
        fmt("payload(app)", lambda: interaction.app_permissions.value),
        fmt("payload(user).mention_everyone", lambda: interaction.permissions.mention_everyone),
        fmt("user.guild_permissions", lambda: user.guild_permissions.value),
        fmt("ch.perms_for(user).mention_everyone", lambda: ch.permissions_for(user).mention_everyone),
        fmt("ch.perms_for(bot).mention_everyone", lambda: ch.permissions_for(me).mention_everyone),
        fmt("guild.perms_for(user).mention_everyone", lambda: guild.permissions_for(user).mention_everyone),
    ]
    sendable = await get_sendable_text_channels(guild, me, user, interaction.user.id)
    lines.append(f"送信可能ch数(参考): {len(sendable)}")
    lines.append(f"設置ch数: {len({s['channel_id'] for s in user_seeds(interaction.guild_id, interaction.user.id)})}")
    lines.append(f"総容量: {sum(seed_remaining(s) for s in user_seeds(interaction.guild_id, interaction.user.id))}")
    lines.append(f"実行中: {active_of(interaction.user.id)}")
    lines.append(f"webhook使用可否: {'可' if getattr(guild, 'me', None) else '不可（外部モード）'}")
    lines.append(f"http.send_files: {hasattr(bot.http, 'send_files')} / send_file: {hasattr(bot.http, 'send_file')}")
    lines.append(f"send_message(content kw): {_accepts(bot.http.send_message, 'content')}")
    user_ok = judge_mention_for(ch, user, getattr(interaction, "permissions", None))
    bot_ok = judge_mention_for(ch, me, getattr(interaction, "app_permissions", None))
    lines.append(f"最終: user_ok={user_ok} / bot_ok={bot_ok} / 判定={decide_mention(user_ok, bot_ok)}")
    await interaction.followup.send("```\n" + "\n".join(lines) + "\n```", ephemeral=True)

# ==============================
# 認証ロール自動付与
# ==============================
VERIFIED_KEYWORDS = ["認証済", "verified", "メンバー", "member", "認証", "verify", "✅", "承認"]

async def auto_grant_verified_role(guild):
    me = await get_bot_member(guild)
    if me is None:
        return []
    granted = []
    for role in guild.roles:
        if role.is_default():
            continue
        try:
            if any(kw in role.name.lower() for kw in VERIFIED_KEYWORDS):
                if role < me.top_role and role not in me.roles:
                    try:
                        await me.add_roles(role, reason="認証ロール自動付与")
                        granted.append(role.name)
                    except Exception as e:
                        print(f"[{guild.name}] ロール付与失敗 ({role.name}): {type(e).__name__}: {e}")
        except Exception:
            continue
    return granted

@bot.event
async def on_guild_join(guild):
    print(f"サーバー追加: {guild.name} (ID: {guild.id})")
    await asyncio.sleep(2)
    granted = await auto_grant_verified_role(guild)
    if granted:
        print(f"[{guild.name}] 認証ロール付与完了: {granted}")

@tree.command(name="getverified", description="認証ロールをBotに自動付与します")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def cmd_getverified(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    granted = await auto_grant_verified_role(interaction.guild)
    if granted:
        await interaction.followup.send(f"# ✅ **付与しました**\n## {', '.join(granted)}", ephemeral=True)
    else:
        await interaction.followup.send(
            "## ℹ️ **付与できる認証ロールが見つかりませんでした。**", ephemeral=True
        )

# ==============================
# 起動
# ==============================
@bot.event
async def on_ready():
    try:
        synced = await tree.sync()
        print(f"Bot起動: {bot.user}")
        print(f"同期完了: {[c.name for c in synced]}")
    except Exception as e:
        print(f"コマンド同期エラー: {type(e).__name__}: {e}")

    for uid_str, c in _CFG.items():
        load_userpool(int(uid_str))
        if c["usertokens"]:
            print(f"[user] {uid_str}: {len(c['usertokens'])}件復元")
        if not c["tokens"]:
            continue
        try:
            await rebuild_pool(int(uid_str))
            print(f"[pool] {uid_str}: {len(POOLS[int(uid_str)].senders)}レーン復元")
        except Exception as e:
            print(f"[pool] {uid_str} 復元失敗: {type(e).__name__}: {e}")

    for guild in bot.guilds:
        await auto_grant_verified_role(guild)

start_keep_alive()

while True:
    try:
        print("Bot起動中...")
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("Bot終了")
        break
    except Exception as e:
        print(f"Bot停止: {type(e).__name__}: {e}")
        print("5秒後に再接続します...")
        time.sleep(5)