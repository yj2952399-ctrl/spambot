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
        
        # deferのみ行い、応答メッセージは送信しない（連続押し可能にするため）
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

        # バックグラウンドで宣伝処理（応答不要）
        asyncio.create_task(
            self._send_spam(
                channel,
                guild,
                send_count,
                self.mention
            )
        )
        # 「送信しました」メッセージは送信しない（連続で押せるように）


    async def _send_spam(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        count: int,
        mention: bool,
    ):
        """メッセージをバックグラウンドで送信"""
        print(f"[spam] start: channel_id={getattr(channel, 'id', None)} guild_id={getattr(guild, 'id', None)} count={count} mention={mention}")
        
        if channel is None:
            print("[spam] ERROR: channel is None")
            return
            
        try:
            prefix = "@everyone " if mention else ""

            # GIFのパスを解決
            base_dir = os.path.dirname(os.path.abspath(__file__))
            gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
            if not os.path.exists(gif_path):
                gif_path = os.path.join(base_dir, "toykami.gif")

            # 宣伝テキスト（メンバーカウント削除）
            ad_text = (
                f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
                f"# **{DEFAULT_DESCRIPTION}**\n"
                f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**"
            )

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

            # 「送信が完了しました」メッセージも不要（削除）

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

        # deferのみ（応答メッセージ不要）
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView] defer failed: {e}")

        print(f"[spamall] ボタン押下: guild_id={getattr(guild,'id',None)} channels={len(channels)} mention={self.mention}")

        asyncio.create_task(self._send_all(channel, guild, channels, send_count, self.mention))

    @discord.ui.button(label="今すぐ実行（全自動）", style=discord.ButtonStyle.green, emoji="🚀")
    async def auto_start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channels = await get_sendable_text_channels(guild)
        channel = interaction.channel

        # deferのみ（応答メッセージ不要）
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            print(f"[SpamAllView.auto] defer failed: {e}")

        print(f"[spamall.auto] ボタン押下: guild_id={getattr(guild,'id',None)} channels={len(channels)} mention={self.mention}")

        asyncio.create_task(self._send_all(channel, guild, channels, send_count, self.mention))

    async def _send_all(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        channels: list,
        count: int,
        mention: bool,
    ):
        """全チャンネルに並列送信"""
        print(f"[spamall] start: guild_id={getattr(guild, 'id', None)} channels={len(channels)} count={count} mention={mention}")
        try:
            prefix = "@everyone " if mention else ""
            base_dir = os.path.dirname(os.path.abspath(__file__))
            gif_path = os.path.join(base_dir, "discord_advertise_bot", "toykami.gif")
            if not os.path.exists(gif_path):
                gif_path = os.path.join(base_dir, "toykami.gif")

            # メンバーカウント削除
            ad_text = (
                f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
                f"# **{DEFAULT_DESCRIPTION}**\n"
                f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**"
            )

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

            # 全チャンネルに同時並列送信
            await asyncio.gather(*[send_to_channel(ch) for ch in channels])
            print(f"[spamall] 完了: {len(channels)}チャンネル × {count}回")
            # 完了メッセージも不要（削除）

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
    
    print(
        f"[DEBUG /spam] "
        f"guild={interaction.guild} "
        f"guild_id={getattr(interaction.guild, 'id', None)} "
        f"channel={interaction.channel} "
        f"channel_id={getattr(interaction.channel, 'id', None)}"
    )
    
    # everyoneの自動判定
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

    # ephemeral=Trueで本人のみ表示（ボタンを連続で押せるように）
    await interaction.response.send_message(
        content="🤓 **スパム開始するチー！**",
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

    if everyone == "yes":
        mention = True
    elif everyone == "no":
        mention = False
    else:
        mention = await can_mention_everyone(channel, guild)

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
        ephemeral=True  # 本人のみ表示に変更（連続実行可能に）
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

    # メンバーカウント削除
    ad_text = (
        f"# **🎉 {DEFAULT_SERVER_NAME} に参加しよう！**\n"
        f"# **{DEFAULT_DESCRIPTION}**\n"
        f"# **🔗 招待リンク: {DEFAULT_INVITE_LINK}**"
    )

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