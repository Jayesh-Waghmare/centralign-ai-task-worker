import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.config import load_local_env

ROOT = Path(__file__).resolve().parents[1] / "data/test-runs"


class ConfigTests(unittest.TestCase):
    def test_known_settings_only_and_environment_precedence(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            path = Path(temporary) / ".env"
            path.write_text("# local settings\nWORKER_API_KEY='synthetic-test-key'\nWORKER_MODEL=fixture-model\nWORKER_API_BASE=https://example.invalid/v1\nPATH=bad\nUNKNOWN=ignored\n", encoding="utf-8")
            with patch.dict(os.environ, {"WORKER_MODEL": "environment-model", "PATH": "original"}, clear=True):
                load_local_env(path)
                self.assertEqual(os.environ["WORKER_API_KEY"], "synthetic-test-key")
                self.assertEqual(os.environ["WORKER_MODEL"], "environment-model")
                self.assertEqual(os.environ["PATH"], "original")
                self.assertNotIn("UNKNOWN", os.environ)

    def test_missing_env_does_not_change_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            load_local_env(ROOT / "missing-do-not-create.env")
            self.assertEqual(dict(os.environ), {})
