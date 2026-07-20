"""Sinh icon viên nang (pill) cho PharmaPrice — pill.png + pill.ico.

Chạy 1 lần khi cần vẽ lại icon:  python assets/make_icon.py
Icon phẳng, 2 màu (nửa xanh accent Windows / nửa trắng), nền trong suốt, xoay 40°.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512  # vẽ lớn rồi thu nhỏ cho nét mượt
BLUE = (26, 115, 232, 255)      # #1a73e8 (accent xanh)
WHITE = (245, 247, 250, 255)    # trắng ngà
OUTLINE = (23, 78, 166, 255)    # xanh đậm viền
SEP = (23, 78, 166, 160)        # vạch ngăn 2 nửa


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Capsule nằm ngang, giữa canvas.
    w, h = 340, 150
    x0 = (SIZE - w) // 2
    y0 = (SIZE - h) // 2
    x1, y1 = x0 + w, y0 + h
    r = h // 2
    midx = x0 + w // 2

    # Mask hình capsule.
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)

    # Lớp màu: nửa trái xanh, nửa phải trắng.
    color = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(color)
    cdraw.rectangle([x0, y0, midx, y1], fill=BLUE)
    cdraw.rectangle([midx, y0, x1, y1], fill=WHITE)
    img = Image.composite(color, img, mask)

    draw = ImageDraw.Draw(img)
    # Vạch ngăn giữa 2 nửa.
    draw.line([midx, y0 + 8, midx, y1 - 8], fill=SEP, width=6)
    # Viền capsule.
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=OUTLINE, width=8)

    # Xoay 40° cho giống viên thuốc thật.
    img = img.rotate(40, resample=Image.BICUBIC, expand=False)
    return img


def main() -> None:
    here = Path(__file__).resolve().parent
    icon = build()
    png = icon.resize((256, 256), Image.LANCZOS)
    png.save(here / "pill.png")
    icon.resize((256, 256), Image.LANCZOS).save(
        here / "pill.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("Wrote", here / "pill.png", "and", here / "pill.ico")


if __name__ == "__main__":
    main()
