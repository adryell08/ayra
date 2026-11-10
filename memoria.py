"""Memória persistente da Ayra: eventos, instruções vigentes, preferências e busca (SQLite local, logs/memoria.db).

Princípios (ver docs/MEMORIA.md):
- Nunca bloqueia voz nem controle: toda escrita vai para uma thread própria; se o banco falhar, o app segue sem memória.
- Sem LLM por turno: instruções, preferências e revogações vêm de regras explícitas ("lembre-se que…", "meu nome é…",
  "esqueça isso"). Preferências ficam como *candidatas* (não verificadas); só `validate_memory` as torna conhecimento global.
- Tudo é separado por usuário (`user_id`). Texto do usuário nunca vira fato técnico global.
- O áudio do operador não é transcrito: registra-se quando ele falou, o que digitou e as instruções que enviou.
"""
from __future__ import annotations

import hashlib
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

# Ayrabrain (mapa de instruções/memórias): limites para o banco e a página continuarem leves
BRAIN_MAX_LINKS = 500                    # ligações por usuário
BRAIN_MAX_DEGREE = 24                    # ligações por ponto
BRAIN_PAGE = 80                          # pontos por página (padrão)
BRAIN_PAGE_MAX = 150                     # máximo por pedido
BRAIN_OFFSET_MAX = 5000
BRAIN_RELATIONS = ("relacionado", "depende", "contradiz", "exemplo")
BRAIN_SYMMETRIC = ("relacionado", "contradiz")          # a↔b = b↔a; as demais têm sentido (a → b)
_REF = re.compile(r"^([mi]):([0-9]{1,9})$")

# Contexto: cabeçalhos das partes fixas (contexto.py monta com os mesmos textos)
HDR_INSTR = "Instruções vigentes (dadas pelo usuário ou operador; valem até serem revogadas; em conflito prevalece a primeira): "
HDR_PREFS = "Informado pelo usuário, não verificado: "
HDR_VALID = "Conhecimento validado: "

HIDDEN_RETENTION_DAYS = 20               # o que foi revogado/substituído/apagado some de verdade depois disso (o resto segue em 90)


def parse_ref(ref):
    """'m:12' → ('m', 12); 'i:3' → ('i', 3); qualquer outra coisa → None (nunca vira SQL)."""
    m = _REF.match(ref) if isinstance(ref, str) else None
    return (m.group(1), int(m.group(2))) if m else None


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


def stem(w: str) -> str:
    """Radical simples (plural/gênero): sensores → sensor, professora → professor."""
    if len(w) > 3 and w.endswith("s"):
        w = w[:-1]
    if len(w) > 4 and w[-1] in "aoe":
        w = w[:-1]
    return w


_PK_VERB = re.compile(r"\beu\s+(?:gosto|prefiro|adoro|amo|odeio|detesto)\s+(?:(?:de|do|da|dos|das|o|a|os|as|um|uma|uns|umas|muito|mais)\s+)*(\w+)")
_PK_FAV = re.compile(r"\bmeu\s+(\w+)\s+favorit[oa]\s+e\b")


def pref_key(text: str) -> Optional[str]:
    """Assunto de uma preferência: duas preferências com a mesma chave falam da MESMA coisa (a nova substitui a antiga).
    'eu prefiro respostas longas' = 'eu prefiro respostas curtas'; 'eu gosto de chá' ≠ 'eu gosto de café'. Sem padrão → None."""
    f = norm(text)
    if f.startswith("nome do usuario"):
        return "nome"
    if f.startswith("prefere ser chamado de"):
        return "chamado"
    m = _PK_FAV.search(f)
    if m:
        return "favorito:" + m.group(1)
    m = _PK_VERB.search(f)
    return m.group(1) if m else None


# Ordem embutida em "instrução" (ignorar regras, mostrar dados de outro usuário): nunca vira regra vigente
_INJECT = [
    re.compile(r"\b(?:ignor\w*|desconsider\w*|desobede\w*)\b.{0,40}\b(?:regras?|instrucoes|instrucao|anteriores|sistema|prompt|limites)\b"),
    re.compile(r"\b(?:mostr\w*|revel\w*|exib\w*|vaz\w*|envi\w*|cont\w*|diga|dizer)\b.{0,40}\b(?:dados|memorias|lembrancas|segredos?|senhas?|chaves?)\b.{0,30}\b(?:outro|outra|outros|outras|usuario|pessoa)\b"),
]


def is_injection(text: str) -> bool:
    f = norm(text)
    return any(rx.search(f) for rx in _INJECT)


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
                 enabled: bool = True, retention_days: int = 90, hidden_retention_days: int = HIDDEN_RETENTION_DAYS):
        self.path = Path(path or DEFAULT_PATH)
        self.user_id, self.robot_id = user_id, robot_id
        self.enabled = bool(enabled)
        self.retention_days = int(retention_days)
        self.hidden_retention_days = int(hidden_retention_days)
        self.blocked_instructions = 0            # ordens embutidas que NÃO viraram regra (contador, sem texto)
        self.retrieval_errors = 0                # falhas do índice de busca (a memória segue viva)
        self.hidden_rev = 0                      # sobe a cada fonte ocultada: quem montou contexto antes relê se mudou
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
                conn.execute("CREATE TABLE IF NOT EXISTS brain_links(id INTEGER PRIMARY KEY, user_id TEXT, a_kind TEXT, a_id INTEGER,"
                             " b_kind TEXT, b_id INTEGER, relation TEXT, created_at REAL)")
                conn.execute("CREATE INDEX IF NOT EXISTS brain_links_user ON brain_links(user_id)")
                # RAG (aditivo; user_version continua 1): fontes ocultas e conhecimento promovido a global
                conn.execute("CREATE TABLE IF NOT EXISTS hidden_sources(kind TEXT, ref INTEGER, user_id TEXT, reason TEXT, at REAL, PRIMARY KEY(kind, ref))")
                conn.execute("CREATE INDEX IF NOT EXISTS hidden_sources_at ON hidden_sources(at)")
                conn.execute("CREATE TABLE IF NOT EXISTS global_promotions(memory_id INTEGER PRIMARY KEY, by TEXT, at REAL)")
                if "source_event_id" not in {r[1] for r in conn.execute("PRAGMA table_info(instructions)")}:
                    conn.execute("ALTER TABLE instructions ADD COLUMN source_event_id INTEGER")
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
        try:
            if self.retention_days > 0:
                self._purge(self._conn, self.retention_days)
            if self.hidden_retention_days > 0:
                self._purge_hidden(self._conn, self.hidden_retention_days)
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
                    blocked_instructions=self.blocked_instructions, retrieval_errors=self.retrieval_errors,
                    write_p50_ms=round(w[len(w) // 2], 2) if w else 0.0,
                    write_p95_ms=round(w[int(len(w) * .95)], 2) if w else 0.0, context_ms=round(self.ctx_ms, 2))

    def _uid(self, user_id: Optional[str]) -> Optional[str]:
        """Identidade da operação. `None` = o usuário da sessão do robô (app de um usuário); texto vazio/inválido = None (falha fechado)."""
        if user_id is None:
            return self.user_id
        if isinstance(user_id, str) and user_id.strip():
            return user_id
        return None

    def _hide(self, conn, uid: str, kind: str, ref, reason: str) -> None:
        """Marca uma fonte como oculta (some da busca, do resumo e da recuperação; o dado só é apagado depois da retenção do oculto)."""
        if ref is None:
            return
        conn.execute("INSERT OR REPLACE INTO hidden_sources(kind,ref,user_id,reason,at) VALUES(?,?,?,?,?)", (kind, int(ref), uid, reason, time.time()))
        self.hidden_rev += 1

    def _unhide(self, conn, kind: str, ref) -> None:
        if ref is not None and conn.execute("DELETE FROM hidden_sources WHERE kind=? AND ref=?", (kind, int(ref))).rowcount:
            self.hidden_rev += 1

    # ---- escrita (assíncrona) ----------------------------------------------------
    def _index(self, conn, text: str, kind: str, ref, owner: str) -> None:
        if self.fts and text:
            conn.execute("INSERT INTO fts(text, kind, ref, owner) VALUES(?,?,?,?)", (text, kind, str(ref), owner))

    def append_event(self, session_id: str, actor_type: str, event_type: str, text: str = "", source: str = "",
                     metadata: Optional[dict] = None, event_key: Optional[str] = None, mode: str = "",
                     user_id: Optional[str] = None) -> bool:
        """Registra um evento normalizado. `event_key` evita duplicar na reconexão (único por sessão)."""
        uid = self._uid(user_id)
        if not self.active or actor_type not in ACTORS or uid is None:
            return False
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
        revogou = found["revoke_all"] or found["revoke_last"] or bool(found["revoke_of"])
        if found["revoke_all"]:
            self._revoke(conn, uid, None, all_=True)
        elif found["revoke_last"]:
            self._revoke(conn, uid, None)
        for target in found["revoke_of"]:
            self._revoke(conn, uid, target)
        if revogou:
            self._hide(conn, uid, "event", event_id, "revoked")          # o comando de revogar também não é "memória"
        for ins in found["instructions"]:
            self._upsert_instruction(conn, uid, session_id, ins, actor, 5 if actor == "operator" else 0, event_id)
        for pref in found["preferences"]:
            self._learn_preference(conn, uid, pref, event_id)

    def _learn_preference(self, conn, uid: str, pref: str, event_id: Optional[int]) -> None:
        """Preferência nova: entra como candidata e SUBSTITUI a antiga do mesmo assunto (pref_key); repetir uma substituída a faz valer de novo."""
        n = norm(pref)
        now = time.time()
        row = conn.execute("SELECT id, validation_status FROM memories WHERE owner_scope='user' AND owner_id=? AND kind='preference' AND norm=?",
                           (uid, n)).fetchone()
        if row is None:
            cur = conn.execute("INSERT INTO memories(owner_scope,owner_id,kind,content,norm,source_event_id,confidence,validation_status,"
                               "created_at,updated_at) VALUES('user',?,'preference',?,?,?,0.6,'candidate',?,?)", (uid, pref, n, event_id, now, now))
            mid = cur.lastrowid
            self._index(conn, pref, "memory", mid, uid)
        else:
            mid = row["id"]
            if row["validation_status"] == "rejected":
                return                                                   # rejeitada por uma pessoa: dizer de novo não a ressuscita
            if row["validation_status"] == "superseded":
                conn.execute("UPDATE memories SET validation_status='candidate', source_event_id=?, updated_at=? WHERE id=?", (event_id, now, mid))
                self._unhide(conn, "memory", mid)
            else:
                conn.execute("UPDATE memories SET updated_at=? WHERE id=?", (now, mid))
        key = pref_key(pref)
        if key:
            for old in conn.execute("SELECT id, content, source_event_id FROM memories WHERE owner_scope='user' AND owner_id=? AND kind='preference' "
                                    "AND validation_status IN ('candidate','validated') AND id!=?", (uid, mid)).fetchall():
                if pref_key(old["content"]) == key:
                    conn.execute("UPDATE memories SET validation_status='superseded', updated_at=? WHERE id=?", (now, old["id"]))
                    self._hide(conn, uid, "memory", old["id"], "superseded")
                    self._hide(conn, uid, "event", old["source_event_id"], "superseded")
                    self._drop_links(conn, "m", old["id"])

    def _upsert_instruction(self, conn, uid, session_id, text, actor, priority, event_id=None):
        n = norm(text)
        if not n:
            return None
        if is_injection(text):                              # ordem embutida: nunca vira regra; a fala original também sai da recuperação
            self.blocked_instructions += 1
            self._hide(conn, uid, "event", event_id, "bloqueada")
            return None
        now = time.time()
        rows = conn.execute("SELECT id, norm, source_event_id FROM instructions WHERE user_id=? AND status='active'", (uid,)).fetchall()
        mine = set(n.split())
        for row in rows:
            if row["norm"] == n:
                conn.execute("UPDATE instructions SET updated_at=?, priority=MAX(priority,?) WHERE id=?", (now, priority, row["id"]))
                return row["id"]
        replaced = []
        for row in rows:                                    # quase igual = a nova substitui a antiga
            theirs = set(row["norm"].split())
            if mine and theirs and len(mine & theirs) / len(mine | theirs) >= 0.6:
                conn.execute("UPDATE instructions SET status='superseded', updated_at=? WHERE id=?", (now, row["id"]))
                self._hide(conn, uid, "instruction", row["id"], "superseded")
                self._hide(conn, uid, "event", row["source_event_id"], "superseded")
                replaced.append(row["id"])
        cur = conn.execute("INSERT INTO instructions(user_id,session_id,instruction,norm,status,priority,actor,created_at,updated_at,source_event_id)"
                           " VALUES(?,?,?,?, 'active',?,?,?,?,?)", (uid, session_id, text[:300], n, priority, actor, now, now, event_id))
        self._index(conn, text, "instruction", cur.lastrowid, uid)
        for old in replaced:                                # as ligações acompanham o texto novo
            self._move_links(conn, "i", old, cur.lastrowid)
        return cur.lastrowid

    def _revoke_row(self, conn, uid, row, now) -> None:
        conn.execute("UPDATE instructions SET status='revoked', updated_at=? WHERE id=?", (now, row["id"]))
        self._drop_links(conn, "i", row["id"])
        self._hide(conn, uid, "instruction", row["id"], "revoked")
        self._hide(conn, uid, "event", row["source_event_id"], "revoked")

    def _revoke(self, conn, uid, target: Optional[str], all_: bool = False) -> None:
        now = time.time()
        rows = conn.execute("SELECT id, norm, source_event_id FROM instructions WHERE user_id=? AND status='active' ORDER BY updated_at DESC",
                            (uid,)).fetchall()
        if all_:
            for row in rows:
                self._revoke_row(conn, uid, row, now)
            return
        if target is None:
            if rows:
                self._revoke_row(conn, uid, rows[0], now)
            return
        words = _tokens(target)
        for row in rows:
            if words & {t for t in row["norm"].split() if len(t) > 3}:
                self._revoke_row(conn, uid, row, now)

    def upsert_instruction(self, text: str, actor: str = "user", session_id: str = "", priority: Optional[int] = None,
                           user_id: Optional[str] = None) -> bool:
        uid = self._uid(user_id)
        if uid is None:
            return False
        prio = priority if priority is not None else (5 if actor == "operator" else 0)
        return self._submit(lambda conn: self._upsert_instruction(conn, uid, session_id, " ".join((text or "").split())[:300], actor, prio))

    def revoke_instruction(self, text: Optional[str] = None, all_: bool = False, user_id: Optional[str] = None) -> bool:
        uid = self._uid(user_id)
        if uid is None:
            return False
        return self._submit(lambda conn: self._revoke(conn, uid, text, all_))

    # ---- gestão pelo painel (operador ou o próprio usuário) ------------------------------
    def revoke_by_id(self, instruction_id: int, user_id: Optional[str] = None) -> bool:
        uid = self._uid(user_id)
        if uid is None:
            return False

        def job(conn):
            row = conn.execute("SELECT id, norm, source_event_id FROM instructions WHERE id=? AND user_id=? AND status='active'",
                               (int(instruction_id), uid)).fetchone()
            if row is not None:
                self._revoke_row(conn, uid, row, time.time())
        return self._submit(job)

    def edit_instruction(self, instruction_id: int, text: str, user_id: Optional[str] = None) -> bool:
        """Editar = a antiga vira 'superseded' e entra uma nova (fica a proveniência)."""
        uid = self._uid(user_id)
        text = " ".join((text or "").split())[:300]
        if uid is None or not text or is_injection(text):
            if uid is not None and text:
                self.blocked_instructions += 1
            return False

        def job(conn):
            row = conn.execute("SELECT session_id, actor, priority, source_event_id FROM instructions WHERE id=? AND user_id=? AND status='active'",
                               (int(instruction_id), uid)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE instructions SET status='superseded', updated_at=? WHERE id=?", (time.time(), int(instruction_id)))
            self._hide(conn, uid, "instruction", int(instruction_id), "superseded")
            self._hide(conn, uid, "event", row["source_event_id"], "superseded")
            new_id = self._upsert_instruction(conn, uid, row["session_id"], text, row["actor"], row["priority"])
            if new_id and new_id != int(instruction_id):
                self._move_links(conn, "i", int(instruction_id), new_id)
        return self._submit(job)

    def delete_memory(self, memory_id: int, user_id: Optional[str] = None) -> bool:
        """Apaga a memória (a linha e o índice); a fala de onde ela veio fica OCULTA (some da busca) e é apagada depois da retenção do oculto."""
        uid = self._uid(user_id)
        if uid is None:
            return False

        def job(conn):
            row = conn.execute("SELECT id, source_event_id FROM memories WHERE id=? AND ((owner_scope='user' AND owner_id=?) OR "
                               "(owner_scope='global' AND (owner_id=? OR owner_id='' OR owner_id IS NULL)))", (int(memory_id), uid, uid)).fetchone()
            if row is None:
                return
            conn.execute("DELETE FROM memories WHERE id=?", (row["id"],))
            conn.execute("DELETE FROM global_promotions WHERE memory_id=?", (row["id"],))
            self._drop_links(conn, "m", row["id"])
            self._drop_fts(conn, "memory", [row["id"]])
            self._unhide(conn, "memory", row["id"])
            self._hide(conn, uid, "event", row["source_event_id"], "memoria_apagada")
        return self._submit(job)

    def overview(self, query: str = "", user_id: Optional[str] = None) -> dict:
        """Tudo que o painel mostra, numa leitura só (≈1 ms): instruções, preferências, busca e métricas."""
        uid = self._uid(user_id)
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
        """Revisão humana. Validada continua PESSOAL (só o dono vê); virar conhecimento de todos exige `promote_to_global`."""
        def job(conn):
            row = conn.execute("SELECT owner_id, source_event_id FROM memories WHERE id=?", (int(memory_id),)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE memories SET validation_status=?, updated_at=? WHERE id=?",
                         ("validated" if ok else "rejected", time.time(), int(memory_id)))
            if not ok:
                self._drop_links(conn, "m", int(memory_id))
                self._hide(conn, row["owner_id"] or "", "memory", int(memory_id), "rejected")
            else:
                self._unhide(conn, "memory", int(memory_id))
            conn.execute("INSERT INTO feedback(memory_id,outcome,reviewer,timestamp) VALUES(?,?,?,?)",
                         (memory_id, "validated" if ok else "rejected", reviewer, time.time()))
        return self._submit(job)

    def promote_to_global(self, memory_id: int, reviewer: str) -> bool:
        """Torna uma memória JÁ VALIDADA conhecimento de todos. Ação explícita de um revisor identificado (sem revisor, não faz nada)."""
        who = (reviewer or "").strip() if isinstance(reviewer, str) else ""

        def job(conn):
            if not who:
                return
            row = conn.execute("SELECT id FROM memories WHERE id=? AND owner_scope='user' AND validation_status='validated'", (int(memory_id),)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE memories SET owner_scope='global', updated_at=? WHERE id=?", (time.time(), row["id"]))
            conn.execute("INSERT OR REPLACE INTO global_promotions(memory_id, by, at) VALUES(?,?,?)", (row["id"], who, time.time()))
            conn.execute("INSERT INTO feedback(memory_id,outcome,reviewer,timestamp) VALUES(?,?,?,?)", (row["id"], "promoted", who, time.time()))
        return self._submit(job)

    def demote_from_global(self, memory_id: int, reviewer: str) -> bool:
        """Desfaz a promoção: volta a ser pessoal (o dado não é perdido)."""
        who = (reviewer or "").strip() if isinstance(reviewer, str) else ""

        def job(conn):
            if not who:
                return
            row = conn.execute("SELECT memory_id FROM global_promotions WHERE memory_id=?", (int(memory_id),)).fetchone()
            if row is None:
                return
            conn.execute("UPDATE memories SET owner_scope='user', updated_at=? WHERE id=? AND owner_id!=''", (time.time(), int(memory_id)))
            conn.execute("DELETE FROM global_promotions WHERE memory_id=?", (int(memory_id),))
            conn.execute("INSERT INTO feedback(memory_id,outcome,reviewer,timestamp) VALUES(?,?,?,?)", (int(memory_id), "demoted", who, time.time()))
        return self._submit(job)

    def scope_report(self) -> dict:
        """Contagem por escopo (leitura): quantas pessoais, promovidas, gerais sem dono e globais LEGADAS (marcadas globais sem promoção)."""
        def one(sql):
            r = self._read(sql)
            return int(r[0][0]) if r else 0
        return dict(
            pessoais=one("SELECT COUNT(*) FROM memories WHERE owner_scope='user'"),
            globais_promovidas=one("SELECT COUNT(*) FROM memories WHERE owner_scope='global' AND id IN (SELECT memory_id FROM global_promotions)"),
            globais_sem_dono=one("SELECT COUNT(*) FROM memories WHERE owner_scope='global' AND (owner_id='' OR owner_id IS NULL)"),
            globais_sem_promocao_legado=one("SELECT COUNT(*) FROM memories WHERE owner_scope='global' AND owner_id!='' AND owner_id IS NOT NULL "
                                            "AND id NOT IN (SELECT memory_id FROM global_promotions)"))

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
            conn.executemany("DELETE FROM hidden_sources WHERE kind='event' AND ref=?", [(i,) for i in ids])
        return len(ids)

    def _purge_hidden(self, conn, days: int) -> int:
        """O que está oculto (revogado, substituído, apagado) há mais de `days` dias sai de verdade: falas, instruções/preferências
        antigas e o índice. O conteúdo NÃO oculto não é tocado (a retenção dele continua sendo a de `_purge`)."""
        cutoff = time.time() - days * 86400
        rows = conn.execute("SELECT kind, ref FROM hidden_sources WHERE at<?", (cutoff,)).fetchall()
        by = {"event": [], "instruction": [], "memory": []}
        for r in rows:
            by.setdefault(r["kind"], []).append(r["ref"])
        with conn:
            for kind, refs in by.items():
                self._drop_fts(conn, kind, refs)
            conn.executemany("DELETE FROM events WHERE id=?", [(i,) for i in by["event"]])
            conn.executemany("DELETE FROM instructions WHERE id=? AND status!='active'", [(i,) for i in by["instruction"]])
            conn.executemany("DELETE FROM memories WHERE id=? AND validation_status IN ('superseded','rejected')", [(i,) for i in by["memory"]])
            for i in by["instruction"]:
                self._drop_links(conn, "i", i)
            for i in by["memory"]:
                self._drop_links(conn, "m", i)
            conn.execute("DELETE FROM hidden_sources WHERE at<?", (cutoff,))
        return len(rows)

    def purge_older(self, days: int) -> bool:
        return self._submit(lambda conn: self._purge(conn, days)) if self.active else False

    def purge_hidden_now(self) -> bool:
        """Aplica já a retenção do conteúdo oculto (normalmente roda na abertura)."""
        return self._submit(lambda conn: self._purge_hidden(conn, self.hidden_retention_days)) if self.active else False

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
            self._drop_fts(conn, "memory", mem)
            for mid in mem:
                self._drop_links(conn, "m", mid)
                self._unhide(conn, "memory", mid)
            for iid in ins:
                self._drop_links(conn, "i", iid)
                self._unhide(conn, "instruction", iid)
            for eid in ev:
                self._unhide(conn, "event", eid)
            conn.execute("DELETE FROM memories WHERE id IN (%s)" % ",".join(map(str, mem or [0])))
            conn.execute("DELETE FROM instructions WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return self._submit(job)

    def forget_all(self, user_id: Optional[str] = None) -> bool:
        uid = self._uid(user_id)
        if uid is None:
            return False

        def job(conn):
            sess = [r[0] for r in conn.execute("SELECT id FROM sessions WHERE user_id=?", (uid,))]
            for sid in sess:
                conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM instructions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM global_promotions WHERE memory_id IN (SELECT id FROM memories WHERE owner_id=?)", (uid,))
            conn.execute("DELETE FROM memories WHERE owner_id=?", (uid,))
            conn.execute("DELETE FROM brain_links WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM hidden_sources WHERE user_id=?", (uid,))
            if self.fts:
                conn.execute("DELETE FROM fts WHERE owner=?", (uid,))
        return self._submit(job)

    # ---- Ayrabrain: ligações explícitas entre instruções e memórias ----------------------------
    @staticmethod
    def _drop_links(conn, kind: str, ref_id: int) -> None:
        conn.execute("DELETE FROM brain_links WHERE (a_kind=? AND a_id=?) OR (b_kind=? AND b_id=?)", (kind, ref_id, kind, ref_id))

    @staticmethod
    def _move_links(conn, kind: str, old: int, new: int) -> None:
        conn.execute("UPDATE brain_links SET a_id=? WHERE a_kind=? AND a_id=?", (new, kind, old))
        conn.execute("UPDATE brain_links SET b_id=? WHERE b_kind=? AND b_id=?", (new, kind, old))

    @staticmethod
    def _vis(uid: str, alias: str = ""):
        """Memórias que `uid` pode ler: as pessoais (candidatas ou validadas), as promovidas a global, o conhecimento geral sem dono e as
        validadas LEGADAS marcadas globais sem promoção (só o dono vê). Devolve (condição SQL, parâmetros)."""
        p = alias + "." if alias else ""
        sql = (f"(({p}owner_scope='user' AND {p}owner_id=? AND {p}validation_status IN ('candidate','validated')) OR "
               f"({p}owner_scope='global' AND {p}validation_status='validated' AND ({p}owner_id='' OR {p}owner_id IS NULL OR {p}owner_id=? "
               f"OR {p}id IN (SELECT memory_id FROM global_promotions))))")
        return sql, (uid, uid)

    def _node_alive(self, kind: str, ref_id: int, uid: str) -> bool:
        if kind == "i":
            return bool(self._read("SELECT 1 FROM instructions WHERE id=? AND user_id=? AND status='active'", (ref_id, uid)))
        cond, args = self._vis(uid)
        return bool(self._read(f"SELECT 1 FROM memories WHERE id=? AND {cond}", (ref_id,) + args))

    def link(self, a: str, b: str, relation: str = "relacionado", user_id: Optional[str] = None, actor: str = ""):
        """Cria uma ligação explícita (feita por uma pessoa; `actor` é só informativo). Devolve (ok, motivo); motivo vazio = criada."""
        uid = self._uid(user_id)
        if uid is None:
            return False, "identidade_invalida"
        ra, rb = parse_ref(a), parse_ref(b)
        if ra is None or rb is None:
            return False, "ref_invalida"
        if ra == rb:
            return False, "mesmo_no"
        if relation not in BRAIN_RELATIONS:
            return False, "relacao_invalida"
        if not self.active:
            return False, "memoria_indisponivel"
        self.flush(2.0)                                       # enxerga o que acabou de ser enfileirado (limites, duplicatas)
        if not (self._node_alive(*ra, uid) and self._node_alive(*rb, uid)):
            return False, "no_inexistente"
        rows = self._read("SELECT a_kind,a_id,b_kind,b_id,relation FROM brain_links WHERE user_id=?", (uid,))
        for r in rows:
            same = (r["a_kind"], r["a_id"], r["b_kind"], r["b_id"]) == (*ra, *rb)
            flipped = (r["a_kind"], r["a_id"], r["b_kind"], r["b_id"]) == (*rb, *ra)
            if r["relation"] == relation and (same or (flipped and relation in BRAIN_SYMMETRIC)):
                return False, "ja_existe"
        if len(rows) >= BRAIN_MAX_LINKS:
            return False, "limite_ligacoes"
        for node in (ra, rb):
            degree = sum(1 for r in rows if (r["a_kind"], r["a_id"]) == node or (r["b_kind"], r["b_id"]) == node)
            if degree >= BRAIN_MAX_DEGREE:
                return False, "limite_no"
        ok = self._submit(lambda conn: conn.execute(
            "INSERT INTO brain_links(user_id,a_kind,a_id,b_kind,b_id,relation,created_at) VALUES(?,?,?,?,?,?,?)",
            (uid, ra[0], ra[1], rb[0], rb[1], relation, time.time())))
        return (True, "") if ok else (False, "memoria_indisponivel")

    def unlink(self, link_id, user_id: Optional[str] = None):
        """Desliga uma ligação pelo id (só as do próprio usuário). Devolve (ok, motivo)."""
        uid = self._uid(user_id)
        if uid is None:
            return False, "identidade_invalida"
        try:
            lid = int(link_id) if isinstance(link_id, (int, str)) and not isinstance(link_id, bool) else None
        except ValueError:
            lid = None
        if lid is None or lid < 0:
            return False, "ref_invalida"
        if not self.active:
            return False, "memoria_indisponivel"
        self.flush(2.0)
        if not self._read("SELECT 1 FROM brain_links WHERE id=? AND user_id=?", (lid, uid)):
            return False, "nao_encontrada"
        ok = self._submit(lambda conn: conn.execute("DELETE FROM brain_links WHERE id=? AND user_id=?", (lid, uid)))
        return (True, "") if ok else (False, "memoria_indisponivel")

    def _brain_nodes(self, uid: str, q: str = ""):
        """Todos os pontos visíveis (instruções vigentes primeiro, depois memórias do usuário e o conhecimento global validado)."""
        like = ""
        args_i, args_m = [uid], [uid]
        if q.strip():
            qn = norm(q)
            if not qn:
                return []
            args_i.append(qn)
            args_m.append(qn)
            like = " AND instr(norm, ?) > 0"
        ins = self._read("SELECT id, instruction AS t, actor, priority FROM instructions WHERE user_id=? AND status='active'" + like +
                         " ORDER BY priority DESC, updated_at DESC, id DESC", args_i)
        cond, vargs = self._vis(uid)
        mems = self._read(f"SELECT id, content AS t, owner_scope, validation_status FROM memories WHERE {cond}" + like +
                          " ORDER BY updated_at DESC, id DESC", list(vargs) + ([qn] if q.strip() else []))
        nodes = [dict(id=f"i:{r['id']}", k="i", t=r["t"], by=r["actor"]) for r in ins]
        nodes += [dict(id=f"m:{r['id']}", k="m", t=r["t"], v=r["validation_status"] == "validated", g=r["owner_scope"] == "global")
                  for r in mems]
        return nodes

    def brain_graph(self, user_id: Optional[str] = None, limit: int = BRAIN_PAGE, offset: int = 0, q: str = "", focus: str = "") -> dict:
        """Uma página do mapa: ≤ 80 pontos por padrão (≤ 150 no máximo) e só as ligações entre eles."""
        uid = self._uid(user_id)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = BRAIN_PAGE
        limit = BRAIN_PAGE if limit <= 0 else min(limit, BRAIN_PAGE_MAX)
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, min(offset, BRAIN_OFFSET_MAX))
        out = dict(version=self.version, nodes=[], links=[], total_nodes=0, total_links=0, has_more=False, limit=limit, offset=offset,
                   focus="", q=q or "")
        if not self.active or uid is None:
            return out
        all_links = self._read("SELECT id, a_kind, a_id, b_kind, b_id, relation FROM brain_links WHERE user_id=? ORDER BY id", (uid,))
        out["total_links"] = len(all_links)
        everyone = self._brain_nodes(uid, q)
        by_id = {n["id"]: n for n in everyone}
        ref = parse_ref(focus) if focus else None
        if ref is not None:
            fid = f"{ref[0]}:{ref[1]}"
            out["focus"] = fid
            if fid not in by_id and not self._node_alive(ref[0], ref[1], uid):
                out["error"] = "no_inexistente"
                return out
            if fid not in by_id:
                by_id = {n["id"]: n for n in self._brain_nodes(uid)}
            near = [fid]
            for r in all_links:
                x, y = f"{r['a_kind']}:{r['a_id']}", f"{r['b_kind']}:{r['b_id']}"
                if x == fid and y not in near:
                    near.append(y)
                elif y == fid and x not in near:
                    near.append(x)
            page = [by_id[i] for i in near if i in by_id]
            out["total_nodes"] = len(page)
        else:
            page = everyone[offset:offset + limit]
            out["total_nodes"] = len(everyone)
            out["has_more"] = offset + limit < len(everyone)
        ids = {n["id"] for n in page}
        out["nodes"] = page
        out["links"] = [dict(id=r["id"], a=f"{r['a_kind']}:{r['a_id']}", b=f"{r['b_kind']}:{r['b_id']}", r=r["relation"])
                        for r in all_links if f"{r['a_kind']}:{r['a_id']}" in ids and f"{r['b_kind']}:{r['b_id']}" in ids]
        return out

    # ---- leitura -----------------------------------------------------------------------------
    def list_active_instructions(self, user_id: Optional[str] = None, limit: int = 8) -> List[dict]:
        uid = self._uid(user_id)
        if uid is None:
            return []
        rows = self._read("SELECT id, instruction, actor, priority, updated_at FROM instructions WHERE user_id=? AND status='active' "
                          "ORDER BY priority DESC, updated_at DESC LIMIT ?", (uid, limit))
        return [dict(r) for r in rows]

    def list_memories(self, user_id: Optional[str] = None, include_candidates: bool = True, limit: int = 20) -> List[dict]:
        uid = self._uid(user_id)
        if uid is None:
            return []
        cond, args = self._vis(uid)
        rows = self._read(f"SELECT id, owner_scope, kind, content, validation_status, confidence FROM memories WHERE {cond} "
                          "ORDER BY updated_at DESC LIMIT ?", args + (limit,))
        out = [dict(r) for r in rows]
        return out if include_candidates else [m for m in out if m["validation_status"] == "validated"]

    _NOT_HIDDEN = "NOT EXISTS (SELECT 1 FROM hidden_sources h WHERE h.kind={kind} AND h.ref={ref})"

    def search_memory(self, query: str, user_id: Optional[str] = None, limit: int = 5) -> List[dict]:
        """Busca textual (FTS5; LIKE se indisponível) em eventos, instruções e preferências do usuário. Fontes ocultas (revogadas,
        substituídas, apagadas) não aparecem."""
        uid = self._uid(user_id)
        words = [w for w in norm(query).split() if len(w) > 1][:8]
        if uid is None or not words:
            return []
        rows: List[sqlite3.Row] = []
        if self.fts:
            hid = self._NOT_HIDDEN.format(kind="fts.kind", ref="CAST(fts.ref AS INTEGER)")
            for joiner in (" AND ", " OR "):
                match = joiner.join('"%s"*' % w.replace('"', "") for w in words)
                rows = self._read(f"SELECT text, kind, ref FROM fts WHERE fts MATCH ? AND owner=? AND {hid} ORDER BY rank LIMIT ?",
                                  (match, uid, limit))
                if rows:
                    break
        else:
            like = "%" + "%".join(words) + "%"
            hid = self._NOT_HIDDEN.format(kind="'event'", ref="events.id")
            rows = self._read(f"SELECT text, 'event' AS kind, id AS ref FROM events WHERE text LIKE ? AND {hid} AND session_id IN "
                              "(SELECT id FROM sessions WHERE user_id=?) ORDER BY timestamp DESC LIMIT ?", (like, uid, limit))
        return [dict(text=r["text"], kind=r["kind"], ref=r["ref"]) for r in rows]

    # ---- recuperação para o RAG (contexto.py) --------------------------------------------------
    def _live(self, conn, uid: str, kind: str, ref: int) -> Optional[dict]:
        """A fonte ainda existe, é deste usuário e não está oculta? Devolve {texto, data, validacao, versao} ou None."""
        if kind == "event":
            r = conn.execute("SELECT e.text, e.timestamp FROM events e JOIN sessions s ON s.id=e.session_id WHERE e.id=? AND s.user_id=? AND e.text!='' "
                             "AND e.actor_type IN ('user','ai','operator') AND " + self._NOT_HIDDEN.format(kind="'event'", ref="e.id"), (ref, uid)).fetchone()
            if r is None:
                return None
            return dict(texto=r["text"], data=r["timestamp"], validacao="fala", versao="e" + hashlib.sha1(r["text"].encode()).hexdigest()[:8])
        if kind == "instruction":
            r = conn.execute("SELECT instruction, updated_at FROM instructions WHERE id=? AND user_id=? AND status='active' AND " +
                             self._NOT_HIDDEN.format(kind="'instruction'", ref="instructions.id"), (ref, uid)).fetchone()
            if r is None:
                return None
            return dict(texto=r["instruction"], data=r["updated_at"], validacao="vigente", versao=f"i{r['updated_at']:.3f}")
        if kind == "memory":
            cond, args = self._vis(uid)
            r = conn.execute(f"SELECT content, updated_at, validation_status, owner_scope FROM memories WHERE id=? AND {cond} AND " +
                             self._NOT_HIDDEN.format(kind="'memory'", ref="memories.id"), (ref,) + args).fetchone()
            if r is None:
                return None
            return dict(texto=r["content"], data=r["updated_at"], validacao=r["validation_status"],
                        versao=f"m{r['validation_status']}:{r['owner_scope']}:{r['updated_at']:.3f}")
        return None

    def revalidate(self, user_id: Optional[str], items) -> dict:
        """Confere de novo, logo antes de usar: devolve {(kind, ref): {texto, data, validacao, versao}} só do que ainda vale."""
        uid = self._uid(user_id)
        out: dict = {}
        if uid is None or not self.active or not items:
            return out
        try:
            conn = self._connect()
            try:
                for kind, ref, _text in items:
                    try:
                        info = self._live(conn, uid, kind, int(ref))
                    except (TypeError, ValueError):
                        info = None
                    if info is not None:
                        out[(kind, int(ref))] = info
            finally:
                conn.close()
        except sqlite3.Error:
            self.retrieval_errors += 1
            return {}
        return out

    def retrieve_candidates(self, user_id: Optional[str], keywords: List[str], limit: int = 30):
        """Candidatos para o RAG: (lista de {text, kind, ref, rank}, motivo). Filtra usuário e validade ANTES de ranquear. Se o índice
        falhar, cai para uma busca simples e devolve motivo 'indice_indisponivel' (a falha é contada, a memória segue viva)."""
        uid = self._uid(user_id)
        stems = []
        for w in keywords or []:
            st = stem(norm(w).replace(" ", ""))
            if len(st) >= 2 and st not in stems:
                stems.append(st)
        if uid is None or not self.active or not stems:
            return [], ""
        stems = stems[:8]
        motivo = ""
        cands: List[dict] = []
        try:
            conn = self._connect()
        except sqlite3.Error:
            self.retrieval_errors += 1
            return [], "indice_indisponivel"
        try:
            rows = None
            if self.fts:
                try:
                    match = " OR ".join('"%s"*' % st.replace('"', "") for st in stems)
                    hid = self._NOT_HIDDEN.format(kind="fts.kind", ref="CAST(fts.ref AS INTEGER)")
                    rows = conn.execute(f"SELECT text, kind, ref, rank FROM fts WHERE fts MATCH ? AND owner=? AND {hid} ORDER BY rank LIMIT ?",
                                        (match, uid, max(limit, 1) * 3)).fetchall()
                except sqlite3.Error:
                    self.retrieval_errors += 1
                    rows = None
            if rows is None:
                motivo = "indice_indisponivel"
                rows = self._varredura(conn, uid, stems, max(limit, 1) * 3)
            seen = set()
            for r in rows:
                ref = int(r["ref"])
                if (r["kind"], ref) in seen:
                    continue
                seen.add((r["kind"], ref))
                if self._live(conn, uid, r["kind"], ref) is not None:
                    cands.append(dict(text=r["text"], kind=r["kind"], ref=ref, rank=float(r["rank"])))
                    if len(cands) >= limit:
                        break
        except sqlite3.Error:
            self.retrieval_errors += 1
            return [], "indice_indisponivel"
        finally:
            conn.close()
        return cands, motivo

    def _varredura(self, conn, uid: str, stems: List[str], cap: int) -> List[dict]:
        """Plano B sem índice: percorre as fontes vivas do usuário (acentos e maiúsculas não importam) e ranqueia por palavras achadas."""
        cond, vargs = self._vis(uid)
        found = []
        sources = (
            ("event", "SELECT e.id AS ref, e.text AS text FROM events e JOIN sessions s ON s.id=e.session_id WHERE s.user_id=? AND e.text!='' AND "
                      "e.actor_type IN ('user','ai','operator') AND " + self._NOT_HIDDEN.format(kind="'event'", ref="e.id") + " ORDER BY e.id DESC LIMIT 5000", (uid,)),
            ("instruction", "SELECT id AS ref, instruction AS text FROM instructions WHERE user_id=? AND status='active' AND " +
                            self._NOT_HIDDEN.format(kind="'instruction'", ref="instructions.id"), (uid,)),
            ("memory", f"SELECT id AS ref, content AS text FROM memories WHERE {cond} AND " + self._NOT_HIDDEN.format(kind="'memory'", ref="memories.id"), vargs))
        for kind, sql, args in sources:
            for r in conn.execute(sql, args):
                n = norm(r["text"])
                hit = sum(1 for st in stems if st in n)
                if hit:
                    found.append({"kind": kind, "ref": r["ref"], "text": r["text"], "rank": -float(hit)})
        found.sort(key=lambda x: x["rank"])
        return found[:cap]

    def session_summary(self, session_id: str, limit: int = 40) -> dict:
        """Resumo incremental, sem LLM: o que o operador fez e as últimas trocas da sessão (fontes ocultas ficam de fora)."""
        rows = self._read("SELECT actor_type, event_type, text, metadata_json FROM events WHERE session_id=? AND " +
                          self._NOT_HIDDEN.format(kind="'event'", ref="events.id") + " ORDER BY timestamp DESC, id DESC LIMIT ?", (session_id, limit))
        rows = list(reversed(rows))
        op = [r for r in rows if r["actor_type"] == "operator"]
        voice = sum(float((json.loads(r["metadata_json"] or "{}")).get("segundos", 0)) for r in op if r["event_type"] == "voice")
        said = [r["text"] for r in op if r["event_type"] in ("say", "instruction") and r["text"]][-3:]
        last = []
        for r in rows:
            if r["actor_type"] in ("user", "ai") and r["text"]:
                last.append(("Usuário" if r["actor_type"] == "user" else "Você") + ": " + r["text"][:90])
        return dict(operator_events=len(op), operator_voice_s=round(voice), operator_said=said, last=last[-4:])

    def context_parts(self, user_id: Optional[str] = None, session_id: str = "", handoff: bool = False) -> dict:
        """As partes fixas do contexto, separadas (contexto.py as orça); `get_session_context` junta as mesmas partes como sempre."""
        parts = dict(instrucoes=[], instrucoes_meta=[], preferencias=[], validado=[], handoff="")
        uid = self._uid(user_id)
        if not self.active or uid is None:
            return parts
        ins = self.list_active_instructions(uid)
        parts["instrucoes"] = [("[operador] " if i["actor"] == "operator" else "") + i["instruction"] for i in ins]
        parts["instrucoes_meta"] = [dict(texto=i["instruction"], atualizado=i["updated_at"]) for i in ins]
        mems = self.list_memories(uid, include_candidates=True, limit=6)
        parts["validado"] = [m["content"] for m in mems if m["validation_status"] == "validated"][:3]
        parts["preferencias"] = [m["content"] for m in mems if m["validation_status"] != "validated"][:4]
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
                parts["handoff"] = "Desde a última resposta sua: " + "; ".join(bits) + ". Não contradiga o que o operador decidiu."
        return parts

    def get_session_context(self, session_id: str = "", user_id: Optional[str] = None, handoff: bool = False,
                            budget_chars: int = 1100) -> str:
        """Texto curto para a Realtime: instruções vigentes, preferências (não verificadas), conhecimento validado e,
        na volta do operador, o resumo do que aconteceu. Vazio se não houver nada (a IA não recebe nada)."""
        if not self.active:
            return ""
        t = time.perf_counter()
        p = self.context_parts(user_id, session_id, handoff)
        parts: List[str] = []
        if p["instrucoes"]:
            parts.append(HDR_INSTR + "; ".join(p["instrucoes"]) + ".")
        if p["preferencias"]:
            parts.append(HDR_PREFS + "; ".join(p["preferencias"]) + ".")
        if p["validado"]:
            parts.append(HDR_VALID + "; ".join(p["validado"]) + ".")
        if p["handoff"]:
            parts.append(p["handoff"])
        text = " ".join(parts)
        if len(text) > budget_chars:
            text = text[:budget_chars - 1].rsplit(" ", 1)[0] + "…"
        self.ctx_ms = (time.perf_counter() - t) * 1000
        return text
