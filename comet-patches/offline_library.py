"""Módulo de biblioteca local offline no Galaxy A71 + bot Telegram (/baixar).

Permite baixar filmes e séries localmente no A71 para reprodução sem buffering,
subindo também no TorBox para cache permanente na nuvem.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

# Imports seguros
try:
    from comet.core.logger import logger
except ImportError:
    logger = logging.getLogger("comet.services.offline_library")
    if not logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(_h)
        logger.setLevel(logging.INFO)

try:
    from comet.core.models import settings
except ImportError:
    settings = None

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from fastapi import APIRouter, HTTPException, Response
    from fastapi.responses import FileResponse
    router: APIRouter | None = APIRouter()
except ImportError:
    APIRouter = None  # type: ignore
    HTTPException = None  # type: ignore
    Response = None  # type: ignore
    FileResponse = None  # type: ignore
    router = None

try:
    from comet.services.telegram_bot import send_telegram
except ImportError:
    async def send_telegram(msg: str) -> bool:
        logger.info(f"Telegram (offline): {msg}")
        return False

try:
    from comet.services import direct_archive
except ImportError:
    try:
        import direct_archive
    except ImportError:
        direct_archive = None

# Constantes e caminhos configuráveis
OFFLINE_DIR = os.environ.get("OFFLINE_DIR", "data/offline")
INDEX_PATH = os.environ.get("OFFLINE_INDEX_PATH", "data/offline_library.json")
MIN_FREE_GB = float(os.environ.get("OFFLINE_MIN_FREE_GB", "15"))
MAX_EP_GB = float(os.environ.get("OFFLINE_MAX_EP_GB", "4.0"))
MAX_MOVIE_GB = float(os.environ.get("OFFLINE_MAX_MOVIE_GB", "12.0"))
LOCAL_COMET_URL = "http://127.0.0.1:8000"
PLAYER_UA = "ExoPlayerLib/2.19.1"
PIECE_LENGTH = 512 * 1024

# Estado em memória
_JOB_QUEUE: list[dict] = []
_CURRENT_JOB: dict | None = None
_CANCEL_FLAG: bool = False
_WORKER_TASK: asyncio.Task | None = None
_WORKER_LOCK: asyncio.Lock | None = None


def _get_worker_lock() -> asyncio.Lock:
    global _WORKER_LOCK
    if _WORKER_LOCK is None:
        _WORKER_LOCK = asyncio.Lock()
    return _WORKER_LOCK


def _torbox_key() -> str:
    if direct_archive and hasattr(direct_archive, "_torbox_key"):
        return direct_archive._torbox_key()
    key = getattr(settings, "TORBOX_API_KEY", None)
    if key:
        return str(key)
    return os.environ.get("TORBOX_API_KEY", "")


def _get_tmdb_token() -> str:
    if direct_archive and hasattr(direct_archive, "_get_tmdb_token"):
        return direct_archive._get_tmdb_token()
    tok = getattr(settings, "TMDB_READ_ACCESS_TOKEN", None)
    if tok:
        return str(tok)
    try:
        from comet.metadata.tmdb import DEFAULT_TMDB_READ_ACCESS_TOKEN
        return DEFAULT_TMDB_READ_ACCESS_TOKEN
    except ImportError:
        return os.environ.get("TMDB_READ_ACCESS_TOKEN", "")


def _hash_file_pieces(filepath: str, piece_length: int = PIECE_LENGTH) -> tuple[int, bytes]:
    if direct_archive and hasattr(direct_archive, "_hash_file_pieces"):
        return direct_archive._hash_file_pieces(filepath, piece_length=piece_length)
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


def is_valid_probe(probe: dict, expected_runtime_min: int | None = None) -> tuple[bool, str]:
    if direct_archive and hasattr(direct_archive, "is_valid_probe"):
        return direct_archive.is_valid_probe(probe, expected_runtime_min)
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


async def _ffprobe_file(path: str) -> dict:
    if direct_archive and hasattr(direct_archive, "_ffprobe_file"):
        return await direct_archive._ffprobe_file(path)
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


def _get_valid_tokens() -> set[str]:
    tokens = set()
    for name in ("NUVIO_LINK_TOKEN", "PERSONAL_LINK_TOKEN", "GUEST_LINK_TOKEN", "KIDS_LINK_TOKEN"):
        val = None
        if settings is not None:
            val = getattr(settings, name, None)
        if not val:
            val = os.environ.get(name)
        if val:
            tokens.add(str(val).strip())

    env_file = "/opt/comet/.env"
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("NUVIO_LINK_TOKEN", "PERSONAL_LINK_TOKEN", "GUEST_LINK_TOKEN", "KIDS_LINK_TOKEN") and v:
                        tokens.add(v)
        except Exception:
            pass
    return tokens


def _is_valid_token(token: str) -> bool:
    valid = _get_valid_tokens()
    if not valid:
        return False
    return token in valid


def _load_index(path: str | None = None) -> dict:
    target_path = path or INDEX_PATH
    if os.path.isfile(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("items"), dict):
                    return data
        except Exception as e:
            logger.warning(f"offline_library: erro ao carregar índice {target_path}: {e}")
    return {"items": {}}


def _save_index(data: dict, path: str | None = None) -> None:
    target_path = path or INDEX_PATH
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(p) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, str(p))


def parse_download_command(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None

    parts = raw.split(maxsplit=1)
    first_token = parts[0]
    cmd = first_token.lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    m_nat = re.match(r"^(?:baixar|baixa)\b(?:\s+(.*))?$", raw, re.IGNORECASE)
    if not cmd.startswith("/") and m_nat:
        cmd = "/baixar"
        arg = (m_nat.group(1) or "").strip()

    if cmd in ("/baixados", "/offline"):
        return {"action": "list"}

    if cmd in ("/cancelar", "/parar"):
        return {"action": "cancel"}

    if cmd == "/apagar":
        if not arg:
            return {"action": "delete", "query": ""}

        m_mid = re.match(r"^(tt\d+):(\d+):(\d+)$", arg, re.IGNORECASE)
        if m_mid:
            return {
                "action": "delete",
                "query": "",
                "imdb": m_mid.group(1).lower(),
                "season": int(m_mid.group(2)),
                "episode": int(m_mid.group(3)),
            }

        season = None
        episode = None
        m_se = re.search(r"(?i)\b(?:s|t|temporada\s*)(\d{1,2})(?:\s*(?:e|ep|episodio\s*|x)(\d{1,3}))\b", arg)
        m_x = re.search(r"(?i)\b(\d{1,2})x(\d{1,3})\b", arg)
        m_s = re.search(r"(?i)\b(?:s|t|temporada\s*)(\d{1,2})\b", arg)
        if m_se:
            season = int(m_se.group(1))
            episode = int(m_se.group(2))
        elif m_x:
            season = int(m_x.group(1))
            episode = int(m_x.group(2))
        elif m_s:
            season = int(m_s.group(1))

        imdb = None
        m_tt = re.search(r"\b(tt\d+)\b", arg, re.IGNORECASE)
        if m_tt:
            imdb = m_tt.group(1).lower()

        q = arg
        if imdb:
            q = re.sub(re.escape(m_tt.group(1)), " ", q, flags=re.IGNORECASE)
        if m_se:
            q = re.sub(re.escape(m_se.group(0)), " ", q, flags=re.IGNORECASE)
        elif m_x:
            q = re.sub(re.escape(m_x.group(0)), " ", q, flags=re.IGNORECASE)
        elif m_s:
            q = re.sub(re.escape(m_s.group(0)), " ", q, flags=re.IGNORECASE)

        q = re.sub(r"(?i)\b(temporadas?|filme|s[eé]rie)\b", " ", q)
        q = re.sub(r"^[^\w]+|[^\w]+$", "", q)
        q = re.sub(r"(?i)^(de|do|da|dos|das|o|a|os|as)\s+", "", q)
        q = re.sub(r"(?i)\s+(de|do|da|dos|das|o|a|os|as)$", "", q)
        q = re.sub(r"\s+", " ", q).strip()

        return {
            "action": "delete",
            "query": q,
            "imdb": imdb,
            "season": season,
            "episode": episode,
        }

    if cmd == "/baixar":
        if not arg:
            return {"action": "status"}

        prefer_dub = True
        if re.search(r"(?i)\b(legendad[oa]s?|origin(?:al|ais)|ingles|ingl[eê]s|sub(?:s|bed)?)\b", arg):
            prefer_dub = False

        imdb = None
        m_tt = re.search(r"\b(tt\d+)\b", arg, re.IGNORECASE)
        if m_tt:
            imdb = m_tt.group(1).lower()

        season = None
        episode = None
        m_all = re.search(r"(?i)\b(todas\s+as\s+temporadas|todas\s+as\s+temps?|todas)\b", arg)
        m_se = re.search(r"(?i)\b(?:s|t|temporada\s*)(\d{1,2})(?:\s*(?:e|ep|episodio\s*|x)(\d{1,3}))\b", arg)
        m_x = re.search(r"(?i)\b(\d{1,2})x(\d{1,3})\b", arg)
        m_s = re.search(r"(?i)\b(?:s|t|temporada\s*)(\d{1,2})\b", arg)

        if m_all:
            season = None
            episode = None
        elif m_se:
            season = int(m_se.group(1))
            episode = int(m_se.group(2))
        elif m_x:
            season = int(m_x.group(1))
            episode = int(m_x.group(2))
        elif m_s:
            season = int(m_s.group(1))

        q = arg
        if imdb:
            q = re.sub(re.escape(m_tt.group(1)), " ", q, flags=re.IGNORECASE)
        if m_all:
            q = re.sub(re.escape(m_all.group(0)), " ", q, flags=re.IGNORECASE)
        if m_se:
            q = re.sub(re.escape(m_se.group(0)), " ", q, flags=re.IGNORECASE)
        elif m_x:
            q = re.sub(re.escape(m_x.group(0)), " ", q, flags=re.IGNORECASE)
        elif m_s:
            q = re.sub(re.escape(m_s.group(0)), " ", q, flags=re.IGNORECASE)

        q = re.sub(r"(?i)\b(dublad[oa]s?|dub|pt-br|pt\s+br|ptbr)\b", " ", q)
        q = re.sub(r"(?i)\b(legendad[oa]s?|origin(?:al|ais)|ingles|ingl[eê]s|sub(?:s|bed)?)\b", " ", q)
        q = re.sub(r"(?i)\b(temporadas?|filme|s[eé]rie)\b", " ", q)
        q = re.sub(r"^[^\w]+|[^\w]+$", "", q)
        q = re.sub(r"(?i)^(de|do|da|dos|das|o|a|os|as)\s+", "", q)
        q = re.sub(r"(?i)\s+(de|do|da|dos|das|o|a|os|as)$", "", q)
        q = re.sub(r"\s+", " ", q).strip()

        return {
            "action": "download",
            "query": q,
            "imdb": imdb,
            "season": season,
            "episode": episode,
            "prefer_dub": prefer_dub,
        }

    return None


def parse_size_bytes(text: str) -> int | None:
    if not text or not isinstance(text, str):
        return None
    m = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(GB|MB|KB|bytes|B|GiB|MiB|KiB)\b", text)
    if not m:
        return None
    try:
        val_str = m.group(1).replace(",", ".")
        val = float(val_str)
        unit = m.group(2).upper()
        if unit.startswith("G"):
            return int(val * 1024**3)
        elif unit.startswith("M"):
            return int(val * 1024**2)
        elif unit.startswith("K"):
            return int(val * 1024)
        else:
            return int(val)
    except Exception:
        return None


def pick_download_candidate(
    streams: list[dict],
    prefer_dub: bool = True,
    max_bytes: int | None = None,
) -> dict | None:
    if not streams or not isinstance(streams, list):
        return None

    streams_by_url: dict[str, dict] = {}
    for st in streams:
        if not isinstance(st, dict):
            continue
        url = st.get("url")
        if not url or not isinstance(url, str):
            continue
        url = url.strip()
        if not url or url.startswith("ftp://"):
            continue

        name = str(st.get("name") or "")
        desc = str(st.get("description") or "")
        combined = f"{name} {desc}".lower()

        if "💾" in combined:
            continue
        if "[aviso]" in combined:
            continue
        bh = st.get("behaviorHints") or {}
        fn = str(bh.get("filename") or "")
        fn_l = fn.lower()
        if "outro corte" in combined or ".cut" in combined or ".cut" in fn_l or "outro corte" in fn_l:
            continue
        if re.search(r"\b(trailer|teaser)\b", combined):
            continue
        if re.search(r"\b(cam|hdcam|ts|telesync)\b", combined):
            continue

        if url not in streams_by_url:
            streams_by_url[url] = st
        else:
            prev = streams_by_url[url]
            prev_name = str(prev.get("name") or "")
            if "▶ Assistir" in prev_name and "▶ Assistir" not in name:
                streams_by_url[url] = st

    candidates = []
    for url, st in streams_by_url.items():
        name = str(st.get("name") or "")
        desc = str(st.get("description") or "")
        bh = st.get("behaviorHints") or {}
        fn = str(bh.get("filename") or "")
        combined = f"{name} {desc} {fn}".lower()

        is_uncached = "⬇️" in name
        if is_uncached:
            continue

        is_pt = "🇧🇷" in name or "🇧🇷" in desc or bool(re.search(r"\b(dublado|dublada|pt-br|pt\s+br|ptbr|dual)\b", combined))
        is_cached_debrid = "⚡" in name or "⚡" in desc or ("/playback/" in url.lower() and not is_uncached)
        is_torbox = "torbox" in combined or "tb⚡" in combined or bool(re.search(r"\btb(?:\b|⚡)", combined))
        is_direct = "🌐" in name or any(x in combined for x in ("fenixflix", "megaembed"))
        is_hls = ".m3u8" in url.lower() or "(hls)" in desc.lower()

        if re.search(r"\b(2160p|4k|uhd)\b", combined):
            res_rank = 2
        elif re.search(r"\b1080p\b", combined):
            res_rank = 0
        elif re.search(r"\b720p\b", combined):
            res_rank = 1
        elif re.search(r"\b(480p|360p|sd)\b", combined):
            res_rank = 4
        else:
            res_rank = 3

        sz = bh.get("videoSize")
        if sz is None or not isinstance(sz, int) or sz <= 0:
            sz = parse_size_bytes(desc)

        if "fenixflix" in combined:
            source = "FenixFlix"
        elif "megaembed" in combined:
            source = "MegaEmbed"
        elif is_torbox:
            source = "TorBox"
        elif "realdebrid" in combined or "real-debrid" in combined or re.search(r"\brd\b", combined):
            source = "RealDebrid"
        elif is_direct:
            source = "Direct"
        elif is_cached_debrid:
            source = "Debrid"
        else:
            source = "Comet"

        candidates.append({
            "url": url,
            "is_hls": is_hls,
            "is_pt": is_pt,
            "is_torbox": is_torbox,
            "is_direct": is_direct,
            "is_cached_debrid": is_cached_debrid,
            "res_rank": res_rank,
            "source": source,
            "name": name,
            "filename": fn,
            "size_bytes": sz,
        })

    if not candidates:
        return None

    def pick_from_list(c_list: list[dict]) -> dict | None:
        if prefer_dub:
            pt_list = [c for c in c_list if c["is_pt"]]
            if not pt_list:
                return None
            debrid_pts = [c for c in pt_list if c["is_cached_debrid"]]
            if debrid_pts:
                debrid_pts.sort(key=lambda c: (
                    0 if c["is_torbox"] else 1,
                    c["res_rank"],
                    c["size_bytes"] if c["size_bytes"] is not None else 999999999999,
                ))
                return debrid_pts[0]

            direct_pts = [c for c in pt_list if c["is_direct"]]
            if direct_pts:
                direct_pts.sort(key=lambda c: (
                    1 if c["is_hls"] else 0,
                    0 if c["source"] == "FenixFlix" else 1 if c["source"] == "MegaEmbed" else 2,
                    c["res_rank"],
                    c["size_bytes"] if c["size_bytes"] is not None else 999999999999,
                ))
                return direct_pts[0]
            return None
        else:
            debrid_list = [c for c in c_list if c["is_cached_debrid"]]
            if debrid_list:
                debrid_list.sort(key=lambda c: (
                    0 if c["is_torbox"] else 1,
                    c["res_rank"],
                    c["size_bytes"] if c["size_bytes"] is not None else 999999999999,
                ))
                return debrid_list[0]
            direct_list = [c for c in c_list if c["is_direct"]]
            if direct_list:
                direct_list.sort(key=lambda c: (
                    1 if c["is_hls"] else 0,
                    c["res_rank"],
                    c["size_bytes"] if c["size_bytes"] is not None else 999999999999,
                ))
                return direct_list[0]
            return None

    if max_bytes is not None:
        under_max = [c for c in candidates if c["size_bytes"] is None or c["size_bytes"] <= max_bytes]
        best = pick_from_list(under_max)
        if best:
            return {
                "url": best["url"],
                "is_hls": best["is_hls"],
                "is_pt": best["is_pt"],
                "is_torbox": best["is_torbox"],
                "is_direct": best["is_direct"],
                "source": best["source"],
                "name": best["name"],
                "filename": best.get("filename", ""),
                "size_bytes": best["size_bytes"],
            }

    best = pick_from_list(candidates)
    if not best:
        return None
    return {
        "url": best["url"],
        "is_hls": best["is_hls"],
        "is_pt": best["is_pt"],
        "is_torbox": best["is_torbox"],
        "is_direct": best["is_direct"],
        "source": best["source"],
        "name": best["name"],
        "filename": best.get("filename", ""),
        "size_bytes": best["size_bytes"],
    }


def make_offline_path(
    base_dir: str,
    media_type: str,
    imdb: str,
    title: str,
    season: int | None,
    episode: int | None,
    height: int,
    dubbed: bool,
    ext: str = ".mp4",
) -> tuple[str, str]:
    norm = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9]+", ".", norm).strip(".")
    if not safe:
        safe = "Series" if media_type == "series" else "Movie"

    res = "2160p" if height >= 1800 else "1080p" if height >= 900 else "720p" if height >= 650 else "480p"
    tag = "Dublado.PT-BR" if dubbed else "Original"
    clean_ext = ext if ext.startswith(".") else f".{ext}"

    if media_type == "series" or (season is not None and episode is not None):
        s = int(season or 1)
        e = int(episode or 1)
        filename = f"{safe}.S{s:02d}E{e:02d}.{res}.{tag}{clean_ext}"
        full_path = os.path.normpath(os.path.join(base_dir, "series", imdb, f"S{s:02d}", filename))
    else:
        filename = f"{safe}.{res}.{tag}{clean_ext}"
        full_path = os.path.normpath(os.path.join(base_dir, "movies", imdb, filename))

    return full_path, filename


def get_local_entry(media_id: str, index_path: str | None = None) -> dict | None:
    idx = _load_index(index_path)
    entry = idx.get("items", {}).get(media_id)
    if not entry or not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not path or not os.path.isfile(path):
        return None
    try:
        if os.path.getsize(path) <= 0:
            return None
    except OSError:
        return None
    return entry


def build_local_stream(
    media_id: str,
    b64config: str | None,
    base_url: str,
    index_path: str | None = None,
) -> dict | None:
    entry = get_local_entry(media_id, index_path)
    if not entry:
        return None

    # A rota /offline só aceita *_LINK_TOKEN; uma config base64 comum daria 403 no play.
    token = (b64config or "").strip()
    if not token or not _is_valid_token(token):
        token = _get_internal_token(fallback=None)
    if not token:
        return None
    filename = entry.get("filename") or os.path.basename(entry.get("path", ""))
    url = f"{base_url.rstrip('/')}/offline/{token}/{media_id}/{filename}"

    height = int(entry.get("height") or 0)
    if height >= 1800:
        res = "4K"
    elif height >= 900:
        res = "1080p"
    elif height >= 650:
        res = "720p"
    elif height > 0:
        res = "480p"
    else:
        fn_l = filename.lower()
        if "2160p" in fn_l or "4k" in fn_l:
            res = "4K"
        elif "1080p" in fn_l:
            res = "1080p"
        elif "720p" in fn_l:
            res = "720p"
        else:
            res = "HD"

    size = int(entry.get("size") or 0)
    if size >= 1024**3:
        size_str = f"{size / (1024**3):.1f} GB".replace(".", ",")
    elif size >= 1024**2:
        size_str = f"{size / (1024**2):.0f} MB"
    else:
        size_str = f"{size} B"

    dubbed = bool(entry.get("dubbed", True))
    lang_tag = "🇧🇷 Dublado" if dubbed else "🎬 Original"

    return {
        "name": f"▶ Assistir\n💾 {res}",
        "description": f"💾 Local no A71 · {lang_tag} · {size_str}\nToca direto do celular (sem buffering)",
        "url": url,
        "behaviorHints": {
            "notWebReady": False,
            "bingeGroup": "comet|local|pt" if dubbed else "comet|local|orig",
            "filename": filename,
            "videoSize": size,
        },
    }


def inject_local_into_streams(streams: list[dict], local_stream: dict | None) -> list[dict]:
    """Insere o stream local em 1º (após avisos) e remove apenas o prefixo '▶ Assistir' das fontes online."""
    if local_stream is None:
        return list(streams)
    out = [dict(s) for s in streams]
    ins = 0
    while ins < len(out) and not (
        out[ins].get("url") or out[ins].get("infoHash") or out[ins].get("externalUrl")
    ):
        ins += 1
    for i in range(ins, len(out)):
        nm = str(out[i].get("name") or "")
        if nm.startswith("▶"):
            demoted = re.sub(r"^▶\s*Assistir\s*\n?", "", nm).strip()
            out[i] = {**out[i], "name": f"☁️ {demoted}" if demoted else "☁️ Online"}
    out.insert(ins, local_stream)
    return out


def has_enough_disk_space(path: str, min_free_gb: float = MIN_FREE_GB) -> tuple[bool, float]:
    p = Path(path).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        usage = shutil.disk_usage(str(p))
        free_gb = usage.free / (1024**3)
        return (free_gb >= min_free_gb, free_gb)
    except Exception:
        return (True, 999.0)


# Rota FastAPI
if router is not None:
    @router.api_route("/offline/{token}/{media_id}/{filename}", methods=["GET", "HEAD"], tags=["Offline"])
    async def stream_offline_file(token: str, media_id: str, filename: str):
        if not _is_valid_token(token):
            raise HTTPException(status_code=403, detail="Acesso negado: token inválido")
        entry = get_local_entry(media_id)
        if not entry or not os.path.isfile(entry.get("path", "")):
            raise HTTPException(status_code=404, detail="Arquivo offline não encontrado")
        filepath = entry["path"]
        ext = os.path.splitext(filepath)[1].lower()
        mime = "video/x-matroska" if ext == ".mkv" else "video/mp4"
        return FileResponse(filepath, media_type=mime, filename=entry.get("filename", filename))


def _get_internal_token(fallback: str | None = "local") -> str | None:
    for name in ("NUVIO_LINK_TOKEN", "PERSONAL_LINK_TOKEN"):
        val = getattr(settings, name, None) if settings is not None else None
        if not val:
            val = os.environ.get(name)
        if val:
            return str(val).strip()
    env_file = "/opt/comet/.env"
    if os.path.isfile(env_file):
        found: dict[str, str] = {}
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("NUVIO_LINK_TOKEN", "PERSONAL_LINK_TOKEN") and v:
                        found[k] = v
        except Exception:
            pass
        for name in ("NUVIO_LINK_TOKEN", "PERSONAL_LINK_TOKEN"):
            if name in found:
                return found[name]
    return fallback


class _ChunkedFileHasher:
    """Grava arquivo em disco e calcula hashes SHA-1 por peça fora do event loop."""

    def __init__(self, path: str, piece_length: int = PIECE_LENGTH):
        self.path = path
        self.piece_length = piece_length
        self.total = 0
        self._pieces = bytearray()
        self._buf = bytearray()
        self._fh = open(path, "wb")

    def write_batch(self, data: bytes) -> int:
        if not data:
            return self.total
        self._fh.write(data)
        self.total += len(data)
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


def _hls_timeout_seconds(media_type: str, expected_runtime_min: int | None) -> float:
    if media_type == "movie":
        return max(2400.0, float(expected_runtime_min or 150) * 18.0)
    return max(900.0, float(expected_runtime_min or 45) * 18.0)


async def _terminate_proc(proc: Any, term_wait_s: float = 5.0) -> None:
    if getattr(proc, "returncode", None) is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=term_wait_s)
    except (asyncio.TimeoutError, TimeoutError):
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            pass
    except Exception:
        pass


async def _wait_hls_proc(proc: Any, timeout_s: float, poll_s: float = 2.0, term_wait_s: float = 5.0) -> str:
    """Aguarda ffmpeg em laço curto, respeitando _CANCEL_FLAG e timeout proporcional."""
    deadline = time.monotonic() + timeout_s
    while proc.returncode is None:
        if _CANCEL_FLAG:
            await _terminate_proc(proc, term_wait_s=term_wait_s)
            return "cancelled"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await _terminate_proc(proc, term_wait_s=term_wait_s)
            return "timeout"
        try:
            await asyncio.wait_for(proc.wait(), timeout=min(poll_s, remaining))
        except (asyncio.TimeoutError, TimeoutError):
            continue
    return "ok" if proc.returncode == 0 else "ffmpeg_failed"


def _is_episode_covered(covered_set: set[tuple[int, int]], season: int | None, episode: int | None) -> bool:
    if (-1, -1) in covered_set:
        return True
    if season is not None and (season, -1) in covered_set:
        return True
    if season is not None and episode is not None and (season, episode) in covered_set:
        return True
    return False


async def _fetch_comet_streams(client: httpx.AsyncClient, media_type: str, media_id: str) -> list[dict]:
    token = _get_internal_token()
    url = f"{LOCAL_COMET_URL}/{token}/stream/{media_type}/{media_id}.json?nolocal=1"
    try:
        resp = await client.get(url, timeout=90.0)
        if resp.status_code == 200:
            return resp.json().get("streams") or []
    except Exception as e:
        logger.warning(f"offline_library: erro ao consultar streams do Comet para {media_id}: {e}")
    return []


async def _download_item(client: httpx.AsyncClient, job_item: dict) -> dict:
    global _CANCEL_FLAG
    media_id = job_item["media_id"]
    media_type = job_item.get("media_type", "series")
    title = job_item.get("title", "")
    season = job_item.get("season")
    episode = job_item.get("episode")
    prefer_dub = bool(job_item.get("prefer_dub", True))
    expected_runtime_min = job_item.get("runtime_min")
    upload_torbox = bool(job_item.get("upload_torbox", True))

    existing = get_local_entry(media_id)
    if existing:
        if upload_torbox and not existing.get("torbox_hash"):
            tb_key = _torbox_key()
            if tb_key and direct_archive and hasattr(direct_archive, "upload_local_file_to_torbox"):
                try:
                    already_covered = False
                    if media_type == "series" and hasattr(direct_archive, "_get_covered_episodes"):
                        covered_set = await direct_archive._get_covered_episodes(client, tb_key, job_item["imdb"])
                        already_covered = _is_episode_covered(covered_set, season, episode)
                    if not already_covered:
                        p_probe = {
                            "width": existing.get("width") or 1280,
                            "height": existing.get("height") or 720,
                            "duration_s": existing.get("duration_s") or 1200.0,
                            "has_audio": True,
                        }
                        tb_res = await direct_archive.upload_local_file_to_torbox(
                            filepath=existing["path"],
                            media_id=media_id,
                            title_name=title,
                            probe=p_probe,
                            dubbed=existing.get("dubbed", True),
                        )
                        if tb_res and tb_res.get("info_hash"):
                            existing["torbox_hash"] = tb_res["info_hash"]
                            idx = _load_index()
                            idx.setdefault("items", {})[media_id] = existing
                            _save_index(idx)
                except Exception as e:
                    logger.warning(f"offline_library: erro upload TorBox arquivo existente {media_id}: {e}")
        return {"status": "skipped_exists", "media_id": media_id, "entry": existing}

    ok_disk, free_gb = has_enough_disk_space(OFFLINE_DIR)
    if not ok_disk:
        logger.warning(f"offline_library: pouco espaço em disco ({free_gb:.1f} GB livres), pausando {media_id}")
        return {"status": "low_disk", "media_id": media_id, "free_gb": free_gb}

    streams = await _fetch_comet_streams(client, media_type, media_id)
    if not streams:
        return {"status": "not_found", "media_id": media_id}

    max_bytes = int(MAX_MOVIE_GB * 1024**3) if media_type == "movie" else int(MAX_EP_GB * 1024**3)
    cand = pick_download_candidate(streams, prefer_dub=prefer_dub, max_bytes=max_bytes)
    if not cand:
        return {"status": "no_candidate", "media_id": media_id}

    cand_url = cand["url"]
    public_base = (getattr(settings, "PUBLIC_BASE_URL", None) or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/")
    if public_base and cand_url.startswith(public_base):
        cand_url = LOCAL_COMET_URL + cand_url[len(public_base):]

    tmp_dir = os.path.join(OFFLINE_DIR, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    clean_mid = media_id.replace(":", "_")
    part_path = os.path.join(tmp_dir, f"{clean_mid}_{int(time.time())}.part")

    total_size = 0
    pieces: bytes | None = None
    final_resp_url = cand_url
    resp_ct = ""

    try:
        if cand["is_hls"]:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error",
                "-user_agent", PLAYER_UA,
                "-i", cand_url,
                "-c", "copy",
                "-movflags", "+faststart",
                part_path,
            )
            try:
                hls_timeout = _hls_timeout_seconds(media_type, expected_runtime_min)
                hls_status = await _wait_hls_proc(proc, hls_timeout)
            finally:
                await _terminate_proc(proc)
            if hls_status != "ok":
                if os.path.exists(part_path):
                    os.unlink(part_path)
                return {"status": hls_status, "media_id": media_id}

            if not os.path.isfile(part_path) or os.path.getsize(part_path) == 0:
                if os.path.exists(part_path):
                    os.unlink(part_path)
                return {"status": "ffmpeg_failed", "media_id": media_id}

            total_size, pieces = await asyncio.to_thread(_hash_file_pieces, part_path, PIECE_LENGTH)
        else:
            writer = _ChunkedFileHasher(part_path, PIECE_LENGTH)
            pending = bytearray()
            headers = {"User-Agent": PLAYER_UA}
            dl_timeout = httpx.Timeout(20.0, read=90.0)
            try:
                async with httpx.AsyncClient(timeout=dl_timeout, follow_redirects=True, verify=False) as dl_client:
                    async with dl_client.stream("GET", cand_url, headers=headers) as resp:
                        resp.raise_for_status()
                        final_resp_url = str(resp.url)
                        resp_ct = str(resp.headers.get("content-type") or "").lower()
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            if _CANCEL_FLAG:
                                await asyncio.to_thread(writer.close)
                                if os.path.exists(part_path):
                                    os.unlink(part_path)
                                return {"status": "cancelled", "media_id": media_id}
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
                total_size, pieces = await asyncio.to_thread(writer.finish)
            finally:
                await asyncio.to_thread(writer.close)

        probe = await _ffprobe_file(part_path)
        dur = float(probe.get("duration_s") or 0.0)
        if media_type == "movie" and dur < 1800.0:
            if os.path.exists(part_path):
                os.unlink(part_path)
            return {"status": "probe_failed", "media_id": media_id, "detail": "movie duration < 30m"}

        valid, msg = is_valid_probe(probe, expected_runtime_min)
        if not valid:
            if os.path.exists(part_path):
                os.unlink(part_path)
            return {"status": "probe_failed", "media_id": media_id, "detail": msg}

        cand_fn = str(cand.get("filename") or "").lower()
        is_mkv = (
            ".mkv" in cand_url.lower()
            or ".mkv" in final_resp_url.lower()
            or cand_fn.endswith(".mkv")
            or "matroska" in resp_ct
        )
        ext = ".mkv" if is_mkv else ".mp4"
        height = int(probe.get("height") or 720)
        final_path, filename = make_offline_path(
            base_dir=OFFLINE_DIR,
            media_type=media_type,
            imdb=job_item["imdb"],
            title=title,
            season=season,
            episode=episode,
            height=height,
            dubbed=cand["is_pt"],
            ext=ext,
        )
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        os.replace(part_path, final_path)
        actual_size = os.path.getsize(final_path)

        entry = {
            "media_id": media_id,
            "media_type": media_type,
            "imdb": job_item["imdb"],
            "title": title,
            "season": season,
            "episode": episode,
            "path": final_path,
            "filename": filename,
            "size": actual_size,
            "width": int(probe.get("width") or 0),
            "height": height,
            "duration_s": dur,
            "dubbed": cand["is_pt"],
            "source": cand["source"],
            "torbox_hash": None,
            "downloaded_at": int(time.time()),
        }

        idx = _load_index()
        idx.setdefault("items", {})[media_id] = entry
        _save_index(idx)

        if upload_torbox and not cand["is_torbox"]:
            tb_key = _torbox_key()
            if tb_key and direct_archive and hasattr(direct_archive, "upload_local_file_to_torbox"):
                try:
                    covered = False
                    if media_type == "series" and hasattr(direct_archive, "_get_covered_episodes"):
                        covered_set = await direct_archive._get_covered_episodes(client, tb_key, job_item["imdb"])
                        covered = _is_episode_covered(covered_set, season, episode)

                    if not covered:
                        d_url = cand["url"] if cand["is_direct"] and not cand["is_hls"] else None
                        tb_res = await direct_archive.upload_local_file_to_torbox(
                            filepath=final_path,
                            media_id=media_id,
                            title_name=title,
                            probe=probe,
                            direct_url=d_url,
                            source=cand["source"],
                            pieces=pieces,
                            total_size=actual_size,
                            dubbed=cand["is_pt"],
                        )
                        if tb_res and tb_res.get("info_hash"):
                            entry["torbox_hash"] = tb_res["info_hash"]
                            idx["items"][media_id] = entry
                            _save_index(idx)
                except Exception as e:
                    logger.warning(f"offline_library: falha ao subir {media_id} pro TorBox: {e}")

        return {"status": "ok", "media_id": media_id, "path": final_path, "size": actual_size}

    except asyncio.CancelledError:
        if os.path.exists(part_path):
            try:
                os.unlink(part_path)
            except Exception:
                pass
        raise
    except Exception as e:
        if os.path.exists(part_path):
            try:
                os.unlink(part_path)
            except Exception:
                pass
        logger.exception(f"offline_library: erro durante download de {media_id}: {e}")
        return {"status": "error", "media_id": media_id, "error": str(e)}


async def _worker_loop() -> None:
    global _CURRENT_JOB, _CANCEL_FLAG, _JOB_QUEUE
    lock = _get_worker_lock()
    async with lock:
        while _JOB_QUEUE and not _CANCEL_FLAG:
            job = _JOB_QUEUE.pop(0)
            _CURRENT_JOB = job
            job["active"] = True
            title = job.get("title", "")
            items = job.get("items", [])
            total_items = len(items)
            job["total_items"] = total_items
            job["done_items"] = 0

            downloaded_cnt = 0
            exists_cnt = 0
            torbox_cnt = 0
            not_found_cnt = 0
            failed_cnt = 0
            low_disk_hit = False
            low_disk_free_gb = 0.0

            season_stats: dict[int, dict] = {}
            announced_seasons: set[int] = set()
            last_s_num: int | None = None

            if httpx is None:
                logger.error("offline_library: httpx não está disponível para o worker")
                break

            async def _announce_season(s_key: int, partial: bool = False) -> None:
                if s_key in announced_seasons:
                    return
                s_info = season_stats.get(s_key, {})
                s_ok = s_info.get("ok", 0)
                s_ex = s_info.get("exist", 0)
                s_tb = s_info.get("tb", 0)
                s_fail = s_info.get("fail", 0)
                if s_ok > 0:
                    announced_seasons.add(s_key)
                    extra_fail = f"\n• Falhas: {s_fail}" if s_fail > 0 else ""
                    hdr = f"parcialmente salva" if partial else "pronta"
                    await send_telegram(
                        f"✅ *{title}* · Temporada {s_key} {hdr} no A71!\n"
                        f"• Baixados: {s_ok} novos ({s_ex} já existiam)\n"
                        f"• Subidos pro TorBox: {s_tb}{extra_fail}"
                    )

            async with httpx.AsyncClient(timeout=60.0) as client:
                for idx, item in enumerate(items):
                    if _CANCEL_FLAG:
                        break

                    s_num = item.get("season")
                    e_num = item.get("episode")
                    last_s_num = s_num
                    label = f"T{s_num}E{e_num}" if s_num and e_num else (f"T{s_num}" if s_num else "Filme")
                    job["current_item_label"] = label

                    res = await _download_item(client, item)
                    job["done_items"] = idx + 1

                    st = res.get("status")
                    if s_num is not None:
                        if s_num not in season_stats:
                            season_stats[s_num] = {"ok": 0, "exist": 0, "tb": 0, "nf": 0, "fail": 0}

                    if st == "ok":
                        downloaded_cnt += 1
                        if s_num is not None:
                            season_stats[s_num]["ok"] += 1
                        entry = get_local_entry(item["media_id"])
                        if entry and entry.get("torbox_hash"):
                            torbox_cnt += 1
                            if s_num is not None:
                                season_stats[s_num]["tb"] += 1
                    elif st == "skipped_exists":
                        exists_cnt += 1
                        if s_num is not None:
                            season_stats[s_num]["exist"] += 1
                    elif st == "low_disk":
                        low_disk_hit = True
                        low_disk_free_gb = float(res.get("free_gb") or 0.0)
                        break
                    elif st == "cancelled":
                        break
                    elif st in ("not_found", "no_candidate"):
                        not_found_cnt += 1
                        if s_num is not None:
                            season_stats[s_num]["nf"] += 1
                    else:
                        failed_cnt += 1
                        if s_num is not None:
                            season_stats[s_num]["fail"] += 1

                    is_last_item = (idx + 1 == len(items))
                    next_s = items[idx + 1].get("season") if not is_last_item else None
                    if s_num is not None and (is_last_item or next_s != s_num):
                        await _announce_season(s_num, partial=False)

                    await asyncio.sleep(2.0)

            if (low_disk_hit or _CANCEL_FLAG) and last_s_num is not None:
                await _announce_season(last_s_num, partial=True)

            _, free_gb = has_enough_disk_space(OFFLINE_DIR)
            if low_disk_hit:
                dropped_titles = [str(j.get("title") or j.get("imdb") or "item") for j in _JOB_QUEUE]
                dropped_cnt = len(dropped_titles)
                _JOB_QUEUE.clear()
                dropped_line = (
                    f"\n• Outros pedidos removidos da fila ({dropped_cnt}): {', '.join(dropped_titles[:5])}"
                    if dropped_cnt > 0
                    else ""
                )
                await send_telegram(
                    f"⚠️ Download de *{title}* interrompido: pouco espaço livre no A71 "
                    f"({low_disk_free_gb:.1f} GB livres; mínimo {MIN_FREE_GB:.0f} GB).\n"
                    f"• Baixados antes de parar: {downloaded_cnt}\n"
                    f"• Já existiam: {exists_cnt}{dropped_line}\n"
                    "Use `/apagar <título>` para liberar espaço."
                )
                _CURRENT_JOB = None
                break
            elif _CANCEL_FLAG:
                await send_telegram(f"⏹️ Download de *{title}* cancelado pelo usuário.")
                _CURRENT_JOB = None
                break
            else:
                fail_line = f"\n• Falhas (erro/timeout/validação): {failed_cnt}" if failed_cnt > 0 else ""
                await send_telegram(
                    f"🎉 Download concluído: *{title}*\n"
                    f"• Novos baixados: {downloaded_cnt}\n"
                    f"• Já existiam no A71: {exists_cnt}\n"
                    f"• Subidos pro TorBox: {torbox_cnt}\n"
                    f"• Não encontrados: {not_found_cnt}{fail_line}\n"
                    f"• Espaço livre no A71: {free_gb:.1f} GB"
                )

            _CURRENT_JOB = None

        if _CANCEL_FLAG:
            _JOB_QUEUE.clear()
            _CURRENT_JOB = None
            _CANCEL_FLAG = False


async def start_service() -> None:
    os.makedirs(OFFLINE_DIR, exist_ok=True)
    tmp_dir = os.path.join(OFFLINE_DIR, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    idx = _load_index()
    items = idx.get("items", {})
    valid_cnt = sum(1 for v in items.values() if os.path.isfile(v.get("path", "")))
    logger.info(f"offline_library: serviço iniciado. {valid_cnt} itens locais no A71.")


HELP = (
    "📥 *Biblioteca Offline no A71*\n"
    "• `/baixar <filme ou série> [T5 | T5E01 | original]` — Baixa no A71 e sobe pro TorBox\n"
    "• `/baixados` ou `/offline` — Lista todo o conteúdo salvo no celular\n"
    "• `/cancelar` ou `/parar` — Interrompe o download em andamento\n"
    "• `/apagar <título> [T5]` — Apaga arquivos locais e libera espaço no A71\n"
    "💡 Dica: você também pode mandar diretamente `baixar 2 broke girls dublado`!"
)


async def status_lines() -> list[str]:
    index = _load_index()
    items = index.get("items", {})
    valid_items = [v for v in items.values() if os.path.isfile(v.get("path", ""))]
    total_size = sum(v.get("size", 0) for v in valid_items)
    size_gb = total_size / (1024**3)
    _, free_gb = has_enough_disk_space(OFFLINE_DIR)

    if _CURRENT_JOB and _CURRENT_JOB.get("active"):
        cur = _CURRENT_JOB
        title = cur.get("title", "")
        total = cur.get("total_items", 0)
        done = cur.get("done_items", 0)
        lbl = cur.get("current_item_label", "")
        lbl_str = f" · {lbl}" if lbl else ""
        return [f"📥 Baixando no A71: *{title}* ({done}/{total} eps{lbl_str}) · {free_gb:.0f} GB livres"]

    if not valid_items:
        return []
    return [f"💾 Offline A71: {len(valid_items)} arq ({size_gb:.1f} GB · {free_gb:.0f} GB livres)"]


async def _resolve_media_tmdb(client: httpx.AsyncClient, query: str, imdb: str | None) -> dict | None:
    tok = _get_tmdb_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    base = "https://api.themoviedb.org/3"
    try:
        if imdb:
            resp = await client.get(f"{base}/find/{imdb}", params={"external_source": "imdb_id"}, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                tv_res = data.get("tv_results") or []
                if tv_res:
                    return {
                        "imdb": imdb,
                        "title": tv_res[0].get("name") or imdb,
                        "media_type": "series",
                        "tmdb_id": tv_res[0]["id"],
                    }
                mov_res = data.get("movie_results") or []
                if mov_res:
                    return {
                        "imdb": imdb,
                        "title": mov_res[0].get("title") or imdb,
                        "media_type": "movie",
                        "tmdb_id": mov_res[0]["id"],
                    }

        if query:
            resp = await client.get(f"{base}/search/multi", params={"query": query, "language": "pt-BR"}, headers=headers)
            if resp.status_code == 200:
                results = resp.json().get("results") or []
                for item in results:
                    m_type = item.get("media_type")
                    if m_type == "tv":
                        tv_id = item["id"]
                        title = item.get("name") or query
                        ext_r = await client.get(f"{base}/tv/{tv_id}/external_ids", headers=headers)
                        ext_imdb = ext_r.json().get("imdb_id") if ext_r.status_code == 200 else None
                        if ext_imdb:
                            return {
                                "imdb": ext_imdb,
                                "title": title,
                                "media_type": "series",
                                "tmdb_id": tv_id,
                            }
                    elif m_type == "movie":
                        mov_id = item["id"]
                        title = item.get("title") or query
                        ext_r = await client.get(f"{base}/movie/{mov_id}/external_ids", headers=headers)
                        ext_imdb = ext_r.json().get("imdb_id") if ext_r.status_code == 200 else None
                        if ext_imdb:
                            return {
                                "imdb": ext_imdb,
                                "title": title,
                                "media_type": "movie",
                                "tmdb_id": mov_id,
                            }
    except Exception as e:
        logger.warning(f"offline_library: erro ao resolver no TMDB: {e}")
    return None


async def _get_series_episodes(client: httpx.AsyncClient, imdb: str) -> list[tuple[int, int]]:
    if direct_archive and hasattr(direct_archive, "_get_series_episodes_from_tmdb"):
        eps = await direct_archive._get_series_episodes_from_tmdb(client, imdb)
        if eps:
            return eps

    tok = _get_tmdb_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    base = "https://api.themoviedb.org/3"
    try:
        resp = await client.get(f"{base}/find/{imdb}", params={"external_source": "imdb_id"}, headers=headers)
        tv_results = resp.json().get("tv_results") or []
        if not tv_results:
            return []
        tv_id = tv_results[0]["id"]
        tv_resp = await client.get(f"{base}/tv/{tv_id}", headers=headers)
        seasons = tv_resp.json().get("seasons") or []
        today_str = datetime.date.today().isoformat()
        episodes_list: list[tuple[int, int]] = []
        for s_info in seasons:
            s_num = int(s_info.get("season_number") or 0)
            if s_num <= 0:
                continue
            try:
                s_resp = await client.get(f"{base}/tv/{tv_id}/season/{s_num}", headers=headers)
                s_data = s_resp.json()
                for ep in s_data.get("episodes") or []:
                    e_num = int(ep.get("episode_number") or 0)
                    air_date = ep.get("air_date") or ""
                    if air_date and air_date > today_str:
                        continue
                    if e_num > 0:
                        episodes_list.append((s_num, e_num))
            except Exception:
                cnt = int(s_info.get("episode_count") or 0)
                for ep_i in range(1, cnt + 1):
                    episodes_list.append((s_num, ep_i))
        return episodes_list
    except Exception as e:
        logger.warning(f"offline_library: erro ao buscar episódios de {imdb}: {e}")
        return []


async def handle_command(text: str) -> str | None:
    global _CANCEL_FLAG, _JOB_QUEUE, _CURRENT_JOB, _WORKER_TASK
    parsed = parse_download_command(text)
    if not parsed:
        return None

    action = parsed["action"]

    if action == "status":
        lines = await status_lines()
        if lines:
            return "\n".join(lines)
        _, free_gb = has_enough_disk_space(OFFLINE_DIR)
        return f"💾 Nenhum download ativo no momento. {free_gb:.0f} GB livres no A71."

    if action == "list":
        index = _load_index()
        valid_items = [v for v in index.get("items", {}).values() if os.path.isfile(v.get("path", ""))]
        _, free_gb = has_enough_disk_space(OFFLINE_DIR)
        if not valid_items:
            return f"💾 Nenhum arquivo baixado offline no A71. ({free_gb:.1f} GB livres)"

        series_map: dict[str, dict] = {}
        movies_list: list[dict] = []
        for v in valid_items:
            if v.get("media_type") == "series" or v.get("season") is not None:
                title = v.get("title") or v.get("imdb") or "Série"
                if title not in series_map:
                    series_map[title] = {"count": 0, "size": 0, "seasons": {}}
                series_map[title]["count"] += 1
                series_map[title]["size"] += v.get("size", 0)
                s = v.get("season") or 1
                series_map[title]["seasons"][s] = series_map[title]["seasons"].get(s, 0) + 1
            else:
                movies_list.append(v)

        lines = ["💾 *Conteúdo Offline no A71:*"]
        if series_map:
            for title, s_info in sorted(series_map.items()):
                cnt = s_info["count"]
                sz_gb = s_info["size"] / (1024**3)
                s_counts = s_info["seasons"]
                s_str = ", ".join(f"T{s}: {s_counts[s]}" for s in sorted(s_counts))
                lines.append(f"• *{title}*: {cnt} eps ({s_str}) · {sz_gb:.1f} GB")

        if movies_list:
            for m in sorted(movies_list, key=lambda x: x.get("title", "")):
                title = m.get("title") or m.get("imdb") or "Filme"
                h = int(m.get("height") or 0)
                res = "4K" if h >= 1800 else "1080p" if h >= 900 else "720p" if h >= 650 else "480p"
                dub = "🇧🇷 Dublado" if m.get("dubbed", True) else "🎬 Original"
                sz_gb = (m.get("size") or 0) / (1024**3)
                lines.append(f"• *{title}* ({res} · {dub}) · {sz_gb:.1f} GB")

        lines.append(f"\nEspaço livre no A71: {free_gb:.1f} GB")
        return "\n".join(lines)

    if action == "cancel":
        _CANCEL_FLAG = True
        cnt = len(_JOB_QUEUE)
        _JOB_QUEUE.clear()
        tmp_dir = os.path.join(OFFLINE_DIR, "tmp")
        if os.path.isdir(tmp_dir):
            for f in os.listdir(tmp_dir):
                if f.endswith(".part"):
                    try:
                        os.unlink(os.path.join(tmp_dir, f))
                    except Exception:
                        pass
        return f"⏹️ Downloads cancelados ({cnt} itens removidos da fila)."

    if action == "delete":
        index = _load_index()
        items = index.get("items", {})
        query = parsed.get("query", "").strip()
        imdb = parsed.get("imdb")
        season = parsed.get("season")
        episode = parsed.get("episode")

        matching: list[tuple[str, dict]] = []
        q_norm = unicodedata.normalize("NFKD", query.lower()).encode("ascii", "ignore").decode("ascii") if query else ""

        for mid, entry in items.items():
            matched = False
            e_imdb = (entry.get("imdb") or "").lower()
            e_title = entry.get("title") or ""
            t_norm = unicodedata.normalize("NFKD", e_title.lower()).encode("ascii", "ignore").decode("ascii")

            if imdb and e_imdb == imdb:
                matched = True
            elif q_norm and (q_norm in t_norm or t_norm in q_norm):
                matched = True

            if matched:
                if season is not None and entry.get("season") != season:
                    matched = False
                if episode is not None and entry.get("episode") != episode:
                    matched = False

            if matched:
                matching.append((mid, entry))

        if not matching:
            return f"❌ Nenhum arquivo encontrado para apagar correspondente a '{query or imdb}'."

        deleted_cnt = 0
        freed_bytes = 0
        for mid, entry in matching:
            p = entry.get("path")
            if p and os.path.isfile(p):
                try:
                    freed_bytes += os.path.getsize(p)
                    os.unlink(p)
                    deleted_cnt += 1
                    parent = os.path.dirname(p)
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                        gp = os.path.dirname(parent)
                        if os.path.isdir(gp) and not os.listdir(gp):
                            os.rmdir(gp)
                except Exception as e:
                    logger.warning(f"offline_library: erro ao apagar {p}: {e}")
            items.pop(mid, None)

        _save_index(index)
        freed_gb = freed_bytes / (1024**3)
        return (
            f"🗑️ *{deleted_cnt}* arquivo(s) apagado(s) do A71 ({freed_gb:.1f} GB liberados)!\n"
            "💡 As cópias no TorBox continuam seguras na nuvem."
        )

    if action == "download":
        query = parsed.get("query", "").strip()
        imdb = parsed.get("imdb")
        season = parsed.get("season")
        episode = parsed.get("episode")
        prefer_dub = bool(parsed.get("prefer_dub", True))

        if httpx is None:
            return "❌ Dependência httpx indisponível."

        async with httpx.AsyncClient(timeout=15.0) as client:
            media_info = await _resolve_media_tmdb(client, query, imdb)
            if not media_info:
                if imdb:
                    media_info = {
                        "imdb": imdb,
                        "title": query or imdb,
                        "media_type": "series" if (season or episode) else "movie",
                    }
                else:
                    return f"❌ Não encontrei '{query}' no TMDB. Tente com o nome exato ou ID do IMDb (`tt...`)."

            m_imdb = media_info["imdb"]
            title = media_info["title"]
            media_type = media_info["media_type"]

            items_to_download: list[dict] = []
            if media_type == "series":
                all_eps = await _get_series_episodes(client, m_imdb)
                if not all_eps:
                    if season is not None:
                        count = 24 if episode is None else episode
                        all_eps = [(season, ep_i) for ep_i in range(1, count + 1)]
                    else:
                        all_eps = [(1, 1)]

                for s_num, e_num in all_eps:
                    if season is not None and s_num != season:
                        continue
                    if episode is not None and e_num != episode:
                        continue
                    m_id = f"{m_imdb}:{s_num}:{e_num}"
                    items_to_download.append({
                        "media_id": m_id,
                        "media_type": "series",
                        "imdb": m_imdb,
                        "title": title,
                        "season": s_num,
                        "episode": e_num,
                        "prefer_dub": prefer_dub,
                        "upload_torbox": True,
                    })
            else:
                items_to_download.append({
                    "media_id": m_imdb,
                    "media_type": "movie",
                    "imdb": m_imdb,
                    "title": title,
                    "season": None,
                    "episode": None,
                    "prefer_dub": prefer_dub,
                    "upload_torbox": True,
                })

        if not items_to_download:
            return f"❌ Nenhum episódio correspondente ao filtro ({f'T{season}' if season else ''}{f'E{episode}' if episode else ''}) foi encontrado."

        existing_count = sum(1 for it in items_to_download if get_local_entry(it["media_id"]))
        missing_count = len(items_to_download) - existing_count

        job = {
            "title": title,
            "imdb": m_imdb,
            "media_type": media_type,
            "prefer_dub": prefer_dub,
            "items": items_to_download,
            "total_items": len(items_to_download),
            "done_items": 0,
            "active": False,
        }

        if _CURRENT_JOB is None:
            _CANCEL_FLAG = False
        _JOB_QUEUE.append(job)

        try:
            loop = asyncio.get_running_loop()
            if _WORKER_TASK is None or _WORKER_TASK.done():
                _WORKER_TASK = loop.create_task(_worker_loop())
        except RuntimeError:
            pass

        _, free_gb = has_enough_disk_space(OFFLINE_DIR)
        lang_str = "Dublado (PT-BR)" if prefer_dub else "Original"

        if media_type == "series":
            filt_str = ""
            if season and episode:
                filt_str = f" T{season}E{episode:02d}"
            elif season:
                filt_str = f" Temporada {season}"
            return (
                f"📥 *{title}{filt_str}* adicionado à fila offline!\n"
                f"• Episódios: {missing_count} para baixar ({existing_count} já no A71)\n"
                f"• Áudio: {lang_str}\n"
                f"• Espaço livre no A71: {free_gb:.1f} GB\n"
                "O download roda no A71 e você será avisado ao concluir cada temporada."
            )
        else:
            return (
                f"📥 Filme *{title}* adicionado à fila offline!\n"
                f"• Status: {'Já no A71' if existing_count > 0 else 'Iniciando download'}\n"
                f"• Áudio: {lang_str}\n"
                f"• Espaço livre no A71: {free_gb:.1f} GB"
            )

    return None
