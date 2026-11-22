from __future__ import annotations

import os
import uuid
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from engine.analyzer import analyze_video, AnalysisError

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
for folder in (UPLOAD_DIR, REPORT_DIR):
    folder.mkdir(exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("VIDTOOL_MAX_UPLOAD_MB", "300"))
MAX_DURATION_SECONDS = int(os.environ.get("VIDTOOL_MAX_DURATION_SECONDS", "900"))
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("VIDTOOL_SECRET_KEY", "local-development-only")


@app.get("/")
def index():
    return render_template("index.html", max_upload_mb=MAX_UPLOAD_MB,
                           max_duration=MAX_DURATION_SECONDS)


@app.get("/ping")
def ping():
    return jsonify({"ok": True, "app": "Vidtool", "mode": "pure-offline"})


@app.post("/analyze")
def analyze():
    uploaded = request.files.get("video")
    if not uploaded or not uploaded.filename:
        return render_template("index.html", error="Choose a video file first.",
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 400
    original = secure_filename(uploaded.filename)
    if not original:
        return render_template("index.html", error="The filename is not valid.",
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 400

    ext = Path(original).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}:
        return render_template("index.html", error="Unsupported file extension. Use MP4, MOV, M4V, MKV, WEBM or AVI.",
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 400

    job_id = uuid.uuid4().hex
    input_path = UPLOAD_DIR / f"{job_id}{ext}"
    try:
        uploaded.save(input_path)
        if input_path.stat().st_size == 0:
            raise AnalysisError("The uploaded file is empty.")
        report = analyze_video(input_path, max_duration=MAX_DURATION_SECONDS)
        report["filename"] = original
        report["job_id"] = job_id
        report_path = REPORT_DIR / f"{job_id}.json"
        import json
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return render_template("index.html", report=report,
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS)
    except AnalysisError as exc:
        return render_template("index.html", error=str(exc),
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 422
    except Exception as exc:
        app.logger.exception("Unexpected analysis failure")
        return render_template("index.html", error=f"Analysis failed safely: {exc}",
                               max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 500
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/report/<job_id>")
def get_report(job_id):
    if not job_id.isalnum() or len(job_id) != 32:
        return jsonify({"error": "Invalid report ID"}), 400
    path = REPORT_DIR / f"{job_id}.json"
    if not path.exists():
        return jsonify({"error": "Report not found"}), 404
    return send_file(path, mimetype="application/json", as_attachment=True,
                     download_name=f"vidtool-{job_id}.json")


@app.errorhandler(413)
def too_large(_error):
    return render_template("index.html", error=f"File exceeds the {MAX_UPLOAD_MB} MB upload limit.",
                           max_upload_mb=MAX_UPLOAD_MB, max_duration=MAX_DURATION_SECONDS), 413


if __name__ == "__main__":
    # Local-only server. Do not expose this development server directly to the public internet.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), debug=False)
