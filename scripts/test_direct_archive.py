import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
COMET_PATCHES = REPO_ROOT / "comet-patches"
if str(COMET_PATCHES) not in sys.path:
    sys.path.insert(0, str(COMET_PATCHES))

import direct_archive


class TestDirectArchive(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "direct_archive.json")
        self.orig_state_path = direct_archive.STATE_PATH
        direct_archive.STATE_PATH = self.state_file
        direct_archive._QUEUED.clear()
        direct_archive._QUEUE.clear()
        direct_archive._FAIL_COOLDOWN.clear()

    def tearDown(self):
        direct_archive.STATE_PATH = self.orig_state_path
        self.temp_dir.cleanup()

    # 1. bencode
    def test_bencode(self):
        self.assertEqual(direct_archive.bencode(42), b"i42e")
        self.assertEqual(direct_archive.bencode(0), b"i0e")
        self.assertEqual(direct_archive.bencode(-10), b"i-10e")
        self.assertEqual(direct_archive.bencode(b"spam"), b"4:spam")
        self.assertEqual(direct_archive.bencode("eggs"), b"4:eggs")
        self.assertEqual(direct_archive.bencode("olá"), b"4:ol\xc3\xa1")
        self.assertEqual(direct_archive.bencode([1, "two", b"three"]), b"li1e3:two5:threee")
        d = {"z": 1, "a": 2, "m": "mid"}
        self.assertEqual(direct_archive.bencode(d), b"d1:ai2e1:m3:mid1:zi1ee")
        d2 = {"b": 2, "a": 1}
        self.assertEqual(direct_archive.bencode(d2), b"d1:ai1e1:bi2ee")
        with self.assertRaises(TypeError):
            direct_archive.bencode(True)
        with self.assertRaises(TypeError):
            direct_archive.bencode(False)
        with self.assertRaises(TypeError):
            direct_archive.bencode(3.14)
        with self.assertRaises(TypeError):
            direct_archive.bencode(None)

    # 2. build_torrent_bytes
    def test_build_torrent_bytes(self):
        pieces = b"\x00" * 20
        t1, hash1 = direct_archive.build_torrent_bytes(
            filename="Test.Release.mp4",
            total_size=1048576,
            pieces=pieces,
            webseed_urls=["http://example.com/video.mp4"],
        )
        t2, hash2 = direct_archive.build_torrent_bytes(
            filename="Test.Release.mp4",
            total_size=1048576,
            pieces=pieces,
            webseed_urls=[],
        )
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 40)
        self.assertIn(b"url-list", t1)
        self.assertIn(b"http://example.com/video.mp4", t1)
        self.assertNotIn(b"url-list", t2)
        t3, hash3 = direct_archive.build_torrent_bytes(
            filename="Test.Release.mp4",
            total_size=1048576,
            pieces=pieces,
            webseed_urls=["http://example.com/video.mp4"],
        )
        self.assertEqual(t1, t3)
        self.assertEqual(hash1, hash3)

    # 3. make_release_filename
    def test_make_release_filename(self):
        fn1 = direct_archive.make_release_filename("2 Broke Girls!", 5, 1, 1080)
        self.assertEqual(fn1, "2.Broke.Girls.S05E01.1080p.WEB-DL.Dublado.PT-BR.mp4")

        self.assertIn("2160p", direct_archive.make_release_filename("Show", 1, 2, 2160))
        self.assertIn("2160p", direct_archive.make_release_filename("Show", 1, 2, 1800))
        self.assertIn("1080p", direct_archive.make_release_filename("Show", 1, 2, 1080))
        self.assertIn("1080p", direct_archive.make_release_filename("Show", 1, 2, 900))
        self.assertIn("720p", direct_archive.make_release_filename("Show", 1, 2, 720))
        self.assertIn("720p", direct_archive.make_release_filename("Show", 1, 2, 650))
        self.assertIn("480p", direct_archive.make_release_filename("Show", 1, 2, 480))
        self.assertIn("480p", direct_archive.make_release_filename("Show", 1, 2, 360))

        fn_fallback = direct_archive.make_release_filename("@#$%^", 2, 3, 720)
        self.assertEqual(fn_fallback, "Series.S02E03.720p.WEB-DL.Dublado.PT-BR.mp4")

    # 4. parse_series_ep
    def test_parse_series_ep(self):
        self.assertEqual(direct_archive.parse_series_ep("tt1845307:5:1"), ("tt1845307", 5, 1))
        self.assertEqual(direct_archive.parse_series_ep("tt0944947:1:10"), ("tt0944947", 1, 10))

        self.assertIsNone(direct_archive.parse_series_ep("tt1845307"))
        self.assertIsNone(direct_archive.parse_series_ep("tt1845307:0:1"))
        self.assertIsNone(direct_archive.parse_series_ep("tt1845307:1:0"))
        self.assertIsNone(direct_archive.parse_series_ep("tt1845307:-1:1"))
        self.assertIsNone(direct_archive.parse_series_ep("tt1845307:s1:e1"))
        self.assertIsNone(direct_archive.parse_series_ep(""))
        self.assertIsNone(direct_archive.parse_series_ep(None))

    # 5. pick_archive_candidates
    def test_pick_archive_candidates(self):
        streams = [
            {"name": "🇺🇸 English Only 1080p", "description": "FenixFlix", "url": "http://fenix.com/en1080.mp4"},
            {"name": "🇧🇷 Dublado (outro corte)", "description": "FenixFlix", "url": "http://fenix.com/cut1.mp4"},
            {"name": "🇧🇷 Dublado 1080p", "description": "Versão com outro corte", "url": "http://fenix.com/cut2.mp4"},
            {"name": "🇧🇷 Dublado 720p", "filename": "video.CUT", "url": "http://fenix.com/cut3.mp4"},
            {"name": "🇧🇷 Dublado 720p", "behaviorHints": {"filename": "FenixFlix.direct.CUT"}, "url": "http://fenix.com/cut4.mp4"},
            {"name": "🇧🇷 Dublado 1080p", "description": "FenixFlix", "url": "ftp://fenix.com/ftp.mp4"},
            {"name": "🇧🇷 Dublado (HLS)", "description": "FenixFlix (HLS)", "url": "http://fenix.com/stream.m3u8"},
            {"name": "🇧🇷 Dublado 720p", "description": "MegaEmbed", "url": "http://mega.com/ep.mp4"},
            {"name": "🇧🇷 Dublado 1080p", "description": "FenixFlix", "url": "http://fenix.com/ep1080.mp4"},
            {"name": "🇧🇷 Dublado 720p", "description": "Direct", "url": "http://direct.com/ep720.mp4"},
            {"name": "🇧🇷 Dublado 480p", "description": "FenixFlix", "url": "http://fenix.com/ep1080.mp4"},
        ]

        cands = direct_archive.pick_archive_candidates(streams)
        urls = [c[0] for c in cands]
        self.assertEqual(urls[0], "http://fenix.com/ep1080.mp4")
        self.assertEqual(cands[0][1], "FenixFlix")
        self.assertFalse(cands[0][2])

        self.assertEqual(urls[1], "http://mega.com/ep.mp4")
        self.assertEqual(cands[1][1], "MegaEmbed")
        self.assertFalse(cands[1][2])

        self.assertEqual(urls[2], "http://direct.com/ep720.mp4")
        self.assertEqual(cands[2][1], "Direct")
        self.assertFalse(cands[2][2])

        self.assertEqual(urls[3], "http://fenix.com/stream.m3u8")
        self.assertTrue(cands[3][2])

        self.assertEqual(urls.count("http://fenix.com/ep1080.mp4"), 1)

    # 6. is_valid_probe
    def test_is_valid_probe(self):
        ok_probe = {"width": 1920, "height": 1080, "has_audio": True, "duration_s": 1320.0}
        valid, msg = direct_archive.is_valid_probe(ok_probe, 22)
        self.assertTrue(valid)
        self.assertEqual(msg, "ok")

        no_audio = {"width": 1920, "height": 1080, "has_audio": False, "duration_s": 1320.0}
        valid, msg = direct_archive.is_valid_probe(no_audio, 22)
        self.assertFalse(valid)
        self.assertIn("audio", msg)

        short_probe = {"width": 1920, "height": 1080, "has_audio": True, "duration_s": 250.0}
        valid, msg = direct_archive.is_valid_probe(short_probe, 22)
        self.assertFalse(valid)
        self.assertIn("short", msg)

        long_probe = {"width": 1920, "height": 1080, "has_audio": True, "duration_s": 120 * 60.0}
        valid, msg = direct_archive.is_valid_probe(long_probe, 40)
        self.assertFalse(valid)
        self.assertIn("long", msg)

        tmdb_long_expected = 60
        cut_short = {"width": 1920, "height": 1080, "has_audio": True, "duration_s": 25 * 60.0}
        valid, msg = direct_archive.is_valid_probe(cut_short, tmdb_long_expected)
        self.assertFalse(valid)
        self.assertIn("short", msg)

        tmdb_short_expected = 20
        cut_too_short = {"width": 1920, "height": 1080, "has_audio": True, "duration_s": 8 * 60.0}
        valid, msg = direct_archive.is_valid_probe(cut_too_short, tmdb_short_expected)
        self.assertFalse(valid)
        self.assertIn("short", msg)

    # 7. _load_state / _save_state em diretório temporário
    def test_state_persistence(self):
        state = direct_archive._load_state(self.state_file)
        self.assertIn("tt1845307", state["watched"])
        self.assertEqual(state["watched"]["tt1845307"]["name"], "2 Broke Girls")
        self.assertIsInstance(state["episodes"], dict)

        state["episodes"]["tt1845307:5:1"] = {"title": "ep1", "height": 1080}
        direct_archive._save_state(state, self.state_file)

        reloaded = direct_archive._load_state(self.state_file)
        self.assertIn("tt1845307:5:1", reloaded["episodes"])
        self.assertEqual(reloaded["episodes"]["tt1845307:5:1"]["height"], 1080)

        with patch.dict(os.environ, {"DIRECT_ARCHIVE_SERIES": "tt0944947,tt0903747"}):
            empty_state_file = os.path.join(self.temp_dir.name, "empty_state.json")
            state_env = direct_archive._load_state(empty_state_file)
            self.assertIn("tt0944947", state_env["watched"])
            self.assertIn("tt0903747", state_env["watched"])
            self.assertIn("tt1845307", state_env["watched"])

    # 8. maybe_enqueue
    def test_maybe_enqueue(self):
        state = direct_archive._load_state(self.state_file)
        state["watched"]["tt1845307"] = {"name": "2 Broke Girls"}
        direct_archive._save_state(state, self.state_file)

        valid_stream = [{"name": "🇧🇷 Dublado 720p", "description": "FenixFlix", "url": "http://fenix.com/ep.mp4"}]

        res = direct_archive.maybe_enqueue("tt1845307:5:1", valid_stream, has_cached_pt_hd=True)
        self.assertFalse(res)

        res = direct_archive.maybe_enqueue("tt9999999:1:1", valid_stream)
        self.assertFalse(res)

        state["episodes"]["tt1845307:5:1"] = {"height": 720}
        direct_archive._save_state(state, self.state_file)
        res = direct_archive.maybe_enqueue("tt1845307:5:1", valid_stream)
        self.assertFalse(res)

        res = direct_archive.maybe_enqueue("tt1845307:5:2", valid_stream)
        self.assertTrue(res)
        self.assertIn("tt1845307:5:2", direct_archive._QUEUED)
        self.assertEqual(len(direct_archive._QUEUE), 1)

        res_dup = direct_archive.maybe_enqueue("tt1845307:5:2", valid_stream)
        self.assertFalse(res_dup)

    # 9. handle_command
    def test_handle_command(self):
        state = direct_archive._load_state(self.state_file)
        state["watched"] = {"tt1845307": {"name": "2 Broke Girls"}}
        state["episodes"] = {
            "tt1845307:5:1": {"title": "ep501", "height": 1080},
            "tt1845307:5:2": {"title": "ep502", "height": 1080},
            "tt1845307:6:1": {"title": "ep601", "height": 720},
        }
        direct_archive._save_state(state, self.state_file)

        res_other = asyncio.run(direct_archive.handle_command("/status"))
        self.assertIsNone(res_other)
        res_help = asyncio.run(direct_archive.handle_command("/ajuda"))
        self.assertIsNone(res_help)

        res_status = asyncio.run(direct_archive.handle_command("/arquivar"))
        self.assertIsNotNone(res_status)
        self.assertIn("2 Broke Girls", res_status)
        self.assertIn("3 eps salvos", res_status)
        self.assertIn("T5: 2", res_status)
        self.assertIn("T6: 1", res_status)

        res_del = asyncio.run(direct_archive.handle_command("/desarquivar tt1845307"))
        self.assertIsNotNone(res_del)
        self.assertIn("removida", res_del)

        reloaded = direct_archive._load_state(self.state_file)
        self.assertNotIn("tt1845307", reloaded["watched"])

        res_not_found = asyncio.run(direct_archive.handle_command("/desarquivar inexistente"))
        self.assertIn("não encontrada", res_not_found)

    # 10. _torbox_active_count ignora torrents inativos (active=False)
    def test_torbox_active_count(self):
        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "data": [
                        {"id": 1, "active": True, "download_present": False, "download_state": "downloading"},
                        {"id": 2, "active": True, "download_present": False, "download_state": "stalled (no seeds)"},
                        {"id": 3, "active": False, "download_present": False, "download_state": "checking"},
                        {"id": 4, "active": False, "download_present": False, "download_state": "incomplete"},
                        {"id": 5, "active": False, "download_present": True, "download_state": "cached"},
                    ]
                }

        class FakeClient:
            async def get(self, url, headers=None, timeout=None):
                return FakeResp()

        cnt = asyncio.run(direct_archive._torbox_active_count(FakeClient(), "k"))
        self.assertEqual(cnt, 2)


if __name__ == "__main__":
    unittest.main()
