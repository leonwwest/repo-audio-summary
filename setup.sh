#!/bin/bash
# ============================================================
# Setup-Skript für Git Audio Summary
# Führe dieses Skript einmal aus: bash setup.sh
# ============================================================

set -e

echo "🚀 Git Audio Summary – Setup"
echo "================================"
echo ""

# ---- 1. Python-Abhängigkeiten installieren ----
echo "📦 Installiere Python-Pakete..."
pip3 install edge-tts requests
echo "✅ Python-Pakete installiert"
echo ""

# ---- 2. Ollama prüfen ----
if command -v ollama &> /dev/null; then
    echo "✅ Ollama ist bereits installiert"
else
    echo "📥 Ollama muss installiert werden."
    echo "   Gehe zu: https://ollama.com/download"
    echo "   Oder installiere per Homebrew: brew install ollama"
    echo ""
    read -p "Drücke Enter wenn Ollama installiert ist..."
fi

# ---- 3. Gemma 4 Modell herunterladen ----
echo ""
echo "📥 Lade Gemma 4 E4B Modell herunter (ca. 5GB, einmalig)..."
ollama pull gemma4:e4b
echo "✅ Modell heruntergeladen"
echo ""

# ---- 4. Telegram Bot Setup ----
echo "🤖 TELEGRAM BOT SETUP"
echo "================================"
echo ""
echo "Folge diesen Schritten:"
echo ""
echo "1. Öffne Telegram und suche nach @BotFather"
echo "2. Sende: /newbot"
echo "3. Gib deinem Bot einen Namen, z.B.: 'Git Summary Bot'"
echo "4. Gib einen Username, z.B.: 'mein_git_summary_bot'"
echo "5. Du bekommst einen API Token – kopiere ihn!"
echo ""
read -p "Gib deinen Bot Token ein: " BOT_TOKEN
echo ""
echo "6. Öffne deinen neuen Bot in Telegram und sende ihm: /start"
echo "7. Dann öffne diese URL im Browser:"
echo "   https://api.telegram.org/bot${BOT_TOKEN}/getUpdates"
echo "8. Suche nach 'chat':{'id': XXXXXXXXX} – das ist deine Chat-ID"
echo ""
read -p "Gib deine Chat-ID ein: " CHAT_ID
echo ""

# ---- 5. Repo-Pfad ----
echo "Standard-Repo: ~/example-repo"
read -p "Gib den Pfad zu deinem Git-Repo ein (Enter für Standard): " REPO_PATH
REPO_PATH="${REPO_PATH:-$HOME/example-repo}"
echo ""

# ---- 6. .env Datei erstellen ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

cat > "$ENV_FILE" << EOF
# Git Audio Summary – Konfiguration
REPO_PATH=${REPO_PATH}
OLLAMA_MODEL=gemma4:e4b
OLLAMA_URL=http://localhost:11434/api/generate
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_CHAT_ID=${CHAT_ID}
TTS_VOICE=de-DE-ConradNeural
OUTPUT_DIR=${SCRIPT_DIR}/audio_output
HOURS_BACK=24
EOF

echo "✅ Konfiguration gespeichert in: ${ENV_FILE}"
echo ""

# ---- 7. Runner-Skript erstellen ----
RUNNER="${SCRIPT_DIR}/run_summary.sh"
cat > "$RUNNER" << 'RUNNER_EOF'
#!/bin/bash
# Lädt die .env und startet das Skript
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# .env laden
set -a
source "${SCRIPT_DIR}/.env"
set +a

# Ollama starten falls nicht läuft
if ! pgrep -x "ollama" > /dev/null; then
    ollama serve &
    sleep 3
fi

# Modus: "daily", "full", oder "both" (Standard)
MODE="${1:-both}"

# Skript ausführen
python3 "${SCRIPT_DIR}/repo_audio_summary.py" "$MODE"
RUNNER_EOF

chmod +x "$RUNNER"
echo "✅ Runner-Skript erstellt: ${RUNNER}"
echo ""

# ---- 8. LaunchAgent erstellen (macOS Cron-Alternative) ----
PLIST_NAME="com.gitaudiosummary.daily"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

cat > "$PLIST_PATH" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNNER}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>22</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/logs/stderr.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST_EOF

mkdir -p "${SCRIPT_DIR}/logs"

# LaunchAgent laden
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "✅ Täglicher Job eingerichtet: Jeden Abend um 22:00 Uhr"
echo ""

# ---- 9. Test ----
echo "🧪 Möchtest du jetzt einen Test durchführen?"
read -p "   (j/n): " DO_TEST

if [[ "$DO_TEST" == "j" || "$DO_TEST" == "J" ]]; then
    echo ""
    echo "🚀 Starte Test..."
    bash "$RUNNER"
fi

echo ""
echo "================================"
echo "✅ SETUP ABGESCHLOSSEN!"
echo "================================"
echo ""
echo "📁 Alle Dateien liegen in: ${SCRIPT_DIR}"
echo "🕙 Jeden Abend um 22:00 bekommst du ZWEI Audio-Zusammenfassungen auf Telegram:"
echo "   📋 Tages-Update: Was hat sich heute geändert?"
echo "   🏗️  Architektur: Wie hängt das ganze Repo zusammen?"
echo ""
echo "Nützliche Befehle:"
echo "  Beide ausführen:    bash ${RUNNER}"
echo "  Nur Tages-Update:   bash ${RUNNER} daily"
echo "  Nur Architektur:    bash ${RUNNER} full"
echo "  Job stoppen:        launchctl unload ${PLIST_PATH}"
echo "  Job starten:        launchctl load ${PLIST_PATH}"
echo "  Logs ansehen:       cat ${SCRIPT_DIR}/logs/stdout.log"
echo ""
