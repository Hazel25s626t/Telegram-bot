import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "golddrop_telegram_bot (1).py"
spec = importlib.util.spec_from_file_location("golddrop_bot", SCRIPT)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class TelegramConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.base_patch = patch.object(bot, "BASE", str(self.base))
        self.file_patch = patch.object(bot, "TG_FILE", str(self.base / "telegram.json"))
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        for item in (self.base_patch, self.file_patch, self.env_patch):
            item.start()
            self.addCleanup(item.stop)

    def test_dotenv_and_environment_precedence(self):
        (self.base / ".env").write_text("BOT_TOKEN=file-token\nCHAT_ID=123\n")
        os.environ["BOT_TOKEN"] = "env-token"
        self.assertEqual(bot.load_tg_config(), {"bot_token": "env-token", "chat_id": "123"})

    def test_json_fallback(self):
        (self.base / "telegram.json").write_text(json.dumps({"bot_token": "old", "chat_id": 42}))
        self.assertEqual(bot.load_tg_config(), {"bot_token": "old", "chat_id": 42})

    def test_partial_configuration_fails(self):
        os.environ["BOT_TOKEN"] = "only-token"
        with self.assertRaisesRegex(ValueError, "both BOT_TOKEN and CHAT_ID"):
            bot.load_tg_config()


if __name__ == "__main__":
    unittest.main()
