from PIL import Image, ImageDraw, ImageFont

W = H = 1080
MINT = (74, 155, 130)
TINT = (223, 243, 236)
SLATE = (43, 47, 51)
CORAL = (194, 75, 54)
WHITE = (255, 255, 255)

BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

img = Image.new("RGB", (W, H), TINT)
d = ImageDraw.Draw(img)

# headline (top third)
head = ImageFont.truetype(BOLD, 96)
title = "Acid, Actually"
bbox = d.textbbox((0, 0), title, font=head)
tw = bbox[2] - bbox[0]
d.text(((W - tw) / 2, 200), title, font=head, fill=SLATE)

# subtitle (middle) with coral highlight
sub = ImageFont.truetype(REG, 44)
line1 = "Antacids, PPIs, and H2 blockers"
line2 = "do three different things"
line3 = "— here's the difference."
def center(txt, y, font, fill):
    b = d.textbbox((0, 0), txt, font=font)
    d.text(((W - (b[2] - b[0])) / 2, y), txt, font=font, fill=fill)
center(line1, 470, sub, SLATE)
# line2: highlight "three different things" in coral
pre = "do "
hi = "three different things"
pre_w = d.textbbox((0, 0), pre, font=sub)[2]
hi_w = d.textbbox((0, 0), hi, font=sub)[2]
start = (W - (pre_w + hi_w)) / 2
d.text((start, 540), pre, font=sub, fill=SLATE)
d.text((start + pre_w, 540), hi, font=sub, fill=CORAL)
center(line3, 610, sub, SLATE)

# minimal stomach icon + three tick marks (flat line-art)
cx, cy = W // 2, 800
stomach = [(cx - 40, cy - 70), (cx + 40, cy - 50), (cx + 70, cy + 10),
           (cx + 40, cy + 70), (cx - 10, cy + 60), (cx - 30, cy + 10), (cx - 40, cy - 30)]
d.line(stomach + [stomach[0]], fill=SLATE, width=8, joint="curve")
for i, yy in enumerate((cy - 40, cy, cy + 40)):
    d.line([(cx - 120, yy), (cx - 90, yy)], fill=SLATE, width=8)
    d.line([(cx - 90, yy), (cx - 70, yy - 12)], fill=CORAL, width=8)

# logo (bottom-right, 8% margin)
logo = ImageFont.truetype(BOLD, 36)
lg = "Health Lexicon"
b = d.textbbox((0, 0), lg, font=logo)
lw = b[2] - b[0]
margin = int(W * 0.08)
d.text((W - lw - margin, H - margin - 50), lg, font=logo, fill=MINT)

img.save("data/ig_media/ig_acid_actually.png")
print("saved data/ig_media/ig_acid_actually.png", img.size)