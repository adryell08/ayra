"""Histórico de conversas: lê, busca, retoma e apaga a partir de logs/sessoes.jsonl (só texto, nunca imagem)."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG_DIR = Path(__file__).resolve().parent / "logs"
FILE = "sessoes.jsonl"
_lock = threading.Lock()
_cache: Dict[str, object] = {"key": None, "rows": []}


def new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _path(log_dir: Optional[Path] = None) -> Path:
    return (log_dir or LOG_DIR) / FILE


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in text if unicodedata.category(c) != "Mn")).strip()


def _read(log_dir: Optional[Path] = None) -> List[dict]:
    path = _path(log_dir)
    rows: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _sid(row: dict) -> str:
    """Registros antigos (sem id) viram uma conversa por dia."""
    return row.get("sessao") or "legado-" + str(row.get("quando", ""))[:10]


def _is_turn(row: dict) -> bool:
    return bool(row.get("pergunta") or row.get("resposta")) and "erro" not in row


def list_sessions(query: str = "", log_dir: Optional[Path] = None, limit: int = 40) -> List[dict]:
    """Conversas, da mais recente para a mais antiga: {id, title, when, turns}. `query` filtra por pergunta/resposta."""
    path = _path(log_dir)
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    with _lock:
        if _cache["key"] != key:
            groups: Dict[str, dict] = {}
            for row in _read(log_dir):
                if not _is_turn(row):
                    continue
                g = groups.setdefault(_sid(row), dict(id=_sid(row), title="", when=row.get("quando", ""), last=row.get("quando", ""),
                                                      turns=0, text=[]))
                g["turns"] += 1
                g["last"] = row.get("quando", g["last"])
                g["title"] = g["title"] or " ".join((row.get("pergunta") or row.get("resposta") or "").split())[:48]
                g["text"].append(_norm((row.get("pergunta") or "") + " " + (row.get("resposta") or "")))
            _cache["key"] = key
            _cache["rows"] = sorted(groups.values(), key=lambda g: g["last"], reverse=True)
        rows = list(_cache["rows"])
    q = _norm(query)
    if q:
        rows = [g for g in rows if all(word in " ".join(g["text"]) for word in q.split())]
    return [dict(id=g["id"], title=g["title"] or "(sem texto)", when=g["when"], turns=g["turns"]) for g in rows[:limit]]


def load(session_id: str, log_dir: Optional[Path] = None) -> List[Tuple[str, str]]:
    """Trocas (pergunta, resposta) da conversa, em ordem."""
    return [((r.get("pergunta") or "").strip(), (r.get("resposta") or "").strip())
            for r in _read(log_dir) if _is_turn(r) and _sid(r) == session_id]


def to_markdown(session_id: str, log_dir: Optional[Path] = None) -> str:
    """Conversa em Markdown para colar numa revisão: trocas em ordem, com emoção, ação, modelo, tempos e avisos de cada resposta."""
    rows = [r for r in _read(log_dir) if _is_turn(r) and _sid(r) == session_id]
    if not rows:
        return ""
    out = [f"# Conversa {session_id}", "", f"- Início: {rows[0].get('quando', '')}", f"- Trocas: {len(rows)}", ""]
    for i, r in enumerate(rows, 1):
        out += [f"## Troca {i} · {r.get('quando', '')}", ""]
        if (r.get("pergunta") or "").strip():
            out += [f"**Pessoa:** {r['pergunta'].strip()}", ""]
        if (r.get("resposta") or "").strip():
            out += [f"**Ayra:** {r['resposta'].strip()}", ""]
        meta = []
        if r.get("emocao"):
            meta.append(f"emoção={r['emocao']}")
        if r.get("acao"):
            meta.append(f"ação={r['acao']}")
        if r.get("modelo"):
            meta.append(f"modelo={r['modelo']}")
        t = r.get("tempos")
        if isinstance(t, dict) and t:
            meta.append("tempos(s): " + ", ".join(f"{k}={v}" for k, v in t.items()))
        if r.get("aviso"):
            meta.append(f"aviso={r['aviso']}")
        if meta:
            out += ["_" + " · ".join(meta) + "_", ""]
    return "\n".join(out).rstrip() + "\n"


def delete(session_id: str, log_dir: Optional[Path] = None) -> int:
    """Remove a conversa do arquivo (reescrita atômica). Devolve quantos registros saíram."""
    path = _path(log_dir)
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return 0
        keep, removed = [], 0
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if isinstance(row, dict) and _sid(row) == session_id:
                removed += 1
            else:
                keep.append(line)
        if removed:
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            os.replace(tmp, path)
            _cache["key"] = None
        return removed
