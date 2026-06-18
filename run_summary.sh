#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
        PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}. Run bash setup.sh first."
    exit 1
fi

set -a
source "${ENV_FILE}"
set +a

OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/audio_output}"
TEXT_OUTPUT_DIR="${TEXT_OUTPUT_DIR:-${OUTPUT_DIR}/transcripts}"
CACHE_DIR="${CACHE_DIR:-${SCRIPT_DIR}/cache}"
LOG_DIR="${LOG_DIR:-${SCRIPT_DIR}/logs}"
STATUS_DIR="${STATUS_DIR:-${LOG_DIR}/status}"
RUN_HOUR="${RUN_HOUR:-22}"
RUN_MINUTE="${RUN_MINUTE:-0}"
OLLAMA_START_TIMEOUT="${OLLAMA_START_TIMEOUT:-30}"

mkdir -p "${OUTPUT_DIR}" "${TEXT_OUTPUT_DIR}" "${CACHE_DIR}" "${LOG_DIR}" "${STATUS_DIR}"

MODE="${1:-both}"
if [[ $# -gt 0 ]]; then
    shift
fi
MODE_ARGS=("$@")
STATE_FILE="${CACHE_DIR}/run_state.json"

python_now() {
    "${PYTHON_BIN}" - "$@" <<'PY'
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

command = sys.argv[1]

if command == "run_id":
    print(datetime.now().strftime("%Y%m%dT%H%M%S"))
elif command == "needs_catchup":
    state_file = Path(sys.argv[2])
    hour = int(sys.argv[3])
    minute = int(sys.argv[4])
    if not state_file.exists():
        print("no")
        raise SystemExit(0)
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        last_success = datetime.fromisoformat(payload["last_success_at"])
    except Exception:
        print("no")
        raise SystemExit(0)
    now = datetime.now().astimezone()
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < due:
        due = due - timedelta(days=1)
    print("yes" if last_success < due else "no")
elif command == "write_state":
    state_file = Path(sys.argv[2])
    mode = sys.argv[3]
    run_kind = sys.argv[4]
    payload = {
        "last_success_at": datetime.now().astimezone().isoformat(),
        "last_mode": mode,
        "last_run_kind": run_kind,
    }
    state_file.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
elif command == "ollama_up":
    url = sys.argv[2]
    try:
        import requests
        response = requests.get(url, timeout=3)
        ok = response.status_code == 200
    except Exception:
        ok = False
    print("yes" if ok else "no")
PY
}

ensure_ollama_running() {
    local tags_url
    tags_url="${OLLAMA_URL%/api/generate}/api/tags"

    if [[ "$(python_now ollama_up "${tags_url}")" == "yes" ]]; then
        return 0
    fi

    if ! command -v ollama >/dev/null 2>&1; then
        echo "Ollama is not running and the ollama CLI is missing."
        return 1
    fi

    echo "Starting ollama service..."
    nohup ollama serve >/dev/null 2>&1 &

    local waited=0
    while [[ "${waited}" -lt "${OLLAMA_START_TIMEOUT}" ]]; do
        if [[ "$(python_now ollama_up "${tags_url}")" == "yes" ]]; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    echo "Ollama did not become ready within ${OLLAMA_START_TIMEOUT}s."
    return 1
}

run_mode() {
    local mode="$1"
    local run_kind="$2"
    shift 2
    local extra_args=("$@")
    local run_id
    local stdout_file
    local stderr_file
    local status_file

    if [[ "${mode}" != "doctor" ]]; then
        ensure_ollama_running
    fi

    run_id="$(python_now run_id)"
    stdout_file="${LOG_DIR}/${run_id}_${run_kind}_${mode}.stdout.log"
    stderr_file="${LOG_DIR}/${run_id}_${run_kind}_${mode}.stderr.log"
    status_file="${STATUS_DIR}/${run_id}_${run_kind}_${mode}.json"

    echo "Running mode=${mode} kind=${run_kind}"
    GAS_STATUS_FILE="${status_file}" GAS_RUN_KIND="${run_kind}" \
        "${PYTHON_BIN}" "${SCRIPT_DIR}/repo_audio_summary.py" "${mode}" "${extra_args[@]}" \
        >>"${stdout_file}" 2>>"${stderr_file}"

    python_now write_state "${STATE_FILE}" "${mode}" "${run_kind}"
    echo "Logs:"
    echo "  stdout: ${stdout_file}"
    echo "  stderr: ${stderr_file}"
    echo "  status: ${status_file}"
}

maybe_run_catchup() {
    if [[ "${MODE}" != "daily" && "${MODE}" != "full" && "${MODE}" != "both" ]]; then
        return 1
    fi

    if [[ "$(python_now needs_catchup "${STATE_FILE}" "${RUN_HOUR}" "${RUN_MINUTE}")" == "yes" ]]; then
        echo "Detected a missed scheduled run. Executing exactly one catch-up run."
        run_mode "both" "catchup"
        return 0
    fi

    return 1
}

case "${MODE}" in
    doctor)
        run_mode "doctor" "doctor"
        ;;
    catchup-check)
        if [[ "$(python_now needs_catchup "${STATE_FILE}" "${RUN_HOUR}" "${RUN_MINUTE}")" == "yes" ]]; then
            echo "Login catch-up detected."
            run_mode "both" "catchup"
        else
            echo "No catch-up required."
        fi
        ;;
    daily|full|both)
        if maybe_run_catchup; then
            echo "Catch-up run completed; skipping duplicate immediate rerun."
            exit 0
        fi
        run_mode "${MODE}" "manual"
        ;;
    deep|deep-dive|topic|thema)
        run_mode "${MODE}" "manual" "${MODE_ARGS[@]}"
        ;;
    *)
        echo "Unknown mode: ${MODE}"
        echo "Use: daily | full | deep <topic> | both | doctor"
        exit 1
        ;;
esac
