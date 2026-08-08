# name=bot.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

# ---- 設定 ----
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN が環境変数に設定されていません。Railway の Environment Variables を確認してください。")

DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww"
DEFAULT_INVITE_LINK = "https://discord.gg/BdB6PjNNT"
SEND_INTERVAL = 0.5
send_count = 6

# ---- キープアライブサーバー ----
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

# ---- Bot の初期化 ----
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # 必須：Developer Portalで有効にすること

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---- ヘルパー: Bot の Member を取得 ----
async def get_bot_member(guild: discord.Guild) -> discord.Member | None:
    if guild is None:
        return None
    # まずは guild.me
    if getattr(guild, "me", None):
        return guild.me
    # 次にキャッシュから
    if bot.user:
        m = guild.get_member(bot.user.id)
        if m:
            return m
    # 最後に fetch
    try:
        m = await guild.fetch_member(bot.user.id)
        return m
    except Exception:
        return None

# ---- ヘルパー: 送信可能チャンネルを集める ----
async def get_sendable_text_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    if guild is None:
        return []
    bot_member = await get_bot_member(guild)
    result = []
    for ch in guild.text_channels:
        try:
            if bot_member:
                perms = ch.permissions_for(bot_member)
                if perms.send_messages:
                    result.append(ch)
            else:
                # bot_member を取得できなければ conservative に全て追加（送信時に例外処理）
                result.append(ch)
        except Exception:
            continue
    return result

# ---- スパム単一チャンネル用 View ----
class SpamView(discord.ui.View):
    def __init__(self, mention: bool, mention_reason: str):
        super().__init__(timeout=None)
        self.mention = mention
        self.mention_reason = mention_reason
        self.running = False

    @discord.ui.button(label="開始", style=discord.ButtonStyle.danger, emoji="📢")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global send_count
        if self.running:
            await interaction.response.send_message("既に送信中です。", ephemeral=True)
            return

        self.running = True
        await interaction.response.edit_message(
            content=(
                f"# 🤓 **スパムを開始します**\n"
                f"## **・送信回数: {send_count}回**\n"
                f"## **・間隔: {SEND_INTERVAL}秒**\n"
                f"## **・@everyone: {self.mention_reason}**\n"
                f"## ⚠️ 実行中..."
            ),
            view=self
        )
        asyncio.create_task(self._send_spam(interaction, interaction.guild, send_count))

    async def _send_spam(self, interaction: discord.Interaction, guild: discord.Guild, count: int):
        prefix_default = "@everyone " if self.mention else ""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
        if not os.path.exists(gif_path):
            gif_path = os.path.join(base_dir, "toykami.gif")

        ad_text = (
            f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
            f"# **{DEFAULT_DESCRIPTION}**\n"
            f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**\n"
            f"# **👥 現在のメンバー数: {guild.member_count}人**"
        )

        ch = interaction.channel
        for i in range(count):
            try:
                # 送信前にチャンネルごとの mention 権限を確認
                bot_member = await get_bot_member(guild)
                can_mention_here = False
                if bot_member and isinstance(ch, discord.TextChannel):
                    try:
                        can_mention_here = ch.permissions_for(bot_member).mention_everyone
                    except Exception:
                        can_mention_here = False

                prefix_here = "@everyone " if (self.mention and can_mention_here) else ""
                allowed_mentions = discord.AllowedMentions(everyone=(self.mention and can_mention_here))

                if os.path.exists(gif_path):
                    await ch.send(content=f"{prefix_here}{ad_text}", file=discord.File(gif_path, filename="toykami.gif"), allowed_mentions=allowed_mentions)
                else:
                    await ch.send(content=f"{prefix_here}{ad_text}", allowed_mentions=allowed_mentions)

            except Exception as e:
                print(f"送信エラー ({i + 1}回目): {e}")

            if i < count - 1:
                await asyncio.sleep(SEND_INTERVAL)

        try:
            await interaction.followup.send("✅ 送信が完了しました。", ephemeral=True)
            await interaction.edit_original_response(content=f"✅ **スパム送信が完了しました** （{count}回）", view=self)
        except Exception:
            pass
        self.running = False

# ---- 全チャンネル送信用 View ----
class SpamAllView(discord.ui.View):
    def __init__(self, mention: bool):
        super().__init__(timeout=None)
        self.mention = mention
        self.running = False

    @discord.ui.button(label="全チャンネルに送信（確認あり）", style=discord.ButtonStyle.danger, emoji="💥")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.running:
            await interaction.response.send_message("既に全チャンネル送信が動作中です。", ephemeral=True)
            return

        guild = interaction.guild
        channels = await get_sendable_text_channels(guild)
        bot_member = await get_bot_member(guild)

        # mention が可能なチャンネル数を数える
        mentionable_count = 0
        for ch in channels:
            try:
                if bot_member and ch.permissions_for(bot_member).mention_everyone:
                    mentionable_count += 1
            except Exception:
                continue

        await interaction.response.edit_message(
            content=(
                f"# 💥 **全チャンネルスパム開始！**\n"
                f"## **・送信可能チャンネル数: {len(channels)}個**\n"
                f"## **・@everyone が使えるチャンネル数: {mentionable_count}個**\n"
                f"## **・送信回数: {send_count}回**\n"
                f"## **・合計送信数: {len(channels) * send_count}回**\n"
                f"## ⚠️ 実行中..."
            ),
            view=self
        )

        self.running = True
        asyncio.create_task(self._send_all(interaction, guild, channels, send_count))

    @discord.ui.button(label="今すぐ実行（全自動）", style=discord.ButtonStyle.green, emoji="🚀")
    async def auto_start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.running:
            await interaction.response.send_message("既に全チャンネル送信が動作中です。", ephemeral=True)
            return

        guild = interaction.guild
        channels = await get_sendable_text_channels(guild)
        bot_member = await get_bot_member(guild)
        mentionable_count = 0
        for ch in channels:
            try:
                if bot_member and ch.permissions_for(bot_member).mention_everyone:
                    mentionable_count += 1
            except Exception:
                continue

        await interaction.response.edit_message(
            content=(
                f"# 🚀 **全自動スパムを即実行します！**\n"
                f"## **対象チャンネル数: {len(channels)}個**\n"
                f"## **@everyone が使えるチャンネル数: {mentionable_count}個**\n"
                f"## **送信回数: {send_count}回**\n"
                f"## ⚠️ 実行中..."
            ),
            view=self
        )
        self.running = True
        asyncio.create_task(self._send_all(interaction, guild, channels, send_count))

    async def _send_all(self, interaction: discord.Interaction, guild: discord.Guild, channels: list, count: int):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
        if not os.path.exists(gif_path):
            gif_path = os.path.join(base_dir, "toykami.gif")

        ad_text = (
            f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
            f"# **{DEFAULT_DESCRIPTION}**\n"
            f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**\n"
            f"# **👥 現在のメンバー数: {guild.member_count}人**"
        )

        async def send_to_channel(ch: discord.TextChannel):
            bot_member = await get_bot_member(guild)
            try:
                can_mention_here = False
                if bot_member:
                    can_mention_here = ch.permissions_for(bot_member).mention_everyone
            except Exception:
                can_mention_here = False

            prefix_here = "@everyone " if (self.mention and can_mention_here) else ""
            allowed_mentions = discord.AllowedMentions(everyone=(self.mention and can_mention_here))

            for i in range(count):
                try:
                    if os.path.exists(gif_path):
                        await ch.send(content=f"{prefix_here}{ad_text}", file=discord.File(gif_path, filename="toykami.gif"), allowed_mentions=allowed_mentions)
                    else:
                        await ch.send(content=f"{prefix_here}{ad_text}", allowed_mentions=allowed_mentions)
                except Exception as e:
                    print(f"送信エラー [{ch.name}] ({i + 1}回目): {e}")
                if i < count - 1:
                    await asyncio.sleep(SEND_INTERVAL)

        # gather で全チャンネルへ並列送信
        try:
            await asyncio.gather(*[send_to_channel(ch) for ch in channels])
            print(f"[spamall] 完了: {len(channels)}チャンネル × {count}回")
        except Exception as e:
            print(f"[spamall] 実行中エラー: {e}")

        try:
            await interaction.followup.send(f"✅ 全チャンネル送信が完了しました（{len(channels)}チャンネル × {count}回）", ephemeral=True)
            await interaction.edit_original_response(content=f"✅ **全チャンネル送信が完了しました**（{len(channels)}チャンネル × {count}回）", view=self)
        except Exception:
            pass
        self.running = False

# ---- /spam コマンド（コントロールパネルを常時チャンネル表示） ----
@tree.command(name="spam", description="サーバーのスパムメッセージを送信します")
@app_commands.describe(everyone="@everyone をつけるか選択（未指定は自動判定）")
@app_commands.choices(everyone=[app_commands.Choice(name="つける", value="yes"), app_commands.Choice(name="つけない", value="no")])
async def advertise(interaction: discord.Interaction, everyone: str = "auto"):
    channel = interaction.channel
    guild = interaction.guild

    if everyone == "yes":
        mention = True
        mention_reason = "手動で指定（あり）"
    elif everyone == "no":
        mention = False
        mention_reason = "手動で指定（なし）"
    else:
        mention = await (lambda ch, g: can_mention_everyone_sync(ch, g))(channel, guild)
        mention_reason = f"自動判定（{'権限あり → つける' if mention else '権限なし → つけない'}）"

    view = SpamView(mention=mention, mention_reason=mention_reason)

    await interaction.response.send_message(
        f"# 📢 **スパムコントロールパネル**\n## **送信回数: {send_count}回**\n## **間隔: {SEND_INTERVAL}秒**\n## **@everyone: {mention_reason}**",
        view=view,
        ephemeral=False
    )

# ---- 同期的に簡易判定（advertise コマンドで使用） ----
def can_mention_everyone_sync(channel: discord.TextChannel, guild: discord.Guild) -> bool:
    """同期的判定：Interaction の段階で簡易判定するために使用（正確さが必要なら非同期 can_mention_everyone を使用）"""
    try:
        bot_member = None
        if guild:
            bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if bot_member and channel:
            return channel.permissions_for(bot_member).mention_everyone
    except Exception:
        pass
    return False

# ---- /spamall コマンド ----
@tree.command(name="spamall", description="サーバーの全チャンネルに同時スパム送信します")
@app_commands.describe(everyone="@everyone をつけるか選択（未指定は自動判定）")
@app_commands.choices(everyone=[app_commands.Choice(name="つける", value="yes"), app_commands.Choice(name="つけない", value="no")])
async def spamall(interaction: discord.Interaction, everyone: str = "auto"):
    guild = interaction.guild
    channel = interaction.channel

    if everyone == "yes":
        mention = True
    elif everyone == "no":
        mention = False
    else:
        mention = can_mention_everyone_sync(channel, guild)

    channels = await get_sendable_text_channels(guild)
    ch_count = len(channels)
    view = SpamAllView(mention=mention)
    await interaction.response.send_message(
        content=(
            f"# 💥 **全チャンネルスパム**\n"
            f"## **送信可能チャンネル数: {ch_count}個**\n"
            f"## **送信回数: {send_count}回**\n"
            f"## **合計送信数: {ch_count * send_count}回**\n"
            f"## ⚠️ **本当に実行しますか？**"
        ),
        view=view,
        ephemeral=False
    )

# ---- デバッグ: /checkperms コマンド（管理者向け） ----
@tree.command(name="checkperms", description="Bot が各チャンネルでメッセージ送信や @everyone を使えるかを確認します（管理者専用推奨）")
async def checkperms(interaction: discord.Interaction):
    guild = interaction.guild
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("このコマンドはサーバー管理者のみ使用できます。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    bot_member = await get_bot_member(guild)
    channels = guild.text_channels if guild else []
    total = len(channels)
    sendable = []
    mentionable = []
    details = []
    for ch in channels:
        try:
            send_ok = False
            mention_ok = False
            if bot_member:
                perms = ch.permissions_for(bot_member)
                send_ok = perms.send_messages
                mention_ok = perms.mention_everyone
            else:
                # bot_member が取れていない場合は不確定
                send_ok = False
                mention_ok = False
            details.append((ch.name, send_ok, mention_ok))
            if send_ok:
                sendable.append(ch)
            if mention_ok:
                mentionable.append(ch)
        except Exception:
            details.append((ch.name, False, False))

    text = (
        f"サーバー: {guild.name}\n"
        f"総チャンネル数: {total}\n"
        f"送信可能チャンネル数: {len(sendable)}\n"
        f"@everyone 使用可能チャンネル数: {len(mentionable)}\n\n"
        "チャンネル詳細（最大100件表示）:\n"
    )
    for name, s, m in details[:100]:
        text += f"- #{name}: send_messages={s}, mention_everyone={m}\n"

    # 長文を分割する余地を残してファイル添付する
    await interaction.followup.send(text, ephemeral=True)

# ---- on_ready ----
@bot.event
async def on_ready():
    print("DEBUG: running file = bot.py")
    try:
        synced = asyncio.run_coroutine_threadsafe(tree.sync(), bot.loop).result()
    except Exception:
        try:
            # 代替同期（環境によってはこれで十分）
            asyncio.create_task(tree.sync())
        except Exception:
            pass
    print(f"Bot起動: {bot.user} ギルド数: {len(bot.guilds)}")

    # 起動時に各ギルドの bot_member を取得（ログ上で確認）
    for guild in bot.guilds:
        m = await get_bot_member(guild)
        print(f"[on_ready] guild={guild.name} bot_member={'OK' if m else 'NOT_OK'}")

# ---- 起動ループ ----
start_keep_alive()
while True:
    try:
        print("Bot起動中...")
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("Bot終了")
        break
    except Exception as e:
        print(f"Bot停止: {e}")
        print("5秒後に再接続します...")
        time.sleep(5)