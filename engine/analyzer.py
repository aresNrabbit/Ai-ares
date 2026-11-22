from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


class AnalysisError(Exception):
    pass


def run_command(command, timeout=120):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise AnalysisError("Video processing timed out.")
    except FileNotFoundError as exc:
        raise AnalysisError(
            "FFmpeg or FFprobe was not found."
        ) from exc

    if result.returncode != 0:
        message = result.stderr.strip()[-1500:]
        raise AnalysisError(message or "Media processing failed.")

    return result.stdout


def get_metadata(video_path):
    output = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json",
        str(video_path),
    ])

    try:
        data = json.loads(output)
        duration = float(data.get("format", {}).get("duration", 0))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("Could not read video metadata.") from exc

    streams = data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"),
        None,
    )

    if not video_stream:
        raise AnalysisError("The uploaded file has no video stream.")

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    fps_text = video_stream.get("r_frame_rate", "0/1")
    try:
        numerator, denominator = fps_text.split("/")
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    return {
        "duration": round(duration, 2),
        "format": data.get("format", {}).get("format_name", "unknown"),
        "video_codec": video_stream.get("codec_name", "unknown"),
        "audio_codec": (
            audio_stream.get("codec_name", "none")
            if audio_stream else "none"
        ),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "fps": round(fps, 2),
    }


def frame_features(image):
    image = image.convert("RGB")
    gray = image.convert("L")

    stat = ImageStat.Stat(image)
    brightness = sum(stat.mean) / 3

    gray_stat = ImageStat.Stat(gray)
    contrast = gray_stat.stddev[0]

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_strength = ImageStat.Stat(edges).mean[0]

    histogram = gray.histogram()
    total = sum(histogram) or 1
    normalized_histogram = [
        value / total for value in histogram
    ]

    return {
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
        "average_rgb": [
            round(value, 2) for value in stat.mean
        ],
        "edge_strength": round(edge_strength, 2),
        "histogram": normalized_histogram,
        "gray": gray.resize((48, 48)),
    }


def histogram_distance(a, b):
    return round(
        sum(abs(x - y) for x, y in zip(a, b)) / 2,
        4,
    )


def image_difference(a, b):
    difference = ImageChops.difference(a, b)
    stats = ImageStat.Stat(difference)
    return round(stats.mean[0], 2)


def analyze_audio(video_path):
    result = {
        "available": False,
        "average_volume": None,
        "max_volume": None,
        "silence_periods": [],
    }

    silence_command = [
        "ffmpeg",
        "-hide_banner",
        "-i", str(video_path),
        "-af", "silencedetect=noise=-35dB:d=0.5",
        "-f", "null",
        "-",
    ]

    try:
        process = subprocess.run(
            silence_command,
            capture_output=True,
            text=True,
            timeout=180,
        )
        audio_text = process.stderr or ""

        if "silencedetect" in audio_text:
            result["available"] = True

        starts = re.findall(
            r"silence_start:\s*([0-9.]+)",
            audio_text,
        )
        ends = re.findall(
            r"silence_end:\s*([0-9.]+)",
            audio_text,
        )

        for start, end in zip(starts, ends):
            result["silence_periods"].append({
                "start": round(float(start), 2),
                "end": round(float(end), 2),
                "duration": round(
                    float(end) - float(start), 2
                ),
            })

        volume_command = [
            "ffmpeg",
            "-hide_banner",
            "-i", str(video_path),
            "-vn",
            "-af", "volumedetect",
            "-f", "null",
            "-",
        ]

        volume_process = subprocess.run(
            volume_command,
            capture_output=True,
            text=True,
            timeout=180,
        )

        volume_text = volume_process.stderr or ""

        average = re.search(
            r"mean_volume:\s*(-?[\d.]+)\s*dB",
            volume_text,
        )
        maximum = re.search(
            r"max_volume:\s*(-?[\d.]+)\s*dB",
            volume_text,
        )

        if average:
            result["average_volume"] = float(average.group(1))
            result["available"] = True

        if maximum:
            result["max_volume"] = float(maximum.group(1))
            result["available"] = True

    except (subprocess.TimeoutExpired, OSError):
        pass

    return result


def analyze_video(video_path, max_duration=900):
    video_path = Path(video_path)

    if not video_path.exists():
        raise AnalysisError("Uploaded video was not found.")

    metadata = get_metadata(video_path)

    duration = metadata["duration"]

    if duration <= 0:
        raise AnalysisError("Video duration is invalid.")

    if duration > max_duration:
        raise AnalysisError(
            f"Video is too long. Maximum duration is {max_duration} seconds."
        )

    interval = max(1, min(3, math.ceil(duration / 180)))

    with tempfile.TemporaryDirectory(prefix="vidtool_") as temp_dir:
        frame_pattern = str(
            Path(temp_dir) / "frame_%05d.jpg"
        )

        run_command([
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vf", f"fps=1/{interval},scale=480:-2",
            "-frames:v", "180",
            "-q:v", "3",
            frame_pattern,
        ], timeout=240)

        frame_paths = sorted(
            Path(temp_dir).glob("frame_*.jpg")
        )

        if not frame_paths:
            raise AnalysisError(
                "FFmpeg could not extract video frames."
            )

        frames = []
        scene_changes = []
        previous = None

        for index, frame_path in enumerate(frame_paths):
            try:
                with Image.open(frame_path) as source:
                    image = source.convert("RGB")
                    feature = frame_features(image)
            except Exception as exc:
                raise AnalysisError(
                    f"Could not read extracted frame {index + 1}."
                ) from exc

            gray = feature.pop("gray")

            timestamp = index * interval

            feature["timestamp"] = timestamp
            frames.append(feature)

            if previous is not None:
                motion = image_difference(
                    previous["gray"], gray
                )
                hist_change = histogram_distance(
                    previous["histogram"],
                    feature["histogram"],
                )

                if hist_change >= 0.38:
                    scene_changes.append({
                        "timestamp": timestamp,
                        "change_score": hist_change,
                        "motion_score": motion,
                    })

            previous = {
                "gray": gray.copy(),
                "histogram": feature["histogram"],
            }

        brightness_values = [
            frame["brightness"] for frame in frames
        ]
        contrast_values = [
            frame["contrast"] for frame in frames
        ]
        motion_values = [
            item["motion_score"] for item in scene_changes
        ]

        average_brightness = round(
            sum(brightness_values) / len(brightness_values), 2
        )
        average_contrast = round(
            sum(contrast_values) / len(contrast_values), 2
        )

        average_motion = (
            round(sum(motion_values) / len(motion_values), 2)
            if motion_values else 0
        )

        audio = analyze_audio(video_path)

        return {
            "filename": video_path.name,
            "metadata": metadata,
            "duration": duration,
            "frame_count": len(frames),
            "sample_interval": interval,
            "scene_changes": scene_changes,
            "scene_count_estimate": len(scene_changes) + 1,
            "average_brightness": average_brightness,
            "average_contrast": average_contrast,
            "average_motion": average_motion,
            "frames": frames,
            "audio": audio,
            "limitations": [
                "This analysis measures visual and audio properties.",
                "It does not reliably identify objects, people, actions, or story meaning.",
                "Scene boundaries are estimates based on visual histogram changes.",
                "Audio loudness and silence measurements do not identify speech meaning.",
            ],
        }


def build_prompt(report):
    metadata = report["metadata"]
    audio = report["audio"]

    lines = [
        "VIDEO RECONSTRUCTION PROMPT",
        "",
        "Create a video using the following measured technical evidence.",
        "",
        f"Duration: {report['duration']} seconds",
        f"Resolution: {metadata['resolution']}",
        f"Frame rate: {metadata['fps']} FPS",
        f"Video codec: {metadata['video_codec']}",
        f"Audio codec: {metadata['audio_codec']}",
        f"Sampled frames: {report['frame_count']}",
        f"Estimated scene changes: {len(report['scene_changes'])}",
        f"Average brightness: {report['average_brightness']}",
        f"Average contrast: {report['average_contrast']}",
        f"Average measured frame change: {report['average_motion']}",
        "",
        "Scene change timestamps:",
    ]

    if report["scene_changes"]:
        for scene in report["scene_changes"]:
            lines.append(
                f"- Around {scene['timestamp']} seconds"
            )
    else:
        lines.append("- No strong scene changes detected.")

    lines.extend([
        "",
        "Audio measurements:",
        f"Audio detected: {audio['available']}",
        f"Average volume: {audio['average_volume']}",
        f"Maximum volume: {audio['max_volume']}",
        f"Silence periods detected: {len(audio['silence_periods'])}",
        "",
        "Reconstruction guidance:",
        "Match the measured duration, aspect ratio, frame rate, "
        "brightness, contrast, and approximate scene timing.",
        "Use the original video as a visual reference for actual "
        "subjects, actions, camera movement, and composition.",
        "",
        "Important limitation:",
        "These measurements do not identify the semantic contents "
        "of the video. Do not assume objects, people, locations, "
        "or actions that were not independently observed.",
    ])

    return "\n".join(lines)
