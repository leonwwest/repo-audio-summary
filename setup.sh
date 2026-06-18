#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
RUNNER="${SCRIPT_DIR}/run_summary.sh"
LOG_DIR="${SCRIPT_DIR}/logs"
CACHE_DIR="${SCRIPT_DIR}/cache"
OUTPUT_DIR="${SCRIPT_DIR}/audio_output"
TEXT_OUTPUT_DIR="${OUTPUT_DIR}/transcripts"
STATUS_DIR="${LOG_DIR}/status"
DAILY_PLIST="${HOME}/Library/LaunchAgents/com.gitaudiosummary.daily.plist"
CATCHUP_PLIST="${HOME}/Library/LaunchAgents/com.gitaudiosummary.catchup.plist"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This setup script is intended for macOS."
    exit 1
fi

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1"
        exit 1
    fi
}

ensure_pip_available() {
    if python3 -m pip --version >/dev/null 2>&1; then
        return 0
    fi
    echo "python3 -m pip is missing. Trying ensurepip..."
    python3 -m ensurepip --upgrade >/dev/null 2>&1 || true
    if ! python3 -m pip --version >/dev/null 2>&1; then
        echo "pip is still unavailable. Please install Python with pip support."
        exit 1
    fi
}

python_has_module() {
    python3 - "$1" <<'PY'
import importlib.util
import sys

name = sys.argv[1]
print("yes" if importlib.util.find_spec(name) else "no")
PY
}

quote_env() {
    python3 - "$1" <<'PY'
import sys

value = sys.argv[1]
print("'" + value.replace("'", "'\"'\"'") + "'")
PY
}

read_existing_env() {
    if [[ -f "${ENV_FILE}" ]]; then
        set -a
        source "${ENV_FILE}"
        set +a
    fi
}

install_python_module_if_missing() {
    local module_name="$1"
    local package_name="$2"
    if [[ "$(python_has_module "${module_name}")" == "yes" ]]; then
        echo "Python module ${package_name} already installed."
        return 0
    fi
    echo "Installing Python package ${package_name}..."
    python3 -m pip install "${package_name}"
}

pull_model_if_missing() {
    local model_name="$1"
    if ollama list | grep -Fq "${model_name}"; then
        echo "Ollama model ${model_name} already available."
        return 0
    fi
    echo "Pulling Ollama model ${model_name}..."
    ollama pull "${model_name}"
}

write_env_file() {
    cat > "${ENV_FILE}" <<EOF
# Git Audio Summary configuration
REPO_PATH=$(quote_env "${REPO_PATH}")
PRIMARY_MODEL=$(quote_env "${PRIMARY_MODEL}")
FALLBACK_MODELS=$(quote_env "${FALLBACK_MODELS}")
OLLAMA_URL=$(quote_env "${OLLAMA_URL}")
TELEGRAM_BOT_TOKEN=$(quote_env "${TELEGRAM_BOT_TOKEN}")
TELEGRAM_CHAT_ID=$(quote_env "${TELEGRAM_CHAT_ID}")
TTS_VOICE=$(quote_env "${TTS_VOICE}")
OUTPUT_DIR=$(quote_env "${OUTPUT_DIR}")
TEXT_OUTPUT_DIR=$(quote_env "${TEXT_OUTPUT_DIR}")
CACHE_DIR=$(quote_env "${CACHE_DIR}")
LOG_DIR=$(quote_env "${LOG_DIR}")
STATUS_DIR=$(quote_env "${STATUS_DIR}")
HOURS_BACK=$(quote_env "${HOURS_BACK}")
RUN_HOUR=$(quote_env "${RUN_HOUR}")
RUN_MINUTE=$(quote_env "${RUN_MINUTE}")
MAX_DIFF_CHARS=$(quote_env "${MAX_DIFF_CHARS}")
TELEGRAM_SEND_RETRIES=$(quote_env "${TELEGRAM_SEND_RETRIES}")
OLLAMA_START_TIMEOUT=$(quote_env "${OLLAMA_START_TIMEOUT}")
EOF
}

write_launch_agents() {
    mkdir -p "${HOME}/Library/LaunchAgents" "${LOG_DIR}" "${STATUS_DIR}" "${CACHE_DIR}" "${OUTPUT_DIR}" "${TEXT_OUTPUT_DIR}"

    cat > "${DAILY_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gitaudiosummary.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNNER}</string>
        <string>both</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${RUN_HOUR}</integer>
        <key>Minute</key>
        <integer>${RUN_MINUTE}</integer>
    </dict>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/launchagent.daily.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/launchagent.daily.stderr.log</string>
</dict>
</plist>
EOF

    cat > "${CATCHUP_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gitaudiosummary.catchup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${RUNNER}</string>
        <string>catchup-check</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/launchagent.catchup.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/launchagent.catchup.stderr.log</string>
</dict>
</plist>
EOF

    launchctl unload "${DAILY_PLIST}" >/dev/null 2>&1 || true
    launchctl unload "${CATCHUP_PLIST}" >/dev/null 2>&1 || true
    launchctl load "${DAILY_PLIST}"
    launchctl load "${CATCHUP_PLIST}"
}

echo "Git Audio Summary setup for macOS"
echo "================================="

require_command python3
require_command git
require_command bash
ensure_pip_available
read_existing_env

REPO_PATH="${REPO_PATH:-${HOME}/example-repo}"
PRIMARY_MODEL="${PRIMARY_MODEL:-gemma4:e4b}"
FALLBACK_MODELS="${FALLBACK_MODELS:-gemma3:4b,qwen2.5:7b}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/api/generate}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
TTS_VOICE="${TTS_VOICE:-de-DE-ConradNeural}"
TEXT_OUTPUT_DIR="${TEXT_OUTPUT_DIR:-${OUTPUT_DIR}/transcripts}"
HOURS_BACK="${HOURS_BACK:-24}"
RUN_HOUR="${RUN_HOUR:-22}"
RUN_MINUTE="${RUN_MINUTE:-0}"
MAX_DIFF_CHARS="${MAX_DIFF_CHARS:-12000}"
TELEGRAM_SEND_RETRIES="${TELEGRAM_SEND_RETRIES:-3}"
OLLAMA_START_TIMEOUT="${OLLAMA_START_TIMEOUT:-30}"

mkdir -p "${LOG_DIR}" "${CACHE_DIR}" "${OUTPUT_DIR}" "${TEXT_OUTPUT_DIR}" "${STATUS_DIR}"
chmod +x "${RUNNER}"

install_python_module_if_missing "requests" "requests"
install_python_module_if_missing "edge_tts" "edge-tts"

if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama is required. Install it from https://ollama.com/download or via Homebrew."
    exit 1
fi
echo "Using Ollama: $(ollama --version 2>/dev/null || echo 'version unknown')"

echo
echo "Telegram setup"
echo "--------------"
echo "1. Open Telegram and talk to @BotFather"
echo "2. Create a bot with /newbot"
echo "3. Start the new bot once"
echo
read -r -p "Telegram bot token [${TELEGRAM_BOT_TOKEN:-none}]: " INPUT_BOT_TOKEN
if [[ -n "${INPUT_BOT_TOKEN}" ]]; then
    TELEGRAM_BOT_TOKEN="${INPUT_BOT_TOKEN}"
fi

echo "Open this URL after starting the bot:"
echo "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
read -r -p "Telegram chat id [${TELEGRAM_CHAT_ID:-none}]: " INPUT_CHAT_ID
if [[ -n "${INPUT_CHAT_ID}" ]]; then
    TELEGRAM_CHAT_ID="${INPUT_CHAT_ID}"
fi

echo
read -r -p "Path to the repo to analyze [${REPO_PATH}]: " INPUT_REPO_PATH
if [[ -n "${INPUT_REPO_PATH}" ]]; then
    REPO_PATH="${INPUT_REPO_PATH}"
fi

read -r -p "Run hour [${RUN_HOUR}]: " INPUT_RUN_HOUR
if [[ -n "${INPUT_RUN_HOUR}" ]]; then
    RUN_HOUR="${INPUT_RUN_HOUR}"
fi

read -r -p "Run minute [${RUN_MINUTE}]: " INPUT_RUN_MINUTE
if [[ -n "${INPUT_RUN_MINUTE}" ]]; then
    RUN_MINUTE="${INPUT_RUN_MINUTE}"
fi

echo
echo "Ensuring configured Ollama models are available..."
pull_model_if_missing "${PRIMARY_MODEL}"
OLD_IFS="${IFS}"
IFS=',' read -r -a FALLBACK_MODELS_ARRAY <<< "${FALLBACK_MODELS}"
IFS="${OLD_IFS}"
for model in "${FALLBACK_MODELS_ARRAY[@]}"; do
    model="$(echo "${model}" | xargs)"
    if [[ -n "${model}" ]]; then
        pull_model_if_missing "${model}"
    fi
done

write_env_file
write_launch_agents

echo
echo "Running doctor check..."
bash "${RUNNER}" doctor

echo
read -r -p "Run an immediate smoke test now? [y/N]: " RUN_SMOKE_TEST
if [[ "${RUN_SMOKE_TEST}" == "y" || "${RUN_SMOKE_TEST}" == "Y" ]]; then
    bash "${RUNNER}" both
fi

echo
echo "Setup complete."
echo "Daily run:    bash ${RUNNER} both"
echo "Daily only:   bash ${RUNNER} daily"
echo "Full only:    bash ${RUNNER} full"
echo "Deep dive:    bash ${RUNNER} deep \"config loader\""
echo "Doctor:       bash ${RUNNER} doctor"
echo "Logs live in: ${LOG_DIR}"
