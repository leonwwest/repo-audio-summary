#!/usr/bin/env python3
"""
Reliable daily Git audio summaries for macOS.

Modes:
  - daily: summarize the last HOURS_BACK hours
  - full: build a repository-wide architecture summary
  - both: run daily and full
  - doctor: run diagnostics without Telegram side effects
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - handled by doctor/setup
    requests = None


EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".runtime",
    "coverage",
    "target",
    "out",
    "vendor",
    "Pods",
}

SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
TEXT_EXTENSIONS = SOURCE_EXTENSIONS | {
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".example",
    ".txt",
    ".sh",
    ".zsh",
    ".bash",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".xml",
    ".java",
    ".rb",
    ".go",
    ".rs",
    ".php",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".wav",
    ".ogg",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".ico",
    ".icns",
    ".jar",
    ".class",
    ".dll",
    ".so",
    ".dylib",
    ".exe",
    ".bin",
    ".pyc",
}

CONFIG_FILE_NAMES = [
    "README.md",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "webpack.config.js",
]

ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
    "main.ts",
    "main.js",
    "index.ts",
    "index.js",
    "src/main.ts",
    "src/main.js",
    "src/index.ts",
    "src/index.js",
    "src/index.tsx",
    "src/index.jsx",
    "src/App.tsx",
    "src/App.jsx",
    "cmd/main.go",
}

DEFAULT_DAILY_NO_CHANGE = (
    "Hier ist deine taegliche Repo-Zusammenfassung. Heute gab es in den letzten "
    "24 Stunden keine neuen Commits. Das ist kein Fehler, sondern einfach ein ruhiger "
    "Tag im Repository. Die Architektur-Zusammenfassung kommt trotzdem wie geplant, "
    "damit du den Gesamtueberblick behaeltst."
)


class SummaryError(RuntimeError):
    """Raised when the summary pipeline cannot continue safely."""


@dataclass
class Config:
    repo_path: Path
    primary_model: str
    fallback_models: list[str]
    ollama_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    tts_voice: str
    output_dir: Path
    text_output_dir: Path
    cache_dir: Path
    log_dir: Path
    status_dir: Path
    hours_back: int
    run_hour: int
    run_minute: int
    max_diff_chars: int
    telegram_send_retries: int
    ollama_start_timeout: int

    @classmethod
    def from_env(cls) -> "Config":
        base_dir = Path(__file__).resolve().parent
        fallback_models = [
            value.strip()
            for value in os.environ.get("FALLBACK_MODELS", "gemma3:4b,qwen2.5:7b").split(",")
            if value.strip()
        ]
        output_dir = Path(os.environ.get("OUTPUT_DIR", str(base_dir / "audio_output"))).expanduser()
        text_output_dir = Path(os.environ.get("TEXT_OUTPUT_DIR", str(output_dir / "transcripts"))).expanduser()
        cache_dir = Path(os.environ.get("CACHE_DIR", str(base_dir / "cache"))).expanduser()
        log_dir = Path(os.environ.get("LOG_DIR", str(base_dir / "logs"))).expanduser()
        status_dir = Path(os.environ.get("STATUS_DIR", str(log_dir / "status"))).expanduser()
        return cls(
            repo_path=Path(os.environ.get("REPO_PATH", "~/example-repo")).expanduser(),
            primary_model=os.environ.get("PRIMARY_MODEL", "gemma4:e4b"),
            fallback_models=fallback_models,
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate"),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            tts_voice=os.environ.get("TTS_VOICE", "de-DE-ConradNeural"),
            output_dir=output_dir,
            text_output_dir=text_output_dir,
            cache_dir=cache_dir,
            log_dir=log_dir,
            status_dir=status_dir,
            hours_back=int(os.environ.get("HOURS_BACK", "24")),
            run_hour=int(os.environ.get("RUN_HOUR", "22")),
            run_minute=int(os.environ.get("RUN_MINUTE", "0")),
            max_diff_chars=int(os.environ.get("MAX_DIFF_CHARS", "12000")),
            telegram_send_retries=int(os.environ.get("TELEGRAM_SEND_RETRIES", "3")),
            ollama_start_timeout=int(os.environ.get("OLLAMA_START_TIMEOUT", "30")),
        )


class RunRecorder:
    """Write a single JSON status file for each wrapper invocation."""

    def __init__(self, mode: str, config: Config):
        self.path = Path(os.environ.get("GAS_STATUS_FILE", "")).expanduser() if os.environ.get("GAS_STATUS_FILE") else None
        self.data: dict[str, Any] = {
            "mode": mode,
            "run_kind": os.environ.get("GAS_RUN_KIND", "manual"),
            "status": "running",
            "started_at": now_iso(),
            "repo_path": str(config.repo_path),
            "results": {},
            "warnings": [],
        }
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.write()

    def add_warning(self, message: str) -> None:
        self.data.setdefault("warnings", []).append(message)
        self.write()

    def set_result(self, key: str, value: Any) -> None:
        self.data.setdefault("results", {})[key] = value
        self.write()

    def finalize(self, status: str, error: str | None = None) -> None:
        self.data["status"] = status
        self.data["finished_at"] = now_iso()
        if error:
            self.data["error"] = error
        self.write()

    def write(self) -> None:
        if not self.path:
            return
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=True), encoding="utf-8")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")


def require_requests() -> Any:
    if requests is None:
        raise SummaryError("Python package 'requests' is not installed. Run setup.sh first.")
    return requests


def run_command(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SummaryError(f"Command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SummaryError(f"Command timed out: {' '.join(args)}") from exc

    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise SummaryError(f"Command failed: {' '.join(args)} :: {detail}")
    return completed


def ensure_runtime_directories(config: Config) -> None:
    for directory in (config.output_dir, config.text_output_dir, config.cache_dir, config.log_dir, config.status_dir):
        directory.mkdir(parents=True, exist_ok=True)


def ensure_git_repository(config: Config) -> None:
    if not config.repo_path.exists():
        raise SummaryError(f"Repository path does not exist: {config.repo_path}")
    run_command(["git", "rev-parse", "--show-toplevel"], config.repo_path, timeout=20)


def parse_package_json_dependencies(content: str) -> list[str]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(payload.get(key, {}))
    return sorted(deps.keys())


def parse_requirements(content: str) -> list[str]:
    values = []
    for line in content.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        cleaned = re.split(r"[<>=~!]", cleaned, maxsplit=1)[0].strip()
        if cleaned:
            values.append(cleaned)
    return sorted(set(values))


def parse_pyproject_dependencies(content: str) -> list[str]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return []
    try:
        payload = tomllib.loads(content)
    except Exception:
        return []

    values: list[str] = []
    project = payload.get("project", {})
    values.extend(project.get("dependencies", []))
    for dependency_group in project.get("optional-dependencies", {}).values():
        values.extend(dependency_group)

    poetry = payload.get("tool", {}).get("poetry", {})
    poetry_deps = poetry.get("dependencies", {})
    for name in poetry_deps.keys():
        if name != "python":
            values.append(name)

    normalized = []
    for value in values:
        if isinstance(value, str):
            normalized.append(re.split(r"[<>=~! ;\[]", value, maxsplit=1)[0].strip())
    return sorted(set(filter(None, normalized)))


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_excluded(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in EXCLUDED_DIR_NAMES for part in rel.parts)


def is_binary_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:1024]
    except OSError:
        return True
    return b"\x00" in sample


def read_text_snippet(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def collect_repo_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if is_excluded(path, root):
            continue
        if is_binary_file(path):
            continue
        files.append(path)
    return sorted(files)


def compute_structure_signature(files: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in files:
        stat = path.stat()
        digest.update(relative_posix(path, root).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
    return digest.hexdigest()


def detect_entrypoints(files: list[Path], root: Path) -> list[str]:
    entrypoints = []
    for path in files:
        rel = relative_posix(path, root)
        if rel in ENTRYPOINT_NAMES:
            entrypoints.append(rel)
            continue
        if path.name in {"main.py", "main.ts", "main.js", "server.py", "app.py"} and (
            rel.count("/") == 0 or rel.startswith("src/")
        ):
            entrypoints.append(rel)
    return sorted(set(entrypoints))


def collect_directory_summary(files: list[Path], root: Path) -> list[str]:
    directories = {"."}
    for path in files:
        rel = path.relative_to(root)
        parent = rel.parent
        if parent == Path("."):
            continue
        parts = parent.parts
        if parts:
            directories.add(parts[0])
        if len(parts) >= 2:
            directories.add("/".join(parts[:2]))
    return sorted(directories)


def build_python_module_map(files: list[Path], root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        rel = relative_posix(path, root)
        parts = list(path.relative_to(root).parts)
        if not parts:
            continue
        if parts[-1] == "__init__.py":
            module = ".".join(parts[:-1])
        else:
            parts[-1] = path.stem
            module = ".".join(parts)
        if module:
            mapping[module] = rel
    return mapping


def python_module_for_path(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        return ".".join(parts[:-1])
    last = Path(parts[-1]).stem
    if len(parts) == 1:
        return last
    return ".".join([*parts[:-1], last])


def resolve_python_import(
    source_rel: str,
    module: str | None,
    level: int,
    module_map: dict[str, str],
) -> str | None:
    source_module = python_module_for_path(source_rel)
    source_parts = source_module.split(".") if source_module else []
    package_parts = source_parts if source_rel.endswith("__init__.py") else source_parts[:-1]

    if level > 0:
        base_parts = package_parts[:-level + 1] if level > 1 else package_parts
        target_parts = base_parts + (module.split(".") if module else [])
        candidate = ".".join(filter(None, target_parts))
        if candidate in module_map:
            return module_map[candidate]
        return None

    if not module:
        return None
    segments = module.split(".")
    for size in range(len(segments), 0, -1):
        candidate = ".".join(segments[:size])
        if candidate in module_map:
            return module_map[candidate]
    return None


def extract_python_imports(source_rel: str, content: str, module_map: dict[str, str]) -> tuple[list[str], list[str]]:
    internal: list[str] = []
    external: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return internal, external

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = resolve_python_import(source_rel, alias.name, 0, module_map)
                if target:
                    internal.append(target)
                else:
                    external.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            target = resolve_python_import(source_rel, module, node.level, module_map)
            if target:
                internal.append(target)
            elif module:
                external.append(module.split(".")[0])
    return sorted(set(internal)), sorted(set(external))


def resolve_js_import(source_rel: str, target: str, available: set[str]) -> str | None:
    if not target.startswith("."):
        return None
    base_dir = Path(source_rel).parent
    normalized = (base_dir / target).as_posix()
    candidates = [
        normalized,
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}.js",
        f"{normalized}.jsx",
        f"{normalized}.mjs",
        f"{normalized}.cjs",
        f"{normalized}/index.ts",
        f"{normalized}/index.tsx",
        f"{normalized}/index.js",
        f"{normalized}/index.jsx",
    ]
    for candidate in candidates:
        clean = Path(candidate).as_posix().lstrip("./")
        if clean in available:
            return clean
    return None


def extract_jsts_imports(source_rel: str, content: str, available: set[str]) -> tuple[list[str], list[str]]:
    internal: list[str] = []
    external: list[str] = []
    pattern = re.compile(r"(?:import|export)\s.+?\sfrom\s+['\"]([^'\"]+)['\"]|require\(\s*['\"]([^'\"]+)['\"]\s*\)")
    for match in pattern.finditer(content):
        target = match.group(1) or match.group(2)
        resolved = resolve_js_import(source_rel, target, available)
        if resolved:
            internal.append(resolved)
        elif target and not target.startswith("."):
            if target.startswith("@"):
                external.append("/".join(target.split("/")[:2]))
            else:
                external.append(target.split("/")[0])
    return sorted(set(internal)), sorted(set(external))


def collect_git_metadata(root: Path) -> dict[str, Any]:
    head_sha = run_command(["git", "rev-parse", "HEAD"], root, timeout=20).stdout.strip()
    branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], root, timeout=20).stdout.strip()
    commit_count = run_command(["git", "rev-list", "--count", "HEAD"], root, timeout=20).stdout.strip()
    first_commit = run_command(["git", "log", "--reverse", "--format=%ci", "-1"], root, timeout=20).stdout.strip()
    last_commit = run_command(["git", "log", "--format=%ci", "-1"], root, timeout=20).stdout.strip()
    contributors = run_command(["git", "shortlog", "-sn", "--all", "--no-merges"], root, timeout=20).stdout.strip().splitlines()
    branches = run_command(["git", "branch", "-a", "--sort=-committerdate"], root, timeout=20).stdout.strip().splitlines()
    return {
        "head_sha": head_sha,
        "branch": branch,
        "commit_count": commit_count,
        "first_commit": first_commit,
        "last_commit": last_commit,
        "contributors": contributors[:12],
        "branches": [item.strip() for item in branches[:12]],
    }


def collect_external_dependencies(config_snippets: dict[str, str]) -> list[str]:
    dependencies: set[str] = set()
    for name, content in config_snippets.items():
        short_name = Path(name).name
        if short_name == "package.json":
            dependencies.update(parse_package_json_dependencies(content))
        elif short_name == "requirements.txt":
            dependencies.update(parse_requirements(content))
        elif short_name == "pyproject.toml":
            dependencies.update(parse_pyproject_dependencies(content))
        elif short_name == "Pipfile":
            dependencies.update(re.findall(r'^\s*([A-Za-z0-9_.-]+)\s*=', content, flags=re.MULTILINE))
    return sorted(dependencies)


def build_architecture_model(index: dict[str, Any]) -> dict[str, Any]:
    files = index["files"]
    components = Counter()
    for rel in files:
        top = rel.split("/", 1)[0] if "/" in rel else "(root)"
        components[top] += 1

    incoming = Counter()
    outgoing = Counter()
    component_edges = Counter()
    for rel, details in index["imports"].items():
        internal_targets = details["internal"]
        outgoing[rel] += len(internal_targets)
        source_component = rel.split("/", 1)[0] if "/" in rel else "(root)"
        for target in internal_targets:
            incoming[target] += 1
            target_component = target.split("/", 1)[0] if "/" in target else "(root)"
            if source_component != target_component:
                component_edges[(source_component, target_component)] += 1

    hotspots = []
    for rel in sorted(set(list(incoming.keys()) + list(outgoing.keys()))):
        score = incoming[rel] * 2 + outgoing[rel]
        hotspots.append(
            {
                "file": rel,
                "incoming": incoming[rel],
                "outgoing": outgoing[rel],
                "score": score,
            }
        )
    hotspots.sort(key=lambda item: (-item["score"], item["file"]))

    component_relationships = [
        {"from": source, "to": target, "count": count}
        for (source, target), count in component_edges.most_common(12)
    ]

    entrypoint_dependencies = []
    for rel in index["entrypoints"][:10]:
        targets = index["imports"].get(rel, {}).get("internal", [])
        entrypoint_dependencies.append({"entrypoint": rel, "touches": targets[:8]})

    return {
        "components": components.most_common(12),
        "hotspots": hotspots[:12],
        "component_relationships": component_relationships,
        "entrypoint_dependencies": entrypoint_dependencies,
    }


def classify_path_role(rel: str) -> str:
    lower = rel.lower()
    name = Path(rel).name.lower()
    if name in {"package.json", "pyproject.toml", "requirements.txt", "dockerfile", "makefile"}:
        return "config"
    if lower.startswith(("src/ui/", "src/components/", "components/", "frontend/", "web/")):
        return "frontend"
    if lower.startswith(("src/api/", "api/", "backend/", "server/", "services/", "app/")):
        return "backend"
    if lower.startswith(("infra/", "terraform/", ".github/", "ops/", "deploy/", "k8s/")):
        return "infrastructure"
    if lower.startswith(("tests/", "test/", "__tests__/")):
        return "tests"
    if any(token in lower for token in ("config", "settings", ".env", "tsconfig", "vite.config", "next.config", "webpack")):
        return "config"
    return "core"


def describe_project_shape(index: dict[str, Any]) -> list[str]:
    roles = Counter(classify_path_role(rel) for rel in index["files"])
    descriptions = []
    if roles["frontend"]:
        descriptions.append("Es gibt einen erkennbaren Frontend-Bereich.")
    if roles["backend"]:
        descriptions.append("Es gibt einen erkennbaren Backend- oder Service-Bereich.")
    if roles["infrastructure"]:
        descriptions.append("Es gibt Infrastruktur- oder Deploy-Konfiguration im Repo.")
    if roles["tests"]:
        descriptions.append("Es gibt einen separaten Testbereich.")
    if not descriptions:
        descriptions.append("Das Repo wirkt eher kompakt und ohne stark getrennte Schichten.")
    return descriptions


def prioritize_changed_files(changes: dict[str, Any], index: dict[str, Any]) -> list[dict[str, Any]]:
    hotspots = {
        item["file"]: item["score"]
        for item in index.get("architecture_model", {}).get("hotspots", [])
    }
    entrypoints = set(index.get("entrypoints", []))
    config_files = set(index.get("config_snippets", {}).keys())
    changed_items = []

    for line in changes["files_changed"].splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        parts = cleaned.split(maxsplit=1)
        status = parts[0]
        rel = parts[1] if len(parts) > 1 else parts[0]
        score = 0
        reasons: list[str] = []

        if rel in entrypoints:
            score += 8
            reasons.append("Entrypoint")
        if rel in config_files:
            score += 7
            reasons.append("Konfiguration")
        hotspot_score = hotspots.get(rel, 0)
        if hotspot_score:
            score += min(hotspot_score, 6)
            reasons.append("zentraler Hotspot")

        role = classify_path_role(rel)
        if role in {"frontend", "backend", "infrastructure"}:
            score += 2
            reasons.append(role)
        if status.startswith("R"):
            score += 3
            reasons.append("Umbenennung")
        elif status.startswith("D"):
            score += 2
            reasons.append("Loeschung")
        else:
            score += 1

        changed_items.append(
            {
                "file": rel,
                "status": status,
                "score": score,
                "role": role,
                "reasons": reasons,
            }
        )

    changed_items.sort(key=lambda item: (-item["score"], item["file"]))
    return changed_items


def summarize_priority_changes(changes: dict[str, Any], index: dict[str, Any]) -> str:
    prioritized = prioritize_changed_files(changes, index)
    if not prioritized:
        return "Keine priorisierten Dateien erkannt."
    lines = []
    for item in prioritized[:12]:
        reason_text = ", ".join(item["reasons"]) if item["reasons"] else "allgemeine Aenderung"
        lines.append(f"- {item['file']} [{item['status']}] -> {reason_text}")
    return "\n".join(lines)


def summarize_transcript_metadata(metadata: dict[str, Any]) -> str:
    lines = []
    for key, value in metadata.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def archive_summary_text(
    config: Config,
    *,
    file_prefix: str,
    summary_text: str,
    metadata: dict[str, Any],
) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    text_path = config.text_output_dir / f"{file_prefix}_{timestamp}.txt"
    payload = (
        "Git Audio Summary Transcript\n"
        "============================\n\n"
        f"{summarize_transcript_metadata(metadata)}\n\n"
        f"{summary_text}\n"
    )
    text_path.write_text(payload, encoding="utf-8")
    return text_path


def build_repo_index(config: Config) -> dict[str, Any]:
    files = collect_repo_files(config.repo_path)
    if not files:
        raise SummaryError("No readable text files found in the repository.")

    rel_files = [relative_posix(path, config.repo_path) for path in files]
    rel_set = set(rel_files)
    git_meta = collect_git_metadata(config.repo_path)
    structure_signature = compute_structure_signature(files, config.repo_path)

    file_types = Counter(path.suffix.lower() or "(no suffix)" for path in files)
    directories = collect_directory_summary(files, config.repo_path)
    entrypoints = detect_entrypoints(files, config.repo_path)

    config_snippets: dict[str, str] = {}
    for path in files:
        if path.name in CONFIG_FILE_NAMES:
            config_snippets[relative_posix(path, config.repo_path)] = read_text_snippet(path, 3000)

    key_file_snippets: dict[str, str] = {}
    selected_key_files = set(entrypoints)
    for rel in rel_files:
        if rel.endswith((".py", ".ts", ".tsx", ".js", ".jsx")) and len(selected_key_files) < 12:
            if rel.startswith("src/") or rel.count("/") <= 1:
                selected_key_files.add(rel)
    for rel in sorted(selected_key_files):
        key_file_snippets[rel] = read_text_snippet(config.repo_path / rel, 3000)

    module_map = build_python_module_map(files, config.repo_path)
    imports: dict[str, dict[str, list[str]]] = {}
    external_modules = Counter()
    for path in files:
        rel = relative_posix(path, config.repo_path)
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        content = read_text_snippet(path, 8000)
        if path.suffix.lower() == ".py":
            internal, external = extract_python_imports(rel, content, module_map)
        else:
            internal, external = extract_jsts_imports(rel, content, rel_set)
        imports[rel] = {"internal": internal, "external": external}
        external_modules.update(external)

    index = {
        "created_at": now_iso(),
        "structure_signature": structure_signature,
        "git": git_meta,
        "files": rel_files,
        "total_files": len(rel_files),
        "directories": directories,
        "file_types": [{"extension": ext, "count": count} for ext, count in file_types.most_common(20)],
        "entrypoints": entrypoints,
        "config_snippets": config_snippets,
        "key_file_snippets": key_file_snippets,
        "imports": imports,
        "external_dependencies": collect_external_dependencies(config_snippets),
        "external_modules": [name for name, _count in external_modules.most_common(30)],
        "project_shape": describe_project_shape({"files": rel_files}),
    }
    index["architecture_model"] = build_architecture_model(index)
    return index


def cache_file(config: Config) -> Path:
    return config.cache_dir / "repo_index.json"


def load_cached_index(config: Config) -> dict[str, Any] | None:
    path = cache_file(config)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_cached_index(config: Config, index: dict[str, Any]) -> None:
    cache_file(config).write_text(json.dumps(index, indent=2, ensure_ascii=True), encoding="utf-8")


def load_or_build_repo_index(config: Config) -> tuple[dict[str, Any], bool]:
    files = collect_repo_files(config.repo_path)
    rel_files = [relative_posix(path, config.repo_path) for path in files]
    structure_signature = compute_structure_signature(files, config.repo_path)
    cached = load_cached_index(config)

    if cached and cached.get("structure_signature") == structure_signature:
        cached["cache_used"] = True
        cached["git"] = collect_git_metadata(config.repo_path)
        cached["total_files"] = len(rel_files)
        cached["files"] = rel_files
        cached["directories"] = collect_directory_summary(files, config.repo_path)
        cached["project_shape"] = cached.get("project_shape") or describe_project_shape({"files": rel_files})
        return cached, True

    index = build_repo_index(config)
    index["cache_used"] = False
    save_cached_index(config, index)
    return index, False


def shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def format_daily_prompt(changes: dict[str, Any], config: Config, index: dict[str, Any]) -> str:
    prioritized_changes = summarize_priority_changes(changes, index)
    return f"""
Du erstellst eine deutsche Audio-Zusammenfassung fuer einen Entwickler.

Ziel:
- erklaere die wichtigsten Aenderungen der letzten {config.hours_back} Stunden
- verbinde Commits, Dateien und moegliche Motivation zu einer klaren Geschichte
- gewichte zentrale Dateien staerker als Nebenaenderungen
- nenne nur die wichtigsten Dateien
- klinge wie eine kurze, natuerliche Sprachnachricht
- keine Markdown-Syntax und keine Aufzaehlungszeichen
- beginne mit "Hier ist deine taegliche Repo-Zusammenfassung."
- ende mit einem kurzen Fazit
- Umfang: etwa 250 bis 380 Woerter
- trenne sichtbar bestaetigte Aenderungen von moeglicher Absicht; wenn du Motivation nur vermutest, sage das kurz als Vermutung

Zeitraum:
{changes["period"]}

Priorisierte geaenderte Dateien:
{prioritized_changes}

Commit-Log:
{changes["log"] or "(keine Commits)"}

Geaenderte Dateien:
{changes["files_changed"] or "(keine Dateien)"}

Diff-Auszug:
{changes["detailed_diff"] or "(kein Diff)"}
""".strip()


def format_full_prompt(index: dict[str, Any]) -> str:
    architecture = index["architecture_model"]
    components_text = "\n".join(f"- {name}: {count} Dateien" for name, count in architecture["components"])
    relationships_text = "\n".join(
        f"- {item['from']} -> {item['to']} ({item['count']} Verbindungen)"
        for item in architecture["component_relationships"]
    ) or "- Keine deutlichen component-uebergreifenden Beziehungen erkannt"
    hotspot_text = "\n".join(
        f"- {item['file']} (eingehend {item['incoming']}, ausgehend {item['outgoing']})"
        for item in architecture["hotspots"]
    ) or "- Keine Hotspots erkannt"
    entrypoint_text = "\n".join(
        f"- {item['entrypoint']} -> {', '.join(item['touches']) if item['touches'] else 'keine internen Ziele erkannt'}"
        for item in architecture["entrypoint_dependencies"]
    ) or "- Keine klaren Entrypoints erkannt"
    configs_text = "\n\n".join(
        f"[{name}]\n{shorten(content, 1200)}"
        for name, content in list(index["config_snippets"].items())[:8]
    )
    key_files_text = "\n\n".join(
        f"[{name}]\n{shorten(content, 1200)}"
        for name, content in list(index["key_file_snippets"].items())[:8]
    )

    return f"""
Du bist ein erfahrener Software-Architekt und erklaerst einem Entwickler ein Repository als Audio-Briefing auf Deutsch.

Ziel:
- gib einen echten Gesamtueberblick ueber Architektur, Verantwortlichkeiten und Zusammenhaenge
- nenne nicht nur Dateilisten, sondern erklaere, wie die Teile miteinander arbeiten
- markiere Vermutungen klar als Vermutung, wenn etwas nicht direkt sichtbar ist
- benenne zuerst sichere Beobachtungen aus dem Code und trenne danach hoechstens wenige vorsichtige Hypothesen
- klinge natuerlich, gesprochensprachlich und ohne Markdown oder Aufzaehlungszeichen
- beginne mit "Willkommen zur Architektur-Analyse deines Repositories."
- ende mit den wichtigsten Erkenntnissen und moeglichen Auffaelligkeiten
- Umfang: etwa 600 bis 900 Woerter

Git-Metadaten:
- Branch: {index["git"]["branch"]}
- HEAD: {index["git"]["head_sha"]}
- Commits: {index["git"]["commit_count"]}
- Erster Commit: {index["git"]["first_commit"]}
- Letzter Commit: {index["git"]["last_commit"]}

Repo-Struktur:
- Gesamtdateien: {index["total_files"]}
- Verzeichnisse: {", ".join(index["directories"][:25])}
- Dateitypen: {", ".join(f"{item['extension']}={item['count']}" for item in index["file_types"][:12])}
- Entrypoints: {", ".join(index["entrypoints"][:12]) or "keine klaren Entrypoints gefunden"}
- Projektform: {" ".join(index.get("project_shape", []))}

Architekturmodell:
Komponenten:
{components_text}

Beziehungen zwischen Komponenten:
{relationships_text}

Hotspots:
{hotspot_text}

Entrypoint-zu-Modul Beziehungen:
{entrypoint_text}

Externe Abhaengigkeiten:
{", ".join(index["external_dependencies"][:40]) or ", ".join(index["external_modules"][:20]) or "keine eindeutigen Abhaengigkeiten erkannt"}

Wichtige Konfigurationen:
{shorten(configs_text, 5000)}

Wichtige Dateien:
{shorten(key_files_text, 5000)}

Ausgabe-Regeln:
- Sage explizit, was aus dem Code sicher sichtbar ist.
- Wenn du etwas nur aus Mustern ableitest, kuendige es mit "Meine Vermutung ist" an.
- Vermeide konkrete Behauptungen ueber Laufzeitverhalten, wenn es dafuer in den Eingaben keinen klaren Beleg gibt.
""".strip()


def get_git_changes(config: Config) -> dict[str, Any]:
    since = (datetime.now() - timedelta(hours=config.hours_back)).strftime("%Y-%m-%d %H:%M")
    log_result = run_command(
        ["git", "log", f"--since={since}", "--pretty=format:%h | %an | %s", "--stat"],
        config.repo_path,
        timeout=60,
    )
    diff_result = run_command(
        ["git", "log", f"--since={since}", "--pretty=format:", "--name-status"],
        config.repo_path,
        timeout=60,
    )
    detailed_diff = run_command(
        ["git", "log", f"--since={since}", "-p", "--stat"],
        config.repo_path,
        timeout=120,
    )
    diff_text = shorten(detailed_diff.stdout.strip(), config.max_diff_chars)
    commit_lines = [line for line in log_result.stdout.splitlines() if "|" in line]
    return {
        "period": f"Letzte {config.hours_back} Stunden (seit {since})",
        "log": log_result.stdout.strip(),
        "files_changed": diff_result.stdout.strip(),
        "detailed_diff": diff_text,
        "commit_count": len(commit_lines),
    }


class ModelRunner:
    def __init__(self, config: Config, recorder: RunRecorder):
        self.config = config
        self.recorder = recorder
        self.model_sequence = [config.primary_model, *config.fallback_models]

    def generate(self, prompt: str, *, max_tokens: int, timeout: int, purpose: str) -> tuple[str, str]:
        requests_module = require_requests()
        errors = []
        for model in self.model_sequence:
            model = model.strip()
            if not model:
                continue
            log(f"Trying model '{model}' for {purpose}")
            try:
                response = requests_module.post(
                    self.config.ollama_url,
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": max_tokens},
                    },
                    timeout=timeout,
                )
                if response.status_code >= 400:
                    body = response.text.strip()
                    errors.append(f"{model}: {body or response.status_code}")
                    continue
                payload = response.json()
                if payload.get("response"):
                    self.recorder.set_result(f"{purpose}_model", model)
                    return payload["response"].strip(), model
                errors.append(f"{model}: empty response")
            except requests_module.Timeout:
                errors.append(f"{model}: timeout")
            except requests_module.RequestException as exc:
                errors.append(f"{model}: {exc}")
        raise SummaryError(f"No Ollama model succeeded for {purpose}: {' | '.join(errors)}")


async def generate_audio(text: str, output_path: Path, voice: str) -> Path:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate="-5%")
    await communicate.save(str(output_path))
    return output_path


def telegram_request(config: Config, endpoint: str, *, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> Any:
    if not config.telegram_bot_token:
        raise SummaryError("Telegram bot token is not configured.")
    requests_module = require_requests()
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/{endpoint}"
    delay = 2
    last_error: Exception | None = None
    for attempt in range(1, config.telegram_send_retries + 1):
        try:
            response = requests_module.post(url, data=data, files=files, timeout=120)
            if response.status_code == 200:
                return response
            last_error = SummaryError(f"Telegram returned {response.status_code}: {response.text}")
        except requests_module.RequestException as exc:
            last_error = exc
        if attempt < config.telegram_send_retries:
            time.sleep(delay)
            delay *= 2
    if last_error:
        raise SummaryError(f"Telegram request failed after retries: {last_error}")
    raise SummaryError("Telegram request failed for an unknown reason.")


def send_telegram_text(config: Config, text: str) -> None:
    if not config.telegram_chat_id:
        raise SummaryError("Telegram chat id is not configured.")
    telegram_request(
        config,
        "sendMessage",
        data={"chat_id": config.telegram_chat_id, "text": text[:4096]},
    )


def send_telegram_audio(config: Config, audio_path: Path, caption: str) -> None:
    if not config.telegram_chat_id:
        raise SummaryError("Telegram chat id is not configured.")
    with audio_path.open("rb") as audio_file:
        telegram_request(
            config,
            "sendAudio",
            data={
                "chat_id": config.telegram_chat_id,
                "caption": caption[:1024],
                "title": audio_path.stem.replace("_", " ").title(),
                "performer": "Git Audio Summary",
            },
            files={"audio": audio_file},
        )


async def render_and_send_audio(
    config: Config,
    recorder: RunRecorder,
    *,
    summary_key: str,
    summary_text: str,
    file_prefix: str,
    caption: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    audio_path = config.output_dir / f"{file_prefix}_{timestamp}.mp3"
    transcript_path = archive_summary_text(
        config,
        file_prefix=file_prefix,
        summary_text=summary_text,
        metadata=metadata,
    )
    await generate_audio(summary_text, audio_path, config.tts_voice)

    compact_caption_parts = [caption]
    if metadata.get("model"):
        compact_caption_parts.append(f"Modell: {metadata['model']}")
    if "cache_used" in metadata:
        compact_caption_parts.append(f"Cache: {'ja' if metadata['cache_used'] else 'nein'}")
    if metadata.get("run_kind"):
        compact_caption_parts.append(f"Run: {metadata['run_kind']}")
    final_caption = " | ".join(compact_caption_parts)

    result = {
        "audio_path": str(audio_path),
        "transcript_path": str(transcript_path),
        "caption": final_caption,
        "telegram_audio_sent": False,
        "telegram_text_fallback": False,
    }
    try:
        send_telegram_audio(config, audio_path, final_caption)
        result["telegram_audio_sent"] = True
    except SummaryError as exc:
        recorder.add_warning(str(exc))
        fallback = (
            f"{final_caption}\n\nDer Audio-Versand ist fehlgeschlagen. "
            f"Die Datei liegt lokal unter: {audio_path}\n"
            f"Das Textarchiv liegt unter: {transcript_path}\n\n"
            f"Kurzfassung:\n{summary_text[:3500]}"
        )
        try:
            send_telegram_text(config, fallback)
            result["telegram_text_fallback"] = True
        except SummaryError as fallback_exc:
            recorder.add_warning(str(fallback_exc))
    recorder.set_result(summary_key, result)
    return result


async def run_daily_summary(config: Config, runner: ModelRunner, recorder: RunRecorder) -> dict[str, Any]:
    changes = get_git_changes(config)
    index, cache_used = load_or_build_repo_index(config)
    if changes["commit_count"] == 0:
        summary = DEFAULT_DAILY_NO_CHANGE
        model_used = None
    else:
        prompt = format_daily_prompt(changes, config, index)
        summary, model_used = runner.generate(prompt, max_tokens=700, timeout=180, purpose="daily")
    caption = (
        f"Daily Update {datetime.now().strftime('%d.%m.%Y')}\n"
        f"Commits in den letzten {config.hours_back}h: {changes['commit_count']}"
    )
    result = {
        "period": changes["period"],
        "commit_count": changes["commit_count"],
        "model": model_used,
        "cache_used": cache_used,
        "summary_preview": summary[:500],
        "top_changes": prioritize_changed_files(changes, index)[:5],
    }
    await render_and_send_audio(
        config,
        recorder,
        summary_key="daily_delivery",
        summary_text=summary,
        file_prefix="daily",
        caption=caption,
        metadata={
            "mode": "daily",
            "model": model_used or "kein Modell benoetigt",
            "cache_used": cache_used,
            "run_kind": os.environ.get("GAS_RUN_KIND", "manual"),
            "commit_count": changes["commit_count"],
        },
    )
    recorder.set_result("daily", result)
    return result


async def run_full_summary(config: Config, runner: ModelRunner, recorder: RunRecorder) -> dict[str, Any]:
    index, cache_used = load_or_build_repo_index(config)
    prompt = format_full_prompt(index)
    summary, model_used = runner.generate(prompt, max_tokens=1800, timeout=300, purpose="full")
    caption = (
        f"Architektur-Analyse {datetime.now().strftime('%d.%m.%Y')}\n"
        f"Dateien: {index['total_files']} | Commits: {index['git']['commit_count']}"
    )
    result = {
        "model": model_used,
        "cache_used": cache_used,
        "total_files": index["total_files"],
        "head_sha": index["git"]["head_sha"],
        "summary_preview": summary[:500],
    }
    await render_and_send_audio(
        config,
        recorder,
        summary_key="full_delivery",
        summary_text=summary,
        file_prefix="architecture",
        caption=caption,
        metadata={
            "mode": "full",
            "model": model_used,
            "cache_used": cache_used,
            "run_kind": os.environ.get("GAS_RUN_KIND", "manual"),
            "head_sha": index["git"]["head_sha"],
            "total_files": index["total_files"],
        },
    )
    recorder.set_result("full", result)
    return result


def module_status(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


async def run_doctor(config: Config, recorder: RunRecorder) -> int:
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str, critical: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "critical": critical})

    requests_installed = module_status("requests")
    edge_tts_installed = module_status("edge_tts")

    add_check("python3", sys.version_info >= (3, 10), sys.version.replace("\n", " "), True)
    add_check("pip", shutil.which("pip3") is not None or shutil.which("pip") is not None, "pip available in PATH", False)
    add_check("requests", requests_installed, "installed" if requests_installed else "missing", True)
    add_check("edge_tts", edge_tts_installed, "installed" if edge_tts_installed else "missing", True)
    add_check("repo_path", config.repo_path.exists(), str(config.repo_path), True)

    try:
        ensure_git_repository(config)
        add_check("git_repo", True, "Git repository is readable", True)
    except SummaryError as exc:
        add_check("git_repo", False, str(exc), True)

    for directory in (config.output_dir, config.cache_dir, config.log_dir, config.status_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            test_path = directory / ".doctor.tmp"
            test_path.write_text("ok", encoding="utf-8")
            test_path.unlink()
            add_check(f"write:{directory.name}", True, str(directory), True)
        except OSError as exc:
            add_check(f"write:{directory.name}", False, f"{directory}: {exc}", True)

    add_check("ollama_cli", shutil.which("ollama") is not None, "ollama command available", False)

    requests_module = requests
    if requests_module is None:
        add_check("ollama_http", False, "Python package 'requests' is missing", True)
        add_check("telegram_bot", False, "Python package 'requests' is missing", True)
        add_check("telegram_chat", False, "Python package 'requests' is missing", True)
    else:
        try:
            tags_url = config.ollama_url.replace("/api/generate", "/api/tags")
            response = requests_module.get(tags_url, timeout=10)
            response.raise_for_status()
            models = [item["name"] for item in response.json().get("models", [])]
            add_check("ollama_http", True, tags_url, True)
            available_models = set(models)
            for model in [config.primary_model, *config.fallback_models]:
                add_check(f"model:{model}", model in available_models, "configured model", False)
        except requests_module.RequestException as exc:
            add_check("ollama_http", False, str(exc), True)

        if config.telegram_bot_token:
            try:
                response = requests_module.get(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/getMe",
                    timeout=10,
                )
                response.raise_for_status()
                add_check("telegram_bot", response.json().get("ok", False), "Bot token accepted", True)
            except requests_module.RequestException as exc:
                add_check("telegram_bot", False, str(exc), True)
        else:
            add_check("telegram_bot", False, "Token missing", True)

        if config.telegram_bot_token and config.telegram_chat_id:
            try:
                response = requests_module.get(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/getChat",
                    params={"chat_id": config.telegram_chat_id},
                    timeout=10,
                )
                response.raise_for_status()
                add_check("telegram_chat", response.json().get("ok", False), "Chat reachable", True)
            except requests_module.RequestException as exc:
                add_check("telegram_chat", False, str(exc), True)
        else:
            add_check("telegram_chat", False, "Chat id missing", True)

    if module_status("edge_tts"):
        temp_audio = None
        try:
            fd, temp_name = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            temp_audio = Path(temp_name)
            await generate_audio("Dies ist ein kurzer Doctor-Test.", temp_audio, config.tts_voice)
            add_check("tts_generation", temp_audio.exists() and temp_audio.stat().st_size > 0, str(temp_audio), True)
        except Exception as exc:
            add_check("tts_generation", False, str(exc), True)
        finally:
            if temp_audio and temp_audio.exists():
                temp_audio.unlink()

    daily_agent = Path.home() / "Library" / "LaunchAgents" / "com.gitaudiosummary.daily.plist"
    catchup_agent = Path.home() / "Library" / "LaunchAgents" / "com.gitaudiosummary.catchup.plist"
    add_check("launchagent_daily", daily_agent.exists(), str(daily_agent), False)
    add_check("launchagent_catchup", catchup_agent.exists(), str(catchup_agent), False)

    recorder.set_result("doctor", {"checks": checks})
    critical_failures = [check for check in checks if check["critical"] and not check["ok"]]

    print(json.dumps({"checks": checks, "critical_failures": len(critical_failures)}, indent=2, ensure_ascii=True))
    return 1 if critical_failures else 0


async def main() -> int:
    config = Config.from_env()
    ensure_runtime_directories(config)
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "both"
    recorder = RunRecorder(mode, config)

    try:
        if mode == "doctor":
            exit_code = await run_doctor(config, recorder)
            recorder.finalize("success" if exit_code == 0 else "failed")
            return exit_code

        ensure_git_repository(config)
        runner = ModelRunner(config, recorder)
        if mode == "daily":
            await run_daily_summary(config, runner, recorder)
        elif mode in {"full", "architecture", "arch", "full-repo"}:
            await run_full_summary(config, runner, recorder)
        elif mode == "both":
            await run_daily_summary(config, runner, recorder)
            await run_full_summary(config, runner, recorder)
        else:
            raise SummaryError(f"Unknown mode: {mode}. Use daily, full, both, or doctor.")
        recorder.finalize("success")
        return 0
    except Exception as exc:
        recorder.finalize("failed", str(exc))
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
