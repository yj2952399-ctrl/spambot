print("✅ ✅ ✅ プログラム開始！一番最初の行まで到達！")

import io
import os
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
# 設定
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN 未設定")

RAID_HEADER = "# raid by TOISHIN!!!!!!!!"
DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww陰キャぼっち生活楽しんでる？😂友達できるといいねwwあ、自hey!showだから無理かww😅"
DEFAULT_INVITE_LINK = "https://discord.gg/yMjkgmg7tS"

SEND_INTERVAL = 0.0
MAX_CONCURRENCY = 3        # 1チャンネルあたりの同時送信数
ALL_CONCURRENCY = 5        # /spamall 全体の同時送信数
FOLLOWUP_LIMIT = 5
SPAMALL_REPORT_INTERVAL = 2.5

DEFAULT_SEND_COUNT = 5
_send_count: dict[int, int] = {}
_mention_mode: dict[int, str] = {}

def get_send_count(guild) -> int:
    return _send_count.get(getattr(guild, "id", 0), DEFAULT_SEND_COUNT)

def get_mention_mode(guild) -> str:
    return _mention_mode.get(getattr(guild, "id", 0), "auto")

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

async def get_sendable_text_channels(guild, me, user=None):
    """Botが実際に「見る＋送る」できるテキストチャンネルだけ返す"""
    if guild is None:
        return []
    subject = me
    if subject is None and isinstance(user, discord.Member):
        subject = user
    if subject is None:
        return []
    out = []
    for ch in guild.text_channels:
        try:
            p = ch.permissions_for(subject)
        except Exception:
            continue
        if _is_adminish(guild, subject) or (p.view_channel and p.send_messages):
            out.append(ch)
    return out

async def _resolve_mention_channel(interaction):
    """interaction.channel が PartialMessageable 等ならIDから引き直す"""
    ch = interaction.channel
    if ch is not None and hasattr(ch, "permissions_for"):
        return ch
    guild = interaction.guild
    cid = getattr(interaction, "channel_id", None)
    if guild is None or cid is None:
        return None
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

# ✅ @everyone：True=可 / False=不可 / None=判定不能
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

def judge_mention_for(channel, subject) -> Optional[bool]:
    """スレッドは親チャンネルも見る"""
    if channel is None or subject is None:
        return None
    targets = [channel]
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if parent is not None:
            targets.append(parent)
    results = [_mention_verdict(t, subject) for t in targets]
    if any(r is True for r in results):
        return True
    if all(r is False for r in results):
        return False
    return None

def decide_mention(user_ok: Optional[bool], bot_ok: Optional[bool]) -> bool:
    """実行者かBotのどちらかが撃てればOK。不明は「つける」に倒す"""
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
        return True, "✅ DM判定: つける"

    channel = await _resolve_mention_channel(interaction)
    if channel is None:
        return True, "✅ チャンネル不明→つける"

    user = await _resolve_member(guild, interaction.user)
    user_ok = judge_mention_for(channel, user) if user is not None else None

    me = await get_bot_member(guild)
    bot_ok = judge_mention_for(channel, me) if me is not None else None

    ok = decide_mention(user_ok, bot_ok)
    if not ok:
        if user_ok is False:
            return False, "❌ 自動判定: 実行者・Botとも権限なし"
        return False, "❌ 自動判定: Botに権限なし"
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

# ==============================
# ✅ 1通配信（通知は1回だけ）
#    native=Botトークン直送 / followup=実行チャンネル限定
# ==============================
async def deliver(content, mention, channel, followup, state) -> bool:
    am = discord.AllowedMentions(everyone=mention, roles=False, users=False)
    has_file = GIF_BYTES is not None and state.get("attach", False)

    targets: list[tuple[str, object]] = []
    if state.get("native") and channel is not None:
        targets.append(("native", channel))
    if followup is not None and state.get("allow_followup", True):
        targets.append(("followup", followup))

    for kind, target in targets:
        if has_file:
            f = make_gif_file()
            if f is not None:
                try:
                    await target.send(content=content, file=f, allowed_mentions=am)
                    return True
                except discord.Forbidden:
                    note(state, f"file403:{kind}", "[送信] ⚠️ 添付が拒否された → テキストで再試行")
                except discord.HTTPException as e:
                    note(state, f"filehttp:{kind}", f"[送信] ⚠️ 添付HTTP {getattr(e, 'status', '?')}")
                    if getattr(e, "status", None) == 429:
                        await asyncio.sleep(min(getattr(e, "retry_after", 1.0) or 1.0, 5.0))
                except Exception as e:
                    note(state, f"fileex:{kind}", f"[送信] ⚠️ 添付例外 {type(e).__name__}: {e}")

        try:
            await target.send(content=content, allowed_mentions=am)
            return True
        except discord.Forbidden:
            if kind == "native":
                state["native"] = False
                note(state, "no-native", "[送信] ⚠️ チャンネル直送は不可")
            else:
                note(state, "no-followup", "[送信] ❌ followupも拒否")
        except discord.HTTPException as e:
            note(state, f"http:{kind}", f"[送信] ⚠️ HTTP {getattr(e, 'status', '?')}: {e}")
            if getattr(e, "status", None) == 429:
                await asyncio.sleep(min(getattr(e, "retry_after", 1.0) or 1.0, 5.0))
        except Exception as e:
            note(state, f"ex:{kind}", f"[送信] ⚠️ {type(e).__name__}: {e}")

    return False

# ==============================
# ✅ バースト送信
# ==============================
async def run_spam(channel, followup, guild, count, mention, state, sem=None, progress=None):
    body = build_advertisement_text(guild)
    if mention:
        body = f"@everyone\n{body}"

    local_sem = sem or asyncio.Semaphore(MAX_CONCURRENCY)
    ok = 0
    fail = 0

    async def one(i: int):
        nonlocal ok, fail
        if SEND_INTERVAL > 0:
            await asyncio.sleep(SEND_INTERVAL * i)
        async with local_sem:
            for attempt in range(3):
                try:
                    if await deliver(body, mention, channel, followup, state):
                        ok += 1
                        if progress is not None:
                            progress["done"] += 1
                        return
                except Exception as e:
                    note(state, f"task:{type(e).__name__}", f"[送信] ❌ {type(e).__name__}: {e}")
                await asyncio.sleep(0.8 * (attempt + 1))
            fail += 1
            if progress is not None:
                progress["done"] += 1
            note(state, "task-fail", "[送信] ❌ 全候補失敗のメッセージあり")

    await asyncio.gather(*(one(i) for i in range(count)))
    print(f"[送信完了] 成功={ok} 失敗={fail}")
    return ok, fail

# ==============================
# View
# ==============================
class SpamView(ui.View):
    def __init__(self, mention: bool, reason: str, count: int):
        super().__init__(timeout=None)
        self.mention = mention
        self.reason = reason
        self.count = count

    @ui.button(label="開始", style=discord.ButtonStyle.danger, emoji="📢")
    async def start_handler(self, interaction: discord.Interaction, btn: ui.Button):
        guild = interaction.guild
        channel = await _resolve_mention_channel(interaction) if guild else interaction.channel
        count = self.count

        # ✅ 5通以下は followup 固定（403ログを出さない）。6通以上のみ直送を試す
        me = await get_bot_member(guild) if guild else None
        caps = channel_caps(channel, me, interaction.user) if guild else {"send": True, "attach": True}
        native = bool(me) and channel is not None and caps["send"] and count > FOLLOWUP_LIMIT
        state = {"native": native, "attach": caps["attach"], "said": set(), "allow_followup": True}

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            print(f"[ボタン] defer失敗: {type(e).__name__}: {e}")
            return

        if not native and count > FOLLOWUP_LIMIT:
            count = FOLLOWUP_LIMIT
            print(f"[ボタン] followup上限のため {self.count} → {count} に制限")

        print(f"[開始] 回数={count} 経路={'直送' if native else 'followup'} 添付={state['attach']}")
        try:
            await run_spam(channel, interaction.followup, guild, count, self.mention, state)
        except Exception as e:
            print(f"[ボタン] 送信例外: {type(e).__name__}: {e}")
            traceback.print_exc()

class SpamAllView(ui.View):
    def __init__(self, mention: bool, reason: str, count: int):
        super().__init__(timeout=None)
        self.mention = mention
        self.reason = reason
        self.count = count

    @ui.button(label="全チャンネルに送信", style=discord.ButtonStyle.danger, emoji="💥")
    async def all_handler(self, interaction: discord.Interaction, btn: ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("## ❌ **サーバー内で実行してください。**", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        me = await get_bot_member(guild)
        if me is None:
            await interaction.followup.send(
                "## ❌ **Botが未導入のため全チャンネル送信は不可です。**\n/spam を使ってください。",
                ephemeral=True,
            )
            return

        # ✅ 他のチャンネルは followup が使えないので、必ずBotトークンで直送する
        user = await _resolve_member(guild, interaction.user)
        channels = await get_sendable_text_channels(guild, me, user)
        if not channels:
            await interaction.followup.send(
                "## ❌ **送信可能なチャンネルがありません。**\n（Botに「メッセージ送信」権限が必要です）",
                ephemeral=True,
            )
            return

        total = len(channels) * self.count
        progress = {"done": 0}
        results: list[tuple[str, str]] = []
        sem = asyncio.Semaphore(ALL_CONCURRENCY)
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                try:
                    await interaction.edit_original_response(
                        content=f"# 💥 **一括送信中…**\n送信 {progress['done']} / {total}（{len(channels)}ch）"
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
            ch_mention = decide_mention(
                judge_mention_for(ch, user), judge_mention_for(ch, me)
            )
            state = {"native": True, "attach": caps["attach"], "said": set(), "allow_followup": False}
            ok, fail = await run_spam(
                ch, None, guild, self.count, ch_mention, state, sem=sem, progress=progress
            )
            results.append((ch.name, f"{ok}ok/{fail}ng"))

        ticker_task = asyncio.create_task(ticker())
        try:
            await asyncio.gather(*(worker(c) for c in channels))
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
@app_commands.describe(everyone="@everyone の扱い（未指定＝サーバー設定に従う）")
@app_commands.choices(everyone=[
    app_commands.Choice(name="自動判定", value="auto"),
    app_commands.Choice(name="つける", value="on"),
    app_commands.Choice(name="つけない", value="off"),
])
async def cmd_spam(interaction: discord.Interaction, everyone: Optional[str] = None):
    mode = everyone or get_mention_mode(interaction.guild)
    mention, reason = await resolve_mention(interaction, mode)
    count = get_send_count(interaction.guild)

    warn = ""
    me = await get_bot_member(interaction.guild) if interaction.guild else None
    if interaction.guild and me is None:
        warn = "\n## ⚠️ **Bot未導入のため最大5通に制限されます（GIFは送れます）。**"

    await interaction.response.send_message(
        f"🤓 **ボタンを押してスパム開始！**\n📋 @everyone: {reason}\n🔢 回数: {count}{warn}",
        view=SpamView(mention=mention, reason=reason, count=count),
        ephemeral=True,
    )

# ==============================
# /spamall
# ==============================
@tree.command(name="spamall", description="全チャンネルに一括送信します")
@app_commands.describe(everyone="@everyone の扱い（未指定＝サーバー設定に従う）")
@app_commands.choices(everyone=[
    app_commands.Choice(name="自動判定", value="auto"),
    app_commands.Choice(name="つける", value="on"),
    app_commands.Choice(name="つけない", value="off"),
])
async def cmd_spamall(interaction: discord.Interaction, everyone: Optional[str] = None):
    mode = everyone or get_mention_mode(interaction.guild)
    mention, reason = await resolve_mention(interaction, mode)
    count = get_send_count(interaction.guild)
    note_txt = "（チャンネルごとに再判定）" if mode == "auto" else ""
    await interaction.response.send_message(
        f"💥 **全チャンネル送信の準備**\n📋 @everyone: {reason}{note_txt}\n🔢 各ch {count}回",
        view=SpamAllView(mention=mention, reason=reason, count=count),
        ephemeral=True,
    )

# ==============================
# /setcount
# ==============================
@tree.command(name="setcount", description="送信回数を変更します（デフォルト: 5）")
@app_commands.describe(count="送信回数")
async def cmd_setcount(interaction: discord.Interaction, count: int):
    if count < 1:
        await interaction.response.send_message("# ❌ **1以上で指定してください。**", ephemeral=True)
        return
    _send_count[getattr(interaction.guild, "id", 0)] = count
    warn = "\n## ⚠️ **Bot未導入時は5回に自動制限されます。**" if count > FOLLOWUP_LIMIT else ""
    await interaction.response.send_message(
        f"# ✅ **送信回数を {count}回 に変更しました。**{warn}", ephemeral=True
    )

# ==============================
# /setinterval
# ==============================
@tree.command(name="setinterval", description="送信間隔を変更（0 = 最速の同時送信）")
@app_commands.describe(interval="送信間隔（秒）。0で最速")
async def cmd_setinterval(interaction: discord.Interaction, interval: float):
    global SEND_INTERVAL
    if interval < 0:
        await interaction.response.send_message("# ❌ **0以上で指定してください。**", ephemeral=True)
        return
    SEND_INTERVAL = interval
    note_txt = "（全弾同時・レートリミッタ任せ）" if interval == 0 else ""
    await interaction.response.send_message(
        f"# ✅ **送信間隔を {interval}秒 に変更しました。**{note_txt}", ephemeral=True
    )

@bot.command(name="setinterval")
async def prefix_setinterval(ctx, interval: float = 0.0):
    global SEND_INTERVAL
    if interval < 0:
        await ctx.send("# ❌ **0以上で指定してください。**")
        return
    SEND_INTERVAL = interval
    await ctx.send(f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**")

# ==============================
# /seteveryone
# ==============================
@tree.command(name="seteveryone", description="@everyone の自動判定を設定します")
@app_commands.choices(mode=[
    app_commands.Choice(name="自動判定", value="auto"),
    app_commands.Choice(name="常につける", value="on"),
    app_commands.Choice(name="常につけない", value="off"),
])
async def cmd_seteveryone(interaction: discord.Interaction, mode: str):
    _mention_mode[getattr(interaction.guild, "id", 0)] = mode
    label = {"auto": "自動判定", "on": "常につける", "off": "常につけない"}[mode]
    await interaction.response.send_message(
        f"# ✅ **@everyone の設定を「{label}」に変更しました。**", ephemeral=True
    )

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