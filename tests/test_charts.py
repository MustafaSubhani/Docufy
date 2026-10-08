from pathlib import Path

import pytest
from PIL import Image

from spark_agent.imagegen import ImageError, generate_image, openai_payload
from spark_docs.charts import ChartError, render_chart
from spark_docs.parser import parse_markdown


def test_a_bar_chart_is_flat_navy_and_purple_on_white(tmp_path):
    path = render_chart(
        {"kind": "bar", "title": "Quantity", "categories": ["A", "B"], "values": [10, 40]},
        tmp_path / "bars.png",
    )
    image = Image.open(path).convert("RGB")
    assert image.getpixel((0, 0)) == (255, 255, 255)
    colors = set(image.get_flattened_data())
    assert (107, 78, 255) in colors
    assert (26, 26, 46) in colors
    purple = [point for point, color in _samples(image) if color == (107, 78, 255)]
    x, y = purple[len(purple) // 2]
    patch = {image.getpixel((x + dx, y)) for dx in range(-4, 5)}
    assert patch == {(107, 78, 255)}


def test_a_line_and_a_second_series_keep_both_brand_colours(tmp_path):
    path = render_chart(
        {
            "kind": "line",
            "title": "Output",
            "categories": ["Q1", "Q2", "Q3"],
            "series": [
                {"name": "Plan", "values": [10, 20, 30]},
                {"name": "Actual", "values": [12, 18, 28]},
            ],
        },
        tmp_path / "lines.png",
    )
    colors = set(Image.open(path).convert("RGB").get_flattened_data())
    assert (107, 78, 255) in colors
    assert (26, 26, 46) in colors
    with pytest.raises(ChartError):
        render_chart({"kind": "bar", "categories": ["A"], "values": ["many"]}, tmp_path / "bad.png")


def test_a_chart_fence_becomes_an_image(tmp_path):
    _meta, blocks = parse_markdown("```chart\nkind: bar\ntitle: Change\ncategories: [A, B]\nvalues: [4, 8]\n```\n", source_dir=tmp_path)
    assert blocks[0]["kind"] == "image"
    assert Path(blocks[0]["path"]).is_file()


def test_logos_and_diagrams_are_refused_and_a_missing_key_does_not_call_out():
    with pytest.raises(ImageError, match="Logos and diagrams"):
        generate_image("Draw the Spark logo", cache_dir=Path("unused"))
    with pytest.raises(ImageError, match="off until a key"):
        generate_image("a quiet site photograph", provider="openai", cache_dir=Path("unused"))
    payload = openai_payload("a quiet site photograph")
    assert "No gradients" in payload["prompt"]
    assert "#1A1A2E" in payload["prompt"]
    assert "logo" not in payload["prompt"].split("\n\n", 1)[0]


def test_the_same_prompt_is_not_sent_twice(tmp_path):
    calls = {"n": 0}

    def backend(prompt, seed, preset):
        calls["n"] += 1
        return _png(), 0.04

    first = generate_image("a quiet site photograph", provider="openai", seed=7, cache_dir=tmp_path, backend=backend)
    second = generate_image("a quiet site photograph", provider="openai", seed=7, cache_dir=tmp_path, backend=backend)
    assert calls["n"] == 1
    assert first.cached is False
    assert second.cached is True
    assert second.path == first.path
    saved = Path(first.path).with_suffix(".json").read_text(encoding="utf-8")
    assert '"seed": 7' in saved
    assert "provider terms" in saved
    assert "0.04" in saved


def _png() -> bytes:
    from io import BytesIO

    image = Image.new("RGB", (8, 8), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _samples(image: Image.Image):
    width, height = image.size
    for y in range(height):
        for x in range(width):
            yield (x, y), image.getpixel((x, y))
