from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageStat, ImageChops


class AnalysisError(Exception):
    pass


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise AnalysisError(f"{name} was not found. Install FFmpeg in Termux with: pkg install ffmpeg")
    return path


def _run(args, timeout=120):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise AnalysisError("Media processing timed out. Try a shorter or smaller video.")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Unknown FFmpeg error").strip()
        raise AnalysisError(detail[-1200:])
    return proc.stdout


def _probe(path: Path):
    raw = _run([_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path)], timeout=45)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise AnalysisError("Could not read the video's metadata. The file may be damaged.")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise AnalysisError("No video stream was found in this file.")
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise AnalysisError("Video duration could not be determined.")
    return {
        "duration_seconds": round(duration, 2),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "video_codec": video.get("codec_name", "unknown"),
        "audio_present": audio is not None,
        "audio_codec": audio.get("codec_name", "none") if audio else "none",
        "format": fmt.get("format_name", "unknown"),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
    }


def _parse_fps(value):
    try:
        a, b = value.split("/")
        return round(float(a) / float(b), 3) if float(b) else None
    except Exception:
        try:
            return round(float(value), 3)
        except Exception:
            return None


def _frame_metrics(path: Path):
    with Image.open(path) as source:
        im = source.convert("RGB")
        im.thumbnail((160, 160))
        stat = ImageStat.Stat(im)
        means = [round(v, 1) for v in stat.mean]
        brightness = round(sum(means) / 3, 1)
        gray = im.convert("L")
        hist = gray.histogram()
        total = sum(hist) or 1
        entropy = -sum((n / total) * math.log2(n / total) for n in hist if n)
        # A lightweight edge proxy from neighboring pixel differences.
        small = gray.resize((80, 80))
        px = small.load()
        w, h = small.size
        edge_sum = 0
        count = 0
        for y in range(h - 1):
            for x in range(w - 1):
                edge_sum += abs(px[x, y] - px[x + 1, y]) + abs(px[x, y] - px[x, y + 1])
                count += 2
        edge_density = round(edge_sum / max(count, 1), 2)
        return {"mean_rgb": means, "brightness": brightness,
                "contrast_proxy": round(stat.stddev[0] if len(stat.stddev) else 0, 1),
                "gray_entropy": round(entropy, 2), "edge_density": edge_density,
                "width": im.width, "height": im.height}, gray


def _difference(a, b):
    a = a.resize((80, 80))
    b = b.resize((80, 80))
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return round(sum(stat.mean) / 255 * 100, 2)


def _audio_summary(path: Path, audio_present: bool):
    if not audio_present:
        return {"present": False, "note": "No audio stream detected."}
    # FFmpeg's built-in volume detector: no external speech service or API.
    result = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-i", str(path),
                             "-vn", "-af", "volumedetect", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=120)
    text = result.stderr or ""
    mean = re.search(r" mean_volume:\s*(-?[\d.]+) dB", text)
    peak = re.search(r" max_volume:\s*(-?[\d.]+) dB", text)
    return {
        "present": True,
        "mean_volume_db": float(mean.group(1)) if mean else None,
        "peak_volume_db": float(peak.group(1)) if peak else None,
        "note": "Volume statistics only. This offline build does not transcribe speech or identify sound sources."
    }


def analyze_video(path: Path, max_duration=900):
    meta = _probe(path)
    if meta["duration_seconds"] > max_duration:
        raise AnalysisError(f"Video is {meta['duration_seconds']} seconds. Current limit is {max_duration} seconds.")
    if meta["width"] <= 0 or meta["height"] <= 0:
        raise AnalysisError("Video dimensions are invalid.")

    # Aim for roughly one sample every 2 seconds, capped to keep mobile memory use modest.
    interval = 2.0
    sample_count = max(1, min(120, int(meta["duration_seconds"] / interval) + 1))
    with tempfile.TemporaryDirectory(prefix="vidtool-") as temp:
        pattern = str(Path(temp) / "frame_%04d.jpg")
        _run([_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path),
              "-vf", f"fps=1/2,scale=480:-2", "-frames:v", "120",
              "-q:v", "4", pattern], timeout=180)
        frame_paths = sorted(Path(temp).glob("frame_*.jpg"))
        if not frame_paths:
            raise AnalysisError("FFmpeg could not extract any frames from this video.")

        frames = []
        previous_gray = None
        for idx, frame_path in enumerate(frame_paths):
            metrics, gray = _frame_metrics(frame_path)
            timestamp = round(idx * interval, 2)
            change = _difference(previous_gray, gray) if previous_gray is not None else 0.0
            metrics["timestamp_seconds"] = timestamp
            metrics["change_from_previous_percent"] = change
            frames.append(metrics)
            previous_gray = gray

    # Scene boundaries are heuristics based on large frame-to-frame visual changes.
    boundaries = [0]
    for i in range(1, len(frames)):
        if frames[i]["change_from_previous_percent"] >= 18:
            boundaries.append(i)
    boundaries.append(len(frames))
    scenes = []
    for n in range(len(boundaries) - 1):
        start_i, end_i = boundaries[n], boundaries[n + 1]
        if end_i <= start_i:
            continue
        subset = frames[start_i:end_i]
        start = subset[0]["timestamp_seconds"]
        end = (frames[end_i]["timestamp_seconds"] if end_i < len(frames)
               else meta["duration_seconds"])
        avg_brightness = round(sum(f["brightness"] for f in subset) / len(subset), 1)
        avg_change = round(sum(f["change_from_previous_percent"] for f in subset) / len(subset), 2)
        scenes.append({
            "scene_number": len(scenes) + 1,
            "start_seconds": start,
            "end_seconds": round(max(start, end), 2),
            "sampled_frames": len(subset),
            "average_brightness": avg_brightness,
            "visual_change_score": avg_change,
            "description": _scene_text(avg_brightness, avg_change, len(subset))
        })

    avg_brightness = round(sum(f["brightness"] for f in frames) / len(frames), 1)
    avg_edge = round(sum(f["edge_density"] for f in frames) / len(frames), 2)
    motion_values = [f["change_from_previous_percent"] for f in frames[1:]]
    avg_change = round(sum(motion_values) / len(motion_values), 2) if motion_values else 0
    motion_label = ("low sampled visual change" if avg_change < 4 else
                    "moderate sampled visual change" if avg_change < 10 else
                    "high sampled visual change")
    audio = _audio_summary(path, meta["audio_present"])
    report = {
        "app": "Vidtool",
        "version": "1.0.0-pure-offline",
        "analysis_mode": "Pure offline algorithms; no AI API and no pretrained model",
        "metadata": meta,
        "visual_summary": {
            "sampled_frames": len(frames),
            "average_brightness_0_255": avg_brightness,
            "average_edge_density_proxy": avg_edge,
            "average_frame_change_percent": avg_change,
            "visual_change_label": motion_label,
            "color_note": "Average RGB values are measurements of sampled frames, not semantic color descriptions."
        },
        "frames": frames,
        "scenes": scenes,
        "audio": audio,
        "limitations": [
            "This build does not recognize people, animals, objects, actions, locations or emotions.",
            "Scene boundaries are estimates from sampled-frame differences; subtle cuts may be missed.",
            "Frame-to-frame change is not the same as camera motion: moving subjects and edits can also cause change.",
            "Audio analysis reports volume only; it does not transcribe speech or identify music/sound effects."
        ]
    }
    report["recreation_prompt"] = build_prompt(report)
    return report


def _scene_text(brightness, change, samples):
    light = "bright" if brightness >= 175 else "mid-lit" if brightness >= 85 else "dark"
    activity = "visually stable" if change < 4 else "visually changing" if change < 10 else "visually dynamic"
    return (f"Estimated {light} segment; {activity} across {samples} sampled frames. "
            "Subject matter and exact actions were not semantically identified.")


def build_prompt(report):
    m = report["metadata"]
    v = report["visual_summary"]
    lines = [
        "Recreate the supplied reference video as closely as possible using the following measured structural characteristics.",
        f"Format: {m['width']}x{m['height']} pixels, approximately {m['fps'] or 'unknown'} fps, "
        f"duration {m['duration_seconds']} seconds.",
        f"Overall sampled visual behavior: {v['visual_change_label']} "
        f"(mean sampled-frame difference {v['average_frame_change_percent']}%).",
        f"Average sampled brightness: {v['average_brightness_0_255']}/255. "
        "Preserve the reference's overall exposure and contrast rather than inventing a new look.",
        "Maintain the original chronological order, pacing, aspect ratio and approximate scene durations."
    ]
    for s in report["scenes"]:
        lines.append(
            f"At {s['start_seconds']}s–{s['end_seconds']}s: {s['description']} "
            "Match the observed visual transition and timing."
        )
    if report["audio"]["present"]:
        lines.append("Retain an audio track with a comparable overall loudness profile; exact speech and sound sources were not identified.")
    else:
        lines.append("The reference contains no detected audio stream; keep the recreation silent unless intentionally changed.")
    lines.append("Do not add objects, people, actions, locations, dialogue or camera movements that are not supported by the reference.")
    lines.append("Note: this prompt is based on offline visual measurements, not semantic scene recognition.")
    return "\n".join(lines)
