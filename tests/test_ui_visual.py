from io import BytesIO

from PIL import Image, ImageDraw

from tests.ui_visual import compare_png


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_visual_comparison_accepts_minor_rendering_noise():
    expected = Image.new("RGB", (100, 100), "#101827")
    actual = expected.copy()
    ImageDraw.Draw(actual).rectangle((0, 0, 19, 19), fill="#243040")

    comparison = compare_png(_png(expected), _png(actual))

    assert comparison.passed
    assert 0 < comparison.significant_pixel_ratio < 0.08


def test_visual_comparison_rejects_material_layout_change():
    expected = Image.new("RGB", (100, 100), "#101827")
    actual = expected.copy()
    ImageDraw.Draw(actual).rectangle((0, 0, 59, 59), fill="#dbeafe")

    comparison = compare_png(_png(expected), _png(actual))

    assert not comparison.passed
    assert comparison.significant_pixel_ratio > 0.08


def test_visual_comparison_rejects_dimension_change():
    expected = Image.new("RGB", (100, 100), "#101827")
    actual = Image.new("RGB", (101, 100), "#101827")

    comparison = compare_png(_png(expected), _png(actual))

    assert not comparison.passed
    assert comparison.expected_size != comparison.actual_size


def test_visual_comparison_accepts_small_full_page_height_delta():
    expected = Image.new("RGB", (100, 112), "#101827")
    actual = Image.new("RGB", (100, 110), "#101827")

    comparison = compare_png(_png(expected), _png(actual))

    assert comparison.passed
    assert comparison.size_compatible


def test_visual_comparison_accepts_bounded_long_page_height_delta():
    expected = Image.new("RGB", (390, 882), "#101827")
    actual = Image.new("RGB", (390, 932), "#101827")

    comparison = compare_png(_png(expected), _png(actual))

    assert comparison.passed
    assert comparison.size_compatible


def test_visual_comparison_rejects_excessive_height_delta():
    expected = Image.new("RGB", (390, 882), "#101827")
    actual = Image.new("RGB", (390, 947), "#101827")

    comparison = compare_png(_png(expected), _png(actual))

    assert not comparison.passed
    assert not comparison.size_compatible
