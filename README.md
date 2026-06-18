# Git Audio Summary

macOS-only automation for a daily German audio briefing about a Git repository.

Every evening the system can send two Telegram audio messages:

1. A daily change summary for the last 24 hours
2. A full architecture summary for the whole repository

Additionally, you can trigger a topic-specific deep-dive audio on demand.

The runtime is designed to stay local:

- Ollama for the LLM
- Local fallback model chain
- `edge-tts` for text-to-speech
- Telegram for delivery
- macOS LaunchAgents for scheduling

## What changed in this version

- Cross-platform-safe repository scan using Python instead of GNU `find`
- Cached repository index for the full architecture analysis
- Automatic model fallback chain
- Retries and fallback text delivery for Telegram failures
- Local `.txt` transcript archive for every generated summary
- A committed `run_summary.sh` wrapper with `daily`, `full`, `deep`, `both`, and `doctor`
- Separate login catch-up handling if the Mac slept through the scheduled run
- JSON status files plus dedicated stdout and stderr logs per run
- Fixture-based tests for repo indexing, cache reuse, and change prioritization

## Requirements

- macOS
- Python 3.10+
- Git
- Ollama installed and usable from the shell
- Telegram account for the bot delivery

## Setup

Run this once on the Mac that should execute the automation:

```bash
bash setup.sh
```

`setup.sh` will:

- install missing Python packages
- ensure the configured Ollama models are available
- ask for Telegram bot token and chat id
- write a safely quoted `.env`
- install a scheduled LaunchAgent for the nightly run
- install a login LaunchAgent that performs exactly one catch-up run if needed
- run `doctor` at the end
- optionally run an immediate smoke test

## Windows prep before moving to the Mac

If you are still on Windows and only want to prepare the handoff, use these files:

- [WINDOWS_PREP.md](/path/to/git-audio-summary/WINDOWS_PREP.md)
- [MAC_HANDOFF_TEMPLATE.md](/path/to/git-audio-summary/MAC_HANDOFF_TEMPLATE.md)
- [windows_prep.ps1](/path/to/git-audio-summary/windows_prep.ps1)

The PowerShell helper gives you a quick status view:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows_prep.ps1
```

## Main entrypoint

Use the committed wrapper:

```bash
bash run_summary.sh both
```

Supported modes:

- `bash run_summary.sh daily`
- `bash run_summary.sh full`
- `bash run_summary.sh deep "auftragsfortschreibung"`
- `bash run_summary.sh both`
- `bash run_summary.sh doctor`

The wrapper also supports an internal `catchup-check` mode used by the login LaunchAgent.

## Deep Dive mode

Use the deep-dive mode when you want a focused audio walkthrough for one topic instead of a whole-repo summary.

Example:

```bash
bash run_summary.sh deep "config loader"
```

The topic can be a component name, file family, workflow, domain concept, or technical concern, for example:

- `bash run_summary.sh deep "auftragsfortschreibung"`
- `bash run_summary.sh deep "telegram delivery"`
- `bash run_summary.sh deep "config loader"`
- `bash run_summary.sh deep "tests"`

The generated transcript stores the requested topic in its metadata and the audio filename is prefixed with `deepdive_...`.

## Configuration

The setup writes `.env` with quoted values so paths with spaces stay safe.

Important keys:

```bash
REPO_PATH='/Users/you/example-repo'
PRIMARY_MODEL='gemma4:e4b'
FALLBACK_MODELS='gemma3:4b,qwen2.5:7b'
OLLAMA_URL='http://localhost:11434/api/generate'
TELEGRAM_BOT_TOKEN='...'
TELEGRAM_CHAT_ID='...'
TTS_VOICE='de-DE-ConradNeural'
OUTPUT_DIR='/path/to/audio_output'
CACHE_DIR='/path/to/cache'
LOG_DIR='/path/to/logs'
STATUS_DIR='/path/to/logs/status'
TEXT_OUTPUT_DIR='/path/to/audio_output/transcripts'
HOURS_BACK='24'
RUN_HOUR='22'
RUN_MINUTE='0'
MAX_DIFF_CHARS='12000'
TELEGRAM_SEND_RETRIES='3'
OLLAMA_START_TIMEOUT='30'
```

## Logs and status files

Each wrapper run writes:

- one stdout log file in `logs/`
- one stderr log file in `logs/`
- one JSON status file in `logs/status/`
- one `.txt` transcript in `audio_output/transcripts/`

The cached repository index for the full analysis is stored in `cache/repo_index.json`.

## Doctor mode

`doctor` performs non-destructive diagnostics for:

- Python packages
- repository path and git access
- write permissions for runtime directories
- Ollama HTTP reachability
- configured model availability
- Telegram bot and chat configuration
- local TTS generation
- LaunchAgent presence
- basic pip availability

It does not send Telegram messages.

## Notes

- The full architecture analysis is generated daily, not weekly.
- Daily summaries now prioritize entrypoints, config files, and architectural hotspots.
- Architecture prompts explicitly separate visible facts from cautious hypotheses.
- If Telegram audio upload fails, the system keeps the local MP3 and tries a Telegram text fallback.
- If the Mac was asleep at the planned time, the login LaunchAgent triggers one catch-up run on the next login.

## Tests

There is a small fixture repository plus unittest coverage for:

- repository indexing and import detection
- cache reuse
- changed-file prioritization
- transcript archiving

Run with:

```bash
python3 -m unittest discover -s tests -v
```
