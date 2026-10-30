"""Memória persistente do Nuria: eventos, instruções vigentes, preferências e busca (SQLite local, logs/memoria.db).

Princípios (ver docs/MEMORIA.md):
- Nunca bloqueia voz nem controle: toda escrita vai para uma thread própria; se o banco falhar, o app segue sem memória.
- Sem LLM por turno: instruções, preferências e revogações vêm de regras explícitas ("lembre-se que…", "meu nome é…",
  "esqueça isso"). Preferências ficam como *candidatas* (não verificadas); só `validate_memory` as torna conhecimento global.
- Tudo é separado por usuário (`user_id`). Texto do usuário nunca vira fato técnico global.
- O áudio do operador não é transcrito: registra-se quando ele falou, o que digitou e as instruções que enviou.
"""
from __future__ import annotations

import json
import queue
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
LOG_DIR = Path(__file__).resolve().parent / "logs"
DEFAULT_PATH = LOG_DIR / "memoria.db"
ACTORS = ("user", "ai", "operator", "system")


# ---------------------------------------------------------------------------
# texto
# ---------------------------------------------------------------------------
def fold(text: str) -> str:
    """Minúsculas, sem acento e sem pontuação, **mantendo o comprimento** (índices valem no texto original)."""
    out = []
    for ch in text or "":
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn").lower()
        out.append(base[0] if base and base[0].isalnum() else " ")
    return "".join(out)


def norm(text: str) -> str:
    return " ".join(fold(text).split())


def _tokens(text: str) -> set:
    return {t for t in norm(text).split() if len(t) > 3}


def _clip(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    text = re.split(r"[.!?;\n]", text)[0].strip(" ,:-") or text
    return text[:n]


# ---------------------------------------------------------------------------
# extração por regras (frases explícitas; perguntas não contam)
# ---------------------------------------------------------------------------
_INSTR = [
    re.compile(r"\b(?:a partir de agora|de agora em diante|daqui pra frente|daqui para frente)\b[\s,:]*(.+)", re.S),
    re.compile(r"\blembre\s+se\s+(?:de\s+)?(?:que\s+)?(.+)", re.S),
    re.compile(r"\blembre\s+(?:de|que)\s+(.+)", re.S),
    re.compile(r"\bnao\s+esqueca\s+(?:de\s+)?(?:que\s+)?(.+)", re.S),
    re.compile(r"\b(?:guarde|memorize)\s+(?:isso|que)[\s,:]*(.+)", re.S),
    re.compile(r"^\s*((?:sempre|nunca)\s+.+)", re.S),
]
_PREF = [
    (re.compile(r"\bmeu\s+nome\s+e\s+(.+)"), lambda m, o: "Nome do usuário: " + _clip(o[m.start(1):])),
    (re.compile(r"\bme\s+chame?\s+de\s+(.+)"), lambda m, o: "Prefere ser chamado de " + _clip(o[m.start(1):])),
    (re.compile(r"\beu\s+(?:gosto|prefiro|adoro|amo|odeio|detesto)\s+.+"), lambda m, o: "Usuário disse: " + _clip(o[m.start():])),
    (re.compile(r"\bmeu\s+\w+\s+favorit[oa]\s+e\s+.+"), lambda m, o: "Usuário disse: " + _clip(o[m.start():])),
]
_REVOKE_ALL = re.compile(r"\besqueca\s+tudo\b")
_REVOKE_LAST = re.compile(r"\b(?:esqueca\s+(?:isso|aquilo|a\s+instrucao|a\s+ultima)|(?:cancele|ignore)\s+(?:a\s+|essa\s+)?(?:instrucao|regra))")
_REVOKE_OF = re.compile(r"\b(?:(?:pode\s+parar|nao\s+precisa\s+mais|pare)\s+de|(?:esqueca|cancele|ignore)\s+(?:a\s+|as\s+)?"
                        r"(?:instrucao|instrucoes|regra|regras)\s+(?:sobre|de|do|da))\s+(.+)")


def _extract_sentence(original: str, out: Dict[str, list]) -> None:
    f = fold(original)
    if _REVOKE_ALL.search(f):
        out["revoke_all"] = True
        return
    m = _REVOKE_OF.search(f)
    if m:
        out["revoke_of"].append(original[m.start(1):].strip(" .!,"))
        return
    if _REVOKE_LAST.search(f):
        out["revoke_last"] = True
        return
    for rx in _INSTR:
        m = rx.search(f)
        if m and len(norm(original[m.start(1):])) >= 4:
            out["instructions"].append(_clip(original[m.start(1):], 200))
            break
    for rx, build in _PREF:
        m = rx.search(f)
        if m:
            out["preferences"].append(build(m, original))


def extract(text: str) -> Dict[str, list]:
    """{'instructions': [..], 'preferences': [..], 'revoke_all': bool, 'revoke_last': bool, 'revoke_of': [..]}.
    Frase a frase; frases com "?" são perguntas e não ensinam nada."""
    out = {"instructions": [], "preferences": [], "revoke_all": False, "revoke_last": False, "revoke_of": []}
    for sentence in re.findall(r"[^.!?\n]+[.!?]*", " ".join((text or "").split())):
        if sentence.strip() and not sentence.rstrip().endswith("?"):
            _extract_sentence(sentence.strip(), out)
    return out


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------
_MIGRATIONS = {
    1: """
    CREATE TABLE users(id TEXT PRIMARY KEY, consent_flags TEXT, created_at REAL);
    CREATE TABLE robots(id TEXT PRIMARY KEY, created_at REAL);
    CREATE TABLE sessions(id TEXT PRIMARY KEY, user_id TEXT, robot_id TEXT, mode TEXT, started_at REAL, ended_at REAL);
    CREATE TABLE events(id INTEGER PRIMARY KEY, session_id TEXT, timestamp REAL, actor_type TEXT, event_type TEXT,
                        text TEXT, source TEXT, metadata_json TEXT, event_key TEXT, UNIQUE(session_id, event_key));
    CREATE INDEX events_session_ts ON events(session_id, timestamp);
    CREATE INDEX events_ts ON events(timestamp);
    CREATE TABLE instructions(id INTEGER PRIMARY KEY, user_id TEXT, session_id TEXT, instruction TEXT, norm TEXT,
                              status TEXT, priority INTEGER, actor TEXT, created_at REAL, updated_at REAL);
    CREATE INDEX instructions_user_status ON instructions(user_id, status);
    CREATE TABLE memories(id INTEGER PRIMARY KEY, owner_scope TEXT, owner_id TEXT, kind TEXT, content TEXT, norm TEXT,
                          source_event_id INTEGER, confidence REAL, validation_status TEXT, created_at REAL, updated_at REAL,
                          UNIQUE(owner_scope, owner_id, kind, norm));
    CREATE TABLE memory_links(source_memory_id INTEGER, target_memory_id INTEGER, relation_type TEXT);
    CREATE TABLE feedback(id INTEGER PRIMARY KEY, memory_id INTEGER, outcome TEXT, reviewer TEXT, timestamp REAL);
    CREATE TABLE jobs(id INTEGER PRIMARY KEY, kind TEXT, status TEXT, attempts INTEGER, created_at REAL);
    """,
}


class Memory:
    def __init__(self, path: Optional[Path] = None, user_id: str = "local", robot_id: str = "nuria",
                 enabled: bool = True, retention_days: int = 90):
        self.path = Path(path or DEFAULT_PATH)
        self.user_id, self.robot_id = user_id, robot_id
        self.enabled = bool(enabled)
        self.retention_days = int(retention_days)
        self.healthy = True
        self.last_error = ""
        self.fts = False
        self.errors = 0
        self.dropped = 0
        self.written = 0
        self.ctx_ms = 0.0
        self.version = 0                         # muda a cada escrita: o painel só recarrega se mudou
        self._write_ms: List[float] = []
        self._q: "queue.Queue" = queue.Queue(maxsize=2000)
        self._worker: Optional[threading.Thread] = None
        self._wlock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        if self.enabled:
            self._init_db()

    # ---- infraestrutura ---------------------------------------------------------
    def _fail(self, error) -> None:
        self.errors += 1
        self.last_error = str(error)[:200]
        if self.errors >= 5:
            self.healthy = False                       # sem memória, mas o app (voz e controle) segue normal

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=2.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error:
                pass
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for v in sorted(_MIGRATIONS):
                if v > version:
                    with conn:
                        for stmt in [s for s in _MIGRATIONS[v].split(";") if s.strip()]:
                            conn.execute(stmt)
                        conn.execute(f"PRAGMA user_version={v}")
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, kind UNINDEXED, ref UNINDEXED, "
                             "owner UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
                self.fts = True
            except sqlite3.Error:
                self.fts = False
            now = time.time()
            with conn:
                conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)",
                             (self.user_id, json.dumps({"memoria": True, "transcricao": True}), now))
                conn.execute("INSERT OR IGNORE INTO robots VALUES(?,?)", (self.robot_id, now))
            self._conn = conn
        except (sqlite3.Error, OSError) as error:
            self.healthy = False
            self.last_error = str(error)[:200]
            self._conn = None

    @property
    def active(self) -> bool:
        return self.enabled and self.healthy and self._conn is not None

    def _submit(self, fn) -> bool:
        if not self.active:
            return False
        with self._wlock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="memoria", daemon=True)
                self._worker.start()
        try:
            self._q.put_nowait(fn)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _work(self) -> None:
        if self.retention_days > 0:
            try:
                self._purge(self._conn, self.retention_days)
            except sqlite3.Error as error:
                self._fail(error)
        while True:
            fn = self._q.get()
            if fn is None:
                self._q.task_done()
                return
            t = time.perf_counter()
            try:
                with self._conn:
                    fn(self._conn)
                self.written += 1
                self.version += 1
                self.errors = 0
            except Exception as error:                # nunca derruba a thread nem o app
                self._fail(error)
            self._write_ms = (self._write_ms + [(time.perf_counter() - t) * 1000])[-200:]
            self._q.task_done()

    def flush(self, timeout: float = 1.0) -> bool:
        """Espera a fila esvaziar (usar fora do caminho de áudio: ao montar contexto, em testes)."""
        end = time.perf_counter() + timeout
        while self._q.unfinished_tasks and time.perf_counter() < end:
            time.sleep(0.005)
        return not self._q.unfinished_tasks

    def close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self._q.put(None)
            self._worker.join(2.0)

    def _read(self, sql: str, args=()) -> List[sqlite3.Row]:
        if not self.active:
            return []
        try:
            conn = self._connect()
            try:
                return conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as error:
            self._fail(error)
            return []

    def stats(self) -> dict:
        w = sorted(self._write_ms)
        return dict(enabled=self.enabled, healthy=self.healthy, written=self.written, dropped=self.dropped,
                    queued=self._q.unfinished_tasks, errors=self.errors, fts=self.fts, last_error=self.last_error,
                    write_p50_ms=round(w[len(w) // 2], 2) if w else 0.0,
                    write_p95_ms=round(w[int(len(w) * .95)], 2) if w else 0.0, context_ms=round(self.ctx_ms, 2))

    # ---- escrita (assíncrona) ----------------------------------------------------
    def _index(self, conn, text: str, kind: str, ref, owner: str) -> None:
        if self.fts and text:
            conn.execute("INSERT INTO fts(text, kind, ref, owner) VALUES(?,?,?,?)", (text, kind, str(ref), owner))

    def append_event(self, session_id: str, actor_type: str, event_type: str, text: str = "", source: str = "",
                     metadata: Optional[dict] = None, event_key: Optional[str] = None, mode: str = "",
                     user_id: Optional[str] = None) -> bool:
        """Registra um evento normalizado. `event_key` evita duplicar na reconexão (único por sessão)."""
        if not self.active or actor_type not in ACTORS:
            return False
        uid = user_id or self.user_id
        text = (text or "")[:2000]
        ts = time.time()
        meta = json.dumps(metadata or {}, ensure_ascii=False)

        def job(conn):
            conn.execute("INSERT OR IGNORE INTO sessions(id,user_id,robot_id,mode,started_at) VALUES(?,?,?,?,?)",
                         (session_id, uid, self.robot_id, mode, ts))
            cur = conn.execute("INSERT OR IGNORE INTO events(session_id,timestamp,actor_type,event_type,text,source,"
                               "metadata_json,event_key) VALUES(?,?,?,?,?,?,?,?)",
                               (session_id, ts, actor_type, event_type, text, source, meta, event_key))
            if not cur.rowcount:
                return                                      # duplicado (reconexão): nada a fazer
            event_id = cur.lastrowid
            if text and actor_type in ("user", "ai", "operator"):
                self._index(conn, text, "event", event_id, uid)
            if text and actor_type in ("user", "operator") and event_type in ("utterance", "text", "say"):
                self._learn(conn, uid, session_id, actor_type, text, event_id)
        return self._submit(job)

    def _learn(self, conn, uid: str, session_id: str, actor: str, text: str, event_id: int) -> None:
        found = extract(text)
        if found["revoke_all"]:
            self._revoke(conn, uid, None, all_=True)
        elif found["revoke_last"]:
            self._revoke(conn, uid, None)
        for target in found["revoke_of"]:
            self._revoke(conn, uid, target)
        for ins in found["instructions"]:
            self._upsert_instruction(conn, uid, session_id, ins, actor, 5 if actor == "operator" else 0)
        for pref in found["preferences"]:
            n = norm(pref)
            now = time.time()
            conn.execute("INSERT OR IGNORE INTO memories(owner_scope,owner_id,kind,content,norm,source_event_id,confidence,"
                         "validation_status,created_at,updated_at) VALUES('user',?,'preference',?,?,?,0.6,'candidate',?,?)",
                         (uid, pref, n, event_id, now, now))
            self._index(conn, pref, "memory", event_id, uid)

    def _upsert_instruction(self, conn, uid, session_id, text, actor, priority) -> None:
        n = norm(text)
        if not n:
            return
        now = time.time()
        rows = conn.execute("SELECT id, norm FROM instructions WHERE user_id=? AND status='active'", (uid,)).fetchall()
        mine = set(n.split())
        for row in rows:
            if row["norm"] == n:
                conn.execute("UPDATE instructions SET updated_at=?, priority=MAX(priority,?) WHERE id=?", (now, priority, row["id"]))
                return
        for row in rows:                                    # quase igual = a nova substitui a antiga
            theirs = set(row["norm"].split())
            if mine and theirs and len(mine & theirs) / len(mine | theirs) >= 0.6:
                conn.execute("UPDATE instructions SET status='superseded', updated_at=? WHERE id=?", (now, row["id"]))
        cur = conn.execute("INSERT INTO instructions(user_id,session_id,instruction,norm,status,priority,actor,created_at,updated_at)"
                           " VALUES(?,?,?,?, 'active',?,?,?,?)", (uid, session_id, text[:300], n, priority, actor, now, now))
        self._index(conn, text, "instruction", cur.lastrowid, uid)

    def _revoke(self, conn, uid, target: Optional[str], all_: bool = False) -> None:
        now = time.time()
        if all_:
            conn.execute("UPDATE instructions SET status='revoked', updated_at=? WHERE user_id=? AND status='active'", (now, uid))
            return
        rows = conn.execute("SELECT id, norm FROM instructions WHERE user_id=? AND status='active' ORDER BY updated_at DESC",
                            (uid,)).fetchall()
        if target is None:
            if rows:
                conn.execute("UPDATE instructions SET status='revoked', updated_at=? WHERE id=?", (now, rows[0]["id"]))
            return
        words = _tokens(target)
        for row in rows:
            if words & {t for t in row["norm"].split() if len(t) > 3}:
                conn.execute("UPDATE instructions SET status='revoked', updated_at=? WHERE id=?", (now, row["id"]))

    def upsert_instruction(self, text: str, actor: str = "user", session_id: str = "", priority: Optional[int] = None,
                           user_id: Optional[str] = None) -> bool:
        uid = user_id or self.user_id
        prio = priority if priority is not None else (5 if actor == "operator" else 0)
        return self._submit(lambda conn: self._upsert_instruction(conn, uid, session_id, " ".join((text or "").split())[:300], actor, prio))

    def revoke_instruction(self, text: Optional[str] = None, all_: bool = False, user_id: Optional[str] = None) -> bool:
        uid = user_id or self.user_id
        return self._submit(lambda conn: self._revoke(conn, uid, text, all_))

    # ---- gestão pelo painel (operador ou o próprio usuário) ------------------------------
    def revoke_by_id(self, instruction_id: int, user_id: Optional[str] = None) -> bool:
        uid = user_id or self.user_id
        return self._submit(lambda conn: conn.execute(
            "UPDATE instructions SET status='revoked', updated_at=? WHERE id=? AND user_id=?", (time.time(), int(instruction_id), uid)))

    def edit_instruction(self, instruction_id: int, text: str, user_id: Optional[str] = None) -> bool:
        """Editar = a antiga vira 'superseded' e entra uma nova (fica a proveniência)."""
        uid = user_id or self.user_id
        text = " ".join((text or "").split())[:300]
        if not text:
            return False

        def job(conn):
            row = conn.execute("SELECT session_id, actor, priority FROM instructions WHERE id=? AND user_id=? AND status='active'",
                               (int(instruction_id), uid)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE instructions SET status='superseded', updated_at=? WHERE id=?", (time.time(), int(instruction_id)))
            self._upsert_instruction(conn, uid, row["session_id"], text, row["actor"], row["priority"])
        return self._submit(job)

    def delete_memory(self, memory_id: int, user_id: Optional[str] = None) -> bool:
        uid = user_id or self.user_id
        return self._submit(lambda conn: conn.execute(
            "DELETE FROM memories WHERE id=? AND ((owner_scope='user' AND owner_id=?) OR owner_scope='global')", (int(memory_id), uid)))

    def overview(self, query: str = "", user_id: Optional[str] = None) -> dict:
        """Tudo que o painel mostra, numa leitura só (≈1 ms): instruções, preferências, busca e métricas."""
        uid = user_id or self.user_id
        ins = self.list_active_instructions(uid, limit=50)
        mems = self.list_memories(uid, include_candidates=True, limit=50)
        return dict(version=self.version, instructions=ins, memories=mems,
                    results=self.search_memory(query, uid, limit=20) if query.strip() else [], stats=self.stats())

    def end_session(self, session_id: str) -> bool:
        return self._submit(lambda conn: conn.execute("UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL",
                                                      (time.time(), session_id)))

    def set_session_mode(self, session_id: str, mode: str) -> bool:
        ts = time.time()

        def job(conn):
            conn.execute("INSERT OR IGNORE INTO sessions(id,user_id,robot_id,mode,started_at) VALUES(?,?,?,?,?)",
                         (session_id, self.user_id, self.robot_id, mode, ts))
            conn.execute("UPDATE sessions SET mode=? WHERE id=?", (mode, session_id))
        return self._submit(job)

    def validate_memory(self, memory_id: int, reviewer: str, ok: bool = True) -> bool:
        """Revisão humana: só `validated` com escopo global ganha autoridade de conhecimento técnico."""
        def job(conn):
            conn.execute("UPDATE memories SET validation_status=?, owner_scope=CASE WHEN ? THEN 'global' ELSE owner_scope END,"
                         " updated_at=? WHERE id=?", ("validated" if ok else "rejected", 1 if ok else 0, time.time(), memory_id))
            conn.execute("INSERT INTO feedback(memory_id,outcome,reviewer,timestamp) VALUES(?,?,?,?)",
                         (memory_id, "validated" if ok else "rejected", reviewer, time.time()))
        return self._submit(job)

    # ---- retenção e esquecimento -----------------------------------------------------
    def _drop_fts(self, conn, kind: str, refs: List) -> None:
        if self.fts:
            for ref in refs:
                conn.execute("DELETE FROM fts WHERE kind=? AND ref=?", (kind, str(ref)))

    def _purge(self, conn, days: int) -> int:
        cutoff = time.time() - days * 86400
        ids = [r[0] for r in conn.execute("SELECT id FROM events WHERE timestamp<?", (cutoff,))]
        with conn:
            self._drop_fts(conn, "event", ids)
            conn.execute("DELETE FROM events WHERE timestamp<?", (cutoff,))
        return len(ids)

    def purge_older(self, days: int) -> bool:
        return self._submit(lambda conn: self._purge(conn, days)) if self.active else False

    def forget_session(self, session_id: str) -> bool:
        """Apaga eventos da conversa e o que foi aprendido nela (instruções e preferências ainda não validadas)."""
        def job(conn):
            ev = [r[0] for r in conn.execute("SELECT id FROM events WHERE session_id=?", (session_id,))]
            ins = [r[0] for r in conn.execute("SELECT id FROM instructions WHERE session_id=?", (session_id,))]
            mem = [r[0] for r in conn.execute(
                "SELECT id FROM memories WHERE validation_status!='validated' AND source_event_id IN "
                "(SELECT id FROM events WHERE session_id=?)", (session_id,))]
            self._drop_fts(conn, "event", ev)
            self._drop_fts(conn, "instruction", ins)
            self._drop_fts(conn, "memory", ev)
            conn.execute("DELETE FROM memories WHERE id IN (%s)" % ",".join(map(str, mem or [0])))
            conn.execute("DELETE FROM instructions WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return self._submit(job)

    def forget_all(self, user_id: Optional[str] = None) -> bool:
        uid = user_id or self.user_id

        def job(conn):
            sess = [r[0] for r in conn.execute("SELECT id FROM sessions WHERE user_id=?", (uid,))]
            for sid in sess:
                conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM instructions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM memories WHERE owner_scope='user' AND owner_id=?", (uid,))
            if self.fts:
                conn.execute("DELETE FROM fts WHERE owner=?", (uid,))
        return self._submit(job)

    # ---- leitura -----------------------------------------------------------------------------
    def list_active_instructions(self, user_id: Optional[str] = None, limit: int = 8) -> List[dict]:
        rows = self._read("SELECT id, instruction, actor, priority, updated_at FROM instructions WHERE user_id=? AND status='active' "
                          "ORDER BY priority DESC, updated_at DESC LIMIT ?", (user_id or self.user_id, limit))
        return [dict(r) for r in rows]

    def list_memories(self, user_id: Optional[str] = None, include_candidates: bool = True, limit: int = 20) -> List[dict]:
        uid = user_id or self.user_id
        rows = self._read("SELECT id, owner_scope, kind, content, validation_status, confidence FROM memories "
                          "WHERE (owner_scope='user' AND owner_id=? AND validation_status IN ('candidate','validated')) "
                          "OR (owner_scope='global' AND validation_status='validated') ORDER BY updated_at DESC LIMIT ?", (uid, limit))
        out = [dict(r) for r in rows]
        return out if include_candidates else [m for m in out if m["validation_status"] == "validated"]

    def search_memory(self, query: str, user_id: Optional[str] = None, limit: int = 5) -> List[dict]:
        """Busca textual (FTS5; LIKE se indisponível) em eventos, instruções e preferências do usuário."""
        uid = user_id or self.user_id
        words = [w for w in norm(query).split() if len(w) > 1][:8]
        if not words:
            return []
        rows: List[sqlite3.Row] = []
        if self.fts:
            for joiner in (" AND ", " OR "):
                match = joiner.join('"%s"*' % w.replace('"', "") for w in words)
                rows = self._read("SELECT text, kind, ref FROM fts WHERE fts MATCH ? AND owner=? ORDER BY rank LIMIT ?",
                                  (match, uid, limit))
                if rows:
                    break
        else:
            like = "%" + "%".join(words) + "%"
            rows = self._read("SELECT text, 'event' AS kind, id AS ref FROM events WHERE text LIKE ? AND session_id IN "
                              "(SELECT id FROM sessions WHERE user_id=?) ORDER BY timestamp DESC LIMIT ?", (like, uid, limit))
        return [dict(text=r["text"], kind=r["kind"], ref=r["ref"]) for r in rows]

    def session_summary(self, session_id: str, limit: int = 40) -> dict:
        """Resumo incremental, sem LLM: o que o operador fez e as últimas trocas da sessão."""
        rows = self._read("SELECT actor_type, event_type, text, metadata_json FROM events WHERE session_id=? "
                          "ORDER BY timestamp DESC, id DESC LIMIT ?", (session_id, limit))
        rows = list(reversed(rows))
        op = [r for r in rows if r["actor_type"] == "operator"]
        voice = sum(float((json.loads(r["metadata_json"] or "{}")).get("segundos", 0)) for r in op if r["event_type"] == "voice")
        said = [r["text"] for r in op if r["event_type"] in ("say", "instruction") and r["text"]][-3:]
        last = []
        for r in rows:
            if r["actor_type"] in ("user", "ai") and r["text"]:
                last.append(("Usuário" if r["actor_type"] == "user" else "Você") + ": " + r["text"][:90])
        return dict(operator_events=len(op), operator_voice_s=round(voice), operator_said=said, last=last[-4:])

    def get_session_context(self, session_id: str = "", user_id: Optional[str] = None, handoff: bool = False,
                            budget_chars: int = 1100) -> str:
        """Texto curto para a Realtime: instruções vigentes, preferências (não verificadas), conhecimento validado e,
        na volta do operador, o resumo do que aconteceu. Vazio se não houver nada (a IA não recebe nada)."""
        if not self.active:
            return ""
        t = time.perf_counter()
        uid = user_id or self.user_id
        parts: List[str] = []
        ins = self.list_active_instructions(uid)
        if ins:
            parts.append("Instruções vigentes (dadas pelo usuário ou operador; valem até serem revogadas; em conflito prevalece a primeira): " + "; ".join(
                ("[operador] " if i["actor"] == "operator" else "") + i["instruction"] for i in ins) + ".")
        mems = self.list_memories(uid, include_candidates=True, limit=6)
        validated = [m["content"] for m in mems if m["validation_status"] == "validated"][:3]
        personal = [m["content"] for m in mems if m["validation_status"] != "validated"][:4]
        if personal:
            parts.append("Informado pelo usuário, não verificado: " + "; ".join(personal) + ".")
        if validated:
            parts.append("Conhecimento validado: " + "; ".join(validated) + ".")
        if handoff and session_id:
            s = self.session_summary(session_id)
            if s["operator_events"]:
                bits = []
                if s["operator_voice_s"]:
                    bits.append(f"o operador falou cerca de {s['operator_voice_s']} s (áudio sem transcrição)")
                if s["operator_said"]:
                    bits.append("o operador disse/escreveu: " + " | ".join(s["operator_said"]))
                if s["last"]:
                    bits.append("últimas trocas: " + " | ".join(s["last"]))
                parts.append("Desde a última resposta sua: " + "; ".join(bits) + ". Não contradiga o que o operador decidiu.")
        text = " ".join(parts)
        if len(text) > budget_chars:
            text = text[:budget_chars - 1].rsplit(" ", 1)[0] + "…"
        self.ctx_ms = (time.perf_counter() - t) * 1000
        return text
