
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename

from engine.analyzer import analyze_video, AnalysisError, build_prompt


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"

UPLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_SIZE = 300 * 1024 * 1024
MAX_DURATION = 900

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

logging.basicConfig(level=logging.INFO)


def render_page(**kwargs):
    return render_template("index.html", **kwargs)


@app.route("/", methods=["GET"])
def index():
    return render_page()


@app.route("/analyze", methods=["POST"])
def analyze():
    uploaded = request.files.get("video")

    if not uploaded or not uploaded.filename:
        return render_page(error="Please select a video file."), 400

    original_name = secure_filename(uploaded.filename)

    if not original_name:
        return render_page(error="Invalid filename."), 400

    report_id = uuid.uuid4().hex
    extension = Path(original_name).suffix or ".mp4"
    input_path = UPLOAD_DIR / f"{report_id}{extension}"

    try:
        uploaded.save(input_path)

        report = analyze_video(
            input_path,
            max_duration=MAX_DURATION,
        )

        report["filename"] = original_name
        report["prompt"] = build_prompt(report)
        report["report_id"] = report_id

        report_path = REPORT_DIR / f"{report_id}.json"

        with report_path.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2, ensure_ascii=False)

        return render_page(report=report)

    except AnalysisError as exc:
        app.logger.exception("Video analysis error")
        return render_page(
            error=f"Video analysis error: {exc}"
        ), 400

    except Exception as exc:
        app.logger.exception("Unexpected analysis error")
        return render_page(
            error=f"Unexpected error: {type(exc).__name__}: {exc}"
        ), 500

    finally:
        if input_path.exists():
            input_path.unlink()


@app.route("/download/<report_id>")
def download_report(report_id):
    if not report_id.isalnum():
        return "Invalid report ID", 400

    report_path = REPORT_DIR / f"{report_id}.json"

    if not report_path.exists():
        return "Report not found", 404

    return send_file(
        report_path,
        as_attachment=True,
        download_name="vidtool-report.json",
    )


@app.route("/download/<report_id>/prompt")
def download_prompt(report_id):
    if not report_id.isalnum():
        return "Invalid report ID", 400

    report_path = REPORT_DIR / f"{report_id}.json"

    if not report_path.exists():
        return "Report not found", 404

    with report_path.open("r", encoding="utf-8") as file:
        report = json.load(file)

    prompt = report.get("prompt", "")

    from flask import Response

    return Response(
        prompt,
        mimetype="text/plain",
        headers={
            "Content-Disposition":
                "attachment; filename=vidtool-prompt.txt"
        },
    )


@app.errorhandler(413)
def file_too_large(error):
    return render_page(
        error="Video is too large. Maximum upload size is 300 MB."
    ), 413


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5050,
        debug=False,
    )
