# Vidtool — Pure Offline Video Analyzer

Vidtool is a self-hostable Flask application that inspects video structure and builds a recreation prompt using **offline, deterministic algorithms**.

## Zero-payment design

- No Gemini, OpenAI, or other AI API.
- No API keys, subscriptions, billing accounts, or cloud services.
- No pretrained model downloads.
- Video processing runs locally through FFmpeg/FFprobe and Pillow.
- The app does not upload videos anywhere.

## Important accuracy statement

This is a real working offline analysis MVP, not a semantic AI vision model. It measures video metadata, sampled-frame brightness/contrast/edge proxies, frame-to-frame visual differences, approximate scene boundaries, and audio loudness. It does **not** identify people, objects, animals, actions, speech, music genres, or locations. It does not claim that pixel changes prove camera movement.

The prompt is intentionally evidence-based. It will not invent subjects or actions. "2000% accurate" is not technically possible; video understanding has uncertainty and this build makes its limits visible.

## Run in Termux (Android)

1. Install Termux from a trusted source such as F-Droid or the official Termux GitHub releases.
2. Copy/extract this project into a folder, then open Termux in that folder.
3. Run:

```bash
bash termux_setup.sh
python app.py
```

4. Open this address in the tablet/phone browser:

`http://127.0.0.1:5050`

If the setup script cannot be executed, run the commands manually:

```bash
pkg update -y
pkg install python ffmpeg
python -m pip install -r requirements.txt
python app.py
```

## Run on Linux / macOS / Windows

Install Python 3.10+ and FFmpeg (including `ffprobe`), then:

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5050`.

## Limits

- Default upload limit: 300 MB (`VIDTOOL_MAX_UPLOAD_MB`).
- Default duration limit: 900 seconds (`VIDTOOL_MAX_DURATION_SECONDS`).
- Samples at about one frame every two seconds, capped at 120 frames.
- Longer/high-resolution clips may take time on mobile devices.
- Reports are stored locally in `reports/`; uploaded source videos are deleted after processing.
- Use only videos you have permission to analyze.

## GitHub

Do not commit personal videos, reports, virtual environments, API keys, or private data. `.gitignore` excludes generated uploads and reports. You can publish the source code in a public repository.

## Security / deployment

The built-in Flask server binds to `127.0.0.1` for local use. It is not a public production server. Do not change it to `0.0.0.0` and expose it to the internet without adding authentication, HTTPS, rate limiting, background job handling, and production hosting.

## Roadmap (not yet implemented)

Potential future improvements include better scene-cut scoring, camera-motion estimation with optical flow, richer audio features, optional local semantic models, and export formats. These should be added as tested features, not simulated outputs.
