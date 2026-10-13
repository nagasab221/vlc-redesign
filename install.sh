#!/bin/bash
# Installs VLC Redesign for the current user.
#
#   curl -fsSL https://raw.githubusercontent.com/nagasab221/vlc-redesign/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/nagasab221/vlc-redesign/main/install.sh | bash -s -- --remove
#
# Or from a clone: ./install.sh (runs the app from the clone) and ./install.sh --remove.
# It never changes your default video player.

set -euo pipefail

REPO="nagasab221/vlc-redesign"
BRANCH="main"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/vlc-redesign"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/scalable/apps"
BIN="$HOME/.local/bin"
DESKTOP="$APPS/vlc-redesign.desktop"

remove() {
    rm -f "$BIN/vlc-redesign" "$DESKTOP" "$ICONS/vlc-redesign.svg"
    if [[ -f "$DEST/main.py" ]]; then
        rm -rf "$DEST"
    fi
    update-desktop-database -q "$APPS" 2>/dev/null || true
    echo "VLC Redesign removed. Your settings are still in ~/.config/vlc-redesign if you want to delete them too."
}

install_packages() {
    local need=()
    python3 -c 'import ctypes; ctypes.CDLL("libvlc.so.5")' 2>/dev/null || need+=(vlc)
    python3 -c 'import PyQt5.QtWidgets, PyQt5.QtNetwork' 2>/dev/null || need+=(python3-pyqt5)
    python3 -c 'import PyQt5.QtSvg' 2>/dev/null || need+=(python3-pyqt5.qtsvg)
    command -v ffmpeg >/dev/null || need+=(ffmpeg)
    python3 -c 'import mutagen' 2>/dev/null || need+=(python3-mutagen)
    python3 -c 'import Xlib' 2>/dev/null || need+=(python3-xlib)
    [[ ${#need[@]} -eq 0 ]] && return

    if ! command -v apt-get >/dev/null; then
        echo "Please install these with your package manager first: ${need[*]}"
        exit 1
    fi
    echo "Installing ${need[*]} (asks for your password)..."
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}" </dev/null >/dev/null
}

download() {
    local tmp
    tmp="$(mktemp -d)"
    echo "Downloading VLC Redesign..."
    if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$tmp"; then
        rm -rf "$tmp"
        echo "Download failed. Check your internet connection and try again."
        exit 1
    fi
    rm -rf "$DEST"
    mkdir -p "$(dirname "$DEST")"
    mv "$tmp/vlc-redesign-$BRANCH" "$DEST"
    rm -rf "$tmp"
}

main() {
    if [[ "${1:-}" == "--remove" ]]; then
        remove
        return
    fi

    install_packages

    # Run from a clone if this script sits next to the app, otherwise fetch the latest version.
    local here="" app
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    fi
    if [[ -n "$here" && -f "$here/main.py" && -d "$here/vlcr" ]]; then
        app="$here"
    else
        download
        app="$DEST"
    fi

    # The bundled python-vlc prints docstring warnings the first time it's compiled.
    python3 -W ignore -m py_compile "$app/vendor/vlc.py" 2>/dev/null || true
    chmod +x "$app/vlc-redesign"

    mkdir -p "$BIN" "$APPS" "$ICONS"
    ln -sf "$app/vlc-redesign" "$BIN/vlc-redesign"
    cp "$app/vlcr/assets/vlc-redesign.svg" "$ICONS/vlc-redesign.svg"

    local mime=""
    if [[ -f /usr/share/applications/vlc.desktop ]]; then
        mime="$(grep -m1 '^MimeType=' /usr/share/applications/vlc.desktop || true)"
    fi
    [[ -n "$mime" ]] || mime="MimeType=video/mp4;video/x-matroska;video/webm;video/x-msvideo;video/quicktime;video/mpeg;audio/mpeg;audio/flac;audio/ogg;audio/x-wav;audio/mp4;"

    cat > "$DESKTOP" <<DESKTOP
[Desktop Entry]
Type=Application
Name=VLC Redesign
GenericName=Media Player
Comment=Play videos and music with the redesigned VLC interface
Exec="$app/vlc-redesign" %U
TryExec=$app/vlc-redesign
Icon=vlc-redesign
Terminal=false
Categories=AudioVideo;Player;Video;Audio;
Keywords=Player;Video;Audio;Music;Movie;VLC;
StartupWMClass=vlc-redesign
$mime
DESKTOP
    update-desktop-database -q "$APPS" 2>/dev/null || true
    gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true

    echo "Done. Open \"VLC Redesign\" from the menu, or right click a video > Open With."
}

main "$@"
