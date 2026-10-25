#!/usr/bin/env bash
# Run from the repository root in an Ubuntu/Debian Codex cloud environment.
set -euo pipefail
if ! command -v ffmpeg >/dev/null || ! fc-match 'Noto Sans CJK SC' | grep -q 'NotoSansCJK'; then
  if [ "$(id -u)" = 0 ]; then
    apt-get update
    apt-get install -y ffmpeg fonts-noto-cjk fontconfig
  else
    sudo apt-get update
    sudo apt-get install -y ffmpeg fonts-noto-cjk fontconfig
  fi
fi
python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
python -m pip install -r cloud/requirements.txt
ffmpeg -version
python -m unittest discover -s tests -p 'test_daily_*.py' -v
python -m unittest discover -s tests -p 'test_*searcher.py' -v
