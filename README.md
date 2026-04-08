# Git Audio Summary

Tägliche Audio-Zusammenfassungen deines Git-Repos – direkt auf Telegram.

## Was macht das?

Jeden Abend um 22:00 Uhr bekommst du **zwei Audio-Nachrichten** auf Telegram:

1. **Tages-Update** (~2-3 Min) – Was hat sich in den letzten 24h geändert?
2. **Architektur-Analyse** (~5-7 Min) – Wie ist das Repo aufgebaut? Wie hängt alles zusammen?

## Tech-Stack (100% kostenlos)

- **LLM:** Ollama + Gemma 4 E4B (lokal, ~8GB RAM)
- **TTS:** edge-tts (Microsoft Edge Stimmen, kein API-Key nötig)
- **Zustellung:** Telegram Bot
- **Scheduling:** macOS LaunchAgent (Cron-Alternative)

## Voraussetzungen

- macOS mit Python 3
- [Ollama](https://ollama.com/download) installiert
- Telegram auf dem Handy

## Setup

```bash
bash setup.sh
```

Das Setup-Skript führt dich interaktiv durch alles:
- Installiert Python-Pakete
- Lädt das Gemma 4 Modell herunter
- Hilft beim Telegram Bot einrichten
- Erstellt den täglichen Cronjob

## Nutzung

```bash
# Beide Zusammenfassungen
bash run_summary.sh

# Nur Tages-Update
bash run_summary.sh daily

# Nur Architektur-Analyse
bash run_summary.sh full
```

## Konfiguration

Nach dem Setup liegt eine `.env` Datei im Ordner:

```
REPO_PATH=/pfad/zu/deinem/repo
OLLAMA_MODEL=gemma4:e4b
TELEGRAM_BOT_TOKEN=dein-token
TELEGRAM_CHAT_ID=deine-id
TTS_VOICE=de-DE-ConradNeural
HOURS_BACK=24
```
