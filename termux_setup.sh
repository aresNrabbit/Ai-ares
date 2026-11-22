#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y
pkg install -y python ffmpeg
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
mkdir -p uploads reports
echo
echo "Setup complete. Start Vidtool with: python app.py"
echo "Then open http://127.0.0.1:5050 in your browser."
