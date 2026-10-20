from pathlib import Path

from PIL import Image, ImageDraw

out = Path(__file__).parent / "assets" / "icon.ico"
out.parent.mkdir(exist_ok=True)
img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, 255, 255), 56, fill="#d97757")
d.ellipse((72, 72, 184, 184), fill="white")
img.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
