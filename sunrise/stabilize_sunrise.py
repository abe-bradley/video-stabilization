#!/usr/bin/env python3
"""Semantic stabilizer for the Kerkyra sunrise timelapse.

The camera motion is measured from two scene features:
  * the straight, dark-land-to-bright-water boundary (roll and vertical shift)
  * the sun / its vertically averaged reflection (horizontal shift)

The script deliberately separates analysis from rendering.  Run ``analyze`` first,
inspect the CSV/plot/overlay, then run ``render``.  ``all`` performs both passes.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable


HERE = Path(__file__).resolve().parent
LOCAL_DEPS = HERE / ".deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))

import cv2  # type: ignore  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import gaussian_filter1d, median_filter  # noqa: E402


DEFAULT_INPUT = HERE.parent / "sunrise_source.mov"
DEFAULT_ANALYSIS = HERE / "Diagnostics"
DEFAULT_OUTPUT = DEFAULT_INPUT.parent / "sunrise_sun-land.mp4"
TRACKS_CSV = "tracks.csv"
SUMMARY_JSON = "analysis_summary.json"
TRACK_PLOT = "tracking_plot.png"
OVERLAY_MP4 = "detection_overlay.mp4"


def canonical_path(path: Path | str) -> str:
    """Return a case-normalized absolute path for safe identity checks."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def verify_analysis_matches_input(summary: dict, input_path: Path) -> None:
    """Refuse to render transforms measured from a different video."""
    analyzed_input = summary.get("input")
    if not analyzed_input:
        raise RuntimeError("Analysis summary does not identify its input video")
    if canonical_path(analyzed_input) != canonical_path(input_path):
        raise RuntimeError(
            "Analysis/input mismatch: diagnostics were measured from "
            f"{analyzed_input}, but the requested render input is {input_path}"
        )


def gaussian_1d(values: np.ndarray, sigma: float) -> np.ndarray:
    values = np.asarray(values, np.float32).reshape(1, -1)
    return cv2.GaussianBlur(
        values, (0, 0), sigmaX=sigma, sigmaY=0, borderType=cv2.BORDER_REPLICATE
    ).ravel()


def robust_prominence(profile: np.ndarray, peak_index: int) -> float:
    profile = np.asarray(profile, np.float64)
    finite = profile[np.isfinite(profile)]
    if finite.size == 0:
        return 0.0
    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))
    return float((profile[peak_index] - med) / max(1.4826 * mad, 1e-6))


def peak_centroid(profile: np.ndarray, peak: int, radius: int) -> float:
    lo = max(0, peak - radius)
    hi = min(profile.size, peak + radius + 1)
    chunk = np.asarray(profile[lo:hi], np.float64)
    baseline = float(np.percentile(chunk, 15))
    weights = np.maximum(chunk - baseline, 0.0)
    if float(weights.sum()) <= 1e-9:
        return float(peak)
    xs = np.arange(lo, hi, dtype=np.float64)
    return float(np.dot(xs, weights) / weights.sum())


def weighted_line_fit(
    xs: np.ndarray, ys: np.ndarray, strengths: np.ndarray, cx: float
) -> tuple[float, float, np.ndarray]:
    """Huber-style robust fit of y = m * (x-cx) + yc."""
    a = np.column_stack((xs - cx, np.ones_like(xs)))
    weights = np.clip(strengths.astype(np.float64), 1.0, 120.0)
    params = np.linalg.lstsq(a * weights[:, None], ys * weights, rcond=None)[0]
    for _ in range(6):
        residual = ys - a @ params
        scale = max(1.4826 * float(np.median(np.abs(residual))), 0.35)
        huber = np.minimum(1.0, (2.0 * scale) / np.maximum(np.abs(residual), 1e-9))
        w = weights * huber
        params = np.linalg.lstsq(a * w[:, None], ys * w, rcond=None)[0]
    return float(params[0]), float(params[1]), ys - a @ params


def detect_horizon(
    frame: np.ndarray, rng: np.random.Generator
) -> tuple[float, float, float, float, np.ndarray]:
    """Return slope, center-y, confidence, median contrast and signed step map."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    smooth = cv2.GaussianBlur(gray, (0, 0), sigmaX=5.0, sigmaY=1.2)
    vertical_mean = cv2.blur(smooth, (1, 7), borderType=cv2.BORDER_REPLICATE)

    gap = 6
    signed_step = np.zeros_like(vertical_mean)
    signed_step[gap:-gap] = vertical_mean[2 * gap :] - vertical_mean[: -2 * gap]

    y_min = int(round(0.38 * height))
    y_max = int(round(0.70 * height))
    xs = np.arange(3, width - 3, 2, dtype=np.float64)
    xi = xs.astype(np.int32)
    roi = signed_step[y_min:y_max, xi]
    ys = np.argmax(roi, axis=0).astype(np.float64) + y_min
    strengths = signed_step[ys.astype(np.int32), xi].astype(np.float64)

    # RANSAC samples one candidate from each side of the frame.  A genuine
    # waterline is coherent across the image; wave edges are not.
    left = np.flatnonzero(xs < 0.45 * width)
    right = np.flatnonzero(xs > 0.55 * width)
    best_score = -np.inf
    best_m = 0.0
    best_yc = float(np.median(ys))
    cx = (width - 1) / 2.0
    for _ in range(180):
        i = int(rng.choice(left))
        j = int(rng.choice(right))
        m = float((ys[j] - ys[i]) / (xs[j] - xs[i]))
        if abs(m) > 0.22:
            continue
        yc = float(ys[i] - m * (xs[i] - cx))
        residual = np.abs(ys - (m * (xs - cx) + yc))
        inliers = residual < 3.0
        count = int(inliers.sum())
        if count < 20:
            continue
        strength_bonus = float(np.mean(np.clip(strengths[inliers], 0, 120))) / 120.0
        score = count + strength_bonus
        if score > best_score:
            best_score = score
            best_m, best_yc = m, yc

    # Snap each column to its strongest positive transition close to the
    # provisional line, then robustly refine the parameters.
    snap_x = np.arange(4, width - 4, 2, dtype=np.float64)
    snap_y: list[float] = []
    snap_s: list[float] = []
    kept_x: list[float] = []
    for x in snap_x:
        predicted = best_m * (x - cx) + best_yc
        lo = max(gap, int(math.floor(predicted - 7)))
        hi = min(height - gap, int(math.ceil(predicted + 8)))
        if hi <= lo:
            continue
        column = signed_step[lo:hi, int(x)]
        k = int(np.argmax(column))
        kept_x.append(float(x))
        snap_y.append(float(lo + k))
        snap_s.append(float(column[k]))

    fit_x = np.asarray(kept_x, np.float64)
    fit_y = np.asarray(snap_y, np.float64)
    fit_s = np.asarray(snap_s, np.float64)
    m, yc, residual = weighted_line_fit(fit_x, fit_y, fit_s, cx)
    inliers = np.abs(residual) < 3.0
    inlier_ratio = float(np.mean(inliers))
    median_contrast = float(np.median(fit_s[inliers])) if np.any(inliers) else 0.0
    residual_mad = (
        float(np.median(np.abs(residual[inliers]))) if np.any(inliers) else 99.0
    )
    confidence = (
        inlier_ratio
        * min(max(median_contrast / 28.0, 0.0), 1.0)
        * math.exp(-residual_mad / 2.5)
    )
    return m, yc, confidence, median_contrast, signed_step


def rotation_for_horizon(
    width: int, height: int, slope: float, center_y: float
) -> tuple[np.ndarray, float]:
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    angle_deg = math.degrees(math.atan(slope))
    matrix = cv2.getRotationMatrix2D((cx, cy), angle_deg, 1.0)
    endpoints = np.array(
        [
            [0.0, center_y + slope * (0.0 - cx), 1.0],
            [width - 1.0, center_y + slope * (width - 1.0 - cx), 1.0],
        ]
    )
    rotated = (matrix @ endpoints.T).T
    return matrix, float(np.mean(rotated[:, 1]))


def detect_sun_x(
    level_frame: np.ndarray, horizon_y: float
) -> tuple[float, float, float, float, float, int, float, float, float]:
    """Detect the sun x in a horizon-level frame using core/reflection/glow cues."""
    height, width = level_frame.shape[:2]
    b, g, r = cv2.split(level_frame.astype(np.float32))
    luma = 0.114 * b + 0.587 * g + 0.299 * r
    warmth = np.maximum(r - b, 0.0)
    margin = max(8, int(round(0.035 * width)))
    valid_x = slice(margin, width - margin)

    h = int(round(horizon_y))
    sky_top = max(0, h - int(round(0.28 * height)))
    sky_bottom = min(height, h + 4)

    core = np.maximum(luma[sky_top:sky_bottom] - 232.0, 0.0)
    core += 0.7 * np.maximum(r[sky_top:sky_bottom] - 242.0, 0.0)
    core_profile = gaussian_1d(core.sum(axis=0), 2.0)
    core_profile[:margin] = 0
    core_profile[width - margin :] = 0
    core_peak = int(np.argmax(core_profile))
    core_x = peak_centroid(core_profile, core_peak, 12)
    core_prom = robust_prominence(core_profile[valid_x], core_peak - margin)
    core_energy = float(core_profile[core_peak])
    core_area = int(np.count_nonzero(core > 1.0))
    core_energy_threshold = 135.0 * (width / 960.0)

    water_top = min(height - 1, max(0, h + 3))
    water_bottom = min(height, h + int(round(0.40 * height)))
    water_signal = 0.6 * luma[water_top:water_bottom] + 0.8 * warmth[
        water_top:water_bottom
    ]
    water_smooth = cv2.GaussianBlur(
        water_signal, (0, 0), sigmaX=7.0, sigmaY=20.0
    )
    row_baseline = np.median(water_smooth[:, valid_x], axis=1, keepdims=True)
    reflection_residual = np.maximum(water_smooth - row_baseline, 0.0)
    reflection_profile = gaussian_1d(reflection_residual.sum(axis=0), 9.0)
    reflection_profile[:margin] = 0
    reflection_profile[width - margin :] = 0
    reflection_peak = int(np.argmax(reflection_profile))
    reflection_x = peak_centroid(reflection_profile, reflection_peak, 18)
    reflection_prom = robust_prominence(
        reflection_profile[valid_x], reflection_peak - margin
    )

    glow_signal = 0.45 * luma[sky_top:sky_bottom] + 1.15 * warmth[
        sky_top:sky_bottom
    ]
    glow_smooth = cv2.GaussianBlur(glow_signal, (0, 0), sigmaX=15.0, sigmaY=9.0)
    glow_baseline = np.median(glow_smooth[:, valid_x], axis=1, keepdims=True)
    glow_residual = np.maximum(glow_smooth - glow_baseline, 0.0)
    glow_profile = gaussian_1d(glow_residual.sum(axis=0), 18.0)
    glow_profile[:margin] = 0
    glow_profile[width - margin :] = 0
    glow_peak = int(np.argmax(glow_profile))
    glow_x = peak_centroid(glow_profile, glow_peak, 28)
    glow_prom = robust_prominence(glow_profile[valid_x], glow_peak - margin)

    candidates: list[tuple[float, float]] = []
    if core_prom >= 4.0 and core_energy >= core_energy_threshold:
        candidates.append((core_x, 3.0 * min(core_prom, 20.0)))
    if reflection_prom >= 3.0:
        candidates.append((reflection_x, 1.5 * min(reflection_prom, 16.0)))
    if glow_prom >= 2.5:
        candidates.append((glow_x, 0.7 * min(glow_prom, 12.0)))
    if not candidates:
        candidates = [(reflection_x, 1.0), (glow_x, 0.5)]

    # If the compact core is strong, discard unrelated secondary peaks far away.
    if core_prom >= 5.0 and core_energy >= core_energy_threshold:
        near_core = [(x, w) for x, w in candidates if abs(x - core_x) < 0.10 * width]
        if near_core:
            candidates = near_core
    total_weight = sum(weight for _, weight in candidates)
    sun_x = sum(x * weight for x, weight in candidates) / total_weight
    spread = math.sqrt(
        sum(weight * (x - sun_x) ** 2 for x, weight in candidates) / total_weight
    )
    cue_strength = max(core_prom, 0.7 * reflection_prom, 0.5 * glow_prom)
    agreement = math.exp(-spread / max(0.035 * width, 1.0))
    confidence = float(min(cue_strength / 8.0, 1.0) * agreement)
    return (
        float(sun_x),
        confidence,
        float(core_x),
        float(core_prom),
        core_energy,
        core_area,
        float(reflection_x),
        float(reflection_prom),
        float(glow_x),
    )


def affine_point(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    result = matrix @ np.array([x, y, 1.0], np.float64)
    return float(result[0]), float(result[1])


def write_overlay_frame(
    frame: np.ndarray,
    frame_index: int,
    slope: float,
    center_y: float,
    sun_x_level: float,
    h_conf: float,
    s_conf: float,
) -> np.ndarray:
    height, width = frame.shape[:2]
    cx = (width - 1) / 2.0
    y0 = int(round(center_y + slope * (0 - cx)))
    y1 = int(round(center_y + slope * ((width - 1) - cx)))
    overlay = frame.copy()
    cv2.line(overlay, (0, y0), (width - 1, y1), (60, 255, 60), 2, cv2.LINE_AA)
    matrix, horizon_level_y = rotation_for_horizon(width, height, slope, center_y)
    inv = cv2.invertAffineTransform(matrix)
    sun_raw_x, sun_raw_y = affine_point(inv, sun_x_level, horizon_level_y)
    cv2.circle(
        overlay,
        (int(round(sun_raw_x)), int(round(sun_raw_y))),
        10,
        (0, 220, 255),
        2,
        cv2.LINE_AA,
    )
    text = (
        f"frame {frame_index:04d}   roll {math.degrees(math.atan(slope)):+.3f} deg   "
        f"h {h_conf:.2f}   sun {s_conf:.2f}"
    )
    cv2.putText(
        overlay,
        text,
        (14, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        text,
        (14, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (15, 15, 15),
        1,
        cv2.LINE_AA,
    )
    return overlay


def ffmpeg_raw_writer(
    output: Path, width: int, height: int, fps: float, crf: int, preset: str
) -> subprocess.Popen[bytes]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s:v",
        f"{width}x{height}",
        "-r",
        f"{fps:.12g}",
        "-i",
        "-",
        "-an",
        "-vf",
        "scale=in_range=pc:out_range=pc,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-color_range",
        "pc",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE)


TRACK_FIELDS = [
    "frame",
    "time_s",
    "horizon_slope",
    "horizon_angle_deg",
    "horizon_center_y",
    "horizon_level_y",
    "horizon_confidence",
    "horizon_contrast",
    "sun_x_level",
    "sun_confidence",
    "sun_core_x",
    "sun_core_prominence",
    "sun_core_energy",
    "sun_core_area",
    "reflection_x",
    "reflection_prominence",
    "glow_x",
]


def analyze_video(
    input_path: Path,
    analysis_dir: Path,
    end_frame: int | None,
    analysis_width: int,
) -> dict:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open input video: {input_path}")
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if end_frame is None:
        end_frame = source_frames
    end_frame = min(end_frame, source_frames)
    analysis_height = int(round(source_height * analysis_width / source_width))
    if analysis_height % 2:
        analysis_height += 1

    overlay_path = analysis_dir / OVERLAY_MP4
    overlay_writer = ffmpeg_raw_writer(
        overlay_path, analysis_width, analysis_height, fps, crf=18, preset="medium"
    )
    rng = np.random.default_rng(20260812)
    rows: list[dict[str, float | int]] = []

    for frame_index in range(end_frame):
        ok, full_frame = capture.read()
        if not ok:
            break
        frame = cv2.resize(
            full_frame, (analysis_width, analysis_height), interpolation=cv2.INTER_AREA
        )
        slope, center_y, h_conf, h_contrast, _ = detect_horizon(frame, rng)
        rotation, horizon_level_y = rotation_for_horizon(
            analysis_width, analysis_height, slope, center_y
        )
        level = cv2.warpAffine(
            frame,
            rotation,
            (analysis_width, analysis_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        (
            sun_x,
            s_conf,
            core_x,
            core_prom,
            core_energy,
            core_area,
            reflection_x,
            reflection_prom,
            glow_x,
        ) = detect_sun_x(level, horizon_level_y)
        row: dict[str, float | int] = {
            "frame": frame_index,
            "time_s": frame_index / fps,
            "horizon_slope": slope,
            "horizon_angle_deg": math.degrees(math.atan(slope)),
            "horizon_center_y": center_y,
            "horizon_level_y": horizon_level_y,
            "horizon_confidence": h_conf,
            "horizon_contrast": h_contrast,
            "sun_x_level": sun_x,
            "sun_confidence": s_conf,
            "sun_core_x": core_x,
            "sun_core_prominence": core_prom,
            "sun_core_energy": core_energy,
            "sun_core_area": core_area,
            "reflection_x": reflection_x,
            "reflection_prominence": reflection_prom,
            "glow_x": glow_x,
        }
        rows.append(row)
        overlay = write_overlay_frame(
            frame, frame_index, slope, center_y, sun_x, h_conf, s_conf
        )
        assert overlay_writer.stdin is not None
        overlay_writer.stdin.write(overlay.tobytes())
        if (frame_index + 1) % 100 == 0 or frame_index + 1 == end_frame:
            print(f"Analyzed {frame_index + 1}/{end_frame} frames", flush=True)

    capture.release()
    assert overlay_writer.stdin is not None
    overlay_writer.stdin.close()
    overlay_status = overlay_writer.wait()
    if overlay_status != 0:
        raise RuntimeError(f"FFmpeg overlay encoder exited with {overlay_status}")
    if not rows:
        raise RuntimeError("No frames were decoded")

    csv_path = analysis_dir / TRACKS_CSV
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    values = {key: np.array([float(row[key]) for row in rows]) for key in TRACK_FIELDS[2:]}
    summary = {
        "input": str(input_path),
        "source_width": source_width,
        "source_height": source_height,
        "analysis_width": analysis_width,
        "analysis_height": analysis_height,
        "fps": fps,
        "source_frames": source_frames,
        "analyzed_frames": len(rows),
        "first_excluded_frame": end_frame,
        "horizon_angle_range_deg": [
            float(values["horizon_angle_deg"].min()),
            float(values["horizon_angle_deg"].max()),
        ],
        "horizon_y_range": [
            float(values["horizon_level_y"].min()),
            float(values["horizon_level_y"].max()),
        ],
        "sun_x_range": [
            float(values["sun_x_level"].min()),
            float(values["sun_x_level"].max()),
        ],
        "median_horizon_confidence": float(np.median(values["horizon_confidence"])),
        "median_sun_confidence": float(np.median(values["sun_confidence"])),
        "median_horizon_contrast": float(np.median(values["horizon_contrast"])),
    }
    (analysis_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    draw_tracking_plot(rows, analysis_dir / TRACK_PLOT, analysis_width)
    print(json.dumps(summary, indent=2))
    return summary


def draw_tracking_plot(rows: list[dict], output: Path, analysis_width: int) -> None:
    canvas_w, panel_h, panels = 1400, 220, 4
    canvas_h = panel_h * panels
    image = Image.new("RGB", (canvas_w, canvas_h), "#11151a")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    series = [
        ("Horizon angle (degrees)", "horizon_angle_deg", "#64d98b"),
        ("Level horizon y (analysis pixels)", "horizon_level_y", "#5eb8ff"),
        ("Sun x after leveling (analysis pixels)", "sun_x_level", "#ffbf47"),
        ("Confidence: horizon green, sun orange", None, "#ffffff"),
    ]
    left, right = 70, canvas_w - 25
    for panel, (title, key, color) in enumerate(series):
        top = panel * panel_h
        bottom = top + panel_h - 25
        draw.rectangle((left, top + 25, right, bottom), outline="#3c4652")
        draw.text((12, top + 7), title, fill="#e8edf2", font=font)
        xs = np.linspace(left, right, len(rows))
        if key is not None:
            vals = np.array([float(row[key]) for row in rows])
            lo, hi = np.percentile(vals, [0.5, 99.5])
            if hi - lo < 1e-8:
                hi = lo + 1
            pad = 0.08 * (hi - lo)
            lo, hi = float(lo - pad), float(hi + pad)
            ys = bottom - (vals - lo) / (hi - lo) * (bottom - (top + 25))
            points = list(zip(xs.tolist(), ys.tolist()))
            draw.line(points, fill=color, width=2)
            draw.text((right - 150, top + 7), f"{lo:.2f} .. {hi:.2f}", fill=color, font=font)
        else:
            for conf_key, conf_color in [
                ("horizon_confidence", "#64d98b"),
                ("sun_confidence", "#ffbf47"),
            ]:
                vals = np.array([float(row[conf_key]) for row in rows])
                ys = bottom - vals * (bottom - (top + 25))
                draw.line(list(zip(xs.tolist(), ys.tolist())), fill=conf_color, width=2)
    image.save(output)


def load_tracks(csv_path: Path) -> dict[str, np.ndarray]:
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"Track CSV is empty: {csv_path}")
    return {
        field: np.array([float(row[field]) for row in rows], np.float64)
        for field in TRACK_FIELDS
    }


def interpolate_bad(values: np.ndarray, good: np.ndarray) -> np.ndarray:
    indices = np.arange(values.size)
    if int(good.sum()) < 2:
        raise RuntimeError("Too few reliable detections to interpolate")
    result = values.copy()
    result[~good] = np.interp(indices[~good], indices[good], values[good])
    return result


def track_early_island_skyline(
    input_path: Path,
    tracks: dict[str, np.ndarray],
    width: int,
    height: int,
    frame_count: int,
    early_end: int = 175,
    reference_frame: int = 100,
) -> tuple[np.ndarray, np.ndarray]:
    """Track the distant island silhouette before the solar disk is visible.

    The leveled sky-to-land edge is converted to a binary silhouette. A wide,
    distinctive central/right template is matched in every early frame. This
    gives a much sharper horizontal motion carrier than the broad glow/reflection.
    """
    if not (0 <= reference_frame < early_end <= frame_count):
        raise ValueError("Invalid early island tracking frame range")
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open input video: {input_path}")
    silhouettes: list[np.ndarray] = []
    for frame_index in range(early_end):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Decode failed at island frame {frame_index}")
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        rotation, _ = rotation_for_horizon(
            width,
            height,
            float(tracks["horizon_slope"][frame_index]),
            float(tracks["horizon_center_y"][frame_index]),
        )
        level = cv2.warpAffine(
            frame,
            rotation,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        gray = cv2.cvtColor(level, cv2.COLOR_BGR2GRAY).astype(np.float32)
        smooth = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.0, sigmaY=1.0)
        mean_y = cv2.blur(smooth, (1, 5), borderType=cv2.BORDER_REPLICATE)
        step = np.zeros_like(mean_y)
        step[4:-4] = mean_y[8:] - mean_y[:-8]
        horizon_y = int(round(tracks["horizon_level_y"][frame_index]))
        top = max(0, horizon_y - 70)
        bottom = min(height, horizon_y - 2)
        skyline = np.argmin(step[top:bottom], axis=0) + top
        skyline = median_filter(skyline, size=7, mode="nearest")
        silhouette = (
            np.arange(height, dtype=np.int32)[:, None] >= skyline[None, :]
        ).astype(np.uint8) * 255
        silhouettes.append(silhouette)
    capture.release()

    # This wide template includes several unmistakable mountain features and
    # avoids the warm central glow. Coordinates are ratios of the 960x540
    # analysis canvas so alternate but same-aspect resolutions still work.
    template_x0 = int(round(500 / 960 * width))
    template_x1 = int(round(880 / 960 * width))
    template_y0 = int(round(180 / 540 * height))
    template_y1 = int(round(350 / 540 * height))
    template = silhouettes[reference_frame][
        template_y0:template_y1, template_x0:template_x1
    ]
    positions = np.empty(early_end, np.float64)
    scores = np.empty(early_end, np.float64)
    search_left = int(round(120 / 960 * width))
    search_right = int(round(80 / 960 * width))
    search_y = int(round(22 / 540 * height))
    roi_x0 = max(0, template_x0 - search_left)
    roi_x1 = min(width, template_x1 + search_right)
    roi_y0 = max(0, template_y0 - search_y)
    roi_y1 = min(height, template_y1 + search_y)
    for frame_index, silhouette in enumerate(silhouettes):
        roi = silhouette[roi_y0:roi_y1, roi_x0:roi_x1]
        response = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(response)
        peak_x, peak_y = location
        position = float(roi_x0 + peak_x)
        # Subpixel parabola through the horizontal correlation peak.
        if 0 < peak_x < response.shape[1] - 1:
            left = float(response[peak_y, peak_x - 1])
            center = float(response[peak_y, peak_x])
            right = float(response[peak_y, peak_x + 1])
            denominator = left - 2.0 * center + right
            if abs(denominator) > 1e-9:
                position += 0.5 * (left - right) / denominator
        positions[frame_index] = position
        scores[frame_index] = float(score)
    return positions, scores


def track_early_island_velocity(
    input_path: Path,
    tracks: dict[str, np.ndarray],
    width: int,
    height: int,
    frame_count: int,
    early_end: int = 175,
) -> tuple[np.ndarray, np.ndarray]:
    """Measure early horizontal camera velocity by phase correlation.

    The whole high-passed island band is correlated between adjacent leveled
    frames. Integrating only the low-pass velocity component yields a stable
    quasi-fixed reference without relying on an absolute repeating silhouette.
    """
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open input video: {input_path}")
    x0 = int(round(20 / 960 * width))
    x1 = int(round(940 / 960 * width))
    band_top = int(round(120 / 540 * height))
    band_bottom = int(round(5 / 540 * height))
    band_height = band_top - band_bottom
    window = cv2.createHanningWindow((x1 - x0, band_height), cv2.CV_32F)
    previous: np.ndarray | None = None
    increments: list[float] = []
    responses: list[float] = []
    for frame_index in range(early_end):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Decode failed at island frame {frame_index}")
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        rotation, _ = rotation_for_horizon(
            width,
            height,
            float(tracks["horizon_slope"][frame_index]),
            float(tracks["horizon_center_y"][frame_index]),
        )
        level = cv2.warpAffine(
            frame,
            rotation,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        gray = cv2.cvtColor(level, cv2.COLOR_BGR2GRAY).astype(np.float32)
        horizon_y = int(round(tracks["horizon_level_y"][frame_index]))
        band = gray[
            horizon_y - band_top : horizon_y - band_bottom,
            x0:x1,
        ]
        band -= cv2.GaussianBlur(band, (0, 0), sigmaX=20.0, sigmaY=0.0)
        if previous is not None:
            (dx, _), response = cv2.phaseCorrelate(
                previous * window, band * window
            )
            increments.append(float(dx))
            responses.append(float(response))
        previous = band
    capture.release()
    return np.asarray(increments), np.asarray(responses)


def build_hybrid_horizontal_anchor(
    broad_anchor: np.ndarray,
    sun_core_anchor: np.ndarray,
    island_position: np.ndarray,
    frame_count: int,
    early_end: int = 175,
    blend_end: int = 235,
    drift_sigma_frames: float = 15.0,
) -> np.ndarray:
    """Fuse a sharp island carrier with slow sun/glow composition.

    `island + lowpass(broad_sun - island)` preserves the slowly changing
    sun-versus-land geometry but removes the broad detector's frame jitter.
    A raised-cosine handoff then transfers control to the compact solar core.
    """
    if island_position.size != early_end:
        raise ValueError("Island position length does not match early_end")
    early_broad = broad_anchor[:early_end]
    slow_offset = gaussian_filter1d(
        early_broad - island_position,
        sigma=drift_sigma_frames,
        mode="nearest",
    )
    early_hybrid = island_position + slow_offset
    anchor = sun_core_anchor[:frame_count].copy()
    anchor[:early_end] = early_hybrid
    blend_end = min(blend_end, frame_count)
    if blend_end > early_end:
        # Continue the early carrier through the blend using its terminal
        # velocity; its influence falls smoothly to zero within one second.
        terminal_velocity = float(np.median(np.diff(early_hybrid[-12:])))
        indices = np.arange(early_end, blend_end)
        continuation = early_hybrid[-1] + terminal_velocity * (
            indices - (early_end - 1)
        )
        phase = (indices - early_end + 1) / (blend_end - early_end + 1)
        sun_weight = 0.5 - 0.5 * np.cos(np.pi * phase)
        anchor[indices] = (
            (1.0 - sun_weight) * continuation
            + sun_weight * sun_core_anchor[indices]
        )
    return anchor


def build_velocity_hybrid_anchor(
    broad_anchor: np.ndarray,
    sun_core_anchor: np.ndarray,
    island_increments: np.ndarray,
    frame_count: int,
    early_end: int = 175,
    blend_end: int = 235,
    velocity_sigma_frames: float = 0.35,
) -> np.ndarray:
    """Use island velocity for the pre-sun phase, then hand off to the disk."""
    if island_increments.size != early_end - 1:
        raise ValueError("Island increment length does not match early_end")
    smooth_velocity = gaussian_filter1d(
        island_increments,
        sigma=velocity_sigma_frames,
        mode="nearest",
    )
    early_track = np.r_[0.0, np.cumsum(smooth_velocity)]
    # Absolute composition comes from the broad sun cue; the island contributes
    # only the precise short-term velocity. A single offset makes them meet.
    early_track += float(
        np.median(broad_anchor[early_end - 30 : early_end] - early_track[-30:])
    )
    anchor = sun_core_anchor[:frame_count].copy()
    anchor[:early_end] = early_track
    blend_end = min(blend_end, frame_count)
    if blend_end > early_end:
        indices = np.arange(early_end, blend_end)
        # Interpolate the remaining low-frequency offset to the sun-core path.
        # This makes both the positional and velocity transition gentle even
        # when the boat changes direction near emergence.
        source_offset = early_track[-1] - sun_core_anchor[early_end - 1]
        continuation = sun_core_anchor[indices] + source_offset
        phase = (indices - early_end + 1) / (blend_end - early_end + 1)
        sun_weight = 0.5 - 0.5 * np.cos(np.pi * phase)
        anchor[indices] = (
            (1.0 - sun_weight) * continuation
            + sun_weight * sun_core_anchor[indices]
        )
    return anchor


def build_transforms(
    tracks: dict[str, np.ndarray], width: int, height: int
) -> tuple[list[np.ndarray], dict]:
    # These thresholds only replace demonstrably weak fits.  Genuine abrupt
    # boat motion is retained rather than temporally smoothed away.
    h_good = (
        (tracks["horizon_confidence"] >= 0.48)
        & (tracks["horizon_contrast"] >= 15.0)
        & (np.abs(tracks["horizon_angle_deg"]) <= 13.0)
    )
    core_good = (
        np.isfinite(tracks["sun_core_x"])
        & (tracks["sun_core_x"] >= 0.035 * width)
        & (tracks["sun_core_x"] <= 0.965 * width)
        & (tracks["sun_core_prominence"] >= 4.0)
        & (tracks["sun_core_energy"] >= 135.0 * (width / 960.0))
    )
    s_good = core_good | (tracks["sun_confidence"] >= 0.28)
    slope = interpolate_bad(tracks["horizon_slope"], h_good)
    center_y = interpolate_bad(tracks["horizon_center_y"], h_good)
    # In this particular clip the compact saturated core is present in every
    # sunrise frame and is a cleaner positional anchor than a weighted blend
    # with the wider reflection/glow.  Retain those other cues as fallbacks.
    sun_anchor = np.where(core_good, tracks["sun_core_x"], tracks["sun_x_level"])
    sun_x_level = interpolate_bad(sun_anchor, s_good)

    base_matrices: list[np.ndarray] = []
    level_ys: list[float] = []
    for m, yc in zip(slope, center_y):
        matrix, level_y = rotation_for_horizon(width, height, float(m), float(yc))
        base_matrices.append(matrix)
        level_ys.append(level_y)
    level_ys_array = np.asarray(level_ys)

    target_horizon_y = float(np.median(level_ys_array))
    target_sun_x = float(np.median(sun_x_level))
    transforms: list[np.ndarray] = []
    for matrix, level_y, sun_x in zip(base_matrices, level_ys_array, sun_x_level):
        result = matrix.copy()
        result[0, 2] += target_sun_x - float(sun_x)
        result[1, 2] += target_horizon_y - float(level_y)
        transforms.append(result)
    info = {
        "target_horizon_y": target_horizon_y,
        "target_sun_x": target_sun_x,
        "sun_anchor": "compact core with reflection/glow fallback",
        "replaced_horizon_frames": np.flatnonzero(~h_good).astype(int).tolist(),
        "replaced_sun_frames": np.flatnonzero(~s_good).astype(int).tolist(),
        "sun_fallback_frames": np.flatnonzero(~core_good).astype(int).tolist(),
    }
    return transforms, info


def common_valid_mask(
    transforms: Iterable[np.ndarray], width: int, height: int, inset: int = 3
) -> np.ndarray:
    mask = np.ones((height, width), np.uint8)
    source_corners = np.array(
        [
            [inset, inset],
            [width - 1 - inset, inset],
            [width - 1 - inset, height - 1 - inset],
            [inset, height - 1 - inset],
        ],
        np.float32,
    ).reshape(-1, 1, 2)
    for matrix in transforms:
        corners = cv2.transform(source_corners, matrix).reshape(-1, 2)
        polygon = np.zeros_like(mask)
        cv2.fillConvexPoly(polygon, np.round(corners).astype(np.int32), 1)
        mask &= polygon
    return mask


def largest_safe_16_9_crop(
    mask: np.ndarray, safety_steps: int = 1
) -> tuple[int, int, int, int]:
    height, width = mask.shape
    # OpenCV 5's integral implementation accepts an 8-bit source here, not
    # an already-promoted int32 array.
    invalid = (mask == 0).astype(np.uint8)
    integral = cv2.integral(invalid, sdepth=cv2.CV_32S)
    chosen: tuple[int, int, int, int] | None = None
    for crop_h in range(height - (height % 9), 80, -9):
        crop_w = crop_h * 16 // 9
        if crop_w > width:
            continue
        sums = (
            integral[crop_h:, crop_w:]
            - integral[:-crop_h, crop_w:]
            - integral[crop_h:, :-crop_w]
            + integral[:-crop_h, :-crop_w]
        )
        candidates = np.argwhere(sums == 0)
        if candidates.size:
            centers_y = candidates[:, 0] + crop_h / 2
            centers_x = candidates[:, 1] + crop_w / 2
            distances = (centers_x - width / 2) ** 2 + (centers_y - height / 2) ** 2
            best = candidates[int(np.argmin(distances))]
            chosen = (int(best[1]), int(best[0]), crop_w, crop_h)
            break
    if chosen is None:
        raise RuntimeError("Could not find a common 16:9 crop")

    # One grid step of headroom prevents interpolation from grazing the border.
    x, y, crop_w, crop_h = chosen
    if crop_h >= 180 and safety_steps > 0:
        safe_h = crop_h - 9 * safety_steps
        safe_w = crop_w - 16 * safety_steps
        x += (crop_w - safe_w) // 2
        y += (crop_h - safe_h) // 2
        crop_w, crop_h = safe_w, safe_h
    return x, y, crop_w, crop_h


def homogeneous(matrix: np.ndarray) -> np.ndarray:
    result = np.eye(3, dtype=np.float64)
    result[:2] = matrix
    return result


def crop_to_canvas_matrix(
    crop: tuple[int, int, int, int], width: int, height: int
) -> np.ndarray:
    x, y, crop_w, crop_h = crop
    scale_x = width / crop_w
    scale_y = height / crop_h
    if abs(scale_x - scale_y) > 1e-9:
        raise RuntimeError("Crop and canvas aspect ratios differ")
    return np.array(
        [[scale_x, 0.0, -scale_x * x], [0.0, scale_y, -scale_y * y], [0, 0, 1]],
        np.float64,
    )


def build_qa_corrections(
    qa_tracks: dict[str, np.ndarray], width: int, height: int, sun_visible_from: int
) -> tuple[list[np.ndarray], dict]:
    """Small empirical correction measured on a first-pass render.

    Horizon corrections are valid throughout.  Horizontal corrections are only
    applied once the compact solar disk is visible; before that, the already-fixed
    reflection/glow anchor is retained rather than chasing water texture.
    """
    h_good = (
        (qa_tracks["horizon_confidence"] >= 0.48)
        & (qa_tracks["horizon_contrast"] >= 15.0)
        & (np.abs(qa_tracks["horizon_angle_deg"]) <= 2.0)
    )
    slope = interpolate_bad(qa_tracks["horizon_slope"], h_good)
    center_y = interpolate_bad(qa_tracks["horizon_center_y"], h_good)

    frame_indices = np.arange(slope.size)
    visible = frame_indices >= sun_visible_from
    core_good = (
        visible
        & np.isfinite(qa_tracks["sun_core_x"])
        & (qa_tracks["sun_core_x"] >= 0.035 * width)
        & (qa_tracks["sun_core_x"] <= 0.965 * width)
        & (qa_tracks["sun_core_energy"] >= 70.0 * (width / 960.0))
    )
    core_x = qa_tracks["sun_core_x"].copy()
    if np.any(visible):
        # Interpolate brief low-energy frames only within the visible-disk phase.
        visible_indices = np.flatnonzero(visible)
        good_indices = np.flatnonzero(core_good)
        if good_indices.size < 2:
            raise RuntimeError("Too few visible sun-core detections in QA pass")
        core_x[visible_indices] = np.interp(
            visible_indices, good_indices, core_x[good_indices]
        )
    target_core_x = float(np.median(core_x[visible]))

    rotations: list[np.ndarray] = []
    level_y_values: list[float] = []
    for m, yc in zip(slope, center_y):
        matrix, level_y = rotation_for_horizon(width, height, float(m), float(yc))
        rotations.append(matrix)
        level_y_values.append(level_y)
    target_horizon_y = float(np.median(level_y_values))

    corrections: list[np.ndarray] = []
    for index, (matrix, level_y) in enumerate(zip(rotations, level_y_values)):
        correction = matrix.copy()
        correction[1, 2] += target_horizon_y - level_y
        if index >= sun_visible_from:
            correction[0, 2] += target_core_x - core_x[index]
        corrections.append(correction)
    return corrections, {
        "qa_target_horizon_y": target_horizon_y,
        "qa_target_visible_sun_x": target_core_x,
        "qa_sun_visible_from_frame": int(sun_visible_from),
        "qa_replaced_horizon_frames": np.flatnonzero(~h_good).astype(int).tolist(),
        "qa_interpolated_core_frames": np.flatnonzero(visible & ~core_good)
        .astype(int)
        .tolist(),
    }


def render_direct_affines(
    input_path: Path,
    output_path: Path,
    affines_small: list[np.ndarray],
    analysis_width: int,
    analysis_height: int,
    source_width: int,
    source_height: int,
    fps: float,
    crf: int,
    preset: str,
) -> None:
    scale_x = source_width / analysis_width
    scale_y = source_height / analysis_height
    if abs(scale_x - scale_y) > 1e-6:
        raise RuntimeError("Analysis and source aspect ratios differ")
    writer = ffmpeg_raw_writer(output_path, source_width, source_height, fps, crf, preset)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not reopen input video: {input_path}")
    for frame_index, small in enumerate(affines_small):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Decode failed at frame {frame_index}")
        full = small.copy()
        full[:, 2] *= scale_x
        output_frame = cv2.warpAffine(
            frame,
            full,
            (source_width, source_height),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        assert writer.stdin is not None
        writer.stdin.write(output_frame.tobytes())
        if (frame_index + 1) % 100 == 0 or frame_index + 1 == len(affines_small):
            print(f"Refined {frame_index + 1}/{len(affines_small)} frames", flush=True)
    capture.release()
    assert writer.stdin is not None
    writer.stdin.close()
    status = writer.wait()
    if status != 0:
        raise RuntimeError(f"FFmpeg refined encoder exited with {status}")


def refine_video(
    input_path: Path,
    analysis_dir: Path,
    qa_dir: Path,
    output_path: Path,
    crf: int,
    preset: str,
) -> dict:
    summary = json.loads((analysis_dir / SUMMARY_JSON).read_text(encoding="utf-8"))
    verify_analysis_matches_input(summary, input_path)
    tracks = load_tracks(analysis_dir / TRACKS_CSV)
    qa_tracks = load_tracks(qa_dir / TRACKS_CSV)
    width = int(summary["analysis_width"])
    height = int(summary["analysis_height"])
    source_w = int(summary["source_width"])
    source_h = int(summary["source_height"])
    fps = float(summary["fps"])

    source_transforms, source_info = build_transforms(tracks, width, height)
    source_mask = common_valid_mask(source_transforms, width, height)
    first_crop = largest_safe_16_9_crop(source_mask)
    crop_matrix = crop_to_canvas_matrix(first_crop, width, height)

    source_core_visible = (
        tracks["sun_core_energy"] >= 135.0 * (width / 960.0)
    )
    visible_indices = np.flatnonzero(source_core_visible)
    if visible_indices.size == 0:
        raise RuntimeError("No visible compact sun core in source analysis")
    sun_visible_from = int(visible_indices[0])
    corrections, correction_info = build_qa_corrections(
        qa_tracks, width, height, sun_visible_from
    )

    composed: list[np.ndarray] = []
    for source_matrix, correction in zip(source_transforms, corrections):
        combined = homogeneous(correction) @ crop_matrix @ homogeneous(source_matrix)
        composed.append(combined[:2])

    # Re-establish a common safe canvas after the small empirical correction.
    corrected_mask = common_valid_mask(composed, width, height)
    second_crop = largest_safe_16_9_crop(corrected_mask, safety_steps=1)
    final_crop_matrix = crop_to_canvas_matrix(second_crop, width, height)
    final_affines = [(final_crop_matrix @ homogeneous(m))[:2] for m in composed]
    final_mask = common_valid_mask(final_affines, width, height)
    invalid_pixels = int(np.count_nonzero(final_mask == 0))
    if invalid_pixels:
        raise RuntimeError(
            f"Refined transform leaves {invalid_pixels} common invalid pixels"
        )

    render_direct_affines(
        input_path,
        output_path,
        final_affines,
        width,
        height,
        source_w,
        source_h,
        fps,
        crf,
        preset,
    )
    total_zoom = (width / first_crop[2]) * (width / second_crop[2])
    result = {
        **source_info,
        **correction_info,
        "output": str(output_path),
        "frames": len(final_affines),
        "duration_s": len(final_affines) / fps,
        "first_crop_analysis": list(first_crop),
        "refinement_crop_analysis": list(second_crop),
        "total_zoom_factor": total_zoom,
        "single_resampling_from_original": True,
        "crf": crf,
        "preset": preset,
    }
    (analysis_dir / "refine_summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return result


def render_hybrid_island_version(
    input_path: Path,
    analysis_dir: Path,
    output_path: Path,
    crf: int,
    preset: str,
) -> dict:
    """Render v2 using the island silhouette before the solar disk appears."""
    summary = json.loads((analysis_dir / SUMMARY_JSON).read_text(encoding="utf-8"))
    verify_analysis_matches_input(summary, input_path)
    tracks = load_tracks(analysis_dir / TRACKS_CSV)
    width = int(summary["analysis_width"])
    height = int(summary["analysis_height"])
    source_w = int(summary["source_width"])
    source_h = int(summary["source_height"])
    fps = float(summary["fps"])
    frame_count = int(summary["analyzed_frames"])
    early_end = 175
    blend_end = 235

    island_increments, island_responses = track_early_island_velocity(
        input_path, tracks, width, height, frame_count, early_end=early_end
    )
    broad_anchor = tracks["sun_x_level"][:frame_count]
    core_anchor = tracks["sun_core_x"][:frame_count]
    hybrid_anchor = build_velocity_hybrid_anchor(
        broad_anchor,
        core_anchor,
        island_increments,
        frame_count,
        early_end=early_end,
        blend_end=blend_end,
    )

    # Reuse the proven roll/y solution, replacing only horizontal anchoring.
    h_good = (
        (tracks["horizon_confidence"] >= 0.48)
        & (tracks["horizon_contrast"] >= 15.0)
        & (np.abs(tracks["horizon_angle_deg"]) <= 13.0)
    )
    slope = interpolate_bad(tracks["horizon_slope"], h_good)
    center_y = interpolate_bad(tracks["horizon_center_y"], h_good)
    base_matrices: list[np.ndarray] = []
    level_ys: list[float] = []
    for m, yc in zip(slope, center_y):
        matrix, level_y = rotation_for_horizon(width, height, float(m), float(yc))
        base_matrices.append(matrix)
        level_ys.append(level_y)
    target_horizon_y = float(np.median(level_ys))
    target_anchor_x = float(np.median(core_anchor[blend_end:]))
    transforms: list[np.ndarray] = []
    for matrix, level_y, anchor_x in zip(base_matrices, level_ys, hybrid_anchor):
        result = matrix.copy()
        result[0, 2] += target_anchor_x - float(anchor_x)
        result[1, 2] += target_horizon_y - float(level_y)
        transforms.append(result)

    mask = common_valid_mask(transforms, width, height)
    crop = largest_safe_16_9_crop(mask)
    crop_matrix = crop_to_canvas_matrix(crop, width, height)
    final_affines = [(crop_matrix @ homogeneous(m))[:2] for m in transforms]
    final_mask = common_valid_mask(final_affines, width, height)
    if np.count_nonzero(final_mask == 0):
        raise RuntimeError("Hybrid final transform has invalid common pixels")
    render_direct_affines(
        input_path,
        output_path,
        final_affines,
        width,
        height,
        source_w,
        source_h,
        fps,
        crf,
        preset,
    )

    plot_hybrid_horizontal_tracks(
        tracks,
        np.r_[0.0, np.cumsum(island_increments)],
        hybrid_anchor,
        early_end,
        blend_end,
        analysis_dir / "horizontal_anchor_v2.png",
    )
    csv_path = analysis_dir / "horizontal_anchor_v2.csv"
    island_position = np.r_[0.0, np.cumsum(island_increments)]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frame",
                "time_s",
                "broad_sun_anchor_x",
                "sun_core_x",
                "island_integrated_position",
                "island_phase_response",
                "hybrid_anchor_x",
            ]
        )
        for index in range(frame_count):
            writer.writerow(
                [
                    index,
                    index / fps,
                    broad_anchor[index],
                    core_anchor[index],
                    (
                        island_position[index]
                        if index < early_end
                        else ""
                    ),
                    (
                        1.0 if index == 0 else island_responses[index - 1]
                        if index < early_end
                        else ""
                    ),
                    hybrid_anchor[index],
                ]
            )
    result = {
        "output": str(output_path),
        "frames": frame_count,
        "duration_s": frame_count / fps,
        "early_island_frames": [0, early_end - 1],
        "raised_cosine_handoff_frames": [early_end, blend_end - 1],
        "island_phase_response_min": float(island_responses.min()),
        "island_phase_response_median": float(np.median(island_responses)),
        "crop_analysis": list(crop),
        "zoom_factor": width / crop[2],
        "rendered_directly_from_input": True,
        "input_is_configured_original": (
            canonical_path(input_path) == canonical_path(DEFAULT_INPUT)
        ),
        "crf": crf,
        "preset": preset,
    }
    (analysis_dir / "hybrid_render_summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return result


def plot_hybrid_horizontal_tracks(
    tracks: dict[str, np.ndarray],
    island_position: np.ndarray,
    hybrid_anchor: np.ndarray,
    early_end: int,
    blend_end: int,
    output: Path,
) -> None:
    width, height = 1500, 720
    image = Image.new("RGB", (width, height), "#11151a")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    left, right, top, bottom = 75, width - 30, 55, height - 60
    draw.rectangle((left, top, right, bottom), outline="#3c4652")
    draw.text(
        (18, 18),
        "Horizontal anchors: broad glow/reflection vs island-carried hybrid vs visible sun core",
        fill="#e8edf2",
        font=font,
    )
    broad = tracks["sun_x_level"][: hybrid_anchor.size]
    core = tracks["sun_core_x"][: hybrid_anchor.size]
    finite_values = np.concatenate((broad, core, hybrid_anchor, island_position))
    lo, hi = np.percentile(finite_values, [0.5, 99.5])
    pad = 0.08 * (hi - lo)
    lo, hi = float(lo - pad), float(hi + pad)

    def points(values: np.ndarray, start: int = 0) -> list[tuple[float, float]]:
        frame_indices = np.arange(start, start + values.size)
        xs = left + frame_indices / max(hybrid_anchor.size - 1, 1) * (right - left)
        ys = bottom - (values - lo) / (hi - lo) * (bottom - top)
        return list(zip(xs.tolist(), ys.tolist()))

    draw.line(points(broad), fill="#a567d3", width=2)
    draw.line(points(hybrid_anchor), fill="#47d7c6", width=3)
    draw.line(points(core), fill="#ffbf47", width=2)
    draw.line(points(island_position), fill="#6f85ff", width=2)
    for frame_index, label in [(early_end, "handoff begins"), (blend_end, "sun core")]:
        x = left + frame_index / (hybrid_anchor.size - 1) * (right - left)
        draw.line((x, top, x, bottom), fill="#e8edf2", width=1)
        draw.text((x + 5, top + 5), label, fill="#e8edf2", font=font)
    legend = [
        ("#a567d3", "broad glow/reflection"),
        ("#6f85ff", "island template (early)"),
        ("#47d7c6", "hybrid anchor used"),
        ("#ffbf47", "compact sun core"),
    ]
    x = left
    for color, label in legend:
        draw.line((x, height - 30, x + 26, height - 30), fill=color, width=3)
        draw.text((x + 32, height - 37), label, fill="#e8edf2", font=font)
        x += 270
    image.save(output)


def write_final_qa_metrics(analysis_dir: Path, qa_dir: Path) -> dict:
    source_summary = json.loads(
        (analysis_dir / SUMMARY_JSON).read_text(encoding="utf-8")
    )
    source_tracks = load_tracks(analysis_dir / TRACKS_CSV)
    qa_tracks = load_tracks(qa_dir / TRACKS_CSV)
    width = int(source_summary["analysis_width"])
    output_scale = float(source_summary["source_width"]) / width
    visible = np.flatnonzero(
        source_tracks["sun_core_energy"] >= 150.0 * (width / 960.0)
    )
    visible_from = int(visible[0]) if visible.size else 0

    angle = qa_tracks["horizon_angle_deg"]
    horizon_y = qa_tracks["horizon_level_y"]
    sun_core = qa_tracks["sun_core_x"][visible_from:]

    def residual_stats(values: np.ndarray) -> tuple[float, float, float]:
        residual = np.abs(values - np.median(values))
        return (
            float(np.std(values)),
            float(np.percentile(residual, 95)),
            float(np.max(residual)),
        )

    angle_std, angle_p95, angle_max = residual_stats(angle)
    y_std, y_p95, y_max = residual_stats(horizon_y)
    sun_std, sun_p95, sun_max = residual_stats(sun_core)
    metrics = {
        "horizon_angle_std_deg": angle_std,
        "horizon_angle_p95_abs_deg": angle_p95,
        "horizon_angle_max_abs_deg": angle_max,
        "horizon_y_std_output_px": y_std * output_scale,
        "horizon_y_p95_abs_output_px": y_p95 * output_scale,
        "horizon_y_max_abs_output_px": y_max * output_scale,
        "visible_sun_core_std_output_px": sun_std * output_scale,
        "visible_sun_core_p95_abs_output_px": sun_p95 * output_scale,
        "visible_sun_core_max_abs_output_px": sun_max * output_scale,
        "visible_sun_from_frame": visible_from,
    }
    (analysis_dir / "final_qa_metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))
    return metrics


def render_video(
    input_path: Path,
    analysis_dir: Path,
    output_path: Path,
    crf: int,
    preset: str,
) -> dict:
    summary = json.loads((analysis_dir / SUMMARY_JSON).read_text(encoding="utf-8"))
    verify_analysis_matches_input(summary, input_path)
    tracks = load_tracks(analysis_dir / TRACKS_CSV)
    analysis_w = int(summary["analysis_width"])
    analysis_h = int(summary["analysis_height"])
    source_w = int(summary["source_width"])
    source_h = int(summary["source_height"])
    fps = float(summary["fps"])
    frame_count = int(summary["analyzed_frames"])

    transforms_small, transform_info = build_transforms(tracks, analysis_w, analysis_h)
    mask = common_valid_mask(transforms_small, analysis_w, analysis_h)
    crop_small = largest_safe_16_9_crop(mask)
    scale_x = source_w / analysis_w
    scale_y = source_h / analysis_h
    if abs(scale_x - scale_y) > 1e-6:
        raise RuntimeError("Analysis and source aspect ratios differ")
    scale = scale_x
    crop_full = tuple(int(round(value * scale)) for value in crop_small)
    crop_x, crop_y, crop_w, crop_h = crop_full

    full_transforms: list[np.ndarray] = []
    for matrix in transforms_small:
        full = matrix.copy()
        full[:, 2] *= scale
        full_transforms.append(full)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = ffmpeg_raw_writer(output_path, source_w, source_h, fps, crf, preset)
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not reopen input video: {input_path}")
    for frame_index, matrix in enumerate(full_transforms):
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f"Decode failed at frame {frame_index}")
        stabilized = cv2.warpAffine(
            frame,
            matrix,
            (source_w, source_h),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        crop = stabilized[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]
        output_frame = cv2.resize(
            crop, (source_w, source_h), interpolation=cv2.INTER_LANCZOS4
        )
        assert writer.stdin is not None
        writer.stdin.write(output_frame.tobytes())
        if (frame_index + 1) % 100 == 0 or frame_index + 1 == frame_count:
            print(f"Rendered {frame_index + 1}/{frame_count} frames", flush=True)
    capture.release()
    assert writer.stdin is not None
    writer.stdin.close()
    status = writer.wait()
    if status != 0:
        raise RuntimeError(f"FFmpeg final encoder exited with {status}")

    render_summary = {
        **transform_info,
        "output": str(output_path),
        "frames": frame_count,
        "duration_s": frame_count / fps,
        "crop_analysis": list(crop_small),
        "crop_source": list(crop_full),
        "retained_linear_fraction": crop_w / source_w,
        "zoom_factor": source_w / crop_w,
        "crf": crf,
        "preset": preset,
    }
    (analysis_dir / "render_summary.json").write_text(
        json.dumps(render_summary, indent=2) + "\n", encoding="utf-8"
    )
    cv2.imwrite(str(analysis_dir / "common_valid_mask.png"), mask * 255)
    print(json.dumps(render_summary, indent=2))
    return render_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["analyze", "render", "refine", "hybrid", "all"]
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument(
        "--qa-dir", type=Path, default=DEFAULT_ANALYSIS / "QA first pass"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT
    )
    parser.add_argument(
        "--end-frame",
        type=int,
        default=650,
        help="Exclusive end frame. Frame 650 begins the final pan away; default: 650.",
    )
    parser.add_argument("--analysis-width", type=int, default=960)
    parser.add_argument("--crf", type=int, default=16)
    parser.add_argument("--preset", default="slow")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    analysis_dir = args.analysis_dir.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    output_path = args.output.resolve()
    qa_dir = args.qa_dir.resolve()
    if args.mode in {"render", "refine", "hybrid", "all"}:
        if canonical_path(input_path) == canonical_path(output_path):
            raise ValueError("Input and output paths must be different")
    if args.mode in {"analyze", "all"}:
        analyze_video(input_path, analysis_dir, args.end_frame, args.analysis_width)
    if args.mode in {"render", "all"}:
        render_video(input_path, analysis_dir, output_path, args.crf, args.preset)
    if args.mode == "all":
        analyze_video(
            output_path, qa_dir, args.end_frame, args.analysis_width
        )
        refine_video(
            input_path,
            analysis_dir,
            qa_dir,
            output_path,
            args.crf,
            args.preset,
        )
        final_qa_dir = analysis_dir / "QA final"
        analyze_video(
            output_path, final_qa_dir, args.end_frame, args.analysis_width
        )
        write_final_qa_metrics(analysis_dir, final_qa_dir)
    if args.mode == "refine":
        refine_video(
            input_path,
            analysis_dir,
            qa_dir,
            output_path,
            args.crf,
            args.preset,
        )
    if args.mode == "hybrid":
        render_hybrid_island_version(
            input_path,
            analysis_dir,
            output_path,
            args.crf,
            args.preset,
        )


if __name__ == "__main__":
    main()
