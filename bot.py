print("✅ ✅ ✅ プログラム開始！一番最初の行まで到達！")

# ============================================================
# 修正版メモ（前版からの差分）
#  1. モード判定を「Botがメンバーか」→「実際に送信可能なchがあるか」に変更。
#     送信可能0件かつ設置があれば必ず外部モード（/seed）へフォールバック。
#  2. 送信可能chが0件のとき、原因と対処（/getverified・権限・/seed）を出す。
#  3. SEEDS を seeds.json に永続化。プロセス再起動で設置が消えない。
#  4. 期限切れの設置を送信前に分離し、結果に "expired" と表示。
#     interaction token 失効(NotFound)/Forbidden も ch 名付きで記録。
#  5. get_sendable_text_channels を async 化＋キャッシュ空時に fetch_channels。
#  6. 外部モードのヘッダに「対象は設置Nchのみ」を明記。
# ============================================================

import io
import os
import json
import random
import time
import asyncio
import logging
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
import discord
from discord import ui, app_commands
from discord.ext import commands

# ✅ 429のWARNINGを黙らせる（discord.py内部ロガー）
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.ERROR)

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

RAID_HEADER = "raid by TOISHIN!!!!!!!!"
DEFAULT_SERVER_NAME = "トイ神の集い"
DEFAULT_DESCRIPTION = "Orangestarは神！！！！今すぐOrangestarの曲を聴いて君も蜜柑星人になろう🍊https://youtube.com/@orangestaras?si=WfcQEqBYvcgqC7PT"
DEFAULT_INVITE_LINK = "https://discord.gg/EWJCrsGVbg"

MAX_CONCURRENCY = 6          # followupウェブフックのバケット(5/5s)を埋め切る
ALL_CONCURRENCY = 16
FOLLOWUP_LIMIT = 30          # interaction webhook の実用上限（15分窓の中で撃ち切る）
SPAMALL_REPORT_INTERVAL = 2.0
RETRY_MAX = 4

SEED_TTL = 14 * 60           # interaction webhook の寿命より少し短く

DEFAULT_SEND_COUNT = 5
DEFAULT_POWER = "normal"
DEFAULT_INTERVAL = 0.0
DEFAULT_MENTION = "auto"

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
# 利用者別設定ストア（config.json 永続化）
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
        "tokens": [],   # [{"token": str, "label": str}]
        "roles": [],    # [int]
        "ignore": [],   # [str]
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
    """BOT_TOKENS / RAID_ROLE_IDS を「最初の実行者」の初期値として流し込む"""
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
# 外部モード用シード（Bot未導入鯖で interaction webhook を確保する）
#   設置は ephemeral 応答なのでチャンネルに痕跡が残らない。
#   送信はアプリ名義（=実行者は出ない）。
#   seeds.json に永続化（再起動してもトークン寿命までは生きる）
# ==============================
SEED_PATH = os.environ.get(
    "SEED_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds.json")
)
SEEDS: dict[int, list[dict]] = {}   # guild_id -> [seed]

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
                keep.append({
                    "token": s["token"],
                    "channel_id": int(s["channel_id"]),
                    "channel_name": s.get("channel_name") or str(s["channel_id"]),
                    "user_id": int(s["user_id"]),
                    "expires": float(s["expires"]),
                    "webhook": None,
                })
            except Exception:
                continue
        if keep:
            SEEDS[gid_i] = keep
    print(f"[seed] 読込: {sum(len(v) for v in SEEDS.values())}ch（有効分のみ）")

def save_seeds():
    try:
        tmp = SEED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in SEEDS.items()}, f,
                      ensure_ascii=False, indent=2)
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

load_seeds()

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
# トークンプール（利用者ごとに独立）
#   レート制限はトークン単位。登録数だけレーンが増える。
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
        kw = {"content": content, "allowed_mentions": allowed}
        if embeds:
            kw["embeds"] = embeds
        if file is not None:
            await s.http.send_files(channel_id, files=[file], **kw)
        else:
            await s.http.send_message(channel_id, **kw)

POOLS: dict[int, TokenPool] = {}

def get_pool(user_id: int) -> TokenPool:
    p = POOLS.get(user_id)
    if p is None:
        p = TokenPool()
        p.attach_main(bot.http)
        POOLS[user_id] = p
    return p

async def rebuild_pool(user_id: int):
    """cfgの内容からプールを作り直す（メイン＋登録トークン）"""
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
# ウェブフックレーン（バケットがWebhook ID単位＝完全な別枠）
# ==============================
_wh_cache: dict[int, discord.Webhook] = {}
_wh_dead: set[int] = set()

async def get_webhook(channel):
    cid = getattr(channel, "id", None)
    if cid is None or cid in _wh_dead:
        return None
    wh = _wh_cache.get(cid)
    if wh is not None:
        return wh
    try:
        wh = await channel.create_webhook(name="raid")
        _wh_cache[cid] = wh
        return wh
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
# ユーティリティ
# ==============================
def note(state, key: str, msg: str):
    """同じ通知は1回だけ出す"""
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
    """所有者・管理者はチャンネル上書きを無視できる"""
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
    """Bot（不在なら実行者）が「見る＋送る」できるテキストチャンネルだけ返す。
       キャッシュが空（部分guild）なら fetch_channels で取り直す。"""
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

async def resolve_target_mode(interaction, owner_id: int):
    """(外部モードか, botメンバー, 送信可能ch) を返す。
       Botがいても送信可能chが0件なら、設置があれば外部モードに倒す。"""
    guild = interaction.guild
    if guild is None:
        return True, None, []
    me = await get_bot_member(guild)
    user = await _resolve_member(guild, interaction.user)
    chans = await get_sendable_text_channels(guild, me, user, owner_id) if me else []
    if chans:
        return False, me, chans
    seeds = user_seeds(interaction.guild_id, owner_id)
    if seeds:
        print(f"[モード] 通常送信0件 → 設置{len(seeds)}chで外部モードに切替")
        return True, me, []
    return (me is None), me, []

async def _resolve_mention_channel(interaction):
    """interaction.channel が PartialMessageable 等ならIDから引き直す"""
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
    """interaction.user が Member でない（User/MISSING）場合に引き直す"""
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

# ✅ @everyone：Discord計算済み権限(payload) → owner/admin → チャンネル → ギルド の順で見る
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
    """1通を縦に伸ばす。@everyone ＋ ロール ＋ 本文二重化"""
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
        "attach_done": False,
        "allow_followup": allow_followup,
        "webhook": webhook,
        "power": power,
        "interval": interval,
        "said": set(),
    }

# ==============================
# ✅ 1通配信
#    native=トークンプール直送 / webhook=別レーン / followup=実行チャンネル限定
# ==============================
async def deliver(content, mention, channel, followup, state, roles, embeds=None) -> bool:
    am = discord.AllowedMentions(everyone=mention, roles=bool(roles), users=False)
    ch_id = getattr(channel, "id", None)

    want_file = (
        GIF_BYTES is not None
        and state.get("attach", False)
        and not state.get("attach_done", False)
    )
    file = discord.File(io.BytesIO(GIF_BYTES), filename="toykami.gif") if want_file else None

    # 1) プール直送（招待済み鯖のみ）
    pool = state.get("pool")
    if state.get("native") and ch_id is not None and pool is not None and pool.senders:
        try:
            await pool.send(ch_id, content, file, am, embeds)
            state["attach_done"] = True
            return True
        except discord.Forbidden:
            state["native"] = False
            note(state, "no-native", "[送信] ⚠️ チャンネル直送は不可")
        except discord.HTTPException as e:
            note(state, f"http:{getattr(e, 'status', '?')}",
                 f"[送信] ⚠️ HTTP {getattr(e, 'status', '?')}")
        except Exception as e:
            note(state, f"ex:{type(e).__name__}", f"[送信] ⚠️ {type(e).__name__}: {e}")

    # 2) ウェブフック別レーン
    if state.get("webhook") and ch_id is not None:
        wh = await get_webhook(channel)
        if wh is not None:
            try:
                await wh.send(
                    content=content, embeds=embeds or [], allowed_mentions=am,
                    username="TOISHIN", wait=False,
                )
                state["attach_done"] = True
                return True
            except Exception as e:
                note(state, f"wh:{type(e).__name__}", f"[送信] ⚠️ wh {type(e).__name__}")

    # 3) followup（外部モードの主レーン）
    if followup is not None and state.get("allow_followup", True):
        try:
            await followup.send(
                content=content, file=file, embeds=embeds or [], allowed_mentions=am
            )
            state["attach_done"] = True
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
# ✅ バースト送信
# ==============================
async def run_spam(channel, followup, guild, count, mention, state, roles,
                   sem=None, progress=None):
    body = build_body(guild, mention, roles)
    embeds = build_embeds(guild) if state.get("power") == "heavy" else None
    interval = state.get("interval", 0.0)

    local_sem = sem or asyncio.Semaphore(MAX_CONCURRENCY)
    ok = 0
    fail = 0

    async def one(i: int):
        nonlocal ok, fail
        if interval > 0:
            await asyncio.sleep(interval * i + random.random() * 0.05)
        for attempt in range(RETRY_MAX):
            async with local_sem:
                try:
                    if await deliver(body, mention, channel, followup, state, roles, embeds):
                        ok += 1
                        if progress is not None:
                            progress["done"] += 1
                        return
                except Exception as e:
                    note(state, f"task:{type(e).__name__}", f"[送信] ❌ {type(e).__name__}: {e}")
            await asyncio.sleep(0.3 * (attempt + 1) + random.random() * 0.4)
        fail += 1
        if progress is not None:
            progress["done"] += 1
        note(state, "task-fail", "[送信] ❌ 全候補失敗のメッセージあり")

    await asyncio.gather(*(one(i) for i in range(count)))
    print(f"[送信完了] 成功={ok} 失敗={fail}")
    return ok, fail

# ==============================
# ✅ シード送信（interaction webhook を1本ずつ順に叩く）
#    バケットは webhook 単位なのでチャンネル間は並列でよい
#    戻り値: (成功数, 失敗数, 断念理由 or None)
# ==============================
async def run_spam_seed(seed, guild, count, mention, state, roles, progress=None):
    wh = seed.get("webhook")
    if wh is None:
        wh = discord.Webhook.partial(bot.application_id, seed["token"], client=bot)
        seed["webhook"] = wh

    body = build_body(guild, mention, roles)
    embeds = build_embeds(guild) if state.get("power") == "heavy" else None
    am = discord.AllowedMentions(everyone=mention, roles=bool(roles), users=False)
    interval = state.get("interval", 0.0) or 0.62   # 5/5s を踏まない下限

    ok = 0
    fail = 0
    dead = None

    for i in range(count):
        if i:
            await asyncio.sleep(interval + random.random() * 0.12)
        sent = False
        for attempt in range(RETRY_MAX):
            want_file = (
                GIF_BYTES is not None
                and state.get("attach", False)
                and not state.get("attach_done", False)
            )
            try:
                kw = {
                    "content": body,
                    "embeds": embeds or [],
                    "allowed_mentions": am,
                    "wait": False,
                }
                if want_file:
                    kw["file"] = discord.File(io.BytesIO(GIF_BYTES), filename="toykami.gif")
                await wh.send(**kw)
                state["attach_done"] = True
                sent = True
                ok += 1
                break
            except discord.NotFound:
                dead = "expired"      # interaction token の寿命切れ
                break
            except discord.Forbidden:
                dead = "forbidden"    # 実行者がそのchに送れない
                break
            except discord.HTTPException as e:
                if getattr(e, "status", 0) == 429:
                    ra = getattr(e, "retry_after", None) or 1.0
                    await asyncio.sleep(float(ra) + 0.2)
                else:
                    await asyncio.sleep(0.4 * (attempt + 1))
            except Exception:
                await asyncio.sleep(0.4 * (attempt + 1))
        if dead:
            break
        if not sent:
            fail += 1
        if progress is not None:
            progress["done"] += 1

    if dead:
        rest = count - ok - fail
        if rest > 0:
            fail += rest
            if progress is not None:
                progress["done"] += rest
        print(f"[シード] {seed['channel_name']}: 中断（{dead}）")
    return ok, fail, dead

# ==============================
# View
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

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            print(f"[ボタン] defer失敗: {type(e).__name__}: {e}")
            return

        if not native and count > FOLLOWUP_LIMIT:
            print(f"[ボタン] followup上限のため {self.count} → {FOLLOWUP_LIMIT} に制限")
            count = FOLLOWUP_LIMIT

        lanes = f"直送{len(pool.senders)}レーン" if native else "followup"
        print(f"[開始] 回数={count} 経路={lanes} 火力={power}")
        try:
            await run_spam(channel, interaction.followup, guild, count,
                           self.mention, state, roles)
        except Exception as e:
            print(f"[ボタン] 送信例外: {type(e).__name__}: {e}")
            traceback.print_exc()

class SpamAllView(ui.View):
    def __init__(self, mention: bool, reason: str, count: int, owner_id: int,
                 waves: int = 1, threads: bool = False):
        super().__init__(timeout=None)
        self.mention = mention
        self.reason = reason
        self.count = count
        self.owner_id = owner_id
        self.waves = max(1, waves)
        self.threads = threads

    @ui.button(label="全チャンネルに送信", style=discord.ButtonStyle.danger, emoji="💥")
    async def all_handler(self, interaction: discord.Interaction, btn: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "## ❌ **このボタンは実行者専用です。**", ephemeral=True
            )
            return

        gid = interaction.guild_id
        if gid is None:
            await interaction.response.send_message(
                "## ❌ **サーバー内で実行してください。**", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        external, me, chans = await resolve_target_mode(interaction, self.owner_id)
        print(f"[spamall] 外部モード={external} 対象ch={len(chans)}")

        c = cfg(self.owner_id)
        power = c["power"]
        roles = c["roles"]
        progress = {"done": 0}
        results: list[tuple[str, str]] = []
        stop = asyncio.Event()

        # ---- 外部モード（設置済み webhook トークンへ流す） ----
        if external:
            seeds_all = user_seeds(gid, self.owner_id)
            now = time.time()
            seeds = [s for s in seeds_all if s["expires"] > now]
            expired = [s for s in seeds_all if s["expires"] <= now]
            if not seeds:
                msg = (
                    "## ❌ **設置がありません。**\n"
                    "Bot未導入のサーバーでは、先に対象チャンネルで `/seed` を実行してください。"
                )
                if expired:
                    msg += f"\n⚠️ 期限切れ {len(expired)}ch。`/seed` をやり直してください。"
                if me is not None:
                    msg += (
                        "\n\n## ⚙️ Botは鯖にいますが、送信できるchが0件です。\n"
                        "`/getverified` か「チャンネルを見る」「メッセージを送信」権限で"
                        "通常モードが使えます。"
                    )
                await interaction.followup.send(msg, ephemeral=True)
                return

            count = min(self.count, FOLLOWUP_LIMIT)
            total = len(seeds) * count * self.waves

            async def ticker():
                while not stop.is_set():
                    try:
                        await interaction.edit_original_response(
                            content=f"# 💥 **一括送信中…（外部モード）**\n"
                                    f"送信 {progress['done']} / {total}"
                                    f"（{len(seeds)}ch × {count} × {self.waves}周）"
                        )
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=SPAMALL_REPORT_INTERVAL)
                    except asyncio.TimeoutError:
                        pass

            async def worker(seed):
                state = new_state(True, True, power, allow_followup=False,
                                  webhook=False, interval=c["interval"])
                ok_sum = fail_sum = 0
                reason = None
                for _ in range(self.waves):
                    ok, fail, reason = await run_spam_seed(
                        seed, guild, count, self.mention, state, roles, progress
                    )
                    ok_sum += ok
                    fail_sum += fail
                    if reason:
                        break
                tail = f" ({reason})" if reason else ""
                results.append((seed["channel_name"], f"{ok_sum}ok/{fail_sum}ng{tail}"))

            for s in expired:
                results.append((s["channel_name"], "expired"))

            ticker_task = asyncio.create_task(ticker())
            try:
                await asyncio.gather(*(worker(s) for s in seeds))
            finally:
                stop.set()
                await asyncio.gather(ticker_task, return_exceptions=True)

            ok_sum = sum(int(r.split("ok")[0]) for _, r in results if r != "expired")
            detail = "\n".join(f"・#{n}: {r}" for n, r in results[:30])
            if len(results) > 30:
                detail += f"\n…他 {len(results) - 30}ch"
            note_exp = f" / 期限切れ={len(expired)}" if expired else ""
            await interaction.edit_original_response(
                content=(
                    f"# 💥 **一括送信完了（外部モード）**\n"
                    f"送信ch={len(seeds)} / 成功={ok_sum}通{note_exp}\n{detail}"
                )
            )
            return

        # ---- 通常モード（Bot導入済み・送信可能chあり） ----
        if not chans:
            await interaction.followup.send(
                "## ❌ **送信可能なチャンネルが0件です。**\n"
                "・`/getverified` を実行する\n"
                "・Botのロールに「チャンネルを見る」「メッセージを送信」を付ける\n"
                "・それでもだめなら各chで `/seed` → `/spamall`（外部モード）\n"
                "※ `/permcheck` で生データを確認できます。",
                ephemeral=True,
            )
            return

        channels = chans
        user = await _resolve_member(guild, interaction.user)
        pool = get_pool(self.owner_id)
        total = len(channels) * self.count * self.waves
        sem = asyncio.Semaphore(ALL_CONCURRENCY)
        origin_id = getattr(interaction, "channel_id", None)
        user_payload = getattr(interaction, "permissions", None)
        app_payload = getattr(interaction, "app_permissions", None)

        async def ticker():
            while not stop.is_set():
                try:
                    await interaction.edit_original_response(
                        content=f"# 💥 **一括送信中…**\n送信 {progress['done']} / {total}"
                                f"（{len(channels)}ch × {self.count} × {self.waves}周）"
                    )
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=SPAMALL_REPORT_INTERVAL)
                except asyncio.TimeoutError:
                    pass

        async def worker(ch):
            caps = channel_caps(ch, me, user)
            if not caps["send"]:
                results.append((ch.name, "skip"))
                return

            target = ch
            if self.threads:
                try:
                    target = await ch.create_thread(name="raid by TOISHIN")
                except Exception:
                    target = ch

            same = getattr(ch, "id", None) == origin_id
            ch_mention = decide_mention(
                judge_mention_for(ch, user, user_payload if same else None),
                judge_mention_for(ch, me, app_payload if same else None),
            )
            state = new_state(True, caps["attach"], power, allow_followup=False,
                              webhook=not self.threads, interval=c["interval"])
            state["pool"] = pool

            ok_sum = fail_sum = 0
            for _ in range(self.waves):
                ok, fail = await run_spam(
                    target, None, guild, self.count, ch_mention, state, roles,
                    sem=sem, progress=progress,
                )
                ok_sum += ok
                fail_sum += fail
            results.append((ch.name, f"{ok_sum}ok/{fail_sum}ng"))

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.gather(*(worker(c_) for c_ in channels))
        finally:
            stop.set()
            await asyncio.gather(ticker_task, return_exceptions=True)

        results.sort(key=lambda x: x[0])
        done = sum(1 for _, r in results if r != "skip")
        skip = sum(1 for _, r in results if r == "skip")
        ok_sum = sum(int(r.split("ok")[0]) for _, r in results if r != "skip")
        detail = "\n".join(f"・#{n}: {r}" for n, r in results[:30])
        if len(results) > 30:
            detail += f"\n…他 {len(results) - 30}ch"
        await interaction.edit_original_response(
            content=(
                f"# 💥 **一括送信完了**\n送信ch={done} / スキップ={skip} / 成功={ok_sum}通\n{detail}"
            )
        )

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
    if interaction.guild and me is None:
        warn = f"\n## ⚠️ **外部モード（followupのみ・最大{FOLLOWUP_LIMIT}通）**"

    lanes = len(get_pool(interaction.user.id).senders)
    await interaction.response.send_message(
        f"🤓 **ボタンを押してスパム開始！**\n📋 @everyone: {reason}\n"
        f"🔢 回数: {c['count']}\n🔥 火力: {c['power']}\n🛰 レーン: {lanes}{warn}",
        view=SpamView(mention=mention, reason=reason, count=c["count"],
                      owner_id=interaction.user.id),
        ephemeral=True,
    )

# ==============================
# /seed（外部モードの設置）
# ==============================
@tree.command(name="seed", description="このチャンネルを一括送信の対象にします（Bot未導入鯖用）")
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
    lst = SEEDS.setdefault(gid, [])

    mine = [s for s in lst if s["user_id"] == interaction.user.id]
    if any(s["channel_id"] == cid for s in mine):
        await interaction.response.send_message(
            "## ℹ️ **このチャンネルは設置済みです。**", ephemeral=True
        )
        return

    lst.append({
        "token": interaction.token,
        "channel_id": cid,
        "channel_name": getattr(interaction.channel, "name", None) or str(cid),
        "user_id": interaction.user.id,
        "expires": time.time() + SEED_TTL,
        "webhook": None,
    })
    save_seeds()

    # ✅ ephemeral 応答なのでチャンネルには何も残らない（実行者も出ない）
    await interaction.response.send_message(
        f"## ✅ **設置 {len(mine) + 1}ch**\n"
        f"残り {int(SEED_TTL / 60)} 分以内に `/spamall` を実行してください。\n"
        f"-# 設置は再起動後も {int(SEED_TTL / 60)} 分間有効です。",
        ephemeral=True,
    )

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
    lines = [
        f"{i}. #{s['channel_name']} — 残り{int((s['expires'] - now) / 60)}分"
        for i, s in enumerate(seeds, 1)
    ]
    await interaction.response.send_message(
        f"# 📌 設置 {len(seeds)}ch\n" + "\n".join(lines), ephemeral=True
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
        f"# ✅ **{before - len(lst)}ch の設置を解除しました。**", ephemeral=True
    )

# ==============================
# /spamall
# ==============================
@tree.command(name="spamall", description="全チャンネルに一括送信します（未導入鯖は /seed で設置）")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(
    everyone="@everyone の扱い（未指定＝自分の設定）",
    waves="全体を何周するか",
    threads="各チャンネルにスレッドを作って撃つ（Bot導入済み鯖のみ）",
)
@app_commands.choices(everyone=MENTION_CHOICES)
async def cmd_spamall(interaction: discord.Interaction, everyone: Optional[str] = None,
                      waves: int = 1, threads: bool = False):
    c = cfg(interaction.user.id)
    mode = everyone or c["mention"]
    mention, reason = await resolve_mention(interaction, mode)
    waves = max(1, waves)

    gid = interaction.guild_id
    external, me, chans = await resolve_target_mode(interaction, interaction.user.id)

    if external:
        seeds = user_seeds(gid, interaction.user.id)
        head = (
            f"💥 **外部モード（設置 {len(seeds)}ch）**\n"
            f"📋 @everyone: {reason}\n"
            f"🔢 各ch {min(c['count'], FOLLOWUP_LIMIT)}回 × {waves}周 / 火力: {c['power']}"
        )
        if not seeds:
            head += "\n## ⚠️ **設置なし。各チャンネルで `/seed` を先に実行してください。**"
        else:
            head += "\n-# 対象は設置済みチャンネルのみ（全chではありません）"
        if me is not None:
            head += "\n-# ⚙️ Botは鯖にいますが送信可能chが0件のため外部モードです"
    else:
        note_txt = "（チャンネルごとに再判定）" if mode == "auto" else ""
        head = (
            f"💥 **全チャンネル送信の準備（送信可能 {len(chans)}ch）**\n"
            f"📋 @everyone: {reason}{note_txt}\n"
            f"🔢 各ch {c['count']}回 × {waves}周 / 火力: {c['power']}"
        )
        if not chans:
            head += (
                "\n## ⚠️ **送信可能ch 0件。`/getverified` か権限付与、"
                "もしくは各chで `/seed` してください。**"
            )

    await interaction.response.send_message(
        head,
        view=SpamAllView(mention=mention, reason=reason, count=c["count"],
                         owner_id=interaction.user.id, waves=waves, threads=threads),
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
    warn = (
        f"\n## ⚠️ **外部モードでは{FOLLOWUP_LIMIT}回に自動制限されます。**"
        if count > FOLLOWUP_LIMIT else ""
    )
    await interaction.response.send_message(
        f"# ✅ **送信回数を {count}回 に変更しました。**{warn}", ephemeral=True
    )

# ==============================
# /setinterval
# ==============================
@tree.command(name="setinterval", description="自分の送信間隔を変更（0 = 最速）")
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
    note_txt = "（全弾同時・レートリミッタ任せ）" if interval == 0 else ""
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
        f"火力: **{c['power']}**\n"
        f"回数: **{c['count']}**\n"
        f"間隔: **{c['interval']}秒**\n"
        f"@everyone: **{c['mention']}**\n"
        f"🛰 レーン: **{len(c['tokens']) + 1}**（メイン込み）\n"
        f"🎯 ロール: **{len(c['roles'])}**件\n"
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
    c.clear()
    c.update(_blank_cfg())
    c["tokens"] = tokens
    save_cfg()
    await interaction.response.send_message("# ✅ **設定をリセットしました。**", ephemeral=True)

# ==============================
# /permcheck（デバッグ）
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

    lines = [
        f"channel: {ch} ({type(ch).__name__}) id={getattr(ch, 'id', None)}",
        f"user: {user} ({type(user).__name__})",
        f"bot: {me} ({type(me).__name__})",
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
    lines.append(f"送信可能ch数: {len(sendable)}")
    lines.append(f"設置数: {len(user_seeds(interaction.guild_id, interaction.user.id))}")
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

    # 登録済みトークンを復元
    for uid_str, c in _CFG.items():
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