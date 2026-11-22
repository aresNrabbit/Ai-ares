
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


class AnalysisError(Exception):
    pass


def run_command(args, timeout=180):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
    except FileNotFoundError as exc:
        raise AnalysisError(
            "FFmpeg and FFprobe must be installed in Termux."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("Video analysis timed out.") from exc

    if result.returncode != 0:
        message = (result.stderr or "Unknown FFmpeg error")[-1200:]
        raise AnalysisError(message)

    return result


def get_metadata(video_path):
    result = run_command([
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration,size,format_name:"
        "stream=codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", str(video_path)
    ])

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AnalysisError("Could not read video metadata.") from exc

    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None
    )

    if not video:
        raise AnalysisError("No video stream was found.")

    try:
        duration = float(data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        duration = 0

    if duration <= 0:
        raise AnalysisError("The video duration could not be determined.")

    return {
        "duration": round(duration, 2),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "codec": video.get("codec_name", "unknown"),
        "format": data.get("format", {}).get("format_name", "unknown"),
        "fps": video.get("r_frame_rate", "unknown"),
        "size_mb": round(
            int(data.get("format", {}).get("size", 0)) / 1048576, 2
        ),
        "has_audio": any(
            s.get("codec_type") == "audio" for s in streams
        )
    }


def frame_features(path):
    with Image.open(path) as original:
        image = original.convert("RGB")
        image.thumbnail((96, 96))

        gray = image.convert("L")
        stat = ImageStat.Stat(image)
        gray_stat = ImageStat.Stat(gray)

        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_mean = ImageStat.Stat(edges).mean[0]

        histogram = gray.histogram()
        total = max(1, sum(histogram))
        bins = []
        for i in range(16):
            start = i * 16
            bins.append(sum(histogram[start:start + 16]) / total)

        return {
            "brightness": round(gray_stat.mean[0], 2),
            "contrast": round(gray_stat.stddev[0], 2),
            "red": round(stat.mean[0], 2),
            "green": round(stat.mean[1], 2),
            "blue": round(stat.mean[2], 2),
            "edge_strength": round(edge_mean, 2),
            "histogram": bins,
            "gray": gray.resize((48, 48))
        }


def histogram_distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / 2


def image_difference(a, b):
    difference = ImageStat.Stat(
        Image.eval_pair(a, b, lambda x, y: abs(x - y))
    ).mean[0]
    return round(difference, 2)


def analyze_audio(video_path):
    if not shutil.which("ffmpeg"):
        return {"available": False, "reason": "FFmpeg unavailable"}

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(video_path),
            "-vn",
            "-af",
            "silencedetect=noise=-35dB:d=0.5,volumedetect",
            "-f", "null", "-"
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False
    )

    log = result.stderr or ""

    mean_match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", log)
    max_match = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", log)

    silences = []
    starts = re.findall(r"silence_start:\s*([\d.]+)", log)
    ends = re.findall(r"silence_end:\s*([\d.]+)", log)

    for index, start in enumerate(starts):
        end = float(ends[index]) if index < len(ends) else None
        silences.append({
            "start": round(float(start), 2),
            "end": round(end, 2) if end is not None else None
        })

    return {
        "available": True,
        "mean_volume_db": float(mean_match.group(1)) if mean_match else None,
        "max_volume_db": float(max_match.group(1)) if max_match else None,
        "silence_regions": silences,
        "note": "Loudness and silence estimates only; no speech recognition."
    }


def analyze_video(video_path, max_duration=900):
    video_path = Path(video_path)

    if not video_path.exists():
        raise AnalysisError("Video file not found.")

    metadata = get_metadata(video_path)

    if metadata["duration"] > max_duration:
        raise AnalysisError(
            f"Video is {metadata['duration']} seconds. "
            f"Maximum allowed is {max_duration} seconds."
        )

    if metadata["width"] <= 0 or metadata["height"] <= 0:
        raise AnalysisError("Invalid video dimensions.")

    # Sample about one frame every two seconds.
    interval = max(1.0, metadata["duration"] / 150.0)
    interval = max(1.0, min(3.0, interval))
    sample_limit = min(180, math.ceil(metadata["duration"] / interval) + 1)

    with tempfile.TemporaryDirectory(prefix="vidtool_") as temp:
        temp_dir = Path(temp)
        pattern = temp_dir / "frame_%05d.jpg"

        run_command([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path),
            "-vf", f"fps=1/{interval:.3f},scale=480:-2",
            "-frames:v", str(sample_limit),
            "-q:v", "4",
            str(pattern)
        ], timeout=240)

        frame_paths = sorted(temp_dir.glob("frame_*.jpg"))

        if not frame_paths:
            raise AnalysisError("FFmpeg could not extract video frames.")

        frames = []
        previous = None

        for index, path in enumerate(frame_paths):
            feature = frame_features(path)
            timestamp = min(
                round(index * interval, 2),
                metadata["duration"]
            )

            motion = 0.0
            scene_change = 0.0

            if previous is not None:
                motion = image_difference(
                    previous["gray"], feature["gray"]
                )
                scene_change = histogram_distance(
                    previous["histogram"], feature["histogram"]
                )

            feature.pop("gray")
            feature["timestamp"] = timestamp
            feature["motion_change"] = motion
            feature["scene_change_score"] = round(scene_change, 4)
            frames.append(feature)

            previous = {
                "gray": Image.open(path).convert("L").resize((48, 48)),
                "histogram": feature["histogram"]
            }

        # Scene boundaries are estimates based on histogram changes.
        cuts = []
        for frame in frames[1:]:
            if frame["scene_change_score"] >= 0.38:
                cuts.append(frame["timestamp"])

        boundaries = [0.0] + cuts + [metadata["duration"]]
        scenes = []

        for index in range(len(boundaries) - 1):
            start = boundaries[index]
            end = boundaries[index + 1]
            members = [
                f for f in frames
                if start <= f["timestamp"] < end
            ]

            if not members:
                continue

            scenes.append({
                "scene": len(scenes) + 1,
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "keyframe_time": members[len(members) // 2]["timestamp"],
                "average_brightness": round(
                    sum(f["brightness"] for f in members) / len(members), 2
                ),
                "average_contrast": round(
                    sum(f["contrast"] for f in members) / len(members), 2
                ),
                "average_motion_change": round(
                    sum(f["motion_change"] for f in members) / len(members), 2
                ),
                "average_rgb": {
                    channel: round(
                        sum(f[channel] for f in members) / len(members), 1
                    )
                    for channel in ("red", "green", "blue")
                }
            })

        average_motion = round(
            sum(f["motion_change"] for f in frames) / max(1, len(frames)), 2
        )

        average_brightness = round(
            sum(f["brightness"] for f in frames) / len(frames), 2
        )

        audio = (
            analyze_audio(video_path)
            if metadata["has_audio"]
            else {"available": False, "reason": "No audio stream"}
        )

        report = {
            "app": "Vidtool",
            "version": "2.0",
            "analysis_type": "pure_offline_algorithms",
            "metadata": metadata,
            "summary": {
                "sampled_frames": len(frames),
                "estimated_scenes": len(scenes),
                "average_brightness": average_brightness,
                "average_frame_change": average_motion,
                "sampling_interval_seconds": round(interval, 2)
            },
            "scenes": scenes,
            "frames": frames,
            "audio": audio,
            "limitations": [
                "Scene boundaries are estimates from sampled-frame changes.",
                "Frame changes do not prove camera movement.",
                "No object, person, action, location or emotion recognition.",
                "Audio analysis measures loudness and silence, not speech meaning."
            ]
        }

        report["prompt"] = build_prompt(report)
        return report


def build_prompt(report):
    meta = report["metadata"]
    lines = [
        "VIDEO RECREATION PROMPT — EVIDENCE-BASED",
        "",
        f"Video duration: {meta['duration']} seconds.",
        f"Frame dimensions: {meta['width']}x{meta['height']}.",
        f"Frame rate metadata: {meta['fps']}.",
        f"Video codec: {meta['codec']}.",
        f"Estimated scene count: {len(report['scenes'])}.",
        "",
        "SCENE STRUCTURE:"
    ]

    for scene in report["scenes"]:
        rgb = scene["average_rgb"]
        lines.append(
            f"Scene {scene['scene']} ({scene['start']}s–{scene['end']}s): "
            f"brightness {scene['average_brightness']}/255, "
            f"contrast {scene['average_contrast']}, "
            f"frame-change level {scene['average_motion_change']}, "
            f"average RGB ({rgb['red']}, {rgb['green']}, {rgb['blue']})."
        )

    audio = report["audio"]
    lines.extend(["", "AUDIO:"])

    if audio.get("available"):
        lines.append(
            f"Mean loudness: {audio.get('mean_volume_db')} dB; "
            f"maximum loudness: {audio.get('max_volume_db')} dB."
        )
        lines.append(
            f"Detected silence regions: {len(audio.get('silence_regions', []))}."
        )
    else:
        lines.append(audio.get("reason", "Audio not analyzed."))

    lines.extend([
        "",
        "RECREATION GUIDANCE:",
        "Use the measured scene timing, visual brightness, contrast, "
        "colour balance and changes in the source video.",
        "Do not assume objects, people, actions, locations, dialogue, "
        "or camera movements that have not been independently identified.",
        "This report contains technical measurements, not semantic scene understanding."
    ])

    return "\n".join(lines)
