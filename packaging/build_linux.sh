#!/bin/sh
# Builds the Linux executable of the PFA tax tracker and puts it in the project folder as
# ./PFA-Tax-Tracker (it keeps its data next to itself, in the same files as the browser version).
#
#   sh packaging/build_linux.sh
#
# Needs: python3 with the packages of requirements.txt, PySide6 and PyInstaller
#        (pip install -r requirements.txt PySide6 pyinstaller).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/packaging/build"
cd "$ROOT"
python3 -c "import PyInstaller, PySide6" || { echo "Install PyInstaller and PySide6 first: pip install pyinstaller PySide6"; exit 1; }
python3 -m PyInstaller --noconfirm --clean \
    --distpath "$BUILD/dist" --workpath "$BUILD/work" \
    packaging/pfa-tracker.spec
cp "$BUILD/dist/PFA-Tax-Tracker" "$ROOT/PFA-Tax-Tracker.new"
mv -f "$ROOT/PFA-Tax-Tracker.new" "$ROOT/PFA-Tax-Tracker"
chmod +x "$ROOT/PFA-Tax-Tracker"
echo
echo "Built $ROOT/PFA-Tax-Tracker ($(du -h "$ROOT/PFA-Tax-Tracker" | cut -f1))"
echo "  desktop window:  ./PFA-Tax-Tracker"
echo "  browser version: ./PFA-Tax-Tracker --web"
