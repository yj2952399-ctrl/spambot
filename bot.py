# bot.py
# 完全版：SpamView / SpamAllView / prefixコマンド / スラッシュコマンド / 認証ロール自動付与 / checkperms / keep-alive 等を含む
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import time
import traceback
from typing import Optional, List

# ===== 設定 =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", None)
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN が環境変数に設定されていません。Railway の Environment Variables を確認してください。")

DEFAULT_SERVER_NAME = "TISN | トイ神"
DEFAULT_DESCRIPTION = "お前らみたいな人生負け組のチー牛🧀🐮🤓と豚丼🐖には眩しすぎて入ることすらできないwww"
DEFAULT_INVITE_LINK = "https://discord.gg/BdB6PjNNT"

# 送信間隔（秒）とデフォルト回数
SEND_INTERVAL = 0.5
send_count = 6

# 認証ロールを検出するキーワード（小文字で比較）
VERIFIED_KEYWORDS = ["認証済", "verified", "メンバー", "member", "認証", "verify", "✅", "承認"]

# ===== キープアライブ用の簡易 HTTP サーバ =====
class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        # ログを出したくない場合は無視
        pass

def start_keep_alive():
    port = int(os.environ.get("BOT_PORT", 8099))
    server = HTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"キープアライブサーバー起動: ポート {port}")

# ===== Bot 初期化 =====
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Developer Portal で有効にすること

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ===== ヘルパー =====
async def get_bot_member(guild: Optional[discord.Guild]) -> Optional[discord.Member]:
    """できるだけ確実に Bot の Member オブジェクトを取得する"""
    if guild is None:
        return None
    try:
        # guild.me があればそれを返す
        if getattr(guild, "me", None):
            return guild.me
        # キャッシュから
        if bot.user:
            m = guild.get_member(bot.user.id)
            if m:
                return m
        # API から取得
        m = await guild.fetch_member(bot.user.id)
        return m
    except Exception:
        return None

async def get_sendable_text_channels(guild: Optional[discord.Guild]) -> List[discord.TextChannel]:
    """Bot が send_messages 権限を持つテキストチャンネルのみを返す（取得失敗時は保守的に空を返す）"""
    if guild is None:
        return []
    bot_member = await get_bot_member(guild)
    result: List[discord.TextChannel] = []
    for ch in guild.text_channels:
        try:
            if bot_member:
                perms = ch.permissions_for(bot_member)
                if perms.send_messages:
                    result.append(ch)
            else:
                # bot_member が取れない場合は全て追加しておく（送信時に例外処理）
                result.append(ch)
        except Exception:
            continue
    return result

def can_mention_everyone_sync(channel: Optional[discord.TextChannel], guild: Optional[discord.Guild]) -> bool:
    """同期的に簡易判定（Interaction レスポンス作成時に使う）"""
    try:
        bot_member = None
        if guild:
            bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if bot_member and channel:
            return channel.permissions_for(bot_member).mention_everyone
    except Exception:
        pass
    return False

# ===== 認証ロール自動付与 =====
async def auto_get_verified(guild: discord.Guild) -> List[str]:
    """サーバー内で認証ロールっぽいロールを Bot に付与する。付与できたロール名を返す"""
    bot_member = await get_bot_member(guild)
    if bot_member is None:
        return []
    granted: List[str] = []
    for role in guild.roles:
        try:
            if role.is_default():
                continue
            name_lower = role.name.lower()
            if any(kw in name_lower for kw in VERIFIED_KEYWORDS):
                # Bot の top_role より下にあるロールだけ付与可能
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

# ===== SpamView (単一チャンネル用) =====
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
        # 常時表示に編集（ephemeral=False のメッセージを想定）
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

        ch = interaction.channel  # 送信先はボタンが置かれたチャンネルを想定
        for i in range(count):
            try:
                # チャンネルごとに mention 権限を確認
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

# ===== SpamAllView (全チャンネル用、表示を共通化) =====
class SpamAllView(discord.ui.View):
    def __init__(self, mention: bool):
        super().__init__(timeout=None)
        self.mention = mention
        self.running = False

    def _status_text(self, mode_label: str, channels_count: int, mentionable_count: int, count: int) -> str:
        return (
            f"# {mode_label}\n"
            f"## **・送信可能チャンネル数: {channels_count}個**\n"
            f"## **・@everyone が使えるチャンネル数: {mentionable_count}個**\n"
            f"## **・送信回数: {count}回**\n"
            f"## **・合計送信数: {channels_count * count}回**\n"
            f"## ⚠️ 実行中..."
        )

    @discord.ui.button(label="全チャンネルに送信（確認あり）", style=discord.ButtonStyle.danger, emoji="💥")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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

        content = self._status_text("💥 **全チャンネルスパム開始！**", len(channels), mentionable_count, send_count)
        await interaction.response.edit_message(content=content, view=self)

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

        content = self._status_text("🚀 **全自動スパムを即実行します！**", len(channels), mentionable_count, send_count)
        await interaction.response.edit_message(content=content, view=self)

        self.running = True
        asyncio.create_task(self._send_all(interaction, guild, channels, send_count))

    async def _send_all(self, interaction: discord.Interaction, guild: discord.Guild, channels: List[discord.TextChannel], count: int):
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

        # 全チャンネルに同時並列送信
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

# ===== スラッシュコマンド: /spam =====
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
        mention = can_mention_everyone_sync(channel, guild)
        mention_reason = f"自動判定（{'権限あり → つける' if mention else '権限なし → つけない'}）"

    view = SpamView(mention=mention, mention_reason=mention_reason)

    await interaction.response.send_message(
        f"# 📢 **スパムコントロールパネル**\n## **送信回数: {send_count}回**\n## **間隔: {SEND_INTERVAL}秒**\n## **@everyone: {mention_reason}**",
        view=view,
        ephemeral=False
    )

# ===== スラッシュコマンド: /spamall =====
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

# ===== プレフィックスコマンド（互換） =====
@bot.command(name="spamall")
async def spamall_prefix(ctx: commands.Context, everyone: str = "auto"):
    guild = ctx.guild
    channel = ctx.channel

    if everyone == "yes":
        mention = True
    elif everyone == "no":
        mention = False
    else:
        mention = can_mention_everyone_sync(channel, guild)

    prefix = "@everyone " if mention else ""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
    if not os.path.exists(gif_path):
        gif_path = os.path.join(base_dir, "toykami.gif")

    channels = await get_sendable_text_channels(guild)

    ad_text = (
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
        f"# **{DEFAULT_DESCRIPTION}**\n"
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**\n"
        f"# **👥 現在のメンバー数: {guild.member_count}人**"
    )

    async def send_to_channel(ch: discord.TextChannel):
        for i in range(send_count):
            try:
                if os.path.exists(gif_path):
                    await ch.send(
                        content=f"{prefix}{ad_text}",
                        file=discord.File(gif_path, filename="toykami.gif"),
                        allowed_mentions=discord.AllowedMentions(everyone=True)
                    )
                else:
                    await ch.send(content=f"{prefix}{ad_text}", allowed_mentions=discord.AllowedMentions(everyone=True))
            except Exception as e:
                print(f"送信エラー [{ch.name}]: {e}")
            if i < send_count - 1:
                await asyncio.sleep(SEND_INTERVAL)

    await ctx.send(f"# 💥 **{len(channels)}チャンネルに同時送信開始！**")
    await asyncio.gather(*[send_to_channel(ch) for ch in channels])

# ===== スラッシュコマンド: /setcount, /setinterval （管理用） =====
@tree.command(name="setcount", description="宣伝の送信回数を変更します（デフォルト: 6）")
@app_commands.describe(count="送信回数（⚠️ 20回以上はレート制限を受けるリスクあり）")
async def setcount(interaction: discord.Interaction, count: int):
    global send_count
    if count < 1:
        await interaction.response.send_message("# ❌ **送信回数は1以上で指定してください。**", ephemeral=True)
        return
    send_count = count
    warning = "\n## ⚠️ **20回以上はDiscordのレート制限を受けるリスクがあります。**" if count >= 20 else ""
    await interaction.response.send_message(f"# ✅ **送信回数を {send_count}回 に変更しました。**{warning}", ephemeral=True)

@tree.command(name="setinterval", description="宣伝の送信間隔を変更します（デフォルト: 0.5秒）")
@app_commands.describe(interval="送信間隔（秒）（⚠️ 0.3秒以下はレート制限を受けるリスクあり）")
async def setinterval(interaction: discord.Interaction, interval: float):
    global SEND_INTERVAL
    if interval <= 0:
        await interaction.response.send_message("# ❌ **送信間隔は0より大きい値で指定してください。**", ephemeral=True)
        return
    SEND_INTERVAL = interval
    warning = "\n## ⚠️ **0.3秒以下はDiscordのレート制限を受けるリスクがあります。**" if interval <= 0.3 else ""
    await interaction.response.send_message(f"# ✅ **送信間隔を {SEND_INTERVAL}秒 に変更しました。**{warning}", ephemeral=True)

# ===== デバッグ: /checkperms =====
@tree.command(name="checkperms", description="Bot が各チャンネルでメッセージ送信や @everyone を使えるかを確認します（管理者向け）")
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

    await interaction.followup.send(text, ephemeral=True)

# ===== 認証ロール手動取得コマンド =====
@tree.command(name="getverified", description="このサーバーの認証ロールをBotに自動付与します")
async def getverified(interaction: discord.Interaction):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    granted = await auto_get_verified(guild)
    if granted:
        await interaction.followup.send(f"# ✅ **認証ロールを付与しました！**\n## {', '.join(granted)}", ephemeral=True)
    else:
        await interaction.followup.send("## ℹ️ **付与できる認証ロールが見つかりませんでした。**\nサーバー管理者にBotへ認証ロールを手動で付与してもらってください。", ephemeral=True)

# ===== on_ready と起動ループ =====
@bot.event
async def on_ready():
    print("DEBUG: running file = bot.py")
    try:
        try:
            await tree.sync()
            print("スラッシュコマンド同期完了")
        except Exception as e:
            print("tree.sync() に失敗しました:", e)
        print(f"Bot起動: {bot.user} ギルド数: {len(bot.guilds)}")
        for guild in bot.guilds:
            m = await get_bot_member(guild)
            print(f"[on_ready] guild={guild.name} bot_member={'OK' if m else 'NOT_OK'}")
    except Exception:
        print("on_ready 例外:")
        traceback.print_exc()

# ===== 起動（ループ再接続） =====
start_keep_alive()
while True:
    try:
        print("Bot起動中...")
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("Bot終了")
        break
    except Exception as e:
        print("Bot停止:", e)
        traceback.print_exc()
        print("5秒後に再接続します...")
        time.sleep(5)