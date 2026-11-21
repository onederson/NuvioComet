"""Módulo de arquivamento direto no TorBox (direct_archive).

Monitora séries prioritárias (ex.: 2 Broke Girls), identifica episódios
faltantes ou em links diretos voláteis (FenixFlix / MegaEmbed), converte em
.torrent com BEP-19 WebSeed (url-list) e envia ao TorBox para cache permanente.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from pathlib import Path

# Logger seguro
try:
    from comet.core.logger import logger
except ImportError:
    logger = logging.getLogger("comet.services.direct_archive")
    if not logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(_h)
        logger.setLevel(logging.INFO)

# Settings do Comet
try:
    from comet.core.models import settings
except ImportError:
    settings = None

# Dependências HTTP e web opcionais
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    import orjson
except ImportError:
    orjson = None  # type: ignore

try:
    from fastapi import APIRouter
    # Router vazio mantido porque o main.py do Comet o inclui; o webseed não fica mais no Comet.
    router: APIRouter | None = APIRouter()
except ImportError:
    router = None

# 1. Constantes
STATE_PATH = "data/direct_archive.json"
DEFAULT_WATCHED = {"tt1845307": {"name": "2 Broke Girls"}}
PIECE_LENGTH = 512 * 1024
MIN_BYTES = 20 * 1024 * 1024
MAX_BYTES = 4 * 1024 * 1024 * 1024
KEEPALIVE_DAYS = 18
SWEEP_INTERVAL_S = 3 * 3600
FAIL_COOLDOWN_S = 2 * 3600
MAX_PER_SWEEP = 50
TORBOX_API = "https://api.torbox.app/v1/api"
USER_AGENT = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
PLAYER_UA = "ExoPlayerLib/2.19.1"
TRACKER_URL = "udp://tracker.opentrackr.org:1337/announce"

# Estado em memória
_FAIL_COOLDOWN: dict[str, float] = {}
_QUEUED: set[str] = set()
_QUEUE: list[tuple[str, list[tuple[str, str, bool]]]] = []
_QUEUE_WORKER_TASK: asyncio.Task | None = None
_WORK_LOCK: asyncio.Lock | None = None
_SWEEP_LOCK: asyncio.Lock | None = None
_ACTIVE_SEEDS: dict[str, tuple[str, float]] = {}
_TB_MAX_ACTIVE_CACHE: tuple[int, float] | None = None
_CF_TUNNEL_PROC: asyncio.subprocess.Process | None = None
_CF_TUNNEL_URL: str | None = None
_CF_TUNNEL_LAST_USE: float = 0.0
_CF_TUNNEL_LOCK: asyncio.Lock | None = None
_CF_IDLE_TASK: asyncio.Task | None = None
_CF_IDLE_TTL_S = 180.0
# O túnel cloudflared aponta só para este servidor mínimo (não para o Comet em :8000),
# então o endereço trycloudflare.com expõe apenas /archive-seed/<token>/<arquivo>.
SEED_HOST = "127.0.0.1"
SEED_PORT = int(os.environ.get("ARCHIVE_SEED_PORT", "8788"))
_SEED_SERVER: asyncio.AbstractServer | None = None
_SEED_CHUNK = 1024 * 1024
_SEED_MAX_CONN = 6  # conexões simultâneas no webseed; acima disso responde 503
_SEED_CONN_SEM: asyncio.Semaphore | None = None
_SEED_BOUND_PORT: int = SEED_PORT  # porta real aberta (SEED_PORT=0 deixa o sistema escolher)
_SEED_WRITE_STALL_S = 60.0  # cliente que não lê por 60 s é desconectado
_SEED_CONN_MAX_S = 1800.0  # nenhuma conexão dura mais que 30 min


def _get_work_lock() -> asyncio.Lock:
    global _WORK_LOCK
    if _WORK_LOCK is None:
        _WORK_LOCK = asyncio.Lock()
    return _WORK_LOCK


def _get_sweep_lock() -> asyncio.Lock:
    global _SWEEP_LOCK
    if _SWEEP_LOCK is None:
        _SWEEP_LOCK = asyncio.Lock()
    return _SWEEP_LOCK


def _load_state(path: str | None = None) -> dict:
    target_path = path or STATE_PATH
    state: dict = {}
    if os.path.isfile(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            logger.warning(f"direct_archive: erro ao carregar {target_path}: {e}")
            state = {}

    if not isinstance(state.get("watched"), dict):
        state["watched"] = {
            k: {**v, "added_at": int(time.time())} for k, v in DEFAULT_WATCHED.items()
        }
    if not isinstance(state.get("episodes"), dict):
        state["episodes"] = {}

    env_series = os.environ.get("DIRECT_ARCHIVE_SERIES", "")
    if env_series:
        for s_id in env_series.split(","):
            s_id = s_id.strip()
            if s_id and s_id not in state["watched"]:
                state["watched"][s_id] = {"name": s_id, "added_at": int(time.time())}

    return state


def _save_state(state: dict, path: str | None = None) -> None:
    target_path = path or STATE_PATH
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(p) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, str(p))


# 2. Funções puras
def bencode(obj) -> bytes:
    if isinstance(obj, bool):
        raise TypeError("bool not supported in bencode")
    if isinstance(obj, int):
        return f"i{obj}e".encode("ascii")
    if isinstance(obj, bytes):
        return f"{len(obj)}:".encode("ascii") + obj
    if isinstance(obj, str):
        raw = obj.encode("utf-8")
        return f"{len(raw)}:".encode("ascii") + raw
    if isinstance(obj, (list, tuple)):
        return b"l" + b"".join(bencode(item) for item in obj) + b"e"
    if isinstance(obj, dict):
        items = []
        for k, v in obj.items():
            if isinstance(k, str):
                kb = k.encode("utf-8")
            elif isinstance(k, bytes):
                kb = k
            else:
                raise TypeError(f"dict key must be str or bytes, got {type(k)}")
            items.append((kb, v))
        items.sort(key=lambda x: x[0])
        out = bytearray(b"d")
        for kb, v in items:
            out.extend(f"{len(kb)}:".encode("ascii") + kb)
            out.extend(bencode(v))
        out.extend(b"e")
        return bytes(out)
    raise TypeError(f"Type {type(obj)} not supported in bencode")


def build_torrent_bytes(
    filename: str,
    total_size: int,
    pieces: bytes,
    webseed_urls: list[str],
    piece_length: int = PIECE_LENGTH,
) -> tuple[bytes, str]:
    info = {
        "length": int(total_size),
        "name": filename,
        "piece length": int(piece_length),
        "pieces": pieces,
    }
    info_bencoded = bencode(info)
    info_hash = hashlib.sha1(info_bencoded).hexdigest().lower()
    meta = {
        "announce": TRACKER_URL,
        "info": info,
    }
    if webseed_urls:
        meta["url-list"] = list(webseed_urls)
    return bencode(meta), info_hash


def make_release_filename(series_name: str, season: int, episode: int, height: int) -> str:
    norm = unicodedata.normalize("NFKD", series_name or "").encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9]+", ".", norm).strip(".")
    if not safe:
        safe = "Series"
    res = "2160p" if height >= 1800 else "1080p" if height >= 900 else "720p" if height >= 650 else "480p"
    return f"{safe}.S{int(season):02d}E{int(episode):02d}.{res}.WEB-DL.Dublado.PT-BR.mp4"


def parse_series_ep(media_id: str) -> tuple[str, int, int] | None:
    if not isinstance(media_id, str):
        return None
    parts = media_id.split(":")
    if len(parts) != 3:
        return None
    imdb, s_str, e_str = parts
    if not imdb or not s_str.isdigit() or not e_str.isdigit():
        return None
    s = int(s_str)
    e = int(e_str)
    if s <= 0 or e <= 0:
        return None
    return (imdb, s, e)


def pick_archive_candidates(direct_streams: list[dict]) -> list[tuple[str, str, bool]]:
    if not direct_streams:
        return []

    scored_cands = []
    for s in direct_streams:
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue

        name = str(s.get("name") or "")
        desc = str(s.get("description") or "")
        bh = s.get("behaviorHints") if isinstance(s.get("behaviorHints"), dict) else {}
        filename = str(s.get("filename") or bh.get("filename") or "")

        name_lower = name.lower()
        desc_lower = desc.lower()
        filename_upper = filename.upper()

        if "outro corte" in name_lower or "outro corte" in desc_lower or filename_upper.endswith(".CUT"):
            continue

        is_pt = (s.get("_is_pt") is True) or ("🇧🇷" in name and "outro corte" not in name_lower)
        if not is_pt:
            continue

        if "fenixflix" in desc_lower:
            source = "FenixFlix"
        elif "megaembed" in desc_lower:
            source = "MegaEmbed"
        else:
            source = "Direct"

        is_hls = (".m3u8" in url.lower()) or ("(hls)" in desc_lower)

        combined = f"{name_lower} {desc_lower} {filename.lower()}"
        h = s.get("height")
        height = int(h) if isinstance(h, (int, float, str)) and str(h).isdigit() else 0

        if "2160p" in combined or "4k" in combined or height >= 1800:
            res_rank = 0
        elif "1080p" in combined or "fhd" in combined or height >= 900:
            res_rank = 1
        elif "720p" in combined or (height >= 650 and height < 900):
            res_rank = 2
        elif "480p" in combined or "sd" in combined or (0 < height < 650):
            res_rank = 4
        else:
            res_rank = 3

        hls_rank = 0 if not is_hls else 1
        src_rank = 0 if source == "FenixFlix" else (1 if source == "MegaEmbed" else 2)
        sort_key = (hls_rank, src_rank, res_rank)

        scored_cands.append((sort_key, (url, source, is_hls)))

    scored_cands.sort(key=lambda x: x[0])

    results = []
    seen_urls = set()
    for _, cand in scored_cands:
        cand_url = cand[0]
        if cand_url not in seen_urls:
            seen_urls.add(cand_url)
            results.append(cand)

    return results


def is_valid_probe(probe: dict, expected_runtime_min: int | None) -> tuple[bool, str]:
    if not isinstance(probe, dict):
        return False, "probe is not a dict"
    width = int(probe.get("width") or 0)
    height = int(probe.get("height") or 0)
    has_audio = bool(probe.get("has_audio"))
    duration_s = float(probe.get("duration_s") or 0.0)

    if width <= 0 or height <= 0:
        return False, "invalid dimensions"
    if not has_audio:
        return False, "no audio"
    if duration_s < 300:
        return False, "duration too short"

    if expected_runtime_min is not None and expected_runtime_min > 0:
        dur_min = duration_s / 60.0
        if expected_runtime_min >= 50 and dur_min < expected_runtime_min * 0.6:
            return False, "duration too short for expected runtime"
        if dur_min >= 50 and dur_min > expected_runtime_min * 1.7:
            return False, "duration too long for expected runtime"
        if expected_runtime_min >= 15 and dur_min < expected_runtime_min * 0.55:
            return False, "duration too short for expected runtime"

    return True, "ok"


# 3. WebSeed temporário: servidor HTTP mínimo em 127.0.0.1:SEED_PORT, exposto só pelo túnel
_SEED_PATH_RE = re.compile(r"^/archive-seed/([A-Za-z0-9_-]{16,64})/([^/?#]+)$")


def _resolve_seed(token: str) -> str | None:
    now = time.time()
    for t in [t for t, (_, exp) in _ACTIVE_SEEDS.items() if now > exp]:
        _ACTIVE_SEEDS.pop(t, None)
    entry = _ACTIVE_SEEDS.get(token)
    if not entry:
        return None
    filepath, exp = entry
    if now > exp or not os.path.isfile(filepath):
        _ACTIVE_SEEDS.pop(token, None)
        return None
    return filepath


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    """Aceita um único intervalo `bytes=a-b`, `bytes=a-` ou `bytes=-n`. None = inválido."""
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", value or "")
    if not m or (not m.group(1) and not m.group(2)) or size <= 0:
        return None
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        n = int(m.group(2))
        if n <= 0:
            return None
        start, end = max(0, size - n), size - 1
    end = min(end, size - 1)
    if start > end:
        return None
    return start, end


async def _seed_send_status(writer: asyncio.StreamWriter, code: int, reason: str, extra: str = "") -> None:
    writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: 0\r\n{extra}Connection: close\r\n\r\n".encode())
    await _seed_drain(writer)


async def _seed_drain(writer: asyncio.StreamWriter) -> None:
    await asyncio.wait_for(writer.drain(), timeout=_SEED_WRITE_STALL_S)


_RANGE_SINGLE_RE = re.compile(r"\s*bytes=\d*-\d*\s*")


async def _seed_handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    sem = _SEED_CONN_SEM
    if sem is not None and sem.locked():
        try:
            await _seed_send_status(writer, 503, "Service Unavailable", "Retry-After: 5\r\n")
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
        return
    if sem is None:
        await _seed_serve(reader, writer)
        return
    async with sem:
        try:
            await asyncio.wait_for(_seed_serve(reader, writer), timeout=_SEED_CONN_MAX_S)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                writer.close()
            except Exception:
                pass


async def _seed_serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global _CF_TUNNEL_LAST_USE
    fh = None
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15.0)
        except (asyncio.TimeoutError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return
        lines = head.decode("latin-1", errors="ignore").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            await _seed_send_status(writer, 400, "Bad Request")
            return
        method, target, _ver = parts
        if method not in ("GET", "HEAD"):
            await _seed_send_status(writer, 405, "Method Not Allowed", "Allow: GET, HEAD\r\n")
            return
        m = _SEED_PATH_RE.match(target.split("?", 1)[0])
        filepath = _resolve_seed(m.group(1)) if m else None
        if not filepath:
            await _seed_send_status(writer, 404, "Not Found")
            return
        _CF_TUNNEL_LAST_USE = time.time()
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        size = os.path.getsize(filepath)
        start, end, code, reason = 0, size - 1, 200, "OK"
        extra = ""
        range_h = headers.get("range", "")
        # Vários intervalos ou Range malformado: a RFC 9110 permite ignorar e mandar o arquivo inteiro.
        if range_h and _RANGE_SINGLE_RE.fullmatch(range_h) and range_h.strip() != "bytes=-":
            rng = _parse_range(range_h, size)
            if rng is None:
                await _seed_send_status(writer, 416, "Range Not Satisfiable", f"Content-Range: bytes */{size}\r\n")
                return
            start, end = rng
            code, reason = 206, "Partial Content"
            extra = f"Content-Range: bytes {start}-{end}/{size}\r\n"
        length = max(0, end - start + 1)
        writer.write(
            (
                f"HTTP/1.1 {code} {reason}\r\n"
                "Content-Type: video/mp4\r\n"
                f"Content-Length: {length}\r\n"
                "Accept-Ranges: bytes\r\n"
                f"{extra}"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        await _seed_drain(writer)
        logger.debug(f"direct_archive: webseed {method} range={range_h or '-'} -> {code} ({length} bytes)")
        if method == "HEAD" or length == 0:
            return
        fh = await asyncio.to_thread(open, filepath, "rb")
        await asyncio.to_thread(fh.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await asyncio.to_thread(fh.read, min(_SEED_CHUNK, remaining))
            if not chunk:
                break
            writer.write(chunk)
            await _seed_drain(writer)
            remaining -= len(chunk)
            _CF_TUNNEL_LAST_USE = time.time()
    except (ConnectionError, asyncio.TimeoutError, TimeoutError):
        pass
    except Exception as e:
        logger.debug(f"direct_archive: erro no webseed: {e}")
    finally:
        if fh is not None:
            try:
                await asyncio.to_thread(fh.close)
            except Exception:
                pass
        try:
            writer.close()
        except Exception:
            pass


async def _ensure_seed_server() -> bool:
    global _SEED_SERVER, _SEED_CONN_SEM, _SEED_BOUND_PORT
    if _SEED_SERVER is not None and _SEED_SERVER.is_serving():
        return True
    try:
        _SEED_CONN_SEM = asyncio.Semaphore(_SEED_MAX_CONN)
        _SEED_SERVER = await asyncio.start_server(_seed_handle, SEED_HOST, SEED_PORT)
        _SEED_BOUND_PORT = int(_SEED_SERVER.sockets[0].getsockname()[1])
        logger.info(f"direct_archive: webseed local em http://{SEED_HOST}:{_SEED_BOUND_PORT}")
        return True
    except Exception as e:
        _SEED_SERVER = None
        logger.warning(f"direct_archive: falha ao abrir webseed em {SEED_HOST}:{SEED_PORT}: {e}")
        return False


async def _stop_seed_server() -> None:
    global _SEED_SERVER
    srv = _SEED_SERVER
    _SEED_SERVER = None
    if srv is not None:
        try:
            srv.close()
            await asyncio.wait_for(srv.wait_closed(), timeout=5.0)
        except Exception:
            pass


def _register_seed(filepath: str, filename: str, ttl: float = 600.0, base_url: str | None = None) -> tuple[str, str] | None:
    base = base_url or getattr(settings, "PUBLIC_BASE_URL", None) or os.environ.get("PUBLIC_BASE_URL", "")
    if not base:
        return None
    tok = secrets.token_urlsafe(24)
    _ACTIVE_SEEDS[tok] = (filepath, time.time() + ttl)
    url = f"{base.rstrip('/')}/archive-seed/{tok}/{filename}"
    return (tok, url)


def _get_cf_lock() -> asyncio.Lock:
    global _CF_TUNNEL_LOCK
    if _CF_TUNNEL_LOCK is None:
        _CF_TUNNEL_LOCK = asyncio.Lock()
    return _CF_TUNNEL_LOCK


async def _stop_cf_tunnel() -> None:
    global _CF_TUNNEL_PROC, _CF_TUNNEL_URL
    proc = _CF_TUNNEL_PROC
    _CF_TUNNEL_PROC = None
    _CF_TUNNEL_URL = None
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    await _stop_seed_server()


async def _cf_idle_watcher() -> None:
    while True:
        await asyncio.sleep(30.0)
        if _CF_TUNNEL_PROC is None:
            return
        if time.time() - _CF_TUNNEL_LAST_USE >= _CF_IDLE_TTL_S and not _ACTIVE_SEEDS:
            async with _get_cf_lock():
                if time.time() - _CF_TUNNEL_LAST_USE >= _CF_IDLE_TTL_S and not _ACTIVE_SEEDS:
                    logger.info("direct_archive: encerrando túnel cloudflared ocioso")
                    await _stop_cf_tunnel()
                    return


async def _ensure_cf_tunnel_url() -> str | None:
    global _CF_TUNNEL_PROC, _CF_TUNNEL_URL, _CF_TUNNEL_LAST_USE, _CF_IDLE_TASK
    cf_bin = "/usr/local/bin/cloudflared"
    if not os.path.isfile(cf_bin):
        import shutil
        cf_bin = shutil.which("cloudflared") or ""
    if not cf_bin:
        return None

    async with _get_cf_lock():
        _CF_TUNNEL_LAST_USE = time.time()
        if _CF_TUNNEL_PROC is not None and _CF_TUNNEL_PROC.returncode is None and _CF_TUNNEL_URL:
            return _CF_TUNNEL_URL
        await _stop_cf_tunnel()
        if not await _ensure_seed_server():
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                cf_bin, "tunnel", "--no-autoupdate", "--url", f"http://{SEED_HOST}:{_SEED_BOUND_PORT}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as e:
            logger.warning(f"direct_archive: falha ao iniciar cloudflared: {e}")
            await _stop_seed_server()
            return None

        url: str | None = None
        deadline = time.time() + 25.0
        while time.time() < deadline and proc.stdout is not None:
            try:
                line_b = await asyncio.wait_for(proc.stdout.readline(), timeout=max(1.0, deadline - time.time()))
            except (asyncio.TimeoutError, TimeoutError):
                break
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="ignore")
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                break

        if not url:
            try:
                proc.terminate()
            except Exception:
                pass
            logger.warning("direct_archive: cloudflared não retornou URL trycloudflare em 25s")
            await _stop_seed_server()
            return None

        _CF_TUNNEL_PROC = proc
        _CF_TUNNEL_URL = url
        _CF_TUNNEL_LAST_USE = time.time()
        await asyncio.sleep(3.0)
        logger.info(f"direct_archive: túnel cloudflared pronto ({url})")
        try:
            loop = asyncio.get_running_loop()
            if _CF_IDLE_TASK is None or _CF_IDLE_TASK.done():
                _CF_IDLE_TASK = loop.create_task(_cf_idle_watcher())
        except RuntimeError:
            pass
        return url


def _hash_file_pieces(filepath: str, piece_length: int = PIECE_LENGTH) -> tuple[int, bytes]:
    total = 0
    pieces = bytearray()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(piece_length)
            if not chunk:
                break
            total += len(chunk)
            pieces.extend(hashlib.sha1(chunk).digest())
    return total, bytes(pieces)


class _FilePieceWriter:
    """Grava bytes em disco e calcula hashes SHA-1 por peça fora do event loop."""

    def __init__(self, path: str, piece_length: int = PIECE_LENGTH, max_bytes: int | None = None):
        self.path = path
        self.piece_length = piece_length
        self.max_bytes = max_bytes
        self.total = 0
        self._pieces = bytearray()
        self._buf = bytearray()
        self._fh = open(path, "wb")

    def write_batch(self, data: bytes) -> int:
        if not data:
            return self.total
        self._fh.write(data)
        self.total += len(data)
        if self.max_bytes is not None and self.total > self.max_bytes:
            raise ValueError(f"File exceeds MAX_BYTES ({self.max_bytes})")
        self._buf.extend(data)
        pl = self.piece_length
        while len(self._buf) >= pl:
            self._pieces.extend(hashlib.sha1(memoryview(self._buf)[:pl]).digest())
            del self._buf[:pl]
        return self.total

    def finish(self) -> tuple[int, bytes]:
        try:
            if self._buf:
                self._pieces.extend(hashlib.sha1(self._buf).digest())
                self._buf.clear()
        finally:
            self.close()
        return self.total, bytes(self._pieces)

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()


# 4. Download, ffprobe e TorBox
async def _download_and_hash(url: str, dest: str, is_hls: bool) -> tuple[int, bytes]:
    if not is_hls:
        if httpx is None:
            raise RuntimeError("httpx is required for HTTP download")
        writer = _FilePieceWriter(dest, PIECE_LENGTH, max_bytes=MAX_BYTES)
        pending = bytearray()
        timeout = httpx.Timeout(20.0, read=60.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as client:
                async with client.stream("GET", url, headers={"User-Agent": PLAYER_UA}) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if not chunk:
                            continue
                        pending.extend(chunk)
                        if len(pending) >= 1024 * 1024:
                            batch = bytes(pending)
                            pending.clear()
                            await asyncio.to_thread(writer.write_batch, batch)
            if pending:
                batch = bytes(pending)
                pending.clear()
                await asyncio.to_thread(writer.write_batch, batch)
            total, pieces = await asyncio.to_thread(writer.finish)
        finally:
            await asyncio.to_thread(writer.close)
        if total < MIN_BYTES or total > MAX_BYTES:
            raise ValueError(f"Total size {total} not in [{MIN_BYTES}, {MAX_BYTES}]")
        return total, pieces
    else:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-user_agent", PLAYER_UA,
            "-i", url, "-c", "copy", "-movflags", "+faststart", dest,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=240.0)
            except (asyncio.TimeoutError, TimeoutError):
                proc.kill()
                raise TimeoutError("ffmpeg remux timed out after 240s")
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with code {proc.returncode}: {stderr.decode('utf-8', errors='ignore')}")

        total, pieces = await asyncio.to_thread(_hash_file_pieces, dest, PIECE_LENGTH)
        if total < MIN_BYTES or total > MAX_BYTES:
            raise ValueError(f"Total size {total} not in [{MIN_BYTES}, {MAX_BYTES}]")
        return total, pieces


async def _ffprobe_url_height(url: str) -> int:
    """Sonda apenas a altura do vídeo direto na URL (sem baixar o arquivo inteiro)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-user_agent", PLAYER_UA,
            "-select_streams", "v:0", "-show_entries", "stream=height",
            "-of", "json", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception:
        return 0
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
    except (asyncio.TimeoutError, TimeoutError):
        try:
            proc.kill()
        except Exception:
            pass
        return 0
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore") or "{}")
        for st in data.get("streams") or []:
            if st.get("height"):
                return int(st["height"])
    except Exception:
        pass
    return 0


async def _ffprobe_file(path: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        raise TimeoutError("ffprobe timed out after 30s")

    data = json.loads(stdout.decode("utf-8", errors="ignore") or "{}")
    streams = data.get("streams", [])
    width = 0
    height = 0
    has_audio = False
    for st in streams:
        ctype = st.get("codec_type")
        if ctype == "video":
            if not width and st.get("width"):
                width = int(st["width"])
            if not height and st.get("height"):
                height = int(st["height"])
        elif ctype == "audio":
            has_audio = True

    duration_s = 0.0
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            duration_s = float(fmt["duration"])
        except (ValueError, TypeError):
            duration_s = 0.0

    return {
        "width": width,
        "height": height,
        "duration_s": duration_s,
        "has_audio": has_audio,
    }


def _torbox_key() -> str:
    key = getattr(settings, "TORBOX_API_KEY", None)
    if key:
        return str(key)
    return os.environ.get("TORBOX_API_KEY", "")


def _torbox_headers(tb_key: str) -> dict:
    return {
        "Authorization": f"Bearer {tb_key}",
        "User-Agent": USER_AGENT,
    }


async def _torbox_max_active(client: httpx.AsyncClient, tb_key: str) -> int:
    global _TB_MAX_ACTIVE_CACHE
    now = time.time()
    if _TB_MAX_ACTIVE_CACHE and now - _TB_MAX_ACTIVE_CACHE[1] < 3600:
        return _TB_MAX_ACTIVE_CACHE[0]
    plan_limits = {0: 1, 1: 3, 2: 10, 3: 5}
    limit = 3
    try:
        resp = await client.get(f"{TORBOX_API}/user/me", headers=_torbox_headers(tb_key), timeout=15.0)
        resp.raise_for_status()
        plan = int((resp.json().get("data") or {}).get("plan") or 1)
        limit = plan_limits.get(plan, 3)
    except Exception:
        pass
    _TB_MAX_ACTIVE_CACHE = (limit, now)
    return limit


async def _torbox_active_count(client: httpx.AsyncClient, tb_key: str) -> int:
    try:
        resp = await client.get(
            f"{TORBOX_API}/torrents/mylist?bypass_cache=true",
            headers=_torbox_headers(tb_key),
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not isinstance(data, list):
            return 0
        return sum(1 for item in data if isinstance(item, dict) and bool(item.get("active")))
    except Exception as e:
        logger.warning(f"direct_archive: erro ao contar torrents ativos no TorBox: {e}")
        return 0


async def _torbox_submit_and_wait(
    client: httpx.AsyncClient,
    tb_key: str,
    fname: str,
    total_size: int,
    pieces: bytes,
    webseed_url: str,
    poll_timeout: float = 110.0,
) -> dict | None:
    tbytes, info_hash = build_torrent_bytes(fname, total_size, pieces, [webseed_url])
    files = {"file": (fname + ".torrent", tbytes, "application/x-bittorrent")}
    form = {"seed": "3", "allow_zip": "false"}
    try:
        resp = await client.post(
            f"{TORBOX_API}/torrents/createtorrent",
            headers=_torbox_headers(tb_key),
            data=form,
            files=files,
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data")
        tid = None
        if isinstance(data, dict):
            tid = data.get("torrent_id") or data.get("id")
            if not tid and data.get("queued_id"):
                qid = data.get("queued_id")
                logger.info(f"direct_archive: TorBox enfileirou ({qid}) por limite de slots, limpando fila")
                try:
                    await client.post(
                        f"{TORBOX_API}/queued/controlqueued",
                        headers=_torbox_headers(tb_key),
                        json={"queued_id": qid, "operation": "delete"},
                        timeout=15.0,
                    )
                except Exception:
                    pass
                return None
        elif isinstance(data, (int, str)):
            tid = data
        if not tid:
            logger.warning(f"direct_archive: TorBox createtorrent sem torrent_id: {body}")
            return None
    except Exception as e:
        logger.warning(f"direct_archive: falha ao enviar torrent ao TorBox: {e}")
        return None

    start_time = time.time()
    while time.time() - start_time < poll_timeout:
        await asyncio.sleep(4)
        try:
            poll_resp = await client.get(
                f"{TORBOX_API}/torrents/mylist?id={tid}&bypass_cache=true",
                headers=_torbox_headers(tb_key),
                timeout=15.0,
            )
            poll_resp.raise_for_status()
            pdata = poll_resp.json().get("data")
            if isinstance(pdata, list) and pdata:
                item = pdata[0]
            elif isinstance(pdata, dict):
                item = pdata
            else:
                item = {}

            dl_present = bool(item.get("download_present"))
            cached = bool(item.get("cached"))
            progress = float(item.get("progress") or 0.0)
            dl_state = str(item.get("download_state") or "").lower()

            if dl_present and (cached or progress >= 1.0 or dl_state in ("completed", "cached", "seeding")):
                if bool(item.get("active")):
                    try:
                        await client.post(
                            f"{TORBOX_API}/torrents/controltorrent",
                            headers=_torbox_headers(tb_key),
                            json={"torrent_id": tid, "operation": "stop_seeding"},
                            timeout=15.0,
                        )
                    except Exception:
                        pass
                return {"info_hash": info_hash, "torrent_id": tid, "size": total_size}

            elapsed = time.time() - start_time
            if elapsed >= 25.0:
                if progress <= 0.0 or any(w in dl_state for w in ("error", "failed", "stall")):
                    logger.warning(f"direct_archive: torrent {tid} travado/falhou no TorBox ({dl_state}, progress={progress})")
                    try:
                        await client.post(
                            f"{TORBOX_API}/torrents/controltorrent",
                            headers=_torbox_headers(tb_key),
                            json={"torrent_id": tid, "operation": "delete"},
                            timeout=15.0,
                        )
                    except Exception:
                        pass
                    return None
        except Exception as e:
            logger.debug(f"direct_archive: polling TorBox {tid} falhou: {e}")

    logger.warning(f"direct_archive: timeout ({poll_timeout}s) aguardando TorBox {tid}")
    try:
        await client.post(
            f"{TORBOX_API}/torrents/controltorrent",
            headers=_torbox_headers(tb_key),
            json={"torrent_id": tid, "operation": "delete"},
            timeout=15.0,
        )
    except Exception:
        pass
    return None


def make_movie_filename(title_name: str, height: int, dubbed: bool = True) -> str:
    norm = unicodedata.normalize("NFKD", title_name or "").encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9]+", ".", norm).strip(".") or "Movie"
    res = "2160p" if height >= 1800 else "1080p" if height >= 900 else "720p" if height >= 650 else "480p"
    tag = "Dublado.PT-BR" if dubbed else "Original"
    return f"{safe}.{res}.WEB-DL.{tag}.mp4"


async def _submit_file_to_torbox(
    client: httpx.AsyncClient,
    tb_key: str,
    filepath: str,
    fname: str,
    total: int,
    pieces: bytes,
    direct_url: str | None = None,
    source: str = "Direct",
) -> dict | None:
    tb_res = None
    # Só tenta direct_url no TorBox se for FenixFlix (MegaEmbed bloqueia IP de fora do Brasil)
    if direct_url and source == "FenixFlix":
        tb_res = await _torbox_submit_and_wait(client, tb_key, fname, total, pieces, direct_url, poll_timeout=120.0)

    if not tb_res:
        cf_url = await _ensure_cf_tunnel_url()
        if cf_url:
            reg = _register_seed(filepath, fname, ttl=900.0, base_url=cf_url)
            if reg:
                reg_token, seed_url = reg
                try:
                    # Timeout maior para uploads maiores pelo link de casa
                    timeout_s = max(180.0, min(900.0, (total / (1024 * 1024)) * 1.2))
                    tb_res = await _torbox_submit_and_wait(
                        client, tb_key, fname, total, pieces, seed_url, poll_timeout=timeout_s
                    )
                finally:
                    _ACTIVE_SEEDS.pop(reg_token, None)

    return tb_res


async def upload_local_file_to_torbox(
    filepath: str,
    media_id: str,
    title_name: str,
    probe: dict,
    direct_url: str | None = None,
    source: str = "Local",
    pieces: bytes | None = None,
    total_size: int | None = None,
    dubbed: bool = True,
) -> dict | None:
    """Sobe um arquivo local já baixado no A71 para o cache do TorBox e sincroniza no SQLite."""
    if not os.path.isfile(filepath):
        return None
    tb_key = _torbox_key()
    if not tb_key or httpx is None:
        return None

    lock = _get_work_lock()
    async with lock:
        if pieces is None or total_size is None:
            total_size, pieces = await asyncio.to_thread(_hash_file_pieces, filepath)

        parsed_ep = parse_series_ep(media_id)
        height = int(probe.get("height") or 720)
        if parsed_ep:
            _, s, e = parsed_ep
            fname = make_release_filename(title_name, s, e, height)
            if not dubbed:
                fname = fname.replace("Dublado.PT-BR", "Original")
        else:
            fname = make_movie_filename(title_name, height, dubbed=dubbed)

        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".mkv", ".mp4") and not fname.lower().endswith(ext):
            fname = os.path.splitext(fname)[0] + ext

        async with httpx.AsyncClient(timeout=30.0) as client:
            active_cnt = await _torbox_active_count(client, tb_key)
            max_active = await _torbox_max_active(client, tb_key)
            if active_cnt >= max_active:
                logger.info(f"direct_archive: {active_cnt}/{max_active} slots ativos no TorBox, pulando upload de {media_id}")
                return None

            tb_res = await _submit_file_to_torbox(
                client, tb_key, filepath, fname, total_size, pieces, direct_url=direct_url, source=source
            )
            if not tb_res:
                return None

            now = int(time.time())
            rec = {
                "info_hash": tb_res["info_hash"],
                "torrent_id": tb_res["torrent_id"],
                "title": fname,
                "size": total_size,
                "width": int(probe.get("width") or 0),
                "height": height,
                "duration_s": float(probe.get("duration_s") or 0.0),
                "source": source,
                "created_at": now,
                "last_keepalive_at": now,
            }
            cur_state = _load_state()
            cur_state.setdefault("episodes", {})[media_id] = rec
            _save_state(cur_state)
            await sync_episode_to_db(media_id, rec)
            logger.info(f"📦 Arquivado no TorBox: {fname} ({rec['info_hash'][:10]})")
            return rec


UPGRADE_COOLDOWN_S = 7 * 86400


def _is_ep_satisfied(ep_rec: dict | None, now: float | None = None) -> bool:
    if not ep_rec or not isinstance(ep_rec, dict):
        return False
    if int(ep_rec.get("height") or 0) >= 650:
        return True
    cur_now = time.time() if now is None else now
    last_try = int(ep_rec.get("last_upgrade_try_at") or ep_rec.get("created_at") or 0)
    return (cur_now - last_try) < UPGRADE_COOLDOWN_S


async def archive_episode(media_id: str, candidates: list[tuple[str, str, bool]]) -> dict | None:
    lock = _get_work_lock()
    async with lock:
        parsed_ep = parse_series_ep(media_id)
        if not parsed_ep:
            return None
        imdb, s, e = parsed_ep

        state = _load_state()
        ep_rec = state.get("episodes", {}).get(media_id)
        if _is_ep_satisfied(ep_rec):
            return ep_rec

        tb_key = _torbox_key()
        if not tb_key:
            logger.warning("direct_archive: TORBOX_API_KEY não configurada")
            return None

        if httpx is None:
            logger.warning("direct_archive: httpx não disponível")
            return None

        async with httpx.AsyncClient(timeout=30.0) as client:
            active_cnt = await _torbox_active_count(client, tb_key)
            max_active = await _torbox_max_active(client, tb_key)
            if active_cnt >= max_active:
                logger.info(f"direct_archive: {active_cnt}/{max_active} slots ativos no TorBox, postergando {media_id}")
                return None

            series_name = state.get("watched", {}).get(imdb, {}).get("name") or "Series"

            for cand_url, source, is_hls in candidates[:2]:
                if ep_rec:
                    url_h = await _ffprobe_url_height(cand_url)
                    if 0 < url_h < 650:
                        logger.info(
                            f"direct_archive: {media_id} URL sonda {url_h}p (<650p), pulando download de upgrade"
                        )
                        continue
                os.makedirs("/tmp", exist_ok=True)
                tmp_file = os.path.join("/tmp", f"comet_arch_{imdb}_{s}_{e}_{secrets.token_hex(4)}.mp4")
                try:
                    total, pieces = await _download_and_hash(cand_url, tmp_file, is_hls)
                    probe = await _ffprobe_file(tmp_file)

                    exp_rt = None
                    try:
                        from comet.services.direct_addons import _tmdb_episode_runtime
                        exp_rt = await asyncio.wait_for(_tmdb_episode_runtime(imdb, s, e), 3.0)
                    except Exception:
                        exp_rt = None

                    valid, reason = is_valid_probe(probe, exp_rt)
                    if not valid:
                        logger.warning(f"direct_archive: probe inválido para {media_id} ({reason})")
                        continue

                    probe_h = int(probe.get("height") or 0)
                    if ep_rec and probe_h < 650:
                        logger.info(
                            f"direct_archive: {media_id} candidato baixado em {probe_h}p (<650p), tentando próximo"
                        )
                        continue

                    fname = make_release_filename(series_name, s, e, probe["height"])
                    tb_res = await _submit_file_to_torbox(
                        client,
                        tb_key,
                        tmp_file,
                        fname,
                        total,
                        pieces,
                        direct_url=(cand_url if not is_hls else None),
                        source=source,
                    )

                    if tb_res:
                        old_tid = ep_rec.get("torrent_id") if isinstance(ep_rec, dict) else None
                        old_hash = str(ep_rec.get("info_hash") or "").lower() if isinstance(ep_rec, dict) else ""
                        if old_tid and old_tid != tb_res["torrent_id"]:
                            try:
                                del_resp = await client.post(
                                    f"{TORBOX_API}/torrents/controltorrent",
                                    headers=_torbox_headers(tb_key),
                                    json={"torrent_id": old_tid, "operation": "delete"},
                                    timeout=15.0,
                                )
                                del_ok = del_resp.status_code == 200 and bool((del_resp.json() or {}).get("success"))
                                if del_ok:
                                    logger.info(
                                        f"direct_archive: torrent 480p antigo ({old_tid}) removido do TorBox após upgrade"
                                    )
                                else:
                                    logger.warning(
                                        f"direct_archive: TorBox recusou remover torrent antigo ({old_tid}): {del_resp.status_code}"
                                    )
                            except Exception as del_err:
                                logger.debug(f"direct_archive: falha ao remover torrent antigo {old_tid}: {del_err}")
                        if old_hash and old_hash != str(tb_res["info_hash"]).lower():
                            try:
                                await remove_hash_from_db(old_hash)
                            except Exception as db_del_err:
                                logger.debug(f"direct_archive: falha ao limpar hash antigo {old_hash} do DB: {db_del_err}")
                        now = int(time.time())
                        rec = {
                            "info_hash": tb_res["info_hash"],
                            "torrent_id": tb_res["torrent_id"],
                            "title": fname,
                            "size": total,
                            "width": probe["width"],
                            "height": probe["height"],
                            "duration_s": probe["duration_s"],
                            "source": source,
                            "created_at": now,
                            "last_keepalive_at": now,
                            "last_upgrade_try_at": now,
                        }
                        cur_state = _load_state()
                        cur_state.setdefault("episodes", {})[media_id] = rec
                        _save_state(cur_state)
                        await sync_episode_to_db(media_id, rec)
                        logger.info(f"📦 Arquivado no TorBox: {fname} ({rec['info_hash'][:10]})")
                        return rec
                except Exception as exc:
                    logger.warning(f"direct_archive: erro ao tentar candidato {cand_url}: {exc}")
                finally:
                    if os.path.exists(tmp_file):
                        try:
                            os.remove(tmp_file)
                        except OSError:
                            pass

        if ep_rec:
            now_ts = int(time.time())
            cur_state = _load_state()
            cur_ep = cur_state.setdefault("episodes", {}).get(media_id) or dict(ep_rec)
            cur_ep["last_upgrade_try_at"] = now_ts
            cur_state["episodes"][media_id] = cur_ep
            _save_state(cur_state)
            return None

        _FAIL_COOLDOWN[media_id] = time.time()
        return None


# 5. Sincronia com o banco SQLite do Comet
async def remove_hash_from_db(info_hash: str) -> None:
    h = str(info_hash or "").strip().lower()
    if not h:
        return
    try:
        from comet.core.database import database
    except ImportError:
        database = None
    if database is not None:
        await database.execute("DELETE FROM torrents WHERE info_hash = :info_hash", {"info_hash": h})
        await database.execute(
            "DELETE FROM debrid_availability WHERE debrid_service = 'torbox' AND info_hash = :info_hash",
            {"info_hash": h},
        )


async def sync_episode_to_db(media_id: str, rec: dict) -> None:
    parsed_ep = parse_series_ep(media_id)
    if parsed_ep:
        imdb, season, episode = parsed_ep
        season_norm = season
        episode_norm = episode
    elif isinstance(media_id, str) and media_id.startswith("tt") and ":" not in media_id:
        imdb = media_id
        season = None
        episode = None
        season_norm = -1
        episode_norm = -1
    else:
        return

    info_hash = str(rec.get("info_hash") or "").lower()
    title = str(rec.get("title") or "")
    size = int(rec.get("size") or 0)
    source = str(rec.get("source") or "Direct")
    tracker = f"DirectArchive|{source}"
    now = int(time.time())

    parsed = None
    try:
        from RTN import parse
        parsed = parse(title)
    except Exception:
        pass

    if parsed is not None:
        try:
            from comet.services.filtering import apply_ptbr_detection
            apply_ptbr_detection(parsed, title, tracker)
        except Exception:
            pass
        try:
            from comet.utils.parsing import ensure_multi_language
            ensure_multi_language(parsed)
        except Exception:
            pass

    parsed_json = "{}"
    if parsed is not None:
        try:
            if orjson is not None and hasattr(parsed, "model_dump"):
                parsed_json = orjson.dumps(parsed.model_dump()).decode("utf-8")
            elif hasattr(parsed, "model_dump"):
                parsed_json = json.dumps(parsed.model_dump(), default=str)
            elif hasattr(parsed, "__dict__"):
                parsed_json = json.dumps(parsed.__dict__, default=str)
            else:
                parsed_json = json.dumps(parsed, default=str)
        except Exception:
            parsed_json = "{}"

    try:
        from comet.core.database import database
    except ImportError:
        database = None

    if database is not None:
        torrent_sql = """
            INSERT OR REPLACE INTO torrents (
                media_id, info_hash, season, episode, season_norm, episode_norm,
                file_index, title, seeders, size, tracker, sources_json, parsed_json, updated_at
            ) VALUES (
                :media_id, :info_hash, :season, :episode, :season_norm, :episode_norm,
                :file_index, :title, :seeders, :size, :tracker, :sources_json, :parsed_json, :updated_at
            )
        """
        torrent_params = {
            "media_id": imdb,
            "info_hash": info_hash,
            "season": season,
            "episode": episode,
            "season_norm": season_norm,
            "episode_norm": episode_norm,
            "file_index": 0,
            "title": title,
            "seeders": 1,
            "size": size,
            "tracker": tracker,
            "sources_json": "[]",
            "parsed_json": parsed_json,
            "updated_at": now,
        }
        await database.execute(torrent_sql, torrent_params)

        debrid_sql = """
            INSERT OR REPLACE INTO debrid_availability (
                debrid_service, info_hash, season, episode, season_norm, episode_norm,
                file_index, title, size, parsed_json, updated_at
            ) VALUES (
                'torbox', :info_hash, :season, :episode, :season_norm, :episode_norm,
                '0', :title, :size, :parsed_json, :updated_at
            )
        """
        debrid_params = {
            "info_hash": info_hash,
            "season": season,
            "episode": episode,
            "season_norm": season_norm,
            "episode_norm": episode_norm,
            "title": title,
            "size": size,
            "parsed_json": parsed_json,
            "updated_at": now,
        }
        await database.execute(debrid_sql, debrid_params)


async def sync_all_to_db() -> None:
    state = _load_state()
    for media_id, rec in state.get("episodes", {}).items():
        try:
            await sync_episode_to_db(media_id, rec)
        except Exception as e:
            logger.warning(f"direct_archive: falha ao sincronizar {media_id} com o banco: {e}")


# 6. Gatilho sob demanda + varredura + keepalive + Telegram
def maybe_enqueue(media_id: str, direct_streams: list[dict], has_cached_pt_hd: bool = False) -> bool:
    if has_cached_pt_hd:
        return False

    parsed_ep = parse_series_ep(media_id)
    if not parsed_ep:
        return False
    imdb, s, e = parsed_ep

    state = _load_state()
    if imdb not in state.get("watched", {}):
        return False

    ep_rec = state.get("episodes", {}).get(media_id)
    if _is_ep_satisfied(ep_rec):
        return False

    if time.time() - _FAIL_COOLDOWN.get(media_id, 0) < FAIL_COOLDOWN_S:
        return False

    cands = pick_archive_candidates(direct_streams)
    if not cands:
        return False

    if media_id not in _QUEUED:
        _QUEUED.add(media_id)
        _QUEUE.append((media_id, cands))
        _ensure_queue_worker()
        return True

    return False


def _ensure_queue_worker() -> None:
    global _QUEUE_WORKER_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _QUEUE_WORKER_TASK is None or _QUEUE_WORKER_TASK.done():
        _QUEUE_WORKER_TASK = loop.create_task(_queue_worker())


async def _queue_worker() -> None:
    while _QUEUE:
        media_id, cands = _QUEUE.pop(0)
        try:
            await archive_episode(media_id, cands)
        except Exception as e:
            logger.warning(f"direct_archive: erro ao arquivar {media_id} da fila: {e}")
        finally:
            _QUEUED.discard(media_id)


def _get_tmdb_token() -> str:
    tok = getattr(settings, "TMDB_READ_ACCESS_TOKEN", None)
    if tok:
        return str(tok)
    try:
        from comet.metadata.tmdb import DEFAULT_TMDB_READ_ACCESS_TOKEN
        return DEFAULT_TMDB_READ_ACCESS_TOKEN
    except ImportError:
        return os.environ.get("TMDB_READ_ACCESS_TOKEN", "")


async def _keepalive_and_verify() -> None:
    tb_key = _torbox_key()
    if not tb_key or httpx is None:
        return

    state = _load_state()
    episodes = state.get("episodes", {})
    if not episodes:
        return

    items = list(episodes.items())
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(items), 50):
            batch = items[i:i + 50]
            hashes = [rec.get("info_hash") for _, rec in batch if rec.get("info_hash")]
            if not hashes:
                continue
            hash_param = ",".join(hashes)
            try:
                resp = await client.get(
                    f"{TORBOX_API}/torrents/checkcached?hash={hash_param}&format=object",
                    headers=_torbox_headers(tb_key),
                )
                resp.raise_for_status()
                cached_data = resp.json().get("data") or {}
            except Exception as e:
                logger.warning(f"direct_archive: falha ao checar cache no TorBox: {e}")
                continue

            now = int(time.time())
            state_changed = False
            for media_id, rec in batch:
                h = rec.get("info_hash")
                is_cached = bool(cached_data.get(h) or (cached_data.get(h.lower()) if h else False)) if h else False
                if not is_cached:
                    logger.info(f"direct_archive: {media_id} ({h}) não está mais em cache no TorBox, removendo")
                    episodes.pop(media_id, None)
                    if h:
                        try:
                            await remove_hash_from_db(h)
                        except Exception:
                            pass
                    state_changed = True
                    continue

                last_keep = rec.get("last_keepalive_at", 0)
                if now - last_keep >= KEEPALIVE_DAYS * 86400:
                    tid = rec.get("torrent_id")
                    if tid:
                        try:
                            dl_resp = await client.get(
                                f"{TORBOX_API}/torrents/requestdl",
                                params={"token": tb_key, "torrent_id": tid, "file_id": 0},
                                headers=_torbox_headers(tb_key),
                            )
                            dl_data = dl_resp.json().get("data")
                            dl_url = dl_data if isinstance(dl_data, str) else (dl_data.get("url") if isinstance(dl_data, dict) else None)
                            if dl_url:
                                await client.get(dl_url, headers={"Range": "bytes=0-1023"}, timeout=15.0)
                                rec["last_keepalive_at"] = now
                                state_changed = True
                                logger.info(f"direct_archive: keepalive renovado para {media_id} ({rec.get('title')})")
                        except Exception as e:
                            logger.warning(f"direct_archive: erro no keepalive de {media_id}: {e}")

            if state_changed:
                _save_state(state)
                await sync_all_to_db()


async def _get_series_episodes_from_tmdb(client: httpx.AsyncClient, imdb: str) -> list[tuple[int, int]]:
    tok = _get_tmdb_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    base = "https://api.themoviedb.org/3"
    try:
        resp = await client.get(f"{base}/find/{imdb}", params={"external_source": "imdb_id"}, headers=headers)
        resp.raise_for_status()
        tv_results = resp.json().get("tv_results") or []
        if not tv_results:
            return []
        tv_id = tv_results[0]["id"]
        tv_resp = await client.get(f"{base}/tv/{tv_id}", headers=headers)
        tv_resp.raise_for_status()
        tv_data = tv_resp.json()
        seasons = tv_data.get("seasons") or []

        today_str = datetime.date.today().isoformat()
        episodes_list: list[tuple[int, int]] = []
        for s_info in seasons:
            s_num = int(s_info.get("season_number") or 0)
            if s_num <= 0:
                continue
            try:
                s_resp = await client.get(f"{base}/tv/{tv_id}/season/{s_num}", headers=headers)
                s_resp.raise_for_status()
                s_data = s_resp.json()
                for ep in s_data.get("episodes") or []:
                    e_num = int(ep.get("episode_number") or 0)
                    air_date = ep.get("air_date") or ""
                    if air_date and air_date > today_str:
                        continue
                    if e_num > 0:
                        episodes_list.append((s_num, e_num))
            except Exception as e:
                logger.warning(f"direct_archive: falha ao buscar T{s_num} de {imdb}: {e}")
                cnt = int(s_info.get("episode_count") or 0)
                for ep_i in range(1, cnt + 1):
                    episodes_list.append((s_num, ep_i))

        return episodes_list
    except Exception as e:
        logger.warning(f"direct_archive: erro ao consultar TMDB para {imdb}: {e}")
        return []


async def _get_covered_episodes(client: httpx.AsyncClient, tb_key: str, imdb: str) -> set[tuple[int, int]]:
    covered = set()
    try:
        from comet.core.database import database
    except ImportError:
        database = None

    if database is None or not tb_key:
        return covered

    try:
        rows = await database.fetch_all(
            """
            SELECT info_hash, season_norm, episode_norm, title, tracker, parsed_json
            FROM torrents
            WHERE media_id = :media_id
            """,
            {"media_id": imdb},
        )
        if not rows:
            return covered

        candidate_rows = []
        hashes_to_check = set()
        for r in rows:
            rd = dict(r._mapping) if hasattr(r, "_mapping") else dict(r)
            p_raw = rd.get("parsed_json") or "{}"
            try:
                p_dict = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw if isinstance(p_raw, dict) else {})
            except Exception:
                p_dict = {}
            title_low = str(rd.get("title") or "").lower()
            langs = [str(x).lower() for x in (p_dict.get("languages") or [])]
            if "es" in langs or "ru" in langs or any(w in title_low for w in ("dual-lat", "chicas", "девиц", "latino")):
                continue
            if "pt" not in langs and not any(w in title_low for w in ("dublado", "pt-br", "ptbr", "portugu")):
                continue
            res = str(p_dict.get("resolution") or "").lower()
            if any(q in res for q in ("480", "360", "sd", "cam")):
                continue

            h = str(rd.get("info_hash") or "").lower()
            if h:
                hashes_to_check.add(h)
                sn = rd.get("season_norm")
                en = rd.get("episode_norm")
                candidate_rows.append((h, int(sn) if sn is not None else -1, int(en) if en is not None else -1))

        if not hashes_to_check:
            return covered

        cached_hashes = set()
        hash_list = list(hashes_to_check)
        for i in range(0, len(hash_list), 50):
            batch = hash_list[i:i + 50]
            hparam = ",".join(batch)
            cresp = await client.get(
                f"{TORBOX_API}/torrents/checkcached?hash={hparam}&format=object",
                headers=_torbox_headers(tb_key),
            )
            cdata = cresp.json().get("data") or {}
            for h in batch:
                if bool(cdata.get(h) or cdata.get(h.lower())):
                    cached_hashes.add(h)

        for h, sn, en in candidate_rows:
            if h in cached_hashes:
                if sn == -1 and en == -1:
                    covered.add((-1, -1))
                elif en == -1:
                    covered.add((sn, -1))
                else:
                    covered.add((sn, en))
    except Exception as e:
        logger.warning(f"direct_archive: erro ao checar cobertura no DB/TorBox: {e}")

    return covered


async def sweep_watched(only_imdb: str | None = None) -> None:
    if httpx is None:
        logger.warning("direct_archive: httpx não disponível para sweep")
        return

    sweep_lock = _get_sweep_lock()
    if sweep_lock.locked():
        logger.debug("direct_archive: sweep já em andamento, pulando chamada paralela")
        return

    async with sweep_lock:
        await _sweep_watched_inner(only_imdb=only_imdb)


async def _sweep_watched_inner(only_imdb: str | None = None) -> None:

    state = _load_state()
    watched = state.get("watched", {})
    if not watched:
        return

    tb_key = _torbox_key()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for imdb in list(watched.keys()):
            if only_imdb and imdb != only_imdb:
                continue

            all_eps = await _get_series_episodes_from_tmdb(client, imdb)
            if not all_eps:
                continue

            covered = await _get_covered_episodes(client, tb_key, imdb) if tb_key else set()

            missing_eps = []
            for s, e in all_eps:
                if (-1, -1) in covered or (s, -1) in covered or (s, e) in covered:
                    continue
                ep_id = f"{imdb}:{s}:{e}"
                ep_rec = state.get("episodes", {}).get(ep_id)
                if _is_ep_satisfied(ep_rec):
                    continue
                if time.time() - _FAIL_COOLDOWN.get(ep_id, 0) < FAIL_COOLDOWN_S:
                    continue
                missing_eps.append((s, e))

            # Ordem crescente de temporada (primeira incompleta primeiro) e crescente de episódio
            missing_eps.sort(key=lambda x: (x[0], x[1]))

            archived_count = 0
            for s, e in missing_eps:
                if archived_count >= MAX_PER_SWEEP:
                    break
                if tb_key:
                    active_cnt = await _torbox_active_count(client, tb_key)
                    max_active = await _torbox_max_active(client, tb_key)
                    if active_cnt >= max_active:
                        logger.info(
                            f"direct_archive: {active_cnt}/{max_active} slots ativos no TorBox, interrompendo sweep"
                        )
                        break
                ep_id = f"{imdb}:{s}:{e}"
                await asyncio.sleep(2.0)

                direct_streams = []
                try:
                    from comet.services.direct_addons import fetch_direct_streams
                    direct_streams = await fetch_direct_streams("series", ep_id)
                except Exception as ex:
                    logger.warning(f"direct_archive: fetch_direct_streams falhou para {ep_id}: {ex}")
                    continue

                cands = pick_archive_candidates(direct_streams)
                if cands:
                    res = await archive_episode(ep_id, cands)
                    if res:
                        archived_count += 1


async def start_service() -> None:
    _ensure_queue_worker()
    logger.info("direct_archive: serviço iniciado, aguardando 90s para primeira sincronização...")
    await asyncio.sleep(90)
    try:
        await sync_all_to_db()
    except Exception as e:
        logger.warning(f"direct_archive: erro no sync_all_to_db inicial: {e}")

    while True:
        try:
            await _keepalive_and_verify()
            await sweep_watched()
        except asyncio.CancelledError:
            logger.info("direct_archive: serviço cancelado")
            await _stop_cf_tunnel()
            raise
        except Exception as e:
            logger.exception(f"direct_archive: erro no ciclo de varredura: {e}")

        try:
            await asyncio.sleep(SWEEP_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("direct_archive: serviço cancelado durante sleep")
            await _stop_cf_tunnel()
            raise


# Plugin Telegram
HELP = (
    "📦 *Arquivamento no TorBox*\n"
    "• `/arquivar` — Mostra o estado das séries vigiadas e episódios arquivados\n"
    "• `/arquivar <nome ou tt...>` — Adiciona série para arquivar episódios dublados no TorBox\n"
    "• `/desarquivar <nome ou tt...>` — Remove série do arquivamento automático"
)


async def status_lines() -> list[str]:
    state = _load_state()
    watched = state.get("watched", {})
    episodes = state.get("episodes", {})
    if not watched:
        return []
    total_eps = len(episodes)
    names = [v.get("name") or k for k, v in watched.items()]
    return [f"📦 Arquivo TorBox: {total_eps} eps salvos ({', '.join(names)})"]


async def handle_command(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd not in ("/arquivar", "/desarquivar"):
        return None

    state = _load_state()
    watched = state.setdefault("watched", {})
    episodes = state.setdefault("episodes", {})

    if cmd == "/arquivar":
        if not arg:
            if not watched:
                return "📦 Nenhuma série vigiada no momento."
            lines = ["📦 *Séries em Arquivamento no TorBox:*"]
            for imdb, info in watched.items():
                name = info.get("name") or imdb
                season_counts: dict[int, int] = {}
                series_total = 0
                for ep_id in episodes:
                    parsed = parse_series_ep(ep_id)
                    if parsed and parsed[0] == imdb:
                        s = parsed[1]
                        season_counts[s] = season_counts.get(s, 0) + 1
                        series_total += 1
                if season_counts:
                    s_str = ", ".join(f"T{s}: {season_counts[s]}" for s in sorted(season_counts))
                    lines.append(f"• *{name}* (`{imdb}`): {series_total} eps salvos ({s_str})")
                else:
                    lines.append(f"• *{name}* (`{imdb}`): 0 eps salvos")
            return "\n".join(lines)

        term = arg
        imdb = None
        series_name = None
        m_tt = re.match(r"^(tt\d+)", term.lower())
        if m_tt:
            imdb = m_tt.group(1)
            tok = _get_tmdb_token()
            if tok and httpx is not None:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(
                            f"https://api.themoviedb.org/3/find/{imdb}",
                            params={"external_source": "imdb_id"},
                            headers={"Authorization": f"Bearer {tok}"},
                        )
                        tv_res = resp.json().get("tv_results") or []
                        if tv_res:
                            series_name = tv_res[0].get("name")
                except Exception:
                    pass
            if not series_name:
                series_name = imdb
        else:
            tok = _get_tmdb_token()
            if tok and httpx is not None:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        s_resp = await client.get(
                            "https://api.themoviedb.org/3/search/tv",
                            params={"query": term},
                            headers={"Authorization": f"Bearer {tok}"},
                        )
                        results = s_resp.json().get("results") or []
                        if results:
                            tv_id = results[0]["id"]
                            series_name = results[0].get("name")
                            ext_resp = await client.get(
                                f"https://api.themoviedb.org/3/tv/{tv_id}/external_ids",
                                headers={"Authorization": f"Bearer {tok}"},
                            )
                            imdb = ext_resp.json().get("imdb_id")
                except Exception as e:
                    logger.warning(f"direct_archive: busca TMDB por '{term}' falhou: {e}")

        if not imdb:
            return f"❌ Série '{term}' não encontrada no TMDB ou sem IMDb ID associado."

        watched[imdb] = {"name": series_name or imdb, "added_at": int(time.time())}
        _save_state(state)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(sweep_watched(only_imdb=imdb))
        except RuntimeError:
            pass

        return f"✅ Série *{series_name}* (`{imdb}`) adicionada ao arquivamento! Varredura iniciada em segundo plano."

    if cmd == "/desarquivar":
        if not arg:
            return "Uso: `/desarquivar <nome ou tt...>`"
        target = arg.strip().lower()
        removed_imdb = None
        removed_name = None
        for imdb, info in list(watched.items()):
            name = (info.get("name") or "").lower()
            if target == imdb.lower() or target in name or name in target:
                removed_imdb = imdb
                removed_name = info.get("name") or imdb
                del watched[imdb]
                break

        if removed_imdb:
            _save_state(state)
            return f"🗑️ Série *{removed_name}* (`{removed_imdb}`) removida do arquivamento."
        else:
            return f"Série '{arg}' não encontrada na lista de vigiadas."

    return None
