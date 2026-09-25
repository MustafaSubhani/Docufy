"""Profile a golden render: py tests/golden/profile_render.py"""

import cProfile
import pstats
from pathlib import Path

from spark_docs.parser import parse_path
from spark_docs.pdf import build_pdf

ROOT = Path(__file__).resolve().parents[2]

meta, blocks = parse_path(ROOT / "tests/golden/adicc.md")
prof = cProfile.Profile()
prof.enable()
build_pdf(meta, blocks, ROOT / "tests/golden/out/speed.pdf")
prof.disable()
pstats.Stats(prof).sort_stats("tottime").print_stats(15)
