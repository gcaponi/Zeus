from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops


MAX_MEAN_ABSOLUTE_ERROR = 3.0
MAX_SIGNIFICANT_PIXEL_RATIO = 0.08
MAX_PERCEPTUAL_ERROR = 2.5
SIGNIFICANT_CHANNEL_DELTA = 24


@dataclass(frozen=True)
class VisualComparison:
    expected_size: tuple[int, int]
    actual_size: tuple[int, int]
    mean_absolute_error: float
    significant_pixel_ratio: float
    perceptual_error: float

    @property
    def passed(self) -> bool:
        return (
            self.expected_size == self.actual_size
            and self.mean_absolute_error <= MAX_MEAN_ABSOLUTE_ERROR
            and self.significant_pixel_ratio <= MAX_SIGNIFICANT_PIXEL_RATIO
            and self.perceptual_error <= MAX_PERCEPTUAL_ERROR
        )

    def summary(self) -> str:
        return (
            f"size={self.actual_size} expected={self.expected_size}; "
            f"mae={self.mean_absolute_error:.3f}/{MAX_MEAN_ABSOLUTE_ERROR}; "
            "significant_pixels="
            f"{self.significant_pixel_ratio:.3%}/{MAX_SIGNIFICANT_PIXEL_RATIO:.1%}; "
            f"perceptual={self.perceptual_error:.3f}/{MAX_PERCEPTUAL_ERROR}"
        )


def _mean_channel_error(diff: Image.Image) -> float:
    pixels = diff.width * diff.height
    if not pixels:
        return 0.0
    histogram = diff.histogram()
    total_error = sum(
        value * histogram[band_offset + value]
        for band_offset in (0, 256, 512)
        for value in range(256)
    )
    return total_error / (pixels * 3)


def compare_png(expected_png: bytes, actual_png: bytes) -> VisualComparison:
    with Image.open(BytesIO(expected_png)) as expected_source:
        expected = expected_source.convert("RGB")
    with Image.open(BytesIO(actual_png)) as actual_source:
        actual = actual_source.convert("RGB")

    if expected.size != actual.size:
        return VisualComparison(expected.size, actual.size, 255.0, 1.0, 255.0)

    diff = ImageChops.difference(expected, actual)
    mean_absolute_error = _mean_channel_error(diff)
    significant_mask = diff.point(
        lambda value: 255 if value > SIGNIFICANT_CHANNEL_DELTA else 0
    ).convert("L")
    significant_pixels = expected.width * expected.height - significant_mask.histogram()[0]
    significant_pixel_ratio = significant_pixels / (expected.width * expected.height)

    sample_size = (min(128, expected.width), min(128, expected.height))
    expected_sample = expected.resize(sample_size, Image.Resampling.LANCZOS)
    actual_sample = actual.resize(sample_size, Image.Resampling.LANCZOS)
    perceptual_error = _mean_channel_error(ImageChops.difference(expected_sample, actual_sample))

    return VisualComparison(
        expected.size,
        actual.size,
        mean_absolute_error,
        significant_pixel_ratio,
        perceptual_error,
    )


def write_visual_diagnostics(
    artifact_dir: Path,
    name: str,
    expected_png: bytes,
    actual_png: bytes,
    comparison: VisualComparison,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{name}-actual.png").write_bytes(actual_png)
    (artifact_dir / f"{name}-metrics.txt").write_text(
        comparison.summary() + "\n",
        encoding="utf-8",
    )

    if comparison.expected_size != comparison.actual_size:
        return
    with Image.open(BytesIO(expected_png)) as expected_source:
        expected = expected_source.convert("RGB")
    with Image.open(BytesIO(actual_png)) as actual_source:
        actual = actual_source.convert("RGB")
    amplified_diff = ImageChops.difference(expected, actual).point(
        lambda value: min(255, value * 4)
    )
    amplified_diff.save(artifact_dir / f"{name}-diff.png")
