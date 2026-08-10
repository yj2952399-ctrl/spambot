from PIL import Image, ImageDraw, ImageFont
import os

WIDTH, HEIGHT = 800, 600
TEXT = "トイ神の集い"
COLORS = [(220, 30, 30), (30, 60, 200), (255, 255, 255)]
TEXT_COLORS = [(255, 255, 255), (255, 255, 255), (0, 0, 0)]
FRAME_DURATION = 100

def get_font(size):
    try:
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf",
            "/tmp/NotoSansCJK-Bold.otf",
        ]
        for path in font_paths:
            if os.path.exists(path):
                print(f"[make_gif] ✅ フォント: {path}")
                return ImageFont.truetype(path, size=size)
        print("[make_gif] ⚠️ フォントなし → 代替フォント")
        return ImageFont.load_default()
    except Exception as e:
        print(f"[make_gif] ⚠️ フォントエラー: {e}")
        return ImageFont.load_default()

# ✅ 絶対パス：「このファイル自身がある場所」に出力
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
out_path = os.path.join(BASE_DIR, "toykami.gif")

print(f"[make_gif] 📁 自身の場所: {BASE_DIR}")
print(f"[make_gif] 📁 出力先: {out_path}")

frames = []
font = get_font(110)

for bg, fg in zip(COLORS, TEXT_COLORS):
    img = Image.new("RGB", (WIDTH, HEIGHT), color=bg)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (WIDTH - text_w) / 2 - bbox[0]
    y = (HEIGHT - text_h) / 2 - bbox[1]
    draw.text((x + 4, y + 4), TEXT, font=font, fill=(0, 0, 0, 80))
    draw.text((x, y), TEXT, font=font, fill=fg)
    frames.append(img)

try:
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=FRAME_DURATION,
        optimize=False,
        disposal=2,
    )
    print(f"[make_gif] ✅ GIF生成成功: {out_path}")
    print(f"[make_gif] ✅ 存在確認: {os.path.exists(out_path)}")
    print(f"[make_gif] ✅ ファイルサイズ: {os.path.getsize(out_path)} bytes")
except Exception as e:
    print(f"[make_gif] ❌ 保存エラー: {type(e).__name__}: {e}")