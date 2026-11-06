print("✅ ✅ ✅ プログラム開始！一番最初の行まで到達！")

import io
import os
import time
import asyncio
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
import discord
from discord import ui, app_commands
from discord.ext import commands

# ==============================================
# ✅ GIF生成（ディスクではなくメモリに bytes で保持）
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

# ✅ 起動時に一度だけ生成。以降はディスクI/Oゼロ
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

DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "#raid by TOISHIN!!!!"
                      "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww陰キャぼっち生活楽しんでる？😂友達できるといいねwwあ、自hey!showだから無理かww😅"
DEFAULT_INVITE_LINK = "https://discord.gg/yMjkgmg7tS"

# ✅ 0.0 = 全弾同時発射（discord.py のレートリミッタに速度を任せる＝最速）
SEND_INTERVAL = 0.0
MAX_CONCURRENCY = 5
FOLLOWUP_LIMIT = 5

DEFAULT_SEND_COUNT = 5
_send_count: dict[int, int] = {}      # guild_id -> 回数
_mention_mode: dict[int, str] = {}    # guild_id -> "auto" / "on" / "off"

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

def channel_caps(channel, me) -> dict:
    """投稿可否・添付可否を事前に確定。me が None なら楽観的に許可。"""
    caps = {"send": True, "attach": True}
    if me is None:
        return caps
    try:
        p = channel.permissions_for(me)
        caps["send"] = p.send_messages
        caps["attach"] = p.attach_files
    except Exception:
        pass
    return caps

async def get_sendable_text_channels(guild, me):
    if guild is None:
        return []
    out = []
    for ch in guild.text_channels:
        try:
            if me and ch.permissions_for(me).view_channel:
                out.append(ch)
        except Exception:
            continue
    return out

# ==============================
# ✅ @everyone 判定（判定対象を「投稿主体」に修正）
# ==============================
async def resolve_mention(interaction, mode: str) -> tuple[bool, str]:
    if mode == "on":
        return True, "✅ 手動指定: つける"
    if mode == "off":
        return False, "❌ 手動指定: つけない"

    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        return True, "✅ DM判定: つける"

    # ① Botがサーバーに居る → 実際に投稿するBotの権限で判定（これが正解）
    me = await get_bot_member(guild)
    if me is not None:
        try:
            ok = channel.permissions_for(me).mention_everyone
            print(f"[権限判定] Bot自身の mention_everyone={ok}")
            return (ok, "✅ 自動判定: Botに権限あり" if ok else "❌ 自動判定: Botに権限なし")
        except Exception as e:
            print(f"[権限判定] Bot権限取得失敗: {type(e).__name__}: {e}")

    # ② 未導入（個人インストール）→ 操作ユーザーの権限で代替判定
    user = interaction.user
    if isinstance(user, discord.Member):
        try:
            ok = channel.permissions_for(user).mention_everyone
            print(f"[権限判定] ユーザー権限で代替判定: mention_everyone={ok}")
            return ok, ("✅ 自動判定: 権限あり" if ok else "❌ 自動判定: 権限なし")
        except Exception:
            pass

    return True, "✅ 自動判定: 不明→つける"

def build_advertisement_text(guild) -> str:
    lines = [
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
        f"# **{DEFAULT_DESCRIPTION}**",
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
    ]
    if guild and guild.member_count:
        lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")
    return "\n".join(lines)

# ==============================
# ✅ 1通送信（403で自動ダウングレード）
# ==============================
async def _post_once(target, content, mention, state):
    am = discord.AllowedMentions(everyone=mention, roles=False, users=False)

    if state["attach"]:
        f = make_gif_file()
        if f:
            try:
                await target.send(content=content, file=f, allowed_mentions=am)
                return
            except discord.Forbidden:
                state["attach"] = False
                print("[送信] ⚠️ ファイル添付権限なし → 以降テキストのみ")
            except discord.HTTPException as e:
                if getattr(e, "status", None) == 403:
                    state["attach"] = False
                    print("[送信] ⚠️ 添付拒否(403) → 以降テキストのみ")
                else:
                    raise

    await target.send(content=content, allowed_mentions=am)

# ==============================
# ✅ バースト送信本体
# ==============================
async def run_spam(channel, followup, guild, count, mention, state):
    content = build_advertisement_text(guild)
    prefix = "@everyone " if mention else ""
    body = f"{prefix}{content}"

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    ok = fail = 0

    async def one(i: int):
        nonlocal ok, fail
        if SEND_INTERVAL > 0:
            await asyncio.sleep(SEND_INTERVAL * i)
        async with sem:
            for attempt in range(3):
                target = channel if state["native"] else followup
                try:
                    await _post_once(target, body, mention, state)
                    ok += 1
                    return
                except discord.Forbidden:
                    if state["native"]:
                        state["native"] = False
                        print("[送信] ⚠️ チャンネル送信不可 → followupに切替")
                        continue
                    fail += 1
                    return
                except discord.HTTPException as e:
                    if getattr(e, "status", None) == 429 and attempt < 2:
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    fail += 1
                    print(f"[送信] ❌ {i+1}: {type(e).__name__}: {e}")
                    return
                except Exception as e:
                    fail += 1
                    print(f"[送信] ❌ {i+1}: {type(e).__name__}: {e}")
                    return

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
        channel = interaction.channel

        # ✅ 投稿経路を決定：Botがネイティブ投稿できるなら followup の5通上限を回避
        state = {"native": False, "attach": True}
        me = await get_bot_member(guild) if guild else None
        if guild and me:
            caps = channel_caps(channel, me)
            state["native"] = caps["send"]
            state["attach"] = caps["attach"]
        else:
            state["attach"] = True

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            print(f"[ボタン] defer失敗: {type(e).__name__}: {e}")
            return

        count = self.count
        if not state["native"] and count > FOLLOWUP_LIMIT:
            print(f"[ボタン] followup上限のため {count} → {FOLLOWUP_LIMIT} に制限")
            count = FOLLOWUP_LIMIT

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
        await interaction.response.defer(ephemeral=True)

        me = await get_bot_member(guild)
        if me is None:
            await interaction.followup.send(
                "## ❌ **Botがサーバーに居ないため全チャンネル送信は不可です。**\n"
                "/spam（このチャンネルのみ）を使ってください。",
                ephemeral=True,
            )
            return

        channels = await get_sendable_text_channels(guild, me)
        if not channels:
            await interaction.followup.send("## ❌ **送信可能なチャンネルがありません。**", ephemeral=True)
            return

        results = []

        async def worker(ch):
            caps = channel_caps(ch, me)
            if not caps["send"]:
                results.append((ch.name, "skip"))
                return
            state = {"native": True, "attach": caps["attach"]}
            ok, fail = await run_spam(ch, None, guild, self.count, self.mention, state)
            results.append((ch.name, f"{ok}ok/{fail}ng"))

        await asyncio.gather(*(worker(c) for c in channels))

        done = sum(1 for _, r in results if r not in ("skip",))
        skip = sum(1 for _, r in results if r == "skip")
        detail = "\n".join(f"・#{n}: {r}" for n, r in results[:20])
        await interaction.followup.send(
            f"# 💥 **一括送信完了**\n送信={done} / スキップ={skip}\n{detail}",
            ephemeral=True,
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

    print(f"[/spam] guild={getattr(interaction.guild, 'id', 'DM')} mode={mode} → {reason} count={count}")
    await interaction.response.send_message(
        f"🤓 **ボタンを押してスパム開始！**\n📋 @everyone: {reason}\n🔢 回数: {count}",
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

    await interaction.response.send_message(
        f"💥 **全チャンネル送信の準備**\n📋 @everyone: {reason}\n🔢 各ch {count}回",
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
    warn = ""
    if count > FOLLOWUP_LIMIT:
        warn = (
            f"\n## ⚠️ **Botが未導入の状態では {FOLLOWUP_LIMIT}回までに自動制限されます。**"
            "\n## 💡 **Botをサーバーに招待すれば上限なしで送信できます。**"
        )
    await interaction.response.send_message(
        f"# ✅ **送信回数を {count}回 に変更しました。**{warn}", ephemeral=True
    )

# ==============================
# /setinterval（0 = 全弾同時）
# ==============================
@tree.command(name="setinterval", description="送信間隔を変更（0 = 最速の同時送信）")
@app_commands.describe(interval="送信間隔（秒）。0で最速")
async def cmd_setinterval(interaction: discord.Interaction, interval: float):
    global SEND_INTERVAL
    if interval < 0:
        await interaction.response.send_message("# ❌ **0以上で指定してください。**", ephemeral=True)
        return
    SEND_INTERVAL = interval
    note = "（全弾同時・レートリミッタ任せ）" if interval == 0 else ""
    await interaction.response.send_message(
        f"# ✅ **送信間隔を {interval}秒 に変更しました。**{note}", ephemeral=True
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
# /seteveryone（自動判定の永続設定）
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