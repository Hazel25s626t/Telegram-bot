import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("bot_bulk", Path(__file__).resolve().parents[1] / "golddrop_telegram_bot (1).py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class BulkImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(bot, "FB_FILE", str(Path(self.tmp.name) / "dbs.txt"))
        p.start()
        self.addCleanup(p.stop)
        self.send = patch.object(bot, "tg_send").start()
        self.addCleanup(patch.stopall)

    def session_for(self, content):
        session = MagicMock()
        meta = MagicMock(status_code=200)
        meta.json.return_value = {"ok": True, "result": {"file_path": "documents/file.txt"}}
        download = MagicMock(status_code=200)
        download.__enter__.return_value = download
        download.iter_content.return_value = iter([content])
        session.get.side_effect = [meta, download]
        return session

    def test_bulk_text_and_document_deduplicate(self):
        bot.handle_tg_command(None, "token", 1, "/add https://a.test https://a.test")
        session = self.session_for(b"# https://ignored.test\nhttps://a.test\nhttps://b.test|||key\n")
        bot.handle_tg_document(session, "token", 1, {"file_name": "db.txt", "file_id": "id"})
        self.assertEqual(bot.load_dbs(), [("https://a.test", ""), ("https://b.test", "key")])
        self.assertIn("1 pehle se saved", self.send.call_args.args[3])

    def test_reject_large_file_and_non_text(self):
        session = MagicMock()
        bot.handle_tg_document(session, "token", 1, {"file_name": "db.txt", "file_size": bot.MAX_DB_FILE_BYTES + 1})
        bot.handle_tg_document(session, "token", 1, {"file_name": "db.pdf"})
        session.get.assert_not_called()
        self.assertEqual(bot.load_dbs(), [])

    def test_stream_size_and_invalid_utf8(self):
        for content in (b'x' * (bot.MAX_DB_FILE_BYTES + 1), b'\xff'):
            bot.handle_tg_document(self.session_for(content), "token", 1, {"file_name": "db.txt", "file_id": "id"})
            self.assertIn("File read nahi hui", self.send.call_args.args[3])
            self.assertEqual(bot.load_dbs(), [])

    def test_untrusted_file_path_is_not_downloaded(self):
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"file_path": "../bad"}}
        session.get.return_value = response
        bot.handle_tg_document(session, "token", 1, {"file_name": "db.txt", "file_id": "id"})
        self.assertEqual(session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
