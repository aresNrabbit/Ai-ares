
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename

from engine.analyzer import analyze_video, AnalysisError

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"

UPLOAD_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("VIDTOOL_MAX_UPLOAD_MB", "300"))
MAX_DURATION = int(os.environ.get("VIDTOOL_MAX_DURATION_SECONDS", "900"))

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def page(**kwargs):
    return render_template(
        "index.html",
        max_upload_mb=MAX_UPLOAD_MB,
        max_duration=MAX_DURATION,
        **kwargs
    )


@app.get("/")
def index():
    return page()


@app.get("/ping")
def ping():
    return jsonify({
        "ok": True,
        "app": "Vidtool",
        "version": "2.0",
        "mode": "pure-offline"
    })


@app.post("/analyze")
def analyze():
    uploaded = request.files.get("video")

    if not uploaded or not uploaded.filename:
        return page(error="Choose a video file first."), 400

    filename = secure_filename(uploaded.filename)
    extension = Path(filename).suffix.lower()

    if not filename or extension not in ALLOWED_EXTENSIONS:
        return page(
            error="Choose a supported MP4, MOV, MKV, WEBM or AVI video."
        ), 400

    job_id = uuid.uuid4().hex
    input_path = UPLOAD_DIR / f"{job_id}{extension}"

    try:
        uploaded.save(input_path)

        if not input_path.exists() or input_path.stat().st_size == 0:
            raise AnalysisError("The uploaded file is empty.")

        report = analyze_video(input_path, max_duration=MAX_DURATION)
        report["filename"] = filename
        report["job_id"] = job_id

        report_path = REPORT_DIR / f"{job_id}.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return page(report=report, report_id=job_id)

    except AnalysisError as exc:
        return page(error=str(exc)), 422

    except Exception:
        app.logger.exception("Unexpected analysis error")
        return page(
            error="Analysis failed. Check that FFmpeg and FFprobe are installed."
        ), 500

    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/report/<report_id>")
def download_report(report_id):
    if len(report_id) != 32 or not all(
        char in "0123456789abcdef" for char in report_id
    ):
        return jsonify({"error": "Invalid report ID"}), 400

    report_path = REPORT_DIR / f"{report_id}.json"

    if not report_path.is_file():
        return jsonify({"error": "Report not found"}), 404

    return send_file(
        report_path,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"vidtool-{report_id}.json"
    )


@app.errorhandler(413)
def too_large(_error):
    return page(
        error=f"File is larger than the {MAX_UPLOAD_MB} MB limit."
    ), 413


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5050")),
        debug=False
    )
