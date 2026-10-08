"""Render calibration specs next to the reference Claude diagrams.

    py tests/golden/diagram_calibration.py      -> tests/golden/out/diagram_<name>.png
"""

from pathlib import Path

import yaml
from PIL import Image, ImageChops, ImageDraw

from diagrams.render import render_png
from diagrams.spec import Diagram

ROOT = Path(__file__).resolve().parents[2]
PAIRS = {
    "p06_estimation": "p06_1.jpeg",
    "p08_schedule": "p08_1.jpeg",
    "p14_platform": "p14_1.jpeg",
    "p24_architecture": "p24_1.jpeg",
    "p27_pipeline": "p27_1.jpeg",
}


def trim(im: Image.Image) -> Image.Image:
    bg = Image.new("RGB", im.size, (255, 255, 255))
    box = ImageChops.difference(im.convert("RGB"), bg).convert("L").point(lambda v: 255 if v > 18 else 0).getbbox()
    return im.crop(box) if box else im


def main():
    out_dir = ROOT / "tests/golden/out"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, ref_name in PAIRS.items():
        spec = yaml.safe_load((ROOT / f"tests/golden/diagrams/{name}.yaml").read_text(encoding="utf-8"))
        ours = trim(Image.open(render_png(Diagram.from_dict(spec), zoom=3.0)).convert("RGB"))
        ref = trim(Image.open(ROOT / f"tests/golden/assets/{ref_name}").convert("RGB"))
        width = 1000
        a = ref.resize((width, int(ref.height * width / ref.width)))
        b = ours.resize((width, int(ours.height * width / ours.width)))
        sheet = Image.new("RGB", (width * 2 + 30, max(a.height, b.height) + 30), "white")
        sheet.paste(a, (0, 30))
        sheet.paste(b, (width + 30, 30))
        d = ImageDraw.Draw(sheet)
        d.text((4, 6), "Claude reference", fill=(0, 0, 0))
        d.text((width + 34, 6), "Spark diagram engine", fill=(0, 0, 0))
        target = out_dir / f"diagram_{name}.png"
        sheet.save(target)
        print(target)


if __name__ == "__main__":
    main()
