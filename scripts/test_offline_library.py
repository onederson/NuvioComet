import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
COMET_PATCHES = REPO_ROOT / "comet-patches"
if str(COMET_PATCHES) not in sys.path:
    sys.path.insert(0, str(COMET_PATCHES))

import offline_library


class TestOfflineLibrary(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_path = os.path.join(self.temp_dir.name, "offline_library.json")
        self.offline_dir = os.path.join(self.temp_dir.name, "offline")
        os.makedirs(self.offline_dir, exist_ok=True)

        self.orig_index_path = offline_library.INDEX_PATH
        self.orig_offline_dir = offline_library.OFFLINE_DIR

        offline_library.INDEX_PATH = self.index_path
        offline_library.OFFLINE_DIR = self.offline_dir
        offline_library._JOB_QUEUE.clear()
        offline_library._CURRENT_JOB = None
        offline_library._CANCEL_FLAG = False

    def tearDown(self):
        offline_library.INDEX_PATH = self.orig_index_path
        offline_library.OFFLINE_DIR = self.orig_offline_dir
        self.temp_dir.cleanup()

    # 1. parse_download_command
    def test_parse_download_command(self):
        # /baixar sem argumento -> status
        res_status = offline_library.parse_download_command("/baixar")
        self.assertEqual(res_status, {"action": "status"})

        # /baixar 2 broke girls
        res_bg = offline_library.parse_download_command("/baixar 2 broke girls")
        self.assertEqual(res_bg["action"], "download")
        self.assertEqual(res_bg["query"], "2 broke girls")
        self.assertIsNone(res_bg["imdb"])
        self.assertIsNone(res_bg["season"])
        self.assertIsNone(res_bg["episode"])
        self.assertTrue(res_bg["prefer_dub"])

        # Texto natural: "Baixar todas as temporadas de 2 broke girls dublado"
        res_nat = offline_library.parse_download_command("Baixar todas as temporadas de 2 broke girls dublado")
        self.assertEqual(res_nat["action"], "download")
        self.assertEqual(res_nat["query"], "2 broke girls")
        self.assertIsNone(res_nat["imdb"])
        self.assertIsNone(res_nat["season"])
        self.assertIsNone(res_nat["episode"])
        self.assertTrue(res_nat["prefer_dub"])

        # /baixar 2 broke girls T5
        res_t5 = offline_library.parse_download_command("/baixar 2 broke girls T5")
        self.assertEqual(res_t5["action"], "download")
        self.assertEqual(res_t5["query"], "2 broke girls")
        self.assertEqual(res_t5["season"], 5)
        self.assertIsNone(res_t5["episode"])
        self.assertTrue(res_t5["prefer_dub"])

        # /baixar 2 broke girls T5E01
        res_t5e1 = offline_library.parse_download_command("/baixar 2 broke girls T5E01")
        self.assertEqual(res_t5e1["action"], "download")
        self.assertEqual(res_t5e1["query"], "2 broke girls")
        self.assertEqual(res_t5e1["season"], 5)
        self.assertEqual(res_t5e1["episode"], 1)
        self.assertTrue(res_t5e1["prefer_dub"])

        # /baixar Matrix original
        res_orig = offline_library.parse_download_command("/baixar Matrix original")
        self.assertEqual(res_orig["action"], "download")
        self.assertEqual(res_orig["query"], "Matrix")
        self.assertIsNone(res_orig["season"])
        self.assertIsNone(res_orig["episode"])
        self.assertFalse(res_orig["prefer_dub"])

        # /baixados e /offline
        self.assertEqual(offline_library.parse_download_command("/baixados"), {"action": "list"})
        self.assertEqual(offline_library.parse_download_command("/offline"), {"action": "list"})

        # /cancelar e /parar
        self.assertEqual(offline_library.parse_download_command("/cancelar"), {"action": "cancel"})
        self.assertEqual(offline_library.parse_download_command("/parar"), {"action": "cancel"})

        # /apagar 2 broke girls T1
        res_del = offline_library.parse_download_command("/apagar 2 broke girls T1")
        self.assertEqual(res_del["action"], "delete")
        self.assertEqual(res_del["query"], "2 broke girls")
        self.assertEqual(res_del["season"], 1)
        self.assertIsNone(res_del["episode"])
        self.assertIsNone(res_del["imdb"])

        # Outros comandos retornam None
        self.assertIsNone(offline_library.parse_download_command("/arquivar 2 broke girls"))
        self.assertIsNone(offline_library.parse_download_command("/status"))
        self.assertIsNone(offline_library.parse_download_command("/ajuda"))
        self.assertIsNone(offline_library.parse_download_command("/help"))
        self.assertIsNone(offline_library.parse_download_command("oi"))
        self.assertIsNone(offline_library.parse_download_command(""))
        self.assertIsNone(offline_library.parse_download_command(None))

    # 2. parse_size_bytes
    def test_parse_size_bytes(self):
        self.assertEqual(offline_library.parse_size_bytes("2,4 GB"), int(2.4 * 1024**3))
        self.assertEqual(offline_library.parse_size_bytes("850 MB"), int(850 * 1024**2))
        self.assertEqual(offline_library.parse_size_bytes("1.5 GB"), int(1.5 * 1024**3))
        self.assertIsNone(offline_library.parse_size_bytes("texto sem tamanho"))
        self.assertIsNone(offline_library.parse_size_bytes(""))
        self.assertIsNone(offline_library.parse_size_bytes(None))

    # 3. pick_download_candidate
    def test_pick_download_candidate(self):
        torbox_pt = {
            "name": "🇧🇷 Dublado 1080p ⚡",
            "description": "TorBox [1080p] · 1,2 GB",
            "url": "https://torbox.app/playback/tb1080.mp4",
        }
        rd_pt = {
            "name": "🇧🇷 Dublado 1080p ⚡",
            "description": "RealDebrid [1080p] · 1,5 GB",
            "url": "https://rd.com/playback/rd1080.mp4",
        }
        fenix_mp4 = {
            "name": "🇧🇷 Dublado 1080p 🌐",
            "description": "FenixFlix · 1,2 GB",
            "url": "https://fenix.com/fenix1080.mp4",
        }
        mega_mp4 = {
            "name": "🇧🇷 Dublado 720p 🌐",
            "description": "MegaEmbed · 800 MB",
            "url": "https://mega.com/mega720.mp4",
        }
        mega_hls = {
            "name": "🇧🇷 Dublado (HLS) 🌐",
            "description": "MegaEmbed (hls) · 800 MB",
            "url": "https://mega.com/playlist.m3u8",
        }

        # Prioridade completa: TorBox > RD > FenixFlix MP4 > MegaEmbed MP4 > MegaEmbed HLS
        all_streams = [mega_hls, mega_mp4, fenix_mp4, rd_pt, torbox_pt]
        cand = offline_library.pick_download_candidate(all_streams, prefer_dub=True)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["url"], torbox_pt["url"])
        self.assertTrue(cand["is_torbox"])

        # Sem TorBox -> RealDebrid
        no_tb = [mega_hls, mega_mp4, fenix_mp4, rd_pt]
        cand2 = offline_library.pick_download_candidate(no_tb, prefer_dub=True)
        self.assertEqual(cand2["url"], rd_pt["url"])

        # Sem debrids -> FenixFlix MP4
        direct_only = [mega_hls, mega_mp4, fenix_mp4]
        cand3 = offline_library.pick_download_candidate(direct_only, prefer_dub=True)
        self.assertEqual(cand3["url"], fenix_mp4["url"])
        self.assertEqual(cand3["source"], "FenixFlix")
        self.assertFalse(cand3["is_hls"])

        # Sem FenixFlix -> MegaEmbed MP4
        mega_only = [mega_hls, mega_mp4]
        cand4 = offline_library.pick_download_candidate(mega_only, prefer_dub=True)
        self.assertEqual(cand4["url"], mega_mp4["url"])
        self.assertFalse(cand4["is_hls"])

        # Apenas HLS
        cand5 = offline_library.pick_download_candidate([mega_hls], prefer_dub=True)
        self.assertEqual(cand5["url"], mega_hls["url"])
        self.assertTrue(cand5["is_hls"])

        # Rejeições: outro corte, CAM, trailer, stream local
        bad_streams = [
            {"name": "🇧🇷 Dublado (outro corte)", "description": "FenixFlix", "url": "https://fenix.com/cut.mp4"},
            {"name": "🇧🇷 Dublado 720p", "description": "outro corte", "url": "https://fenix.com/cut2.mp4"},
            {"name": "🇧🇷 Dublado CAM 1080p", "description": "FenixFlix", "url": "https://fenix.com/cam.mp4"},
            {"name": "🇧🇷 Trailer Oficial 1080p", "description": "FenixFlix", "url": "https://fenix.com/trailer.mp4"},
            {"name": "▶ Assistir\n💾 1080p", "description": "💾 Local no A71", "url": "http://127.0.0.1:8000/offline/..."},
        ]
        self.assertIsNone(offline_library.pick_download_candidate(bad_streams, prefer_dub=True))

        # prefer_dub=True com apenas stream em inglês -> retorna None
        en_stream = {
            "name": "🇺🇸 English Only 1080p ⚡",
            "description": "TorBox [1080p] · 1,2 GB",
            "url": "https://torbox.app/playback/en1080.mp4",
        }
        self.assertIsNone(offline_library.pick_download_candidate([en_stream], prefer_dub=True))

        # prefer_dub=False escolhe o original
        cand_orig = offline_library.pick_download_candidate([en_stream], prefer_dub=False)
        self.assertIsNotNone(cand_orig)
        self.assertEqual(cand_orig["url"], en_stream["url"])

        # Limite max_bytes
        huge_stream = {
            "name": "🇧🇷 Dublado 1080p ⚡",
            "description": "TorBox [1080p] · 6,0 GB",
            "url": "https://torbox.app/playback/huge.mp4",
        }
        small_stream = {
            "name": "🇧🇷 Dublado 720p ⚡",
            "description": "TorBox [720p] · 1,5 GB",
            "url": "https://torbox.app/playback/small.mp4",
        }
        cand_limit = offline_library.pick_download_candidate(
            [huge_stream, small_stream],
            prefer_dub=True,
            max_bytes=int(3.0 * 1024**3),
        )
        self.assertEqual(cand_limit["url"], small_stream["url"])

    # 4. make_offline_path
    def test_make_offline_path(self):
        # Série
        full_p, fn = offline_library.make_offline_path(
            base_dir="/data/offline",
            media_type="series",
            imdb="tt1845307",
            title="2 Broke Girls!",
            season=5,
            episode=1,
            height=720,
            dubbed=True,
            ext=".mp4",
        )
        self.assertEqual(fn, "2.Broke.Girls.S05E01.720p.Dublado.PT-BR.mp4")
        expected_series_p = os.path.normpath("/data/offline/series/tt1845307/S05/2.Broke.Girls.S05E01.720p.Dublado.PT-BR.mp4")
        self.assertEqual(full_p, expected_series_p)

        # Filme original 1080p
        full_mov, fn_mov = offline_library.make_offline_path(
            base_dir="/data/offline",
            media_type="movie",
            imdb="tt0133093",
            title="The Matrix",
            season=None,
            episode=None,
            height=1080,
            dubbed=False,
            ext=".mp4",
        )
        self.assertEqual(fn_mov, "The.Matrix.1080p.Original.mp4")
        expected_mov_p = os.path.normpath("/data/offline/movies/tt0133093/The.Matrix.1080p.Original.mp4")
        self.assertEqual(full_mov, expected_mov_p)

        # Resoluções 2160p e 480p
        _, fn_4k = offline_library.make_offline_path("/data/offline", "movie", "tt1", "Show", None, None, 2160, True)
        self.assertIn("2160p", fn_4k)
        _, fn_sd = offline_library.make_offline_path("/data/offline", "movie", "tt1", "Show", None, None, 480, True)
        self.assertIn("480p", fn_sd)

    # 5. Persistência de índice + get_local_entry + build_local_stream
    def test_index_and_stream(self):
        # Arquivo inexistente no disco
        self.assertIsNone(offline_library.get_local_entry("tt1845307:5:1"))
        self.assertIsNone(offline_library.build_local_stream("tt1845307:5:1", "cfg", "http://127.0.0.1:8000"))

        # Cria arquivo real no disco
        series_dir = os.path.join(self.offline_dir, "series", "tt1845307", "S05")
        os.makedirs(series_dir, exist_ok=True)
        video_file = os.path.join(series_dir, "2.Broke.Girls.S05E01.720p.Dublado.PT-BR.mp4")
        with open(video_file, "wb") as f:
            f.write(b"0" * 1024 * 1024)  # 1 MB

        idx_data = {
            "items": {
                "tt1845307:5:1": {
                    "media_id": "tt1845307:5:1",
                    "media_type": "series",
                    "imdb": "tt1845307",
                    "title": "2 Broke Girls",
                    "season": 5,
                    "episode": 1,
                    "path": video_file,
                    "filename": "2.Broke.Girls.S05E01.720p.Dublado.PT-BR.mp4",
                    "size": 1024 * 1024,
                    "width": 1280,
                    "height": 720,
                    "duration_s": 1320.0,
                    "dubbed": True,
                    "source": "TorBox",
                    "torbox_hash": "dummyhash",
                    "downloaded_at": 1700000000,
                }
            }
        }
        offline_library._save_index(idx_data, self.index_path)

        entry = offline_library.get_local_entry("tt1845307:5:1", self.index_path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["title"], "2 Broke Girls")

        stream = offline_library.build_local_stream(
            "tt1845307:5:1",
            "mytoken",
            "http://127.0.0.1:8000",
            self.index_path,
        )
        self.assertIsNotNone(stream)
        self.assertEqual(stream["name"], "▶ Assistir\n💾 720p")
        self.assertIn("💾 Local no A71", stream["description"])
        self.assertIn("🇧🇷 Dublado", stream["description"])
        self.assertIn("http://127.0.0.1:8000/offline/mytoken/tt1845307:5:1/", stream["url"])
        self.assertEqual(stream["behaviorHints"]["bingeGroup"], "comet|local|pt")

        # Se arquivo for removido do disco, get_local_entry retorna None
        os.unlink(video_file)
        self.assertIsNone(offline_library.get_local_entry("tt1845307:5:1", self.index_path))
        self.assertIsNone(offline_library.build_local_stream("tt1845307:5:1", "mytoken", "http://127.0.0.1:8000", self.index_path))

    # 6. handle_command
    def test_handle_command(self):
        # /baixar -> status
        res_status = asyncio.run(offline_library.handle_command("/baixar"))
        self.assertIsNotNone(res_status)
        self.assertIn("A71", res_status)

        # Prepara arquivo de teste para /baixados e /apagar
        test_file = os.path.join(self.offline_dir, "test.mp4")
        with open(test_file, "wb") as f:
            f.write(b"X" * 1024 * 1024)

        idx_data = {
            "items": {
                "tt1845307:5:1": {
                    "media_id": "tt1845307:5:1",
                    "media_type": "series",
                    "imdb": "tt1845307",
                    "title": "2 Broke Girls",
                    "season": 5,
                    "episode": 1,
                    "path": test_file,
                    "filename": "test.mp4",
                    "size": 1024 * 1024,
                    "height": 720,
                    "dubbed": True,
                }
            }
        }
        offline_library._save_index(idx_data, self.index_path)

        # /baixados
        res_list = asyncio.run(offline_library.handle_command("/baixados"))
        self.assertIsNotNone(res_list)
        self.assertIn("2 Broke Girls", res_list)
        self.assertIn("T5: 1", res_list)

        # /cancelar
        res_cancel = asyncio.run(offline_library.handle_command("/cancelar"))
        self.assertIsNotNone(res_cancel)
        self.assertIn("cancelados", res_cancel)
        self.assertTrue(offline_library._CANCEL_FLAG)

        # /apagar
        res_del = asyncio.run(offline_library.handle_command("/apagar 2 broke girls T5"))
        self.assertIsNotNone(res_del)
        self.assertIn("apagado", res_del)
        self.assertFalse(os.path.exists(test_file))

        reloaded = offline_library._load_index(self.index_path)
        self.assertNotIn("tt1845307:5:1", reloaded.get("items", {}))

    # 7. _ChunkedFileHasher bate exatamente com _hash_file_pieces
    def test_chunked_file_hasher(self):
        p = os.path.join(self.temp_dir.name, "sample.bin")
        data = os.urandom(1200 * 1024)  # > 2 peças de 512 KB
        w = offline_library._ChunkedFileHasher(p)
        for off in range(0, len(data), 100 * 1024):
            w.write_batch(data[off:off + 100 * 1024])
        total, pieces = w.finish()
        ref_total, ref_pieces = offline_library._hash_file_pieces(p)
        self.assertEqual(total, len(data))
        self.assertEqual(total, ref_total)
        self.assertEqual(pieces, ref_pieces)

    # 8. HLS timeout proporcional, timeout real, cancelamento e fallback para proc.kill()
    def test_hls_timeout_and_cancel(self):
        self.assertGreaterEqual(offline_library._hls_timeout_seconds("movie", None), 2400.0)
        self.assertGreaterEqual(offline_library._hls_timeout_seconds("movie", 180), 3240.0)
        self.assertGreaterEqual(offline_library._hls_timeout_seconds("series", 45), 900.0)

        class FakeProc:
            def __init__(self, ignore_terminate: bool = False):
                self.returncode = None
                self.terminated = False
                self.killed = False
                self.ignore_terminate = ignore_terminate
                self._ev = asyncio.Event()

            def terminate(self):
                self.terminated = True
                if not self.ignore_terminate:
                    self.returncode = -15
                    self._ev.set()

            def kill(self):
                self.killed = True
                self.returncode = -9
                self._ev.set()

            async def wait(self):
                await self._ev.wait()
                return self.returncode

        async def run_cancel():
            proc = FakeProc()
            offline_library._CANCEL_FLAG = True
            st = await offline_library._wait_hls_proc(proc, timeout_s=60.0, poll_s=0.02)
            return st, proc.terminated

        st, terminated = asyncio.run(run_cancel())
        self.assertEqual(st, "cancelled")
        self.assertTrue(terminated)
        offline_library._CANCEL_FLAG = False

        # Caso timeout real + processo que ignora SIGTERM e exige SIGKILL
        async def run_timeout_kill():
            proc = FakeProc(ignore_terminate=True)
            st_to = await offline_library._wait_hls_proc(proc, timeout_s=0.03, poll_s=0.01, term_wait_s=0.02)
            return st_to, proc.terminated, proc.killed

        st_to, term_to, killed_to = asyncio.run(run_timeout_kill())
        self.assertEqual(st_to, "timeout")
        self.assertTrue(term_to)
        self.assertTrue(killed_to)

    # 9. build_local_stream sem b64config não retorna URL quebrada com 'local'
    def test_build_local_stream_requires_valid_token(self):
        video_file = os.path.join(self.offline_dir, "movie.mp4")
        with open(video_file, "wb") as f:
            f.write(b"1" * 1024)
        offline_library._save_index(
            {"items": {"tt0133093": {"path": video_file, "filename": "movie.mp4", "size": 1024, "height": 1080, "dubbed": True}}},
            self.index_path,
        )
        with patch.object(offline_library, "_get_internal_token", return_value=None):
            self.assertIsNone(offline_library.build_local_stream("tt0133093", None, "https://x.test", self.index_path))
        with patch.object(offline_library, "_get_internal_token", return_value="real_tok"):
            st = offline_library.build_local_stream("tt0133093", None, "https://x.test", self.index_path)
            self.assertIsNotNone(st)
            self.assertIn("/offline/real_tok/tt0133093/movie.mp4", st["url"])

    # 10. _worker_loop para imediatamente em low_disk e separa falhas de não encontrados
    def test_worker_low_disk_and_failure_counts(self):
        sent = []

        async def fake_send(msg: str) -> bool:
            sent.append(msg)
            return True

        # Caso A: low_disk interrompe a fila imediatamente
        async def fake_dl_low(client, item):
            return {"status": "low_disk", "media_id": item["media_id"], "free_gb": 11.2}

        offline_library._JOB_QUEUE.append({
            "title": "Show LowDisk",
            "items": [
                {"media_id": "tt1:1:1", "season": 1, "episode": 1},
                {"media_id": "tt1:1:2", "season": 1, "episode": 2},
            ],
        })
        offline_library._JOB_QUEUE.append({"title": "Outro Job", "items": [{"media_id": "tt2"}]})
        with patch.object(offline_library, "_download_item", side_effect=fake_dl_low), \
             patch.object(offline_library, "send_telegram", side_effect=fake_send):
            asyncio.run(offline_library._worker_loop())

        self.assertEqual(len(offline_library._JOB_QUEUE), 0)
        self.assertTrue(any("pouco espaço livre" in m for m in sent))
        self.assertTrue(any("Outros pedidos removidos da fila (1): Outro Job" in m for m in sent))
        self.assertFalse(any("Não encontrados" in m for m in sent))

        # Caso B: separa falhas de não encontrados
        sent.clear()
        statuses = iter([
            {"status": "not_found", "media_id": "tt1:1:1"},
            {"status": "timeout", "media_id": "tt1:1:2"},
        ])

        async def fake_dl_mix(client, item):
            return next(statuses)

        offline_library._JOB_QUEUE.append({
            "title": "Show Mix",
            "items": [
                {"media_id": "tt1:1:1", "season": 1, "episode": 1},
                {"media_id": "tt1:1:2", "season": 1, "episode": 2},
            ],
        })
        with patch.object(offline_library, "_download_item", side_effect=fake_dl_mix), \
             patch.object(offline_library, "send_telegram", side_effect=fake_send), \
             patch.object(asyncio, "sleep", new_callable=AsyncMock):
            asyncio.run(offline_library._worker_loop())

        self.assertTrue(any("Não encontrados: 1" in m and "Falhas (erro/timeout/validação): 1" in m for m in sent))

    # 11. inject_local_into_streams insere o local em 1º e rebaixa '▶ Assistir' online sem sumir com ele
    def test_inject_local_into_streams(self):
        notice = {"name": "ℹ️ Aviso", "description": "Aviso sem URL"}
        online_best = {"name": "▶ Assistir\n1080p", "description": "TorBox · 1,2 GB", "url": "https://tb/1"}
        online_orig = {"name": "🎧 Original\n1080p", "description": "Inglês", "url": "https://tb/2"}
        local_st = {"name": "▶ Assistir\n💾 720p", "description": "Local no A71", "url": "https://a71/offline/1"}

        merged = offline_library.inject_local_into_streams([notice, online_best, online_orig], local_st)
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[0]["name"], "ℹ️ Aviso")
        self.assertEqual(merged[1]["name"], "▶ Assistir\n💾 720p")
        self.assertEqual(merged[1]["url"], "https://a71/offline/1")
        # A melhor fonte online continua na lista logo abaixo (índice 2), sem prefixo '▶ Assistir'
        self.assertEqual(merged[2]["url"], "https://tb/1")
        self.assertNotIn("Assistir", merged[2]["name"])
        self.assertIn("1080p", merged[2]["name"])
        self.assertEqual(merged[3]["url"], "https://tb/2")


if __name__ == "__main__":
    unittest.main()
