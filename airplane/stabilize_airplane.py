#!/usr/bin/env python3
"""Semantic stabilizer for the Kerkyra airplane landing clip.

The scene supplies three independent constraints:

* the sea/land boundary plus textured-land optical flow provide roll and y;
* the aircraft provides x only, preserving its genuine vertical descent;
* a single fixed zoom prevents borders and avoids crop breathing.

Analysis is performed at reduced resolution. The HDR master is rendered once
from the original 10-bit HLG frames; SDR previews are explicitly tone-mapped.
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
from typing import BinaryIO, Iterable


HERE = Path(__file__).resolve().parent
LOCAL_DEPS = HERE / ".deps"
WORKSPACE_DEPS = HERE.parent / ".deps"
for dependency_dir in (LOCAL_DEPS, WORKSPACE_DEPS):
    if dependency_dir.exists():
        sys.path.insert(0, str(dependency_dir))

import cv2  # type: ignore  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import gaussian_filter1d, median_filter  # noqa: E402


DEFAULT_INPUT = HERE.parent / "airplane_source.MOV"
DEFAULT_ANALYSIS = HERE / "Diagnostics refined"
DEFAULT_PREVIEW = DEFAULT_ANALYSIS / "airplane_plane-lock-refined_preview.mp4"
DEFAULT_OUTPUT = HERE.parent / "airplane_plane-lock-refined.mp4"
TRACKS_CSV = "tracks.csv"
SOLUTION_CSV = "solution_tracks.csv"
ANALYSIS_JSON = "analysis_summary.json"
SOLUTION_JSON = "solution_summary.json"
OVERLAY_MP4 = "detection_overlay.mp4"
TRACKING_PLOT = "tracking_plot.png"
QA_JSON = "qa_metrics.json"

TRACK_FIELDS = [
    "frame",
    "time_s",
    "shoreline_slope",
    "shoreline_angle_deg",
    "shoreline_center_y",
    "shoreline_level_y",
    "shoreline_confidence",
    "shoreline_contrast",
    "plane_x_raw_source",
    "plane_y_raw_source",
    "plane_bbox_x_source",
    "plane_bbox_y_source",
    "plane_score",
    "land_dx_analysis",
    "land_dy_analysis",
    "land_angle_deg",
    "land_scale",
    "land_detected_points",
    "land_tracked_points",
    "land_inliers",
    "land_inlier_ratio",
    "land_median_fb_error",
    "land_valid",
]


def canonical_path(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def stop_raw_decoder(process: subprocess.Popen[bytes]) -> None:
    """Stop a decoder cleanly when the output intentionally trims its tail."""
    if process.stdout is not None:
        process.stdout.close()
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def parse_fraction(value: str) -> float:
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator)


def probe_video(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_frames,pix_fmt,color_range,color_space,color_transfer,color_primaries",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": parse_fraction(stream["avg_frame_rate"]),
        "fps_fraction": stream["avg_frame_rate"],
        "frames": int(stream["nb_frames"]),
        "duration": float(data["format"]["duration"]),
        "pix_fmt": stream.get("pix_fmt"),
        "color_range": stream.get("color_range"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
    }


def weighted_line_fit(
    xs: np.ndarray, ys: np.ndarray, strengths: np.ndarray, cx: float
) -> tuple[float, float, np.ndarray]:
    design = np.column_stack((xs - cx, np.ones_like(xs)))
    weights = np.clip(strengths.astype(np.float64), 1.0, 120.0)
    params = np.linalg.lstsq(
        design * weights[:, None], ys * weights, rcond=None
    )[0]
    for _ in range(6):
        residual = ys - design @ params
        scale = max(1.4826 * float(np.median(np.abs(residual))), 0.35)
        huber = np.minimum(
            1.0, (2.0 * scale) / np.maximum(np.abs(residual), 1e-9)
        )
        combined = weights * huber
        params = np.linalg.lstsq(
            design * combined[:, None], ys * combined, rcond=None
        )[0]
    return float(params[0]), float(params[1]), ys - design @ params


def detect_shoreline(
    frame: np.ndarray,
    rng: np.random.Generator,
    search_center_y: float | None = None,
    search_half_height: float = 24.0,
) -> tuple[float, float, float, float]:
    """Fit the dark-land-above / bright-water-below boundary."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    smooth = cv2.GaussianBlur(gray, (0, 0), sigmaX=5.0, sigmaY=1.2)
    vertical_mean = cv2.blur(smooth, (1, 7), borderType=cv2.BORDER_REPLICATE)
    gap = 6
    signed_step = np.zeros_like(vertical_mean)
    signed_step[gap:-gap] = vertical_mean[2 * gap :] - vertical_mean[: -2 * gap]

    if search_center_y is None:
        y_min = int(round(0.34 * height))
        y_max = int(round(0.84 * height))
    else:
        # Stabilized-output QA knows where the shoreline should be. Restricting
        # the search prevents brighter, unrelated hillside edges from winning.
        y_min = max(gap, int(math.floor(search_center_y - search_half_height)))
        y_max = min(
            height - gap,
            int(math.ceil(search_center_y + search_half_height + 1.0)),
        )
    xs = np.arange(3, width - 3, 2, dtype=np.float64)
    xi = xs.astype(np.int32)
    roi = signed_step[y_min:y_max, xi]
    ys = np.argmax(roi, axis=0).astype(np.float64) + y_min
    strengths = signed_step[ys.astype(np.int32), xi].astype(np.float64)

    left = np.flatnonzero(xs < 0.45 * width)
    right = np.flatnonzero(xs > 0.55 * width)
    cx = (width - 1) / 2.0
    best_score = -np.inf
    best_m = 0.0
    best_yc = float(np.median(ys))
    for _ in range(220):
        i = int(rng.choice(left))
        j = int(rng.choice(right))
        slope = float((ys[j] - ys[i]) / (xs[j] - xs[i]))
        if abs(slope) > 0.24:
            continue
        center_y = float(ys[i] - slope * (xs[i] - cx))
        residual = np.abs(ys - (slope * (xs - cx) + center_y))
        inliers = residual < 3.0
        count = int(inliers.sum())
        if count < 24:
            continue
        bonus = float(np.mean(np.clip(strengths[inliers], 0, 120))) / 120.0
        score = count + bonus
        if score > best_score:
            best_score = score
            best_m, best_yc = slope, center_y

    kept_x: list[float] = []
    snap_y: list[float] = []
    snap_s: list[float] = []
    for x in np.arange(4, width - 4, 2, dtype=np.float64):
        predicted = best_m * (x - cx) + best_yc
        lo = max(gap, int(math.floor(predicted - 8)))
        hi = min(height - gap, int(math.ceil(predicted + 9)))
        if hi <= lo:
            continue
        column = signed_step[lo:hi, int(x)]
        index = int(np.argmax(column))
        kept_x.append(float(x))
        snap_y.append(float(lo + index))
        snap_s.append(float(column[index]))

    fit_x = np.asarray(kept_x, np.float64)
    fit_y = np.asarray(snap_y, np.float64)
    fit_s = np.asarray(snap_s, np.float64)
    slope, center_y, residual = weighted_line_fit(fit_x, fit_y, fit_s, cx)
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
    return slope, center_y, confidence, median_contrast


def rotation_for_shoreline(
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


class PlaneTracker:
    """Static bright-airframe template with a constant-velocity local gate."""

    def __init__(self, first_frame: np.ndarray) -> None:
        source_h, source_w = first_frame.shape[:2]
        self.x = int(round(235 / 1920 * source_w))
        self.y = int(round(545 / 1080 * source_h))
        self.w = int(round(140 / 1920 * source_w))
        self.h = int(round(65 / 1080 * source_h))
        gray = cv2.cvtColor(
            first_frame[self.y : self.y + self.h, self.x : self.x + self.w],
            cv2.COLOR_BGR2GRAY,
        )
        self.template = np.where(gray > 215, 255, 0).astype(np.uint8)
        self.template = cv2.morphologyEx(
            self.template, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
        )
        self.vx = 0.0
        self.vy = 0.0
        self.frame_index = 0

    def update(self, frame: np.ndarray) -> tuple[float, float, float, float, float]:
        if self.frame_index == 0:
            score = 1.0
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            predicted_x = int(round(self.x + self.vx))
            predicted_y = int(round(self.y + self.vy))
            search_x = max(24, int(round(45 / 1920 * frame.shape[1])))
            search_y = max(18, int(round(35 / 1080 * frame.shape[0])))
            left = max(0, predicted_x - search_x)
            top = max(0, predicted_y - search_y)
            right = min(frame.shape[1], predicted_x + self.w + search_x)
            bottom = min(frame.shape[0], predicted_y + self.h + search_y)
            threshold = 215
            binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
            search = binary[top:bottom, left:right]
            response = cv2.matchTemplate(search, self.template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(response)
            peak_x, peak_y = location
            sub_x = 0.0
            sub_y = 0.0
            if 0 < peak_x < response.shape[1] - 1:
                l = float(response[peak_y, peak_x - 1])
                c = float(response[peak_y, peak_x])
                r = float(response[peak_y, peak_x + 1])
                denominator = l - 2 * c + r
                if abs(denominator) > 1e-9:
                    sub_x = 0.5 * (l - r) / denominator
            if 0 < peak_y < response.shape[0] - 1:
                u = float(response[peak_y - 1, peak_x])
                c = float(response[peak_y, peak_x])
                d = float(response[peak_y + 1, peak_x])
                denominator = u - 2 * c + d
                if abs(denominator) > 1e-9:
                    sub_y = 0.5 * (u - d) / denominator
            new_x = float(left + peak_x + sub_x)
            new_y = float(top + peak_y + sub_y)
            dx = new_x - self.x
            dy = new_y - self.y
            self.vx = 0.65 * self.vx + 0.35 * dx
            self.vy = 0.65 * self.vy + 0.35 * dy
            self.x = new_x
            self.y = new_y
        self.frame_index += 1
        return (
            float(self.x + self.w / 2),
            float(self.y + self.h / 2),
            float(score),
            float(self.x),
            float(self.y),
        )


def land_mask(
    width: int,
    height: int,
    shoreline_y: float,
    plane_bbox: tuple[int, int, int, int] | None,
) -> np.ndarray:
    mask = np.zeros((height, width), np.uint8)
    bottom = min(height, max(1, int(round(shoreline_y - 5))))
    top = max(0, bottom - int(round(0.35 * height)))
    mask[top:bottom] = 255
    margin = max(12, int(round(0.02 * width)))
    mask[:, :margin] = 0
    mask[:, width - margin :] = 0
    if plane_bbox is not None:
        x, y, w, h = plane_bbox
        pad_x = max(8, w // 2)
        pad_y = max(6, h // 2)
        cv2.rectangle(
            mask,
            (max(0, x - pad_x), max(0, y - pad_y)),
            (min(width - 1, x + w + pad_x), min(height - 1, y + h + pad_y)),
            0,
            -1,
        )
    return mask


def estimate_land_motion(
    previous: np.ndarray,
    current: np.ndarray,
    previous_shoreline_y: float,
    plane_bbox: tuple[int, int, int, int] | None,
    anchor_y: float | None = None,
) -> dict[str, float | int]:
    height, width = previous.shape
    points = cv2.goodFeaturesToTrack(
        previous,
        maxCorners=900,
        qualityLevel=0.008,
        minDistance=6,
        mask=land_mask(width, height, previous_shoreline_y, plane_bbox),
        blockSize=5,
    )
    default: dict[str, float | int] = {
        "dx": 0.0,
        "dy": 0.0,
        "angle": 0.0,
        "scale": 1.0,
        "detected": 0 if points is None else len(points),
        "tracked": 0,
        "inliers": 0,
        "ratio": 0.0,
        "fb": 99.0,
        "valid": 0,
    }
    if points is None or len(points) < 20:
        return default
    forward, status, _ = cv2.calcOpticalFlowPyrLK(
        previous,
        current,
        points,
        None,
        winSize=(25, 25),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    backward, back_status, _ = cv2.calcOpticalFlowPyrLK(
        current,
        previous,
        forward,
        None,
        winSize=(25, 25),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    fb_error = np.linalg.norm(
        points.reshape(-1, 2) - backward.reshape(-1, 2), axis=1
    )
    good = (
        status.ravel().astype(bool)
        & back_status.ravel().astype(bool)
        & (fb_error < 1.1)
    )
    source = points.reshape(-1, 2)[good]
    destination = forward.reshape(-1, 2)[good]
    tracked = len(source)
    if tracked < 20:
        default.update(
            {
                "tracked": tracked,
                "fb": float(np.median(fb_error[good])) if np.any(good) else 99.0,
            }
        )
        return default
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        destination,
        method=cv2.RANSAC,
        ransacReprojThreshold=1.25,
        maxIters=2000,
        confidence=0.995,
        refineIters=10,
    )
    inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    if matrix is None or inliers < 18:
        default.update(
            {
                "tracked": tracked,
                "inliers": inliers,
                "ratio": inliers / max(tracked, 1),
                "fb": float(np.median(fb_error[good])),
            }
        )
        return default
    a, b = float(matrix[0, 0]), float(matrix[1, 0])
    scale = float(np.hypot(a, b))
    angle = float(np.degrees(np.arctan2(b, a)))
    anchor = np.array(
        [
            [
                (width - 1) / 2,
                (height - 1) / 2 if anchor_y is None else float(anchor_y),
            ]
        ],
        np.float64,
    )
    mapped = anchor @ matrix[:, :2].T + matrix[:, 2]
    dx, dy = (mapped - anchor)[0]
    valid = 0.97 <= scale <= 1.03 and abs(angle) <= 3.0
    return {
        "dx": float(dx) if valid else 0.0,
        "dy": float(dy) if valid else 0.0,
        "angle": angle if valid else 0.0,
        "scale": scale if valid else 1.0,
        "detected": len(points),
        "tracked": tracked,
        "inliers": inliers,
        "ratio": inliers / max(tracked, 1),
        "fb": float(np.median(fb_error[good])),
        "valid": int(valid),
    }


def diagnostic_writer(
    output: Path, width: int, height: int, fps: float
) -> subprocess.Popen[bytes]:
    output.parent.mkdir(parents=True, exist_ok=True)
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
        "scale=in_range=pc:out_range=tv,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
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


def draw_analysis_overlay(
    frame: np.ndarray,
    index: int,
    slope: float,
    center_y: float,
    shoreline_confidence: float,
    plane_x: float,
    plane_y: float,
    plane_score: float,
    source_scale: float,
    bbox_w_source: int,
    bbox_h_source: int,
    land_inliers: int,
) -> np.ndarray:
    overlay = frame.copy()
    height, width = frame.shape[:2]
    cx = (width - 1) / 2.0
    left_y = int(round(center_y + slope * (0 - cx)))
    right_y = int(round(center_y + slope * (width - 1 - cx)))
    cv2.line(overlay, (0, left_y), (width - 1, right_y), (50, 235, 85), 2)
    px = int(round(plane_x / source_scale))
    py = int(round(plane_y / source_scale))
    bw = int(round(bbox_w_source / source_scale))
    bh = int(round(bbox_h_source / source_scale))
    cv2.rectangle(
        overlay,
        (px - bw // 2, py - bh // 2),
        (px + bw // 2, py + bh // 2),
        (0, 180, 255),
        2,
    )
    text = (
        f"f {index:04d}  roll {math.degrees(math.atan(slope)):+.3f}  "
        f"shore {shoreline_confidence:.2f}  plane {plane_score:.2f}  "
        f"land inliers {land_inliers}"
    )
    cv2.putText(
        overlay,
        text,
        (14, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.60,
        (10, 10, 10),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        text,
        (14, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.60,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return overlay


def draw_tracking_plot(rows: list[dict], output: Path) -> None:
    width, panel_h, panels = 1500, 220, 4
    height = panel_h * panels
    image = Image.new("RGB", (width, height), "#0b131d")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    left, right = 78, width - 28
    series = [
        ("Shoreline angle (degrees)", "shoreline_angle_deg", "#55d98a"),
        ("Leveled shoreline y (analysis pixels)", "shoreline_level_y", "#57b9ff"),
        ("Raw plane x (source pixels)", "plane_x_raw_source", "#ffb347"),
        ("Confidence: shoreline / plane / land inlier ratio", None, "#ffffff"),
    ]
    xs = np.linspace(left, right, len(rows))
    for panel, (title, key, color) in enumerate(series):
        top = panel * panel_h
        plot_top, bottom = top + 30, top + panel_h - 25
        draw.rectangle((left, plot_top, right, bottom), outline="#384b60")
        draw.text((15, top + 8), title, fill="#e8edf2", font=font)
        if key is not None:
            values = np.asarray([float(row[key]) for row in rows])
            lo, hi = np.percentile(values, [0.5, 99.5])
            if hi - lo < 1e-6:
                hi = lo + 1
            pad = 0.08 * (hi - lo)
            lo, hi = float(lo - pad), float(hi + pad)
            ys = bottom - (values - lo) / (hi - lo) * (bottom - plot_top)
            draw.line(list(zip(xs.tolist(), ys.tolist())), fill=color, width=2)
            draw.text(
                (right - 165, top + 8),
                f"{lo:.2f} .. {hi:.2f}",
                fill=color,
                font=font,
            )
        else:
            confidence_series = [
                ("shoreline_confidence", "#55d98a"),
                ("plane_score", "#ffb347"),
                ("land_inlier_ratio", "#57b9ff"),
            ]
            for field, series_color in confidence_series:
                values = np.clip(
                    np.asarray([float(row[field]) for row in rows]), 0.0, 1.0
                )
                ys = bottom - values * (bottom - plot_top)
                draw.line(
                    list(zip(xs.tolist(), ys.tolist())),
                    fill=series_color,
                    width=2,
                )
    image.save(output)


def analyze_video(
    input_path: Path,
    analysis_dir: Path,
    analysis_width: int,
    plane_visible_end: int,
) -> dict:
    metadata = probe_video(input_path)
    source_w, source_h = metadata["width"], metadata["height"]
    analysis_h = int(round(source_h * analysis_width / source_w))
    if analysis_h % 2:
        analysis_h += 1
    scale = source_w / analysis_width
    if abs(scale - source_h / analysis_h) > 1e-6:
        raise RuntimeError("Source and analysis aspect ratios differ")
    frame_count = metadata["frames"]
    plane_visible_end = min(plane_visible_end, frame_count)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open {input_path}")
    ok, first = capture.read()
    if not ok:
        raise RuntimeError("Could not decode frame zero")
    tracker = PlaneTracker(first)
    writer = diagnostic_writer(
        analysis_dir / OVERLAY_MP4, analysis_width, analysis_h, metadata["fps"]
    )
    rng = np.random.default_rng(20260813)
    rows: list[dict[str, float | int]] = []
    previous_gray: np.ndarray | None = None
    previous_shoreline_y = analysis_h * 0.70
    source_frame = first

    for index in range(frame_count):
        if index > 0:
            ok, source_frame = capture.read()
            if not ok:
                break
        plane_x, plane_y, plane_score, bbox_x, bbox_y = tracker.update(source_frame)
        frame = cv2.resize(
            source_frame, (analysis_width, analysis_h), interpolation=cv2.INTER_AREA
        )
        slope, center_y, shore_conf, contrast = detect_shoreline(frame, rng)
        _, level_y = rotation_for_shoreline(
            analysis_width, analysis_h, slope, center_y
        )
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scaled_bbox = (
            int(round(bbox_x / scale)),
            int(round(bbox_y / scale)),
            int(round(tracker.w / scale)),
            int(round(tracker.h / scale)),
        )
        if previous_gray is None:
            land = {
                "dx": 0.0,
                "dy": 0.0,
                "angle": 0.0,
                "scale": 1.0,
                "detected": 0,
                "tracked": 0,
                "inliers": 0,
                "ratio": 0.0,
                "fb": 0.0,
                "valid": 1,
            }
        else:
            land = estimate_land_motion(
                previous_gray,
                gray,
                previous_shoreline_y,
                scaled_bbox,
            )
        row: dict[str, float | int] = {
            "frame": index,
            "time_s": index / metadata["fps"],
            "shoreline_slope": slope,
            "shoreline_angle_deg": math.degrees(math.atan(slope)),
            "shoreline_center_y": center_y,
            "shoreline_level_y": level_y,
            "shoreline_confidence": shore_conf,
            "shoreline_contrast": contrast,
            "plane_x_raw_source": plane_x,
            "plane_y_raw_source": plane_y,
            "plane_bbox_x_source": bbox_x,
            "plane_bbox_y_source": bbox_y,
            "plane_score": plane_score,
            "land_dx_analysis": land["dx"],
            "land_dy_analysis": land["dy"],
            "land_angle_deg": land["angle"],
            "land_scale": land["scale"],
            "land_detected_points": land["detected"],
            "land_tracked_points": land["tracked"],
            "land_inliers": land["inliers"],
            "land_inlier_ratio": land["ratio"],
            "land_median_fb_error": land["fb"],
            "land_valid": land["valid"],
        }
        rows.append(row)
        overlay = draw_analysis_overlay(
            frame,
            index,
            slope,
            center_y,
            shore_conf,
            plane_x,
            plane_y,
            plane_score,
            scale,
            tracker.w,
            tracker.h,
            int(land["inliers"]),
        )
        assert writer.stdin is not None
        writer.stdin.write(overlay.tobytes())
        previous_gray = gray
        previous_shoreline_y = level_y
        if (index + 1) % 200 == 0 or index + 1 == frame_count:
            print(f"Analyzed {index + 1}/{frame_count}", flush=True)

    capture.release()
    assert writer.stdin is not None
    writer.stdin.close()
    if writer.wait() != 0:
        raise RuntimeError("Diagnostic video encoder failed")
    if len(rows) != frame_count:
        raise RuntimeError(f"Decoded {len(rows)} of {frame_count} frames")

    with (analysis_dir / TRACKS_CSV).open("w", newline="", encoding="utf-8") as handle:
        csv_writer = csv.DictWriter(handle, fieldnames=TRACK_FIELDS)
        csv_writer.writeheader()
        csv_writer.writerows(rows)
    draw_tracking_plot(rows, analysis_dir / TRACKING_PLOT)

    plane_scores = np.asarray([float(row["plane_score"]) for row in rows])
    shore_confidence = np.asarray(
        [float(row["shoreline_confidence"]) for row in rows]
    )
    land_inliers = np.asarray([float(row["land_inliers"]) for row in rows])
    summary = {
        "input": str(input_path.resolve()),
        "source_width": source_w,
        "source_height": source_h,
        "analysis_width": analysis_width,
        "analysis_height": analysis_h,
        "fps": metadata["fps"],
        "fps_fraction": metadata["fps_fraction"],
        "frames": frame_count,
        "duration_s": metadata["duration"],
        "plane_visible_end_exclusive": plane_visible_end,
        "plane_seed_bbox_source": [
            int(round(235 / 1920 * source_w)),
            int(round(545 / 1080 * source_h)),
            tracker.w,
            tracker.h,
        ],
        "source_media": {
            key: metadata[key]
            for key in (
                "pix_fmt",
                "color_range",
                "color_space",
                "color_transfer",
                "color_primaries",
            )
        },
        "median_plane_score_visible": float(
            np.median(plane_scores[:plane_visible_end])
        ),
        "minimum_plane_score_visible": float(
            np.min(plane_scores[:plane_visible_end])
        ),
        "median_shoreline_confidence": float(np.median(shore_confidence)),
        "median_land_inliers": float(np.median(land_inliers[1:])),
    }
    (analysis_dir / ANALYSIS_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return summary


def load_tracks(path: Path) -> dict[str, np.ndarray]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"Empty track file: {path}")
    return {
        field: np.asarray([float(row[field]) for row in rows], np.float64)
        for field in rows[0]
    }


def weighted_fused_path(
    absolute: np.ndarray,
    increments: np.ndarray,
    weights: np.ndarray,
    sigma_frames: float,
) -> np.ndarray:
    if increments.size != absolute.size:
        raise ValueError("Increment and absolute arrays must have equal length")
    sharp_increments = gaussian_filter1d(increments, sigma=0.35, mode="nearest")
    sharp_increments[0] = 0.0
    relative = np.r_[0.0, np.cumsum(sharp_increments[1:])]
    offset = absolute - relative
    numerator = gaussian_filter1d(offset * weights, sigma_frames, mode="nearest")
    denominator = gaussian_filter1d(weights, sigma_frames, mode="nearest")
    if np.any(denominator < 1e-6):
        raise RuntimeError("Insufficient absolute shoreline support")
    return relative + numerator / denominator


def robust_trimmed_mean(values: np.ndarray, fraction: float = 0.05) -> float:
    sorted_values = np.sort(np.asarray(values, np.float64))
    trim = int(round(fraction * sorted_values.size))
    if 2 * trim >= sorted_values.size:
        raise ValueError("Trim removes all values")
    return float(np.mean(sorted_values[trim : sorted_values.size - trim]))


def build_output_affines(
    solution_tracks: dict[str, np.ndarray],
    solution: dict,
    width: int,
    height: int,
    zoom: float | None = None,
) -> list[np.ndarray]:
    source_w = float(solution["source_width"])
    source_h = float(solution["source_height"])
    analysis_h = float(solution["analysis_height"])
    if abs(width / source_w - height / source_h) > 1e-8:
        raise RuntimeError("Render and source aspect ratios differ")
    x_scale = width / source_w
    y_scale = height / source_h
    target_x = float(solution["target_plane_raw_source_x"]) * x_scale
    target_y = float(solution["target_shoreline_analysis_y"]) * (
        height / analysis_h
    )
    plane_x = solution_tracks["plane_x_used_source"] * x_scale
    plane_y = solution_tracks["plane_y_used_source"] * y_scale
    shoreline_y = solution_tracks["fused_shoreline_y_analysis"] * (
        height / analysis_h
    )
    angle = solution_tracks["fused_roll_deg"]
    actual_zoom = float(solution["safe_zoom"] if zoom is None else zoom)
    center = ((width - 1) / 2.0, (height - 1) / 2.0)
    matrices: list[np.ndarray] = []
    for angle_deg, h_y, p_x, p_y in zip(angle, shoreline_y, plane_x, plane_y):
        rotation = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0)
        plane_point = rotation @ np.array([p_x, p_y, 1.0])
        matrix = actual_zoom * rotation
        matrix[0, 2] += target_x - actual_zoom * plane_point[0]
        matrix[1, 2] += target_y - actual_zoom * h_y
        matrices.append(matrix)
    return matrices


def affines_are_safe(
    matrices: Iterable[np.ndarray], width: int, height: int, inset: int = 4
) -> bool:
    output_corners = np.array(
        [
            [0.0, 0.0],
            [width - 1.0, 0.0],
            [width - 1.0, height - 1.0],
            [0.0, height - 1.0],
        ],
        np.float64,
    )[None, :, :]
    for matrix in matrices:
        inverse = cv2.invertAffineTransform(matrix)
        source = cv2.transform(output_corners, inverse)[0]
        if (
            source[:, 0].min() < inset
            or source[:, 0].max() > width - 1 - inset
            or source[:, 1].min() < inset
            or source[:, 1].max() > height - 1 - inset
        ):
            return False
    return True


def solve_transforms(
    input_path: Path,
    analysis_dir: Path,
    zoom_safety: float,
    output_end: int,
    shoreline_from_bottom: float,
    handoff_frames: int,
) -> dict:
    summary = json.loads((analysis_dir / ANALYSIS_JSON).read_text(encoding="utf-8"))
    if canonical_path(summary["input"]) != canonical_path(input_path):
        raise RuntimeError("Analysis input does not match requested input")
    tracks = load_tracks(analysis_dir / TRACKS_CSV)
    source_frame_count = int(summary["frames"])
    frame_count = min(output_end, source_frame_count)
    visible_end = min(int(summary["plane_visible_end_exclusive"]), frame_count)
    if not 0.0 < shoreline_from_bottom < 1.0:
        raise ValueError("shoreline_from_bottom must lie strictly between 0 and 1")
    if handoff_frames < 1:
        raise ValueError("handoff_frames must be positive")
    tracks = {field: values[:frame_count] for field, values in tracks.items()}

    land_valid = tracks["land_valid"] >= 0.5
    if not np.all(land_valid[1:]):
        indices = np.arange(frame_count)
        for field in ("land_dx_analysis", "land_dy_analysis", "land_angle_deg"):
            values = tracks[field].copy()
            values[~land_valid] = np.interp(
                indices[~land_valid], indices[land_valid], values[land_valid]
            )
            tracks[field] = values

    shore_weights = np.clip(
        (tracks["shoreline_confidence"] - 0.08) / 0.42, 0.0, 1.0
    ) * np.clip((tracks["shoreline_contrast"] - 7.0) / 35.0, 0.0, 1.0)
    shore_weights[np.abs(tracks["shoreline_angle_deg"]) > 7.0] = 0.0
    # A bright foreground edge briefly masquerades as the shoreline around
    # 15.5 s.  Reject temporal jumps before the non-causal fusion can smear
    # them backward into the visible 14.5 s dip.  Land KLT bridges the gap.
    shoreline_median = median_filter(
        tracks["shoreline_level_y"], size=61, mode="nearest"
    )
    shoreline_temporal_outlier = (
        np.abs(tracks["shoreline_level_y"] - shoreline_median) > 40.0
    )
    shore_weights[shoreline_temporal_outlier] = 0.0
    fused_roll = weighted_fused_path(
        tracks["shoreline_angle_deg"],
        tracks["land_angle_deg"],
        shore_weights,
        sigma_frames=45.0,
    )
    fused_y = weighted_fused_path(
        tracks["shoreline_level_y"],
        tracks["land_dy_analysis"],
        shore_weights,
        sigma_frames=45.0,
    )

    plane_x_measured = tracks["plane_x_raw_source"].copy()
    plane_y_measured = tracks["plane_y_raw_source"].copy()
    visible_good = (
        (np.arange(frame_count) < visible_end)
        & (tracks["plane_score"] >= 0.45)
    )
    if int(visible_good.sum()) < int(0.95 * visible_end):
        raise RuntimeError("Too many weak plane detections before occlusion")
    target_plane_x = robust_trimmed_mean(plane_x_measured[visible_good], 0.05)

    plane_x_used = plane_x_measured.copy()
    plane_y_used = plane_y_measured.copy()
    source_per_analysis = float(summary["source_width"]) / float(
        summary["analysis_width"]
    )
    land_dx_source = tracks["land_dx_analysis"] * source_per_analysis
    land_dy_source = tracks["land_dy_analysis"] * source_per_analysis
    relative_window_start = max(1, visible_end - 120)
    plane_dx = np.diff(plane_x_measured)
    plane_dy = np.diff(plane_y_measured)
    relative_dx = float(
        np.median(
            plane_dx[relative_window_start - 1 : visible_end - 1]
            - land_dx_source[relative_window_start:visible_end]
        )
    )
    relative_dy = float(
        np.median(
            plane_dy[relative_window_start - 1 : visible_end - 1]
            - land_dy_source[relative_window_start:visible_end]
        )
    )
    source_center = np.array(
        [
            (float(summary["source_width"]) - 1.0) / 2.0,
            (float(summary["source_height"]) - 1.0) / 2.0,
        ],
        np.float64,
    )
    handoff_end = min(visible_end + handoff_frames, frame_count)
    handoff_weights = np.zeros(frame_count, np.float64)
    for index in range(visible_end, frame_count):
        angle_rad = math.radians(float(tracks["land_angle_deg"][index]))
        cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
        rotation = np.array([[cosine, -sine], [sine, cosine]], np.float64)
        previous_point = np.array(
            [plane_x_used[index - 1], plane_y_used[index - 1]], np.float64
        )
        background_point = (
            source_center
            + np.array(
                [land_dx_source[index], land_dy_source[index]], np.float64
            )
            + float(tracks["land_scale"][index])
            * (rotation @ (previous_point - source_center))
        )
        if index < handoff_end:
            phase = (index - visible_end + 1) / (handoff_frames + 1)
            relative_weight = 0.5 + 0.5 * math.cos(math.pi * phase)
        else:
            relative_weight = 0.0
        handoff_weights[index] = relative_weight
        plane_x_used[index] = background_point[0] + relative_weight * relative_dx
        plane_y_used[index] = background_point[1] + relative_weight * relative_dy

    solution_rows: list[dict[str, float | int]] = []
    for index in range(frame_count):
        solution_rows.append(
            {
                "frame": index,
                "time_s": index / float(summary["fps"]),
                "fused_roll_deg": fused_roll[index],
                "fused_shoreline_y_analysis": fused_y[index],
                "plane_x_measured_source": plane_x_measured[index],
                "plane_y_measured_source": plane_y_measured[index],
                "plane_x_used_source": plane_x_used[index],
                "plane_y_used_source": plane_y_used[index],
                "plane_score": tracks["plane_score"][index],
                "plane_prediction": int(index >= visible_end),
                "plane_handoff_weight": handoff_weights[index],
                "background_only": int(index >= handoff_end),
                "shoreline_weight": shore_weights[index],
                "land_inliers": tracks["land_inliers"][index],
            }
        )
    with (analysis_dir / SOLUTION_CSV).open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(solution_rows[0]))
        writer.writeheader()
        writer.writerows(solution_rows)

    target_shoreline_y = float(
        (1.0 - shoreline_from_bottom) * int(summary["analysis_height"])
    )
    provisional = {
        "source_width": int(summary["source_width"]),
        "source_height": int(summary["source_height"]),
        "analysis_width": int(summary["analysis_width"]),
        "analysis_height": int(summary["analysis_height"]),
        "target_plane_raw_source_x": target_plane_x,
        "target_shoreline_analysis_y": target_shoreline_y,
        "safe_zoom": 1.0,
    }
    solution_tracks = load_tracks(analysis_dir / SOLUTION_CSV)
    lo, hi = 1.0, 3.5
    for _ in range(55):
        mid = (lo + hi) / 2
        matrices = build_output_affines(
            solution_tracks,
            provisional,
            int(summary["analysis_width"]),
            int(summary["analysis_height"]),
            zoom=mid,
        )
        if affines_are_safe(
            matrices,
            int(summary["analysis_width"]),
            int(summary["analysis_height"]),
            inset=3,
        ):
            hi = mid
        else:
            lo = mid
    minimum_zoom = hi
    safe_zoom = minimum_zoom * zoom_safety
    provisional["safe_zoom"] = safe_zoom
    final_matrices = build_output_affines(
        solution_tracks,
        provisional,
        int(summary["analysis_width"]),
        int(summary["analysis_height"]),
    )
    if not affines_are_safe(
        final_matrices,
        int(summary["analysis_width"]),
        int(summary["analysis_height"]),
        inset=3,
    ):
        raise RuntimeError("Safe zoom still exposes a source border")

    result = {
        **provisional,
        "input": str(input_path.resolve()),
        "frames": frame_count,
        "source_frames": source_frame_count,
        "first_excluded_source_frame": frame_count,
        "trim_reason": (
            "full source retained"
            if frame_count == source_frame_count
            else "explicit exclusive output-end trim"
        ),
        "fps": float(summary["fps"]),
        "fps_fraction": summary["fps_fraction"],
        "plane_visible_frames": [0, visible_end - 1],
        "plane_prediction_frames": (
            [visible_end, handoff_end - 1] if handoff_end > visible_end else []
        ),
        "background_only_frames": (
            [handoff_end, frame_count - 1] if frame_count > handoff_end else []
        ),
        "plane_handoff_frames": handoff_frames,
        "target_shoreline_fraction_from_bottom": shoreline_from_bottom,
        "target_plane_fraction_of_width": target_plane_x
        / float(summary["source_width"]),
        "minimum_zoom": minimum_zoom,
        "zoom_safety_multiplier": zoom_safety,
        "safe_zoom": safe_zoom,
        "retained_linear_fraction": 1.0 / safe_zoom,
        "relative_plane_velocity_at_occlusion_source_px_per_frame": [
            relative_dx,
            relative_dy,
        ],
        "shoreline_zero_weight_frames": np.flatnonzero(shore_weights <= 1e-6)
        .astype(int)
        .tolist(),
        "shoreline_temporal_outlier_frames": np.flatnonzero(
            shoreline_temporal_outlier
        )
        .astype(int)
        .tolist(),
        "render_model": (
            "fixed zoom; roll+y from outlier-gated shoreline/KLT fusion; "
            "x plane-lock with raised-cosine handoff to land background"
        ),
        "plane_y_constrained": False,
        "master_color_intent": "10-bit HLG BT.2020; Dolby Vision RPU not retained after geometry",
    }
    (analysis_dir / SOLUTION_JSON).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return result


def open_sdr_decoder(
    input_path: Path, width: int, height: int
) -> subprocess.Popen[bytes]:
    filter_chain = (
        "zscale=t=linear:npl=100,format=gbrpf32le,"
        "tonemap=tonemap=hable:desat=0,"
        "zscale=p=bt709:t=bt709:m=bt709:r=tv,"
        f"scale={width}:{height}:flags=lanczos,format=bgr24"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-vf",
        filter_chain,
        "-vsync",
        "0",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def open_sdr_writer(
    output_path: Path,
    audio_source: Path,
    width: int,
    height: int,
    fps_fraction: str,
    duration_seconds: float,
    crf: int,
    preset: str,
) -> subprocess.Popen[bytes]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        fps_fraction,
        "-i",
        "-",
        "-t",
        f"{duration_seconds:.12f}",
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-vf",
        "scale=in_range=pc:out_range=tv,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "copy",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def render_sdr_preview(
    input_path: Path,
    analysis_dir: Path,
    output_path: Path,
    crf: int,
    preset: str,
) -> None:
    solution = json.loads((analysis_dir / SOLUTION_JSON).read_text(encoding="utf-8"))
    if canonical_path(solution["input"]) != canonical_path(input_path):
        raise RuntimeError("Solution input does not match preview input")
    tracks = load_tracks(analysis_dir / SOLUTION_CSV)
    width = int(solution["analysis_width"])
    height = int(solution["analysis_height"])
    matrices = build_output_affines(tracks, solution, width, height)
    decoder = open_sdr_decoder(input_path, width, height)
    writer = open_sdr_writer(
        output_path,
        input_path,
        width,
        height,
        solution["fps_fraction"],
        len(matrices) / parse_fraction(solution["fps_fraction"]),
        crf,
        preset,
    )
    assert decoder.stdout is not None
    assert writer.stdin is not None
    frame_bytes = width * height * 3
    for index, matrix in enumerate(matrices):
        raw = read_exact(decoder.stdout, frame_bytes)
        if len(raw) != frame_bytes:
            raise RuntimeError(f"SDR decoder ended at frame {index}")
        frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
        output = cv2.warpAffine(
            frame,
            matrix,
            (width, height),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        writer.stdin.write(output.tobytes())
        if (index + 1) % 200 == 0 or index + 1 == len(matrices):
            print(f"Preview {index + 1}/{len(matrices)}", flush=True)
    stop_raw_decoder(decoder)
    writer.stdin.close()
    if writer.wait() != 0:
        raise RuntimeError("SDR preview encoder failed")


def open_hdr_decoder(input_path: Path) -> subprocess.Popen[bytes]:
    filter_chain = (
        "zscale=matrixin=2020_ncl:transferin=arib-std-b67:primariesin=2020:"
        "rangein=limited:matrix=gbr:transfer=arib-std-b67:primaries=2020:"
        "range=full,format=gbrp16le,format=rgb48le"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        filter_chain,
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb48le",
        "-",
    ]
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def open_hdr_writer(
    output_path: Path,
    audio_source: Path,
    width: int,
    height: int,
    fps_fraction: str,
    duration_seconds: float,
    crf: int,
    preset: str,
) -> subprocess.Popen[bytes]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_chain = (
        "format=gbrp16le,"
        "zscale=matrixin=gbr:transferin=arib-std-b67:primariesin=2020:"
        "rangein=full:matrix=2020_ncl:transfer=arib-std-b67:primaries=2020:"
        "range=limited,format=yuv420p10le"
    )
    x265 = "repeat-headers=1:colorprim=9:transfer=18:colormatrix=9:range=limited"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb48le",
        "-s:v",
        f"{width}x{height}",
        "-r",
        fps_fraction,
        "-i",
        "-",
        "-t",
        f"{duration_seconds:.12f}",
        "-i",
        str(audio_source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-vf",
        filter_chain,
        "-c:v",
        "libx265",
        "-profile:v",
        "main10",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-x265-params",
        x265,
        "-tag:v",
        "hvc1",
        "-c:a",
        "copy",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "arib-std-b67",
        "-colorspace",
        "bt2020nc",
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def render_hdr_master(
    input_path: Path,
    analysis_dir: Path,
    output_path: Path,
    crf: int,
    preset: str,
) -> None:
    solution = json.loads((analysis_dir / SOLUTION_JSON).read_text(encoding="utf-8"))
    if canonical_path(solution["input"]) != canonical_path(input_path):
        raise RuntimeError("Solution input does not match HDR input")
    tracks = load_tracks(analysis_dir / SOLUTION_CSV)
    width = int(solution["source_width"])
    height = int(solution["source_height"])
    matrices = build_output_affines(tracks, solution, width, height)
    decoder = open_hdr_decoder(input_path)
    writer = open_hdr_writer(
        output_path,
        input_path,
        width,
        height,
        solution["fps_fraction"],
        len(matrices) / parse_fraction(solution["fps_fraction"]),
        crf,
        preset,
    )
    assert decoder.stdout is not None
    assert writer.stdin is not None
    frame_bytes = width * height * 3 * 2
    for index, matrix in enumerate(matrices):
        raw = read_exact(decoder.stdout, frame_bytes)
        if len(raw) != frame_bytes:
            raise RuntimeError(f"HDR decoder ended at frame {index}")
        frame = np.frombuffer(raw, dtype="<u2").reshape(height, width, 3)
        output = cv2.warpAffine(
            frame,
            matrix,
            (width, height),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        writer.stdin.write(output.astype("<u2", copy=False).tobytes())
        if (index + 1) % 100 == 0 or index + 1 == len(matrices):
            print(f"HDR render {index + 1}/{len(matrices)}", flush=True)
    stop_raw_decoder(decoder)
    writer.stdin.close()
    if writer.wait() != 0:
        raise RuntimeError("HDR master encoder failed")


def analyze_preview_qa(
    preview_path: Path,
    analysis_dir: Path,
) -> dict:
    solution = json.loads((analysis_dir / SOLUTION_JSON).read_text(encoding="utf-8"))
    tracks = load_tracks(analysis_dir / SOLUTION_CSV)
    width = int(solution["analysis_width"])
    height = int(solution["analysis_height"])
    matrices = build_output_affines(tracks, solution, width, height)
    visible_end = int(solution["plane_visible_frames"][1]) + 1
    expected_shoreline_y = float(solution["target_shoreline_analysis_y"])
    capture = cv2.VideoCapture(str(preview_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open preview {preview_path}")
    ok, first = capture.read()
    if not ok:
        raise RuntimeError("Could not decode preview frame zero")

    source_seed = np.array(
        [
            [235 / 2, 545 / 2],
            [(235 + 140) / 2, (545 + 65) / 2],
        ],
        np.float32,
    )[None, :, :]
    transformed = cv2.transform(source_seed, matrices[0])[0]
    x0, y0 = np.floor(transformed[0]).astype(int)
    x1, y1 = np.ceil(transformed[1]).astype(int)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    template_gray = cv2.cvtColor(first[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    threshold = max(150.0, float(np.percentile(template_gray, 76)))
    template = np.where(template_gray > threshold, 255, 0).astype(np.uint8)
    template = cv2.morphologyEx(
        template, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
    )
    bbox_w, bbox_h = x1 - x0, y1 - y0
    plane_x_values: list[float] = []
    plane_scores: list[float] = []
    shore_angles: list[float] = []
    shore_y_values: list[float] = []
    shore_confidence: list[float] = []
    land_dx_values: list[float] = []
    land_dy_values: list[float] = []
    land_angle_values: list[float] = []
    land_scale_values: list[float] = []
    land_inlier_values: list[int] = []
    land_ratio_values: list[float] = []
    land_valid_values: list[int] = []
    qa_rows: list[dict[str, float | int]] = []
    rng = np.random.default_rng(20260814)
    current_x, current_y = float(x0), float(y0)
    vx = vy = 0.0
    previous_gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    previous_plane_bbox: tuple[int, int, int, int] | None = None

    for index in range(int(solution["frames"])):
        if index == 0:
            frame = first
            gray = previous_gray
            score = 1.0
            land = {
                "dx": 0.0,
                "dy": 0.0,
                "angle": 0.0,
                "scale": 1.0,
                "inliers": 0,
                "ratio": 0.0,
                "valid": 0,
            }
        else:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Preview decode stopped at frame {index}")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if index < visible_end:
                predicted_x = int(round(current_x + vx))
                predicted_y = int(round(current_y + vy))
                left = max(0, predicted_x - 35)
                top = max(0, predicted_y - 35)
                right = min(width, predicted_x + bbox_w + 35)
                bottom = min(height, predicted_y + bbox_h + 35)
                local_threshold = max(150.0, float(np.percentile(gray[top:bottom, left:right], 91)))
                binary = np.where(gray > local_threshold, 255, 0).astype(np.uint8)
                response = cv2.matchTemplate(
                    binary[top:bottom, left:right], template, cv2.TM_CCOEFF_NORMED
                )
                _, score, _, location = cv2.minMaxLoc(response)
                new_x = float(left + location[0])
                new_y = float(top + location[1])
                dx, dy = new_x - current_x, new_y - current_y
                vx = 0.65 * vx + 0.35 * dx
                vy = 0.65 * vy + 0.35 * dy
                current_x, current_y = new_x, new_y
            else:
                score = 0.0
            land = estimate_land_motion(
                previous_gray,
                gray,
                expected_shoreline_y,
                previous_plane_bbox,
                anchor_y=expected_shoreline_y,
            )
            land_dx_values.append(float(land["dx"]))
            land_dy_values.append(float(land["dy"]))
            land_angle_values.append(float(land["angle"]))
            land_scale_values.append(float(land["scale"]))
            land_inlier_values.append(int(land["inliers"]))
            land_ratio_values.append(float(land["ratio"]))
            land_valid_values.append(int(land["valid"]))
        slope, center_y, confidence, _ = detect_shoreline(
            frame,
            rng,
            search_center_y=expected_shoreline_y,
            search_half_height=22.0,
        )
        _, level_y = rotation_for_shoreline(width, height, slope, center_y)
        shore_angles.append(math.degrees(math.atan(slope)))
        shore_y_values.append(level_y)
        shore_confidence.append(confidence)
        plane_x_values.append(current_x + bbox_w / 2)
        plane_scores.append(float(score))
        qa_rows.append(
            {
                "frame": index,
                "time_s": index / float(solution["fps"]),
                "shoreline_angle_deg": math.degrees(math.atan(slope)),
                "shoreline_level_y": level_y,
                "shoreline_confidence": confidence,
                "plane_x": current_x + bbox_w / 2,
                "plane_score": float(score),
                "land_residual_dx": float(land["dx"]),
                "land_residual_dy": float(land["dy"]),
                "land_residual_angle_deg": float(land["angle"]),
                "land_residual_scale": float(land["scale"]),
                "land_inliers": int(land["inliers"]),
                "land_inlier_ratio": float(land["ratio"]),
                "land_valid": int(land["valid"]),
            }
        )
        previous_gray = gray
        previous_plane_bbox = (
            (
                int(round(current_x)),
                int(round(current_y)),
                bbox_w,
                bbox_h,
            )
            if index < visible_end
            else None
        )
    capture.release()

    with (analysis_dir / "qa_tracks.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(qa_rows[0]))
        writer.writeheader()
        writer.writerows(qa_rows)

    angle = np.asarray(shore_angles)
    shore_y = np.asarray(shore_y_values)
    plane_x = np.asarray(plane_x_values[:visible_end])
    expected_plane_x = float(solution["target_plane_raw_source_x"]) * (
        width / float(solution["source_width"])
    )
    shoreline_y_tolerance = 12.0
    shoreline_angle_tolerance = 1.5
    shore_good = (
        (np.abs(shore_y - expected_shoreline_y) <= shoreline_y_tolerance)
        & (np.abs(angle) <= shoreline_angle_tolerance)
    )
    plane_residual = plane_x - expected_plane_x

    land_dx = np.asarray(land_dx_values)
    land_dy = np.asarray(land_dy_values)
    land_angle = np.asarray(land_angle_values)
    land_scale = np.asarray(land_scale_values)
    land_inliers = np.asarray(land_inlier_values)
    land_ratio = np.asarray(land_ratio_values)
    land_valid = (
        (np.asarray(land_valid_values) >= 1)
        & (land_inliers >= 40)
        & (land_ratio >= 0.50)
        & np.isfinite(land_dy)
        & np.isfinite(land_angle)
    )
    minimum_land_coverage = 0.90
    land_coverage = float(np.mean(land_valid))
    if land_coverage < minimum_land_coverage:
        raise RuntimeError(
            "Too few reliable decoded-preview land registrations: "
            f"{int(land_valid.sum())}/{land_valid.size}"
        )

    def invalid_ranges(valid: np.ndarray) -> list[list[int]]:
        invalid = np.flatnonzero(~valid)
        if invalid.size == 0:
            return []
        breaks = np.flatnonzero(np.diff(invalid) > 1) + 1
        groups = np.split(invalid, breaks)
        return [[int(group[0]), int(group[-1])] for group in groups]

    def stats(values: np.ndarray) -> dict[str, float]:
        return {
            "median": float(np.median(values)),
            "rms": float(np.sqrt(np.mean(values**2))),
            "p95_abs": float(np.percentile(np.abs(values), 95)),
            "max_abs": float(np.max(np.abs(values))),
        }

    shoreline_invalid_ranges = invalid_ranges(shore_good)
    shoreline_gap_lengths = [
        end - start + 1 for start, end in shoreline_invalid_ranges
    ]
    cumulative_land_y = np.r_[
        0.0, np.cumsum(np.where(land_valid, land_dy, 0.0))
    ]
    cumulative_y_min_frame = int(np.argmin(cumulative_land_y))
    cumulative_y_max_frame = int(np.argmax(cumulative_land_y))

    metrics = {
        "preview": str(preview_path.resolve()),
        "frames": len(shore_angles),
        "target_plane_x_preview_px": expected_plane_x,
        "measured_plane_x_median_preview_px": float(np.median(plane_x)),
        "plane_x_error_preview_px": stats(plane_residual),
        "primary_stability_metric": (
            "decoded-preview adjacent-frame land KLT/RANSAC; horizontal motion "
            "ignored"
        ),
        "land_registration_valid_pairs": int(land_valid.sum()),
        "land_registration_total_pairs": int(land_valid.size),
        "land_registration_coverage_fraction": land_coverage,
        "land_registration_minimum_coverage_fraction": minimum_land_coverage,
        "land_registration_quality_gate": {
            "minimum_inliers": 40,
            "minimum_inlier_ratio": 0.50,
        },
        "land_residual_roll_deg_per_frame": stats(land_angle[land_valid]),
        "land_residual_vertical_preview_px_per_frame": stats(
            land_dy[land_valid]
        ),
        "land_residual_cumulative_vertical_preview_px": {
            "end": float(cumulative_land_y[-1]),
            "minimum": float(cumulative_land_y[cumulative_y_min_frame]),
            "minimum_frame": cumulative_y_min_frame,
            "maximum": float(cumulative_land_y[cumulative_y_max_frame]),
            "maximum_frame": cumulative_y_max_frame,
            "peak_to_peak": float(np.ptp(cumulative_land_y)),
        },
        "land_scale_error_per_frame": stats(land_scale[land_valid] - 1.0),
        "median_land_registration_inliers": float(
            np.median(land_inliers[land_valid])
        ),
        "median_land_registration_inlier_ratio": float(
            np.median(land_ratio[land_valid])
        ),
        "horizontal_land_motion_ignored": True,
        "shoreline_role": (
            "geometry-only diagnostic coverage; not a stability metric and not "
            "interpolated"
        ),
        "target_shoreline_y_preview_px": expected_shoreline_y,
        "shoreline_geometry_tolerances": {
            "y_preview_px": shoreline_y_tolerance,
            "angle_deg": shoreline_angle_tolerance,
        },
        "shoreline_geometry_valid_frames": int(shore_good.sum()),
        "shoreline_geometry_total_frames": int(shore_good.size),
        "shoreline_geometry_coverage_fraction": float(np.mean(shore_good)),
        "shoreline_geometry_invalid_gap_count": len(shoreline_invalid_ranges),
        "shoreline_geometry_longest_invalid_gap_frames": (
            max(shoreline_gap_lengths) if shoreline_gap_lengths else 0
        ),
        "shoreline_geometry_longest_invalid_gap_seconds": (
            max(shoreline_gap_lengths) / float(solution["fps"])
            if shoreline_gap_lengths
            else 0.0
        ),
        "shoreline_geometry_invalid_ranges": shoreline_invalid_ranges,
        "shoreline_geometry_valid_y_error_preview_px": stats(
            shore_y[shore_good] - expected_shoreline_y
        ),
        "shoreline_geometry_valid_angle_deg": stats(angle[shore_good]),
        "median_plane_qa_score": float(np.median(plane_scores[:visible_end])),
        "median_shoreline_qa_confidence": float(np.median(shore_confidence)),
    }
    (analysis_dir / QA_JSON).write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["analyze", "solve", "preview", "qa", "render", "all"]
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--analysis-width", type=int, default=960)
    parser.add_argument(
        "--plane-visible-end",
        type=int,
        default=1128,
        help="Exclusive last fully visible aircraft frame; default: 1128.",
    )
    parser.add_argument(
        "--output-end",
        type=int,
        default=1223,
        help=(
            "Exclusive output end frame; default 1223 retains the full source."
        ),
    )
    parser.add_argument(
        "--shoreline-from-bottom",
        type=float,
        default=0.30,
        help="Output shoreline position measured upward from bottom; default 0.30.",
    )
    parser.add_argument(
        "--handoff-frames",
        type=int,
        default=32,
        help="Raised-cosine aircraft-to-background handoff length; default 32.",
    )
    parser.add_argument("--zoom-safety", type=float, default=1.02)
    parser.add_argument("--preview-crf", type=int, default=18)
    parser.add_argument("--master-crf", type=int, default=17)
    parser.add_argument("--preset", default="slow")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    analysis_dir = args.analysis_dir.resolve()
    preview_path = args.preview.resolve()
    output_path = args.output.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if args.mode in {"render", "all"} and canonical_path(input_path) == canonical_path(
        output_path
    ):
        raise ValueError("Input and output paths must differ")
    if args.mode in {"preview", "all"} and canonical_path(input_path) == canonical_path(
        preview_path
    ):
        raise ValueError("Input and preview paths must differ")

    if args.mode in {"analyze", "all"}:
        analyze_video(
            input_path,
            analysis_dir,
            args.analysis_width,
            args.plane_visible_end,
        )
    if args.mode in {"solve", "all"}:
        solve_transforms(
            input_path,
            analysis_dir,
            args.zoom_safety,
            args.output_end,
            args.shoreline_from_bottom,
            args.handoff_frames,
        )
    if args.mode in {"preview", "all"}:
        render_sdr_preview(
            input_path,
            analysis_dir,
            preview_path,
            args.preview_crf,
            args.preset,
        )
    if args.mode in {"qa", "all"}:
        analyze_preview_qa(preview_path, analysis_dir)
    if args.mode in {"render", "all"}:
        render_hdr_master(
            input_path,
            analysis_dir,
            output_path,
            args.master_crf,
            args.preset,
        )


if __name__ == "__main__":
    main()
