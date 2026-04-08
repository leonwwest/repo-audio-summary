import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "repo_audio_summary.py"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "sample_repo"

SPEC = importlib.util.spec_from_file_location("repo_audio_summary", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["repo_audio_summary"] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_config(repo_path: Path) -> MODULE.Config:
    runtime_root = repo_path.parent / ".runtime"
    return MODULE.Config(
        repo_path=repo_path,
        primary_model="gemma4:e4b",
        fallback_models=["gemma3:4b"],
        ollama_url="http://localhost:11434/api/generate",
        telegram_bot_token="token",
        telegram_chat_id="chat",
        tts_voice="de-DE-ConradNeural",
        output_dir=runtime_root / "audio",
        text_output_dir=runtime_root / "audio" / "transcripts",
        cache_dir=runtime_root / "cache",
        log_dir=runtime_root / "logs",
        status_dir=runtime_root / "logs" / "status",
        hours_back=24,
        run_hour=22,
        run_minute=0,
        max_diff_chars=12000,
        telegram_send_retries=3,
        ollama_start_timeout=30,
    )


class RepoAudioSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="git-audio-summary-"))
        self.repo_path = self.temp_dir / "repo"
        shutil.copytree(FIXTURE_ROOT, self.repo_path)
        subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Fixture Tester"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.com"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial fixture"], cwd=self.repo_path, check=True, capture_output=True)
        self.config = make_config(self.repo_path)
        MODULE.ensure_runtime_directories(self.config)

    def tearDown(self) -> None:
        def handle_remove_readonly(func, path, exc_info):
            Path(path).chmod(0o700)
            func(path)

        shutil.rmtree(self.temp_dir, onexc=handle_remove_readonly)

    def test_build_repo_index_detects_entrypoints_imports_and_shape(self) -> None:
        index = MODULE.build_repo_index(self.config)

        self.assertIn("src/index.ts", index["entrypoints"])
        self.assertIn("src/components/App.tsx", index["imports"]["src/index.ts"]["internal"])
        self.assertIn("backend/app.py", index["imports"]["backend/server.py"]["internal"])
        self.assertIn("Es gibt einen erkennbaren Frontend-Bereich.", index["project_shape"])
        self.assertIn("Es gibt einen erkennbaren Backend- oder Service-Bereich.", index["project_shape"])

    def test_prioritize_changed_files_prefers_entrypoints_and_configs(self) -> None:
        index = MODULE.build_repo_index(self.config)
        changes = {
            "files_changed": "\n".join(
                [
                    "M docs/notes.md",
                    "M package.json",
                    "M backend/app.py",
                    "M src/index.ts",
                ]
            )
        }

        prioritized = MODULE.prioritize_changed_files(changes, index)
        top_files = [item["file"] for item in prioritized[:2]]

        self.assertIn("package.json", top_files)
        self.assertIn("src/index.ts", top_files)
        self.assertGreater(prioritized[0]["score"], prioritized[-1]["score"])

    def test_archive_summary_text_writes_transcript(self) -> None:
        path = MODULE.archive_summary_text(
            self.config,
            file_prefix="daily",
            summary_text="Kurze Zusammenfassung.",
            metadata={"mode": "daily", "model": "gemma4:e4b"},
        )

        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("mode: daily", content)
        self.assertIn("Kurze Zusammenfassung.", content)

    def test_load_or_build_repo_index_uses_cache_after_first_run(self) -> None:
        _index1, cache_used1 = MODULE.load_or_build_repo_index(self.config)
        _index2, cache_used2 = MODULE.load_or_build_repo_index(self.config)

        self.assertFalse(cache_used1)
        self.assertTrue(cache_used2)


if __name__ == "__main__":
    unittest.main()
