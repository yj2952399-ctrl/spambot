from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 600  # 4:3
FONT_PATH = "/tmp/NotoSansCJK-Bold.otf"
TEXT = "トイ神の集い"
COLORS = [(220, 30, 30), (30, 60, 200), (255, 255, 255)]  # 赤・青・白
TEXT_COLORS = [(255, 255, 255), (255, 255, 255), (0, 0, 0)]  # 文字色（白背景のときは黒）
FRAME_DURATION = 100  # 0.1秒 = 100ms

frames = []

font = ImageFont.truetype(FONT_PATH, size=110)

for bg, fg in zip(COLORS, TEXT_COLORS):
    img = Image.new("RGB", (WIDTH, HEIGHT), color=bg)
    draw = ImageDraw.Draw(img)

    # テキストを中央に配置
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (WIDTH - text_w) / 2 - bbox[0]
    y = (HEIGHT - text_h) / 2 - bbox[1]

    # 影（立体感）
    draw.text((x + 4, y + 4), TEXT, font=font, fill=(0, 0, 0, 80))
    draw.text((x, y), TEXT, font=font, fill=fg)

    frames.append(img)

out_path = "discord_advertise_bot/toykami.gif"
frames[0].save(
    out_path,
    save_all=True,
    append_images=frames[1:],
    loop=0,
    duration=FRAME_DURATION,
    optimize=False,
    disposal=2,   # 各フレームを描画前にクリア → Discordで自動再生される
)
print(f"GIF生成完了: {out_path}")
