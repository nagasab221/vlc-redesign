#!/bin/bash
# Installs VLC Redesign for the current user.
#
#   curl -fsSL https://raw.githubusercontent.com/nagasab221/vlc-redesign/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/nagasab221/vlc-redesign/main/install.sh | bash -s -- --remove
#
# Or from a clone: ./install.sh (runs the app from the clone) and ./install.sh --remove.
# Works with apt (Mint, Ubuntu, Debian), pacman (Arch and friends) and dnf (Fedora).
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
CLASSIC="$APPS/vlc.desktop"            # user copy of the stock VLC entry, renamed "VLC Classic"
CLASSIC_MARK="# Renamed to VLC Classic by the VLC Redesign installer"

remove() {
    rm -f "$BIN/vlc-redesign" "$DESKTOP" "$ICONS/vlc-redesign.svg"
    if [[ -f "$CLASSIC" ]] && grep -qF "$CLASSIC_MARK" "$CLASSIC"; then
        rm -f "$CLASSIC"
    fi
    if [[ -f "$DEST/main.py" ]]; then
        rm -rf "$DEST"
    fi
    update-desktop-database -q "$APPS" 2>/dev/null || true
    echo "VLC Redesign removed (your normal VLC is called VLC again). Your settings are still in ~/.config/vlc-redesign if you want to delete them too."
}

install_packages() {
    local need=()
    python3 -c 'import ctypes; ctypes.CDLL("libvlc.so.5")' 2>/dev/null || need+=(vlc)
    python3 -c 'import PyQt5.QtWidgets, PyQt5.QtNetwork' 2>/dev/null || need+=(pyqt5)
    python3 -c 'import PyQt5.QtSvg' 2>/dev/null || need+=(qtsvg)
    command -v ffmpeg >/dev/null || need+=(ffmpeg)
    python3 -c 'import mutagen' 2>/dev/null || need+=(mutagen)
    python3 -c 'import Xlib' 2>/dev/null || need+=(xlib)
    [[ ${#need[@]} -eq 0 ]] && return

    # The same things under each distro's package names.
    local pm="" names=() n out
    local -A pkg
    if command -v apt-get >/dev/null; then
        pm=apt
        pkg=([vlc]=vlc [pyqt5]=python3-pyqt5 [qtsvg]=python3-pyqt5.qtsvg [ffmpeg]=ffmpeg
             [mutagen]=python3-mutagen [xlib]=python3-xlib)
    elif command -v pacman >/dev/null; then
        pm=pacman
        pkg=([vlc]="vlc vlc-plugins-all" [pyqt5]=python-pyqt5 [qtsvg]=qt5-svg [ffmpeg]=ffmpeg
             [mutagen]=python-mutagen [xlib]=python-xlib)
    elif command -v dnf >/dev/null; then
        pm=dnf
        pkg=([vlc]=vlc [pyqt5]=python3-qt5 [qtsvg]=qt5-qtsvg [ffmpeg]=ffmpeg-free
             [mutagen]=python3-mutagen [xlib]=python3-xlib)
    else
        echo "Please install VLC, PyQt5 (with QtSvg), ffmpeg, mutagen and python-xlib with your package manager first."
        exit 1
    fi
    for n in "${need[@]}"; do
        read -ra more <<< "${pkg[$n]}"
        names+=("${more[@]}")
    done

    echo "Installing ${names[*]} (asks for your password)..."
    case $pm in
        apt)
            sudo apt-get update -qq
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${names[@]}" </dev/null >/dev/null ;;
        pacman)
            out=$(sudo pacman -S --needed --noconfirm "${names[@]}" </dev/null 2>&1) || { echo "$out" | tail -20; exit 1; } ;;
        dnf)
            out=$(sudo dnf install -y -q "${names[@]}" </dev/null 2>&1) || { echo "$out" | tail -20; exit 1; }
            echo "Fedora's own packages leave out some codecs (HEVC, for one); add RPM Fusion if a file won't play." ;;
    esac
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
    # The menu entry points straight at the icon file: looking it up by name goes through icon caches
    # (a stale one, e.g. left by Steam in ~/.local/share/icons/hicolor, hides it). The themed copy is
    # still there for anything that asks for "vlc-redesign".
    cp "$app/vlcr/assets/vlc-redesign.svg" "$ICONS/vlc-redesign.svg"

    local mime=""
    if [[ -f /usr/share/applications/vlc.desktop ]]; then
        mime="$(grep -m1 '^MimeType=' /usr/share/applications/vlc.desktop || true)"
    fi
    [[ -n "$mime" ]] || mime="MimeType=video/mp4;video/x-matroska;video/webm;video/x-msvideo;video/quicktime;video/mpeg;audio/mpeg;audio/flac;audio/ogg;audio/x-wav;audio/mp4;"

    cat > "$DESKTOP" <<DESKTOP
[Desktop Entry]
Type=Application
Name=VLC
GenericName=Media Player
Comment=Play videos and music with the redesigned VLC interface
Exec="$app/vlc-redesign" %U
TryExec=$app/vlc-redesign
Icon=$app/vlcr/assets/vlc-redesign.svg
Terminal=false
Categories=AudioVideo;Player;Video;Audio;
Keywords=Player;Video;Audio;Music;Movie;VLC;
StartupWMClass=vlc-redesign
$mime
DESKTOP
    # Two "VLC"s in the menu would be confusing, so the stock one becomes "VLC Classic" (a copy in
    # your own applications folder; the system file isn't touched and --remove takes it away).
    local sys=/usr/share/applications/vlc.desktop
    if [[ -f "$sys" ]] && { [[ ! -f "$CLASSIC" ]] || grep -qF "$CLASSIC_MARK" "$CLASSIC"; }; then
        { echo "$CLASSIC_MARK"
          sed -E -e 's/^Name(\[[^]]*\])?=.*/Name\1=VLC Classic/' \
                 -e 's/^X-GNOME-FullName(\[[^]]*\])?=.*/X-GNOME-FullName\1=VLC Classic/' "$sys"; } > "$CLASSIC"
    fi

    update-desktop-database -q "$APPS" 2>/dev/null || true
    gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true

    echo "Done. Open \"VLC\" from the menu (your old one is now \"VLC Classic\"), or right click a video > Open With."
}

main "$@"
