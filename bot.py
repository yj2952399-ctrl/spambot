import discord
from discord import ui, app_commands
from discord.ext import commands
import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import traceback

# ==============================
# 設定
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN が環境変数に設定されていません。起動時に環境変数を確認してください。")

DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww"
DEFAULT_INVITE_LINK = "https://discord.gg/BdB6PjNNT"

SEND_INTERVAL = 0.5

# ==============================
# キープアライブ用Webサーバー
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"キープアライブサーバー起動: ポート {port}")

# ==============================
# Bot設定
# ==============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
send_count = 6

# ==============================
# ユーティリティ関数
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

async def get_sendable_text_channels(guild):
    if guild is None:
        return []
    bot_member = await get_bot_member(guild)
    result = []
    for ch in guild.text_channels:
        try:
            if bot_member and ch.permissions_for(bot_member).view_channel:
                result.append(ch)
        except Exception:
            continue
    return result

# ✅ 【修正版】@everyone 権限判定：「実行者の権限」で判定
async def can_mention_everyone(interaction):
    channel = interaction.channel
    guild = interaction.guild
    if not guild or not channel:
        print("[権限判定] ギルド/チャンネル情報なし → @everyoneなし")
        return False
    try:
        # ✅ Botの権限ではなく「コマンドを打った人の権限」で判定
        member = interaction.user
        perms = channel.permissions_for(member)
        result = perms.mention_everyone
        print(f"[権限判定] {member.name} の権限: mention_everyone={result}")
        return result
    except Exception as e:
        print(f"[権限判定エラー] {type(e).__name__}: {e}")
        return False

def build_advertisement_text(guild):
    ad_lines = [
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
        f"# **{DEFAULT_DESCRIPTION}**",
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
    ]
    if guild:
        ad_lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")
    return "\n".join(ad_lines)

def get_gif_attachment():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
    if not os.path.exists(gif_path):
        gif_path = os.path.join(base_dir, "toykami.gif")
    if os.path.exists(gif_path):
        return discord.File(gif_path, filename="toykami.gif")
    return None

# ==============================
# 返信形式で連続送信
# ==============================
async def send_advertisement_followup(followup_obj, count, mention, guild):
    print(f"[送信処理] 開始: count={count}, mention={mention}, guild_id={getattr(guild, 'id', None)}")
    prefix = "@everyone " if mention else ""
    ad_text = build_advertisement_text(guild)
    gif_file = get_gif_attachment()
    allowed_mentions = discord.AllowedMentions(everyone=mention)

    for i in range(count):
        try:
            if gif_file:
                await followup_obj.send(
                    content=f"{prefix}{ad_text}",
                    file=gif_file,
                    allowed_mentions=allowed_mentions
                )
            else:
                await followup_obj.send(
                    content=f"{prefix}{ad_text}",
                    allowed_mentions=allowed_mentions
                )
            print(f"[送信処理] 送信完了 {i+1}/{count} | @everyone={'✅ あり' if mention else '❌ なし'}")
        except Exception as e:
            print(f"[送信処理] 送信エラー ({i+1}回目): {type(e).__name__}: {e}")
            traceback.print_exc()
        if i < count - 1:
            await asyncio.sleep(SEND_INTERVAL)
    print("[送信処理] 全メッセージ送信完了")

# ==============================
# ✅ 宣伝開始ボタンView：判定結果を保存して使う
# ==============================
class SpamView(ui.View):
    def __init__(self, mention: bool, mention_reason: str):
        super().__init__(timeout=None)
        self.target_mention = mention   # ✅ コマンド時の判定結果を保存
        self.mention_reason = mention_reason

    @ui.button(
        label="開始",
        style=discord.ButtonStyle.danger,
        emoji="📢"
    )
    async def start_handler(self, interaction, btn):
        global send_count
        guild = interaction.guild

        print(f"[ボタン押下] 保存された設定: @everyone={self.target_mention} ({self.mention_reason})")

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            print(f"[ボタン処理] defer失敗: {type(e).__name__}: {e}")
            return

        # ✅ 「コマンド時に判定した結果」をそのまま使う → 再判定しない！
        asyncio.create_task(
            send_advertisement_followup(
                interaction.followup,
                send_count,
                self.target_mention,  # ここが超重要
                guild
            )
        )

# ==============================
# 全チャンネル送信ボタンView
# ==============================
class SpamAllView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="全チャンネルに送信", style=discord.ButtonStyle.danger, emoji="💥")
    async def all_handler(self, interaction, btn):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView] defer失敗: {type(e).__name__}: {e}")
            return
        await interaction.followup.send(
            "⚠️ **全チャンネル一括送信はBotをサーバーに追加する必要があります。**\n"
            "個人インストール状態では /spam（このチャンネルへの送信）のみ使用可能です。",
            ephemeral=True
        )

# ==============================
# ✅ /spam コマンド：判定結果を表示してボタンに渡す
# ==============================
@tree.command(name="spam", description="宣伝メッセージを送信します（ボタンを押して実行）")
@app_commands.describe(everyone="@everyone をつけるか選択（未指定の場合自動判定）")
@app_commands.choices(everyone=[
    app_commands.Choice(name="つける", value="yes"),
    app_commands.Choice(name="つけない", value="no"),
])
async def cmd_spam(interaction, everyone: str = "auto"):
    channel = interaction.channel
    guild = interaction.guild

    if everyone == "yes":
        mention = True
        mention_reason = "✅ 手動指定: @everyone つける"
    elif everyone == "no":
        mention = False
        mention_reason = "❌ 手動指定: @everyone つけない"
    else:
        # ✅ 自動判定
        mention = await can_mention_everyone(interaction)
        mention_reason = f"{'✅ 自動判定: 権限あり→つける' if mention else '❌ 自動判定: 権限なし→つけない'}"

    print(f"[/spam実行] guild_id={getattr(guild, 'id', None)} | {mention_reason}")

    # ✅ 判定結果をボタンに渡して保存
    view = SpamView(mention=mention, mention_reason=mention_reason)

    # ✅ ボタン＋判定結果は「自分だけに表示」
    await interaction.response.send_message(
        f"🤓 **ボタンを押してスパム開始！**\n📋 設定: {mention_reason}",
        view=view,
        ephemeral=True
    )

# ==============================
# スラッシュコマンド: /spamall
# ==============================
@tree.command(name="spamall", description="全チャンネル一括送信（サーバー追加版Botのみ）")
async def cmd_spamall(interaction):
    view = SpamAllView()
    await interaction.response.send_message(
        "⚠️ **全チャンネル送信はサーバー追加版Botのみ対応**",
        view=view,
        ephemeral=True
    )

# ==============================
# スラッシュコマンド: /setcount
# ==============================
@tree.command(name="setcount", description="宣伝の送信回数を変更します（デフォルト: 6）")
@app_commands.describe(count="送信回数（⚠️ 20回以上はDiscordの制限に注意）")
async def cmd_setcount(interaction, count: int):
    global send_count
    if count < 1:
        await interaction.response.send_message(
            "# ❌ **送信回数は1以上で指定してください。**",
            ephemeral=True
        )
        return
    send_count = count
    warning = "\n## ⚠️ **20回以上はDiscordのレート制限を受ける可能性があります。**" if count >= 20 else ""
    await interaction.response.send_message(
        f"# ✅ **送信回数を {send_count}回 に変更しました。**{warning}",
        ephemeral=True
    )

# ==============================
# スラッシュコマンド: /setinterval
# ==============================
@tree.command(name="setinterval", description="宣伝の送信間隔を変更します（デフォルト: 0.5秒）")
@app_commands.describe(interval="送信間隔（秒）（⚠️ 0.3秒以下は制限注意）")
async def cmd_setinterval(interaction, interval: float):
    global SEND_INTERVAL
    if interval <= 0:
        await interaction.response.send_message(
            "# ❌ **送信間隔は0より大きい値で指定してください。**",
            ephemeral=True
        )
        return
    SEND_INTERVAL = interval
    warning = "\n## ⚠️ **0.3秒以下はDiscordのレート制限を受ける可能性があります。**" if interval <= 0.3 else ""
    await interaction.response.send_message(
        f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**{warning}",
        ephemeral=True
    )

# ==============================
# プレフィックスコマンド: !setinterval
# ==============================
@bot.command(name="setinterval")
async def prefix_setinterval(ctx, interval: float = 0.5):
    global SEND_INTERVAL
    if interval <= 0:
        await ctx.send("# ❌ **送信間隔は0より大きい値で指定してください。**")
        return
    SEND_INTERVAL = interval
    warning = "\n## ⚠️ **0.3秒以下はDiscordのレート制限を受ける可能性があります。**" if interval <= 0.3 else ""
    await ctx.send(f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**{warning}")

# ==============================
# 認証ロール自動付与関連
# ==============================
VERIFIED_KEYWORDS = ["認証済", "verified", "メンバー", "member", "認証", "verify", "✅", "承認"]

async def auto_grant_verified_role(guild):
    bot_member = await get_bot_member(guild)
    if bot_member is None:
        return []
    granted = []
    for role in guild.roles:
        if role.is_default():
            continue
        try:
            if any(kw in role.name.lower() for kw in VERIFIED_KEYWORDS):
                if role < bot_member.top_role and role not in bot_member.roles:
                    try:
                        await bot_member.add_roles(role, reason="認証ロール自動付与")
                        granted.append(role.name)
                        print(f"[{guild.name}] 認証ロール付与: {role.name}")
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
async def cmd_getverified(interaction):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    granted = await auto_grant_verified_role(guild)
    if granted:
        await interaction.followup.send(
            f"# ✅ **認証ロールを付与しました！**\n## {', '.join(granted)}",
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            "## ℹ️ **付与できる認証ロールが見つかりませんでした。**\n"
            "サーバー管理者にBotへ認証ロールを手動で付与してもらってください。",
            ephemeral=True
        )

# ==============================
# Bot起動時処理
# ==============================
@bot.event
async def on_ready():
    try:
        synced = await tree.sync()
        print(f"Bot起動: {bot.user}")
        print(f"スラッシュコマンド同期完了: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"コマンド同期エラー: {type(e).__name__}: {e}")
    for guild in bot.guilds:
        await auto_grant_verified_role(guild)

# ==============================
# 起動ループ
# ==============================
import time
start_keep_alive()

while True:
    try:
        print("Bot起動中...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Bot停止: {type(e).__name__}: {e}")
        print("5秒後に再接続します...")
        time.sleep(5)
    except KeyboardInterrupt:
        print("Bot終了")
        break