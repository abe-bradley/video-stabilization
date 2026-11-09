#!/usr/bin/env python3
"""Quantify convergence of the second semantic-stabilization pass.

The comparison deliberately removes motion slower than 0.5 seconds before
reporting jitter.  This separates frame-scale residual error from genuine slow
scene evolution and from the intentional island-to-sun handoff.

Run with no arguments from this directory::

    python compare_feedback.py

The defaults compare the tracks measured on ``sunrise_sun-land`` against the
tracks measured after feeding that video through the stabilizer once more.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
LOCAL_DEPS = HERE / ".deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import gaussian_filter1d  # noqa: E402

import stabilize_sunrise as stabilizer  # noqa: E402


PACKAGED_EXPERIMENT = HERE / "Diagnostics" / "Sun-land 2-pass feedback"
LOCAL_EXPERIMENT = HERE / "analysis_feedback_second_pass"
DEFAULT_EXPERIMENT = (
    PACKAGED_EXPERIMENT if PACKAGED_EXPERIMENT.exists() else LOCAL_EXPERIMENT
)
DEFAULT_BEFORE_TRACKS = DEFAULT_EXPERIMENT / "tracks.csv"
DEFAULT_AFTER_TRACKS = DEFAULT_EXPERIMENT / "QA" / "tracks.csv"
PACKAGED_MEDIA_ROOT = HERE.parent
DEFAULT_BEFORE_VIDEO = (
    PACKAGED_MEDIA_ROOT / "sunrise_sun-land.mp4"
    if (PACKAGED_MEDIA_ROOT / "sunrise_sun-land.mp4").exists()
    else HERE / "Kerkyra_sunrise_stabilized_v2.mp4"
)
DEFAULT_AFTER_VIDEO = (
    PACKAGED_MEDIA_ROOT / "sunrise_sun-land-2pass.mp4"
    if (PACKAGED_MEDIA_ROOT / "sunrise_sun-land-2pass.mp4").exists()
    else HERE / "sunrise_sun-land-2pass.mp4"
)
DEFAULT_JSON = DEFAULT_EXPERIMENT / "feedback_stability_metrics.json"
DEFAULT_PLOT = DEFAULT_EXPERIMENT / "feedback_stability_before_after.png"

FPS = 30.0
OUTPUT_PIXEL_SCALE = 2.0  # detector coordinates are 960x540; video is 1920x1080
DETREND_SIGMA_FRAMES = 15.0
MAX_EDGE_TRIM_FRAMES = 15
EARLY_END = 175
HANDOFF_END = 235
SETTLED_START = 300


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-tracks", type=Path, default=DEFAULT_BEFORE_TRACKS)
    parser.add_argument("--after-tracks", type=Path, default=DEFAULT_AFTER_TRACKS)
    parser.add_argument("--before-video", type=Path, default=DEFAULT_BEFORE_VIDEO)
    parser.add_argument("--after-video", type=Path, default=DEFAULT_AFTER_VIDEO)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-plot", type=Path, default=DEFAULT_PLOT)
    parser.add_argument(
        "--skip-island",
        action="store_true",
        help="Skip the slower early-island phase-correlation video measurement.",
    )
    return parser.parse_args()


def read_tracks(path: Path) -> dict[str, np.ndarray]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"Track CSV is empty: {path}")
    return {
        name: np.asarray([float(row[name]) for row in rows], np.float64)
        for name in rows[0]
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def finite_interpolation(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, np.float64)
    indices = np.arange(values.size)
    good = np.isfinite(values)
    if np.count_nonzero(good) < 2:
        raise RuntimeError("A metric has fewer than two finite measurements")
    return np.interp(indices, indices[good], values[good])


def highpass(values: np.ndarray) -> np.ndarray:
    values = finite_interpolation(values)
    slow = gaussian_filter1d(
        values, sigma=DETREND_SIGMA_FRAMES, mode="nearest"
    )
    return values - slow


def edge_trim_for_length(length: int) -> int:
    """Trim at most 0.5 s, and no more than 20% of a segment per side."""
    return min(MAX_EDGE_TRIM_FRAMES, length // 5)


def trim_edges(values: np.ndarray, trim: int) -> np.ndarray:
    return values[trim:-trim] if trim else values


def scalar_stats(values: np.ndarray, trim: int) -> dict[str, float]:
    values = finite_interpolation(values)
    residual = trim_edges(highpass(values), trim)
    delta = np.diff(values)
    centered = values - np.median(values)
    return {
        "raw_std": float(np.std(values)),
        "median_abs_from_median": float(np.median(np.abs(centered))),
        "p95_abs_from_median": float(np.percentile(np.abs(centered), 95)),
        "highpass_rms": float(np.sqrt(np.mean(residual**2))),
        "highpass_p95_abs": float(np.percentile(np.abs(residual), 95)),
        "highpass_max_abs": float(np.max(np.abs(residual))),
        "frame_delta_rms": float(np.sqrt(np.mean(delta**2))),
        "frame_delta_p95_abs": float(np.percentile(np.abs(delta), 95)),
        "frame_delta_max_abs": float(np.max(np.abs(delta))),
    }


def compare_segment(
    before: np.ndarray,
    after: np.ndarray,
    start: int,
    end: int,
    scale: float,
) -> dict[str, Any]:
    before_values = finite_interpolation(before[start:end]) * scale
    after_values = finite_interpolation(after[start:end]) * scale
    trim = edge_trim_for_length(end - start)
    before_hp = trim_edges(highpass(before_values), trim)
    after_hp = trim_edges(highpass(after_values), trim)
    before_rms = float(np.sqrt(np.mean(before_hp**2)))
    after_rms = float(np.sqrt(np.mean(after_hp**2)))
    gain = float(np.dot(before_hp, after_hp) / np.dot(before_hp, before_hp))
    modeled_noise = after_hp - gain * before_hp
    correlation = float(np.corrcoef(before_hp, after_hp)[0, 1])
    return {
        "frames": [start, end - 1],
        "edge_trim_frames_each_side": trim,
        "before": scalar_stats(before_values, trim),
        "after": scalar_stats(after_values, trim),
        "highpass_rms_ratio_after_over_before": after_rms / before_rms,
        "highpass_rms_reduction_factor": before_rms / after_rms,
        "closed_loop_residual_gain": gain,
        "effective_cancellation_fraction": 1.0 - gain,
        "before_after_highpass_correlation": correlation,
        "modeled_measurement_floor_rms": float(
            np.sqrt(np.mean(modeled_noise**2))
        ),
        "convergent_by_abs_gain_lt_1": abs(gain) < 1.0,
        # Tiny negative gains can occur when the remaining signal is
        # uncorrelated measurement noise.  Reserve "sign reversing" for a
        # negative gain large enough to be dynamically meaningful.
        "gain_numerically_negative": gain < 0.0,
        "meaningfully_sign_reversing_by_gain_lt_minus_0_05": gain < -0.05,
    }


def metric_segments(frame_count: int) -> dict[str, tuple[int, int]]:
    return {
        "early_island": (0, min(EARLY_END, frame_count)),
        "handoff": (min(EARLY_END, frame_count), min(HANDOFF_END, frame_count)),
        "late_sun": (min(HANDOFF_END, frame_count), frame_count),
        "settled_sun": (min(SETTLED_START, frame_count), frame_count),
        "whole_clip": (0, frame_count),
    }


def compare_track_metric(
    before: np.ndarray,
    after: np.ndarray,
    scale: float,
    segment_names: list[str],
) -> dict[str, Any]:
    segments = metric_segments(min(before.size, after.size))
    return {
        name: compare_segment(before, after, *segments[name], scale)
        for name in segment_names
    }


def island_measurement(
    video_path: Path,
    tracks: dict[str, np.ndarray],
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    increments, responses = stabilizer.track_early_island_velocity(
        video_path,
        tracks,
        width=960,
        height=540,
        frame_count=min(650, tracks["frame"].size),
        early_end=EARLY_END,
    )
    increments_output = increments * OUTPUT_PIXEL_SCALE
    velocity_hp = highpass(increments_output)
    position_output = np.r_[0.0, np.cumsum(increments)] * OUTPUT_PIXEL_SCALE
    position_hp = highpass(position_output)
    trim = MAX_EDGE_TRIM_FRAMES
    result = {
        "frames": [0, EARLY_END - 1],
        "edge_trim_frames_each_side_for_highpass": trim,
        "displacement_output_px_per_frame": {
            "rms": float(np.sqrt(np.mean(increments_output**2))),
            "p95_abs": float(np.percentile(np.abs(increments_output), 95)),
            "max_abs": float(np.max(np.abs(increments_output))),
        },
        "highpass_velocity_output_px_per_frame": {
            "rms": float(
                np.sqrt(np.mean(trim_edges(velocity_hp, trim) ** 2))
            ),
            "p95_abs": float(
                np.percentile(np.abs(trim_edges(velocity_hp, trim)), 95)
            ),
        },
        "highpass_integrated_position_output_px": {
            "rms": float(
                np.sqrt(np.mean(trim_edges(position_hp, trim) ** 2))
            ),
            "p95_abs": float(
                np.percentile(np.abs(trim_edges(position_hp, trim)), 95)
            ),
        },
        "phase_correlation_response": {
            "minimum": float(np.min(responses)),
            "median": float(np.median(responses)),
            "p05": float(np.percentile(responses, 5)),
        },
    }
    arrays = {
        "increments": increments_output,
        "velocity_highpass": velocity_hp,
        "position_highpass": position_hp,
    }
    return result, arrays


def compare_island(
    before_video: Path,
    after_video: Path,
    before_tracks: dict[str, np.ndarray],
    after_tracks: dict[str, np.ndarray],
) -> tuple[dict[str, Any], dict[str, dict[str, np.ndarray]]]:
    before, before_arrays = island_measurement(before_video, before_tracks)
    after, after_arrays = island_measurement(after_video, after_tracks)
    comparisons: dict[str, float] = {}
    for key in (
        "displacement_output_px_per_frame",
        "highpass_velocity_output_px_per_frame",
        "highpass_integrated_position_output_px",
    ):
        comparisons[f"{key}_rms_reduction_factor"] = (
            before[key]["rms"] / after[key]["rms"]
        )
        comparisons[f"{key}_p95_reduction_factor"] = (
            before[key]["p95_abs"] / after[key]["p95_abs"]
        )
    before_hp = trim_edges(before_arrays["velocity_highpass"], MAX_EDGE_TRIM_FRAMES)
    after_hp = trim_edges(after_arrays["velocity_highpass"], MAX_EDGE_TRIM_FRAMES)
    gain = float(np.dot(before_hp, after_hp) / np.dot(before_hp, before_hp))
    comparisons.update(
        {
            "highpass_velocity_closed_loop_residual_gain": gain,
            "highpass_velocity_effective_cancellation_fraction": 1.0 - gain,
            "convergent_by_abs_gain_lt_1": abs(gain) < 1.0,
            "gain_numerically_negative": gain < 0.0,
            "meaningfully_sign_reversing_by_gain_lt_minus_0_05": gain < -0.05,
        }
    )
    return (
        {"before": before, "after": after, "comparison": comparisons},
        {"before": before_arrays, "after": after_arrays},
    )


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    unit: str,
    frames: np.ndarray,
    before: np.ndarray,
    after: np.ndarray,
    reduction: float,
    boundaries: tuple[int, ...] = (),
) -> None:
    left, top, right, bottom = box
    axis_left, axis_top = left + 72, top + 54
    axis_right, axis_bottom = right - 22, bottom - 48
    title_font = load_font(22, bold=True)
    small_font = load_font(16)
    tiny_font = load_font(14)
    draw.rounded_rectangle(box, radius=16, fill="#121b27", outline="#2b3c50", width=2)
    draw.text((left + 22, top + 15), title, font=title_font, fill="#f3f7fb")
    draw.text(
        (right - 190, top + 18),
        f"{reduction:.2f}x RMS reduction",
        font=small_font,
        fill="#8ee6b0",
    )

    limit = float(max(np.percentile(np.abs(before), 99), np.percentile(np.abs(after), 99)))
    limit = max(limit, 1e-6) * 1.08
    zero_y = (axis_top + axis_bottom) / 2
    draw.line((axis_left, zero_y, axis_right, zero_y), fill="#526477", width=1)
    draw.rectangle((axis_left, axis_top, axis_right, axis_bottom), outline="#31445a", width=1)
    draw.text((left + 12, axis_top - 10), f"+{limit:.2f}", font=tiny_font, fill="#9baabb")
    draw.text((left + 12, zero_y - 10), "0", font=tiny_font, fill="#9baabb")
    draw.text((left + 12, axis_bottom - 12), f"-{limit:.2f}", font=tiny_font, fill="#9baabb")
    draw.text((left + 15, bottom - 30), unit, font=tiny_font, fill="#9baabb")

    frame_min, frame_max = float(frames[0]), float(frames[-1])

    def x_for(frame: float) -> float:
        return axis_left + (frame - frame_min) / max(frame_max - frame_min, 1) * (
            axis_right - axis_left
        )

    def y_for(value: float) -> float:
        y = zero_y - value / limit * (axis_bottom - axis_top) / 2
        return min(max(y, axis_top), axis_bottom)

    for boundary in boundaries:
        if frame_min <= boundary <= frame_max:
            x = x_for(boundary)
            draw.line((x, axis_top, x, axis_bottom), fill="#45576b", width=1)
            draw.text((x + 4, axis_top + 3), f"f{boundary}", font=tiny_font, fill="#8191a3")

    def points(values: np.ndarray) -> list[tuple[float, float]]:
        return [(x_for(float(frame)), y_for(float(value))) for frame, value in zip(frames, values)]

    draw.line(points(before), fill="#ffad5a", width=2)
    draw.line(points(after), fill="#62d7f2", width=2)
    legend_y = bottom - 21
    before_x = axis_right - 215
    after_x = axis_right - 105
    draw.line((before_x, legend_y, before_x + 25, legend_y), fill="#ffad5a", width=3)
    draw.text((before_x + 32, bottom - 31), "before", font=small_font, fill="#c6d0db")
    draw.line((after_x, legend_y, after_x + 25, legend_y), fill="#62d7f2", width=3)
    draw.text((after_x + 32, bottom - 31), "after", font=small_font, fill="#c6d0db")


def make_plot(
    path: Path,
    before_tracks: dict[str, np.ndarray],
    after_tracks: dict[str, np.ndarray],
    metrics: dict[str, Any],
    island_arrays: dict[str, dict[str, np.ndarray]] | None,
) -> None:
    width, height = 1600, 1120
    image = Image.new("RGB", (width, height), "#09111b")
    draw = ImageDraw.Draw(image)
    title_font = load_font(34, bold=True)
    body_font = load_font(18)
    draw.text((48, 28), "Feedback stabilization: residual jitter before vs after", font=title_font, fill="#f5f8fc")
    draw.text(
        (50, 76),
        "High-pass residuals after Gaussian detrending (sigma 15 frames = 0.50 s). Lower amplitude is better.",
        font=body_font,
        fill="#aab8c8",
    )

    boxes = [
        (42, 120, 785, 545),
        (815, 120, 1558, 545),
        (42, 575, 785, 1000),
        (815, 575, 1558, 1000),
    ]
    frames = np.arange(before_tracks["frame"].size)
    before_roll = highpass(before_tracks["horizon_angle_deg"])
    after_roll = highpass(after_tracks["horizon_angle_deg"])
    before_y = highpass(before_tracks["horizon_level_y"] * OUTPUT_PIXEL_SCALE)
    after_y = highpass(after_tracks["horizon_level_y"] * OUTPUT_PIXEL_SCALE)
    draw_panel(
        draw,
        boxes[0],
        "Horizon roll residual",
        "degrees",
        frames,
        before_roll,
        after_roll,
        metrics["horizon_roll_deg"]["whole_clip"]["highpass_rms_reduction_factor"],
        (EARLY_END, HANDOFF_END),
    )
    draw_panel(
        draw,
        boxes[1],
        "Horizon vertical residual",
        "output pixels",
        frames,
        before_y,
        after_y,
        metrics["horizon_y_output_px"]["whole_clip"]["highpass_rms_reduction_factor"],
        (EARLY_END, HANDOFF_END),
    )

    sun_frames = frames[HANDOFF_END:]
    before_sun = highpass(before_tracks["sun_core_x"][HANDOFF_END:] * OUTPUT_PIXEL_SCALE)
    after_sun = highpass(after_tracks["sun_core_x"][HANDOFF_END:] * OUTPUT_PIXEL_SCALE)
    draw_panel(
        draw,
        boxes[2],
        "Sun horizontal residual (late phase)",
        "output pixels",
        sun_frames,
        before_sun,
        after_sun,
        metrics["sun_x_output_px"]["late_sun"]["highpass_rms_reduction_factor"],
        (SETTLED_START,),
    )

    if island_arrays is not None:
        island_frames = np.arange(EARLY_END)
        before_island = island_arrays["before"]["position_highpass"]
        after_island = island_arrays["after"]["position_highpass"]
        reduction = metrics["early_island_phase_correlation"]["comparison"][
            "highpass_integrated_position_output_px_rms_reduction_factor"
        ]
        draw_panel(
            draw,
            boxes[3],
            "Early island horizontal residual",
            "output pixels",
            island_frames,
            before_island,
            after_island,
            reduction,
        )
    else:
        draw.rounded_rectangle(boxes[3], radius=16, fill="#121b27", outline="#2b3c50", width=2)
        draw.text((boxes[3][0] + 30, boxes[3][1] + 40), "Island measurement skipped", font=title_font, fill="#aab8c8")

    draw.text(
        (48, 1038),
        "Orange: residual measured on sunrise_sun-land.  Blue: residual after one feedback pass.  Vertical markers show handoff phases.",
        font=body_font,
        fill="#aab8c8",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)


def main() -> None:
    args = parse_args()
    before_tracks_path = args.before_tracks.resolve()
    after_tracks_path = args.after_tracks.resolve()
    before_tracks = read_tracks(before_tracks_path)
    after_tracks = read_tracks(after_tracks_path)
    frame_count = min(before_tracks["frame"].size, after_tracks["frame"].size)
    if frame_count < HANDOFF_END + 1:
        raise RuntimeError(f"Expected at least {HANDOFF_END + 1} frames, got {frame_count}")

    all_segments = ["early_island", "handoff", "late_sun", "settled_sun", "whole_clip"]
    metrics: dict[str, Any] = {
        "horizon_roll_deg": compare_track_metric(
            before_tracks["horizon_angle_deg"],
            after_tracks["horizon_angle_deg"],
            1.0,
            all_segments,
        ),
        "horizon_y_output_px": compare_track_metric(
            before_tracks["horizon_level_y"],
            after_tracks["horizon_level_y"],
            OUTPUT_PIXEL_SCALE,
            all_segments,
        ),
        "sun_x_output_px": compare_track_metric(
            before_tracks["sun_core_x"],
            after_tracks["sun_core_x"],
            OUTPUT_PIXEL_SCALE,
            ["handoff", "late_sun", "settled_sun"],
        ),
    }

    island_arrays: dict[str, dict[str, np.ndarray]] | None = None
    if not args.skip_island:
        island, island_arrays = compare_island(
            args.before_video.resolve(),
            args.after_video.resolve(),
            before_tracks,
            after_tracks,
        )
        metrics["early_island_phase_correlation"] = island

    report = {
        "experiment": "one feedback pass: sunrise_sun-land -> sunrise_sun-land-2pass",
        "inputs": {
            "before_tracks": str(before_tracks_path),
            "before_tracks_sha256": sha256(before_tracks_path),
            "after_tracks": str(after_tracks_path),
            "after_tracks_sha256": sha256(after_tracks_path),
            "before_video": str(args.before_video.resolve()),
            "after_video": str(args.after_video.resolve()),
        },
        "methodology": {
            "frame_count": int(frame_count),
            "fps": FPS,
            "analysis_resolution": [960, 540],
            "output_resolution": [1920, 1080],
            "output_pixel_scale_from_analysis": OUTPUT_PIXEL_SCALE,
            "highpass_definition": "track - gaussian_filter1d(track, sigma=15 frames, mode=nearest)",
            "gaussian_detrend_sigma_frames": DETREND_SIGMA_FRAMES,
            "gaussian_detrend_sigma_seconds": DETREND_SIGMA_FRAMES / FPS,
            "edge_trim_rule": "min(15 frames, floor(segment_length/5)) from each side after detrending",
            "segment_frame_convention": "zero-based, inclusive endpoints in JSON",
            "segments": {
                name: [start, end - 1]
                for name, (start, end) in metric_segments(frame_count).items()
            },
            "closed_loop_model": "after_highpass = residual_gain * before_highpass + modeled_measurement_floor",
            "residual_gain_definition": "least-squares dot(before, after) / dot(before, before)",
            "convergence_test": "abs(residual_gain) < 1",
            "meaningful_sign_reversal_test": "residual_gain < -0.05; smaller negative estimates are treated as a measurement-floor correlation consistent with zero",
            "rms_reduction_factor": "before_highpass_rms / after_highpass_rms",
            "island_measurement": "adjacent-frame phase correlation of the leveled, high-passed island band; coordinates reported at 1920x1080 output scale",
        },
        "metrics": metrics,
        "interpretation": {
            "all_reported_closed_loop_metrics_convergent": bool(
                all(
                    segment["convergent_by_abs_gain_lt_1"]
                    for metric in (
                        metrics["horizon_roll_deg"],
                        metrics["horizon_y_output_px"],
                        metrics["sun_x_output_px"],
                    )
                    for segment in metric.values()
                )
            ),
            "meaningful_oscillatory_sign_reversal_observed": bool(
                any(
                    segment["meaningfully_sign_reversing_by_gain_lt_minus_0_05"]
                    for metric in (
                        metrics["horizon_roll_deg"],
                        metrics["horizon_y_output_px"],
                        metrics["sun_x_output_px"],
                    )
                    for segment in metric.values()
                )
            ),
            "recommendation": "Stop after the second pass: the remaining residual is close to the detector/codec floor, so another full-gain pass is more likely to chase measurement noise than to produce a visible improvement.",
        },
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    make_plot(args.output_plot, before_tracks, after_tracks, metrics, island_arrays)
    print(json.dumps({
        "output_json": str(args.output_json.resolve()),
        "output_plot": str(args.output_plot.resolve()),
        "horizon_roll_rms_reduction": metrics["horizon_roll_deg"]["whole_clip"]["highpass_rms_reduction_factor"],
        "horizon_y_rms_reduction": metrics["horizon_y_output_px"]["whole_clip"]["highpass_rms_reduction_factor"],
        "late_sun_rms_reduction": metrics["sun_x_output_px"]["late_sun"]["highpass_rms_reduction_factor"],
        "early_island_displacement_rms_reduction": (
            metrics.get("early_island_phase_correlation", {})
            .get("comparison", {})
            .get("displacement_output_px_per_frame_rms_reduction_factor")
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
