#!/usr/bin/env python3
"""
Git Audio Summary – Zwei Modi:
  1. DAILY:     Tägliche Änderungen zusammenfassen (was hat sich heute getan?)
  2. FULL-REPO: Komplette Repo-Architektur analysieren (wie hängt alles zusammen?)

Nutzung:
  python3 repo_audio_summary.py              → Beide: Daily + Full-Repo
  python3 repo_audio_summary.py daily        → Nur tägliche Änderungen
  python3 repo_audio_summary.py full         → Nur volle Repo-Analyse
"""

import subprocess
import asyncio
import os
import sys
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ============================================================
# KONFIGURATION – Passe diese Werte an!
# ============================================================

REPO_PATH = os.environ.get("REPO_PATH", os.path.expanduser("~/example-repo"))
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "DEIN_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "DEINE_CHAT_ID")
TTS_VOICE = os.environ.get("TTS_VOICE", "de-DE-ConradNeural")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", str(Path(__file__).parent / "audio_output"))
HOURS_BACK = int(os.environ.get("HOURS_BACK", "24"))

# ============================================================
# OLLAMA HELPER
# ============================================================

def ask_ollama(prompt: str, max_tokens: int = 1024, timeout: int = 180) -> str:
    """Sendet einen Prompt an Ollama und gibt die Antwort zurück."""
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": max_tokens}
            },
            timeout=timeout
        )
        response.raise_for_status()
        return response.json().get("response", "Fehler: Keine Antwort vom Modell.")
    except requests.exceptions.ConnectionError:
        print("❌ Ollama läuft nicht! Starte Ollama mit: ollama serve")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Fehler bei Ollama: {e}")
        sys.exit(1)

# ============================================================
# TEIL 1: TÄGLICHE ÄNDERUNGEN
# ============================================================

def get_git_changes(repo_path: str, hours_back: int = 24) -> dict:
    """Sammelt Git-Änderungen der letzten X Stunden."""
    since = (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%d %H:%M")

    log_result = subprocess.run(
        ["git", "log", f"--since={since}", "--pretty=format:%h | %an | %s", "--stat"],
        cwd=repo_path, capture_output=True, text=True
    )
    diff_result = subprocess.run(
        ["git", "log", f"--since={since}", "--pretty=format:", "--name-status"],
        cwd=repo_path, capture_output=True, text=True
    )
    detailed_diff = subprocess.run(
        ["git", "log", f"--since={since}", "-p", "--stat"],
        cwd=repo_path, capture_output=True, text=True
    )

    diff_text = detailed_diff.stdout[:8000]
    if len(detailed_diff.stdout) > 8000:
        diff_text += "\n\n... (weitere Änderungen gekürzt)"

    return {
        "log": log_result.stdout.strip(),
        "files_changed": diff_result.stdout.strip(),
        "detailed_diff": diff_text,
        "period": f"Letzte {hours_back} Stunden (seit {since})"
    }


def generate_daily_summary(changes: dict) -> str:
    """Generiert die tägliche Zusammenfassung."""
    if not changes["log"]:
        return "Heute gab es keine neuen Änderungen im Repository."

    prompt = f"""Du bist ein hilfreicher Assistent, der Git-Repository-Änderungen zusammenfasst.
Erstelle eine klare, verständliche Zusammenfassung auf Deutsch, die für einen Audio-Podcast geeignet ist.

Wichtig:
- Erkläre die ZUSAMMENHÄNGE zwischen den Änderungen
- Fasse zusammen, WARUM Dinge geändert wurden (wenn erkennbar)
- Nenne die wichtigsten Dateien und was sich dort geändert hat
- Halte es kurz und prägnant (max. 2-3 Minuten Sprechzeit, ca. 300-400 Wörter)
- Verwende eine natürliche, gesprochene Sprache (kein Markdown, keine Aufzählungszeichen)
- Beginne mit "Hier ist deine tägliche Repo-Zusammenfassung."
- Ende mit einem kurzen Fazit

Zeitraum: {changes["period"]}

=== COMMIT LOG ===
{changes["log"]}

=== GEÄNDERTE DATEIEN ===
{changes["files_changed"]}

=== DETAILLIERTER DIFF ===
{changes["detailed_diff"]}
"""
    print("🤖 Generiere tägliche Zusammenfassung...")
    return ask_ollama(prompt)

# ============================================================
# TEIL 2: VOLLE REPO-ANALYSE
# ============================================================

def scan_repo_structure(repo_path: str) -> dict:
    """Scannt die komplette Repo-Struktur und sammelt Architektur-Informationen."""

    # 1. Verzeichnisbaum (ohne .git, node_modules, etc.)
    tree_result = subprocess.run(
        ["find", ".", "-type", "f",
         "-not", "-path", "./.git/*",
         "-not", "-path", "./node_modules/*",
         "-not", "-path", "./__pycache__/*",
         "-not", "-path", "./.venv/*",
         "-not", "-path", "./venv/*",
         "-not", "-path", "./.next/*",
         "-not", "-path", "./dist/*",
         "-not", "-path", "./build/*",
         "-not", "-path", "./.cache/*"],
        cwd=repo_path, capture_output=True, text=True
    )
    all_files = [f.strip() for f in tree_result.stdout.strip().split("\n") if f.strip()]

    # 2. Datei-Statistiken nach Typ
    file_types = defaultdict(list)
    for f in all_files:
        ext = Path(f).suffix.lower() or "(kein Suffix)"
        file_types[ext].append(f)

    # 3. Ordnerstruktur (Top-Level + 1 Ebene tief)
    dir_result = subprocess.run(
        ["find", ".", "-type", "d", "-maxdepth", "2",
         "-not", "-path", "./.git*",
         "-not", "-path", "./node_modules*",
         "-not", "-path", "./__pycache__*",
         "-not", "-path", "./.venv*"],
        cwd=repo_path, capture_output=True, text=True
    )
    directories = [d.strip() for d in dir_result.stdout.strip().split("\n") if d.strip()]

    # 4. Konfigurations- und Projektdateien lesen
    config_files = {}
    important_configs = [
        "package.json", "Cargo.toml", "pyproject.toml", "setup.py", "setup.cfg",
        "go.mod", "pom.xml", "build.gradle", "Makefile", "CMakeLists.txt",
        "docker-compose.yml", "Dockerfile", "requirements.txt", "Pipfile",
        ".env.example", "tsconfig.json", "webpack.config.js", "vite.config.ts",
        "vite.config.js", "next.config.js", "README.md"
    ]
    for config in important_configs:
        config_path = os.path.join(repo_path, config)
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(3000)  # Max 3KB pro Datei
                config_files[config] = content
            except Exception:
                pass

    # 5. Import/Dependency-Analyse: Schlüsseldateien lesen
    key_files_content = {}
    # Entrypoints und wichtige Dateien finden
    entrypoint_patterns = [
        "main.py", "app.py", "index.py", "server.py", "__init__.py",
        "index.ts", "index.js", "app.ts", "app.js", "main.ts", "main.js",
        "src/main.rs", "main.go", "cmd/main.go",
        "src/index.tsx", "src/App.tsx", "src/app.tsx",
        "src/index.ts", "src/App.ts",
    ]
    for pattern in entrypoint_patterns:
        for f in all_files:
            if f.endswith(pattern) or f == f"./{pattern}":
                full_path = os.path.join(repo_path, f.lstrip("./"))
                if os.path.exists(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                            key_files_content[f] = fh.read(4000)
                    except Exception:
                        pass

    # 6. Git-Statistiken
    contributors = subprocess.run(
        ["git", "shortlog", "-sn", "--all", "--no-merges"],
        cwd=repo_path, capture_output=True, text=True
    )
    recent_branches = subprocess.run(
        ["git", "branch", "-a", "--sort=-committerdate"],
        cwd=repo_path, capture_output=True, text=True
    )
    total_commits = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_path, capture_output=True, text=True
    )
    first_commit = subprocess.run(
        ["git", "log", "--reverse", "--format=%ci", "-1"],
        cwd=repo_path, capture_output=True, text=True
    )
    last_commit = subprocess.run(
        ["git", "log", "--format=%ci", "-1"],
        cwd=repo_path, capture_output=True, text=True
    )

    return {
        "total_files": len(all_files),
        "file_types": {k: {"count": len(v), "examples": v[:5]} for k, v in sorted(file_types.items(), key=lambda x: -len(x[1]))},
        "directories": directories[:50],
        "config_files": config_files,
        "key_files": key_files_content,
        "contributors": contributors.stdout.strip()[:500],
        "branches": recent_branches.stdout.strip()[:500],
        "total_commits": total_commits.stdout.strip(),
        "first_commit": first_commit.stdout.strip(),
        "last_commit": last_commit.stdout.strip(),
    }


def generate_full_repo_summary(scan: dict) -> str:
    """Generiert eine tiefgehende Architektur-Analyse des gesamten Repos."""

    # Konfig-Dateien formatieren
    configs_text = ""
    for name, content in scan["config_files"].items():
        configs_text += f"\n--- {name} ---\n{content[:1500]}\n"

    # Schlüsseldateien formatieren
    key_files_text = ""
    for name, content in scan["key_files"].items():
        key_files_text += f"\n--- {name} ---\n{content[:2000]}\n"

    # Dateitypen formatieren
    types_text = "\n".join(
        f"  {ext}: {info['count']} Dateien (z.B. {', '.join(info['examples'][:3])})"
        for ext, info in list(scan["file_types"].items())[:15]
    )

    prompt = f"""Du bist ein erfahrener Software-Architekt, der ein Git-Repository analysiert.
Erstelle eine umfassende, verständliche Audio-Zusammenfassung auf Deutsch.

Deine Analyse soll folgende Fragen beantworten:
1. WAS IST DAS FÜR EIN PROJEKT? (Zweck, Technologie-Stack, Sprache)
2. WIE IST ES AUFGEBAUT? (Ordnerstruktur, Architektur-Pattern wie MVC, Microservices, etc.)
3. WIE HÄNGEN DIE TEILE ZUSAMMEN? (Welche Module/Dateien rufen welche auf? Datenfluss?)
4. WAS SIND DIE KERN-ABHÄNGIGKEITEN? (Externe Libraries, warum werden sie gebraucht?)
5. WAS SIND DIE WICHTIGSTEN DATEIEN? (Entrypoints, Konfigurationen, zentrale Module)
6. WIE AKTIV IST DAS PROJEKT? (Commits, Contributors, Branches)

Wichtig für die Audio-Ausgabe:
- Sprich natürlich, als würdest du einem Kollegen das Projekt erklären
- Kein Markdown, keine Aufzählungszeichen, keine Code-Blöcke
- Erkläre Fachbegriffe kurz wenn nötig
- Circa 5-7 Minuten Sprechzeit (600-900 Wörter)
- Beginne mit "Willkommen zur Architektur-Analyse deines Repositories."
- Baue die Erklärung logisch auf: erst der Überblick, dann die Details, dann die Zusammenhänge
- Ende mit den wichtigsten Erkenntnissen und eventuellen Auffälligkeiten

=== PROJEKT-STATISTIKEN ===
Gesamtdateien: {scan["total_files"]}
Gesamtcommits: {scan["total_commits"]}
Erster Commit: {scan["first_commit"]}
Letzter Commit: {scan["last_commit"]}

=== DATEITYPEN ===
{types_text}

=== ORDNERSTRUKTUR ===
{chr(10).join(scan["directories"][:40])}

=== KONFIGURATIONS-DATEIEN ===
{configs_text[:6000]}

=== SCHLÜSSEL-DATEIEN (Entrypoints, zentrale Module) ===
{key_files_text[:6000]}

=== CONTRIBUTORS ===
{scan["contributors"]}

=== BRANCHES ===
{scan["branches"]}
"""

    print("🏗️  Generiere Architektur-Analyse...")
    # Mehr Tokens für die ausführlichere Analyse
    return ask_ollama(prompt, max_tokens=2048, timeout=300)

# ============================================================
# AUDIO & TELEGRAM
# ============================================================

async def generate_audio(text: str, output_path: str) -> str:
    """Generiert eine Audio-Datei per edge-tts."""
    import edge_tts
    print(f"🔊 Generiere Audio mit Stimme '{TTS_VOICE}'...")
    communicate = edge_tts.Communicate(text, TTS_VOICE, rate="-5%")
    await communicate.save(output_path)
    print(f"✅ Audio gespeichert: {output_path}")
    return output_path


def send_telegram_audio(audio_path: str, caption: str):
    """Sendet die Audio-Datei über Telegram."""
    print("📤 Sende Audio über Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption[:1024],
                "title": Path(audio_path).stem.replace("_", " ").title(),
                "performer": "Git Audio Summary"
            },
            files={"audio": audio_file},
            timeout=120
        )

    if response.status_code == 200:
        print("✅ Audio erfolgreich über Telegram gesendet!")
    else:
        print(f"❌ Telegram-Fehler: {response.status_code} – {response.text}")


def send_telegram_text(text: str):
    """Sendet eine Text-Nachricht über Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4096],
        "parse_mode": "HTML"
    }, timeout=30)

# ============================================================
# MAIN
# ============================================================

async def run_daily():
    """Tägliche Änderungs-Zusammenfassung."""
    print(f"\n{'='*50}")
    print(f"📋 TÄGLICHE ZUSAMMENFASSUNG")
    print(f"{'='*50}\n")

    changes = get_git_changes(REPO_PATH, HOURS_BACK)

    if not changes["log"]:
        msg = f"📭 Keine Änderungen in den letzten {HOURS_BACK} Stunden."
        print(msg)
        send_telegram_text(msg)
        return

    commit_count = len([l for l in changes["log"].split("\n") if l.strip() and "|" in l])
    print(f"📊 {commit_count} Commits gefunden\n")

    summary = generate_daily_summary(changes)
    print(f"\n📝 Zusammenfassung:\n{'-'*40}\n{summary}\n{'-'*40}\n")

    date_str = datetime.now().strftime("%Y-%m-%d")
    audio_path = os.path.join(OUTPUT_DIR, f"daily_{date_str}.mp3")
    await generate_audio(summary, audio_path)

    caption = f"📋 Tages-Update {datetime.now().strftime('%d.%m.%Y')}\n📊 {commit_count} Commits in den letzten {HOURS_BACK}h"
    send_telegram_audio(audio_path, caption)


async def run_full_repo():
    """Volle Architektur-Analyse des gesamten Repos."""
    print(f"\n{'='*50}")
    print(f"🏗️  ARCHITEKTUR-ANALYSE")
    print(f"{'='*50}\n")

    print(f"📂 Scanne Repository: {REPO_PATH}")
    scan = scan_repo_structure(REPO_PATH)
    print(f"📊 {scan['total_files']} Dateien, {scan['total_commits']} Commits, {len(scan['config_files'])} Config-Dateien gefunden\n")

    summary = generate_full_repo_summary(scan)
    print(f"\n🏗️  Analyse:\n{'-'*40}\n{summary}\n{'-'*40}\n")

    date_str = datetime.now().strftime("%Y-%m-%d")
    audio_path = os.path.join(OUTPUT_DIR, f"architecture_{date_str}.mp3")
    await generate_audio(summary, audio_path)

    caption = f"🏗️ Architektur-Analyse {datetime.now().strftime('%d.%m.%Y')}\n📁 {scan['total_files']} Dateien | {scan['total_commits']} Commits"
    send_telegram_audio(audio_path, caption)


async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Modus bestimmen
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "both"

    print(f"\n🎙️  Git Audio Summary – {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print(f"   Modus: {mode.upper()}")
    print(f"   Repo:  {REPO_PATH}\n")

    if mode == "daily":
        await run_daily()
    elif mode in ("full", "full-repo", "architecture", "arch"):
        await run_full_repo()
    elif mode == "both":
        await run_daily()
        await run_full_repo()
    else:
        print(f"❌ Unbekannter Modus: {mode}")
        print("   Nutze: daily | full | both")
        sys.exit(1)

    print(f"\n✅ Alles fertig!")


if __name__ == "__main__":
    asyncio.run(main())
