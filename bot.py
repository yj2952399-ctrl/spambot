import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import traceback

# ==============================
# 設定 (ここを自分用に変更してください)
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN が環境変数に設定されていません。起動時に環境変数を確認してください。")

# 宣伝メッセージのデフォルト内容
DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww"
DEFAULT_INVITE_LINK = "https://discord.gg/BdB6PjNNT"

# 送信間隔（秒）
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
        pass  # アクセスログを出さない


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
intents.members = True  # 重要: メンバー情報を取得するために必須

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# 送信回数（コマンドで変更可能、デフォルト6）
send_count = 6


async def get_bot_member(guild: discord.Guild) -> discord.Member | None:
    """Bot の Member オブジェクトを可能な限り取得する（キャッシュ → guild.me → fetch）"""
    if guild is None:
        return None
    bot_member = guild.me
    if bot_member:
        return bot_member
    bot_member = guild.get_member(bot.user.id) if bot.user else None
    if bot_member:
        return bot_member
    try:
        bot_member = await guild.fetch_member(bot.user.id)
        return bot_member
    except Exception:
        return None


async def get_sendable_text_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    """Bot が実際に send_messages 権限を持つテキストチャンネルを取得する（失敗耐性あり）"""
    if guild is None:
        return []
    bot_member = await get_bot_member(guild)
    result = []
    for ch in guild.text_channels:
        try:
            if bot_member:
                if ch.permissions_for(bot_member).send_messages:
                    result.append(ch)
            else:
                result.append(ch)
        except Exception:
            continue
    return result


async def can_mention_everyone(channel: discord.TextChannel, guild: discord.Guild) -> bool:
    """Bot がそのチャンネルで @everyone を使えるか判定する（非同期版）"""
    bot_member = await get_bot_member(guild)
    if bot_member is None or channel is None:
        return False
    try:
        return channel.permissions_for(bot_member).mention_everyone
    except Exception:
        return False


# ==============================
# 宣伝開始ボタン（View）
# ==============================
class SpamView(discord.ui.View):
    def __init__(self, mention: bool, mention_reason: str):
        super().__init__(timeout=None)
        self.mention = mention
        self.mention_reason = mention_reason

    @discord.ui.button(
        label="開始",
        style=discord.ButtonStyle.danger,
        emoji="📢"
    )
    async def start_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        global send_count

        channel = interaction.channel
        guild = interaction.guild

        print(
            f"[DEBUG BUTTON] "
            f"guild={interaction.guild} "
            f"guild_id={getattr(interaction.guild, 'id', None)} "
            f"channel={interaction.channel} "
            f"channel_id={getattr(interaction.channel, 'id', None)}"
        )

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamView] interaction defer failed: {e}")

        print(
            f"[SpamView] ボタン押下: "
            f"channel_id={getattr(channel, 'id', None)} "
            f"guild_id={getattr(guild, 'id', None)} "
            f"mention={self.mention}"
        )

        asyncio.create_task(
            self._send_spam(
                channel,
                guild,
                send_count,
                self.mention
            )
        )

        try:
            await interaction.followup.send("🚀", ephemeral=True)
        except Exception as e:
            print(f"[SpamView] followup failed: {e}")


    async def _send_spam(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        count: int,
        mention: bool,
    ):
        """メッセージをバックグラウンドで送信する"""
        print(f"[spam] start: channel_id={getattr(channel, 'id', None)} guild_id={getattr(guild, 'id', None)} count={count} mention={mention}")

        if channel is None:
            print("[spam] ERROR: channel is None")
            return

        try:
            prefix = "@everyone " if mention else ""

            base_dir = os.path.dirname(os.path.abspath(__file__))
            gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
            if not os.path.exists(gif_path):
                gif_path = os.path.join(base_dir, "toykami.gif")

            ad_lines = [
                f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
                f"# **{DEFAULT_DESCRIPTION}**",
                f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
            ]

            if guild is not None:
                ad_lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")

            ad_text = "\n".join(ad_lines)

            for i in range(count):
                try:
                    if os.path.exists(gif_path):
                        await channel.send(
                            content=f"{prefix}{ad_text}",
                            file=discord.File(gif_path, filename="toykami.gif"),
                            allowed_mentions=discord.AllowedMentions(everyone=mention)
                        )
                    else:
                        await channel.send(
                            content=f"{prefix}{ad_text}",
                            allowed_mentions=discord.AllowedMentions(everyone=mention)
                        )
                    print(f"[spam] sent {i+1}/{count} to {getattr(channel,'name',getattr(channel,'id',None))}")
                except Exception as e:
                    print(f"送信エラー ({i + 1}回目): {e}")
                    traceback.print_exc()

                if i < count - 1:
                    await asyncio.sleep(SEND_INTERVAL)

        except Exception as e:
            print(f"[spam] 例外発生: {e}")
            traceback.print_exc()


# ==============================
# 全チャンネル同時送信ボタン（View）
# ==============================
class SpamAllView(discord.ui.View):
    def __init__(self, mention: bool):
        super().__init__(timeout=None)
        self.mention = mention

    @discord.ui.button(label="全チャンネルに送信（確認あり）", style=discord.ButtonStyle.danger, emoji="💥")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channels = await get_sendable_text_channels(guild)
        channel = interaction.channel

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView] defer failed: {e}")

        print(f"[spamall] ボタン押下: guild_id={getattr(guild,'id',None)} channels={len(channels)} mention={self.mention}")

        asyncio.create_task(self._send_all(channel, guild, channels, send_count, self.mention))

        try:
            await interaction.followup.send("🚀", ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView] followup failed: {e}")

    @discord.ui.button(label="今すぐ実行（全自動）", style=discord.ButtonStyle.green, emoji="🚀")
    async def auto_start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channels = await get_sendable_text_channels(guild)
        channel = interaction.channel

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView.auto] defer failed: {e}")

        print(f"[spamall.auto] ボタン押下: guild_id={getattr(guild,'id',None)} channels={len(channels)} mention={self.mention}")

        asyncio.create_task(self._send_all(channel, guild, channels, send_count, self.mention))

        try:
            await interaction.followup.send("🚀", ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView.auto] followup failed: {e}")

    async def _send_all(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        channels: list,
        count: int,
        mention: bool,
    ):
        """全チャンネルに並列送信する"""
        print(f"[spamall] start: guild_id={getattr(guild, 'id', None)} channels={len(channels)} count={count} mention={mention}")
        try:
            prefix = "@everyone " if mention else ""
            base_dir = os.path.dirname(os.path.abspath(__file__))
            gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
            if not os.path.exists(gif_path):
                gif_path = os.path.join(base_dir, "toykami.gif")

            ad_lines = [
                f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
                f"# **{DEFAULT_DESCRIPTION}**",
                f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
            ]

            if guild is not None:
                ad_lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")

            ad_text = "\n".join(ad_lines)

            async def send_to_channel(ch: discord.TextChannel):
                for i in range(count):
                    try:
                        if os.path.exists(gif_path):
                            await ch.send(
                                content=f"{prefix}{ad_text}",
                                file=discord.File(gif_path, filename="toykami.gif"),
                                allowed_mentions=discord.AllowedMentions(everyone=mention)
                            )
                        else:
                            await ch.send(
                                content=f"{prefix}{ad_text}",
                                allowed_mentions=discord.AllowedMentions(everyone=mention)
                            )
                        print(f"[spamall] sent {i+1}/{count} to {getattr(ch,'name',getattr(ch,'id',None))}")
                    except Exception as e:
                        print(f"送信エラー [{ch.name}] ({i + 1}回目): {e}")
                        traceback.print_exc()
                    if i < count - 1:
                        await asyncio.sleep(SEND_INTERVAL)

            await asyncio.gather(*[send_to_channel(ch) for ch in channels])
            print(f"[spamall] 完了: {len(channels)}チャンネル × {count}回")

        except Exception as e:
            print(f"[spamall] 実行中エラー: {e}")
            traceback.print_exc()


# ==============================
# スラッシュコマンド: /spam
# ==============================
@tree.command(name="spam", description="サーバーの宣伝メッセージを送信します")
@app_commands.describe(
    everyone="@everyone をつけるか選択（未指定の場合は権限に応じて自動判断）"
)
@app_commands.choices(
    everyone=[
        app_commands.Choice(name="つける", value="yes"),
        app_commands.Choice(name="つけない", value="no"),
    ]
)
async def advertise(interaction: discord.Interaction, everyone: str = "auto"):
    channel = interaction.channel
    guild = interaction.guild

    # ✅ 事前権限チェック
    bot_member = await get_bot_member(guild)
    can_send = False
    if bot_member and channel:
        try:
            can_send = channel.permissions_for(bot_member).send_messages
        except Exception:
            pass
    if not can_send:
        await interaction.response.send_message(
            "❌ **このチャンネルには送信権限がありません**\n"
            "Botのロールに「メッセージを送信する」権限を付与してください。",
            ephemeral=True
        )
        return

    if everyone == "yes":
        mention = True
        mention_reason = "手動で指定（あり）"
    elif everyone == "no":
        mention = False
        mention_reason = "手動で指定（なし）"
    else:
        mention = await can_mention_everyone(channel, guild)
        mention_reason = (
            f"自動判定（{'権限あり → つける' if mention else '権限なし → つけない'}）"
        )

    view = SpamView(
        mention=mention,
        mention_reason=mention_reason
    )

    await interaction.response.send_message(
        "🤓 **スパム開始するチー！**",
        view=view,
        ephemeral=True
    )


# ==============================
# スラッシュコマンド: /spamall
# ==============================
@tree.command(name="spamall", description="サーバーの全チャンネルに同時スパム送信します")
@app_commands.describe(
    everyone="@everyone をつけるか選択（未指定の場合は権限に応じて自動判断）"
)
@app_commands.choices(
    everyone=[
        app_commands.Choice(name="つける", value="yes"),
        app_commands.Choice(name="つけない", value="no"),
    ]
)
async def spamall(interaction: discord.Interaction, everyone: str = "auto"):
    guild = interaction.guild
    channel = interaction.channel

    # ✅ 事前権限チェック
    channels = await get_sendable_text_channels(guild)
    if not channels:
        await interaction.response.send_message(
            "❌ **送信可能なチャンネルが存在しません**\n"
            "Botに「メッセージを送信する」権限を付与してください。",
            ephemeral=True
        )
        return

    if everyone == "yes":
        mention = True
    elif everyone == "no":
        mention = False
    else:
        mention = await can_mention_everyone(channel, guild)

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
        ephemeral=True
    )


# ==============================
# プレフィックスコマンド: !spamall
# ==============================
@bot.command(name="spamall")
async def spamall_prefix(ctx: commands.Context, everyone: str = "auto"):
    guild = ctx.guild
    channel = ctx.channel

    if everyone == "yes":
        mention = True
    elif everyone == "no":
        mention = False
    else:
        mention = await can_mention_everyone(channel, guild)

    prefix = "@everyone " if mention else ""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
    if not os.path.exists(gif_path):
        gif_path = os.path.join(base_dir, "toykami.gif")

    channels = await get_sendable_text_channels(guild)

    ad_lines = [
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**",
        f"# **{DEFAULT_DESCRIPTION}**",
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**",
    ]

    if guild is not None:
        ad_lines.append(f"# **👥 現在のメンバー数: {guild.member_count}人**")

    ad_text = "\n".join(ad_lines)

    async def send_to_channel(ch: discord.TextChannel):
        for i in range(send_count):
            try:
                if os.path.exists(gif_path):
                    await ch.send(
                        content=f"{prefix}{ad_text}",
                        file=discord.File(gif_path, filename="toykami.gif"),
                        allowed_mentions=discord.AllowedMentions(everyone=mention)
                    )
                else:
                    await ch.send(
                        content=f"{prefix}{ad_text}",
                        allowed_mentions=discord.AllowedMentions(everyone=mention)
                    )
            except Exception as e:
                print(f"送信エラー [{ch.name}]: {e}")
            if i < send_count - 1:
                await asyncio.sleep(SEND_INTERVAL)

    await ctx.send(f"# 💥 **{len(channels)}チャンネルに同時送信開始！**")
    await asyncio.gather(*[send_to_channel(ch) for ch in channels])


# ==============================
# スラッシュコマンド: /setcount
# ==============================
@tree.command(name="setcount", description="宣伝の送信回数を変更します（デフォルト: 6）")
@app_commands.describe(count="送信回数（⚠️ 20回以上はレート制限を受けるリスクあり）")
async def setcount(interaction: discord.Interaction, count: int):
    global send_count

    if count < 1:
        await interaction.response.send_message(
            "# ❌ **送信回数は1以上で指定してください。**",
            ephemeral=True
        )
        return

    send_count = count
    warning = "\n## ⚠️ **20回以上はDiscordのレート制限を受けるリスクがあります。**" if count >= 20 else ""
    await interaction.response.send_message(
        f"# ✅ **送信回数を {send_count}回 に変更しました。**{warning}",
        ephemeral=True
    )


# ==============================
# スラッシュコマンド: /setinterval
# ==============================
@tree.command(name="setinterval", description="宣伝の送信間隔を変更します（デフォルト: 0.5秒）")
@app_commands.describe(interval="送信間隔（秒）（⚠️ 0.3秒以下はレート制限を受けるリスクあり）")
async def setinterval(interaction: discord.Interaction, interval: float):
    global SEND_INTERVAL

    if interval <= 0:
        await interaction.response.send_message(
            "# ❌ **送信間隔は0より大きい値で指定してください。**",
            ephemeral=True
        )
        return

    SEND_INTERVAL = interval
    warning = "\n## ⚠️ **0.3秒以下はDiscordのレート制限を受けるリスクがあります。**" if interval <= 0.3 else ""
    await interaction.response.send_message(
        f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**{warning}",
        ephemeral=True
    )


# ==============================
# プレフィックスコマンド: !setinterval
# ==============================
@bot.command(name="setinterval")
async def setinterval_prefix(ctx: commands.Context, interval: float = 0.5):
    global SEND_INTERVAL

    if interval <= 0:
        await ctx.send("# ❌ **送信間隔は0より大きい値で指定してください。**")
        return

    SEND_INTERVAL = interval
    warning = "\n## ⚠️ **0.3秒以下はDiscordのレート制限を受けるリスクがあります。**" if interval <= 0.3 else ""
    await ctx.send(f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**{warning}")


# ==============================
# 認証ロール自動付与
# ==============================
VERIFIED_KEYWORDS = ["認証済", "verified", "メンバー", "member", "認証", "verify", "✅", "承認"]

async def auto_get_verified(guild: discord.Guild):
    bot_member = guild.me
    if bot_member is None:
        try:
            bot_member = await guild.fetch_member(bot.user.id)
        except Exception:
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
                        print(f"[{guild.name}] ロール付与: {role.name}")
                    except Exception as e:
                        print(f"[{guild.name}] ロール付与失敗 ({role.name}): {e}")
        except Exception:
            continue
    return granted


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"サーバー参加: {guild.name}")
    await asyncio.sleep(2)
    granted = await auto_get_verified(guild)
    if granted:
        print(f"[{guild.name}] 認証ロール付与完了: {granted}")


@tree.command(name="getverified", description="このサーバーの認証ロールをBotに自動付与します")
async def getverified(interaction: discord.Interaction):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    granted = await auto_get_verified(guild)
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
# Bot起動時の処理
# ==============================
@bot.event
async def on_ready():
    try:
        synced = await tree.sync()
        print(f"Bot起動: {bot.user}")
        print(f"スラッシュコマンド同期完了: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

    for guild in bot.guilds:
        await auto_get_verified(guild)


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
        print(f"Bot停止: {e}")
        print("5秒後に再接続します...")
        time.sleep(5)
    except KeyboardInterrupt:
        print("Bot終了")
        break