"""Orquestrador LOCAL de comportamento: quem manda em cada canal do corpo, por quanto tempo e o que fazer com o resto.

Fontes (prioridade): operador (3) > IA (2) > espontâneo (1). Canais: expressão/olhos, cabeça, braço esquerdo e direito.
Regras:
  * um pedido ocupa só os canais que usa (gesto de cabeça não segura os braços; de dois braços segura ambos);
  * prioridade maior assume os canais ocupados por menor; igual substitui; menor é bloqueado — tudo ou nada:
    um pedido bloqueado não mexe em canal nenhum;
  * bloqueado, o gesto da IA espera numa fila curta (TTL) e some se não der tempo; o espontâneo é descartado;
  * teleoperado: só o operador age; IA e espontâneo são recusados e o que estava pendente ou ativo deles é cancelado
    (nenhum gesto tardio da IA reaparece);
  * filas e eventos são limitados (nunca crescem sem fim) e tudo é contado em `stats()`.
Não faz rede, não chama a IA, não grava nada e não conhece o operador pelo nome (só pela prioridade).
O tempo é sempre passado de fora (`now`, em segundos), o que o torna determinístico e fácil de testar.
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, FrozenSet, List, Optional, Tuple

import gestos

OPERATOR, AI, SPONTANEOUS = 3, 2, 1
SOURCES = {"operator": OPERATOR, "operador": OPERATOR, "ai": AI, "ia": AI,
           "spontaneous": SPONTANEOUS, "espontaneo": SPONTANEOUS, "espontâneo": SPONTANEOUS}
SOURCE_NAMES = {OPERATOR: "operator", AI: "ai", SPONTANEOUS: "spontaneous"}
EXPRESSION, HEAD, ARM_L, ARM_R = "expression", "head", "arm_l", "arm_r"
CHANNELS = (EXPRESSION, HEAD, ARM_L, ARM_R)
MODES = ("autonomo", "assistido", "teleoperado")

AI_TTL = 1.5               # s que um gesto da IA bloqueado espera antes de ser descartado
MAX_PENDING = 4            # fila de gestos da IA esperando canal
MAX_OUT = 16               # comandos prontos para o corpo ainda não lidos
EXPRESSION_HOLD = 3.0      # s que uma expressão pontual do operador (piscar, chorar...) segura o canal contra a IA


def channels_for(name: str, side=None) -> FrozenSet[str]:
    """Canais que um gesto usa. Gesto desconhecido = nenhum."""
    if name not in gestos.LIBRARY:
        return frozenset()
    arms = gestos.gesture_arms(name)
    if not arms:
        return frozenset({HEAD})
    if len(arms) == 2:
        return frozenset({ARM_L, ARM_R})
    return frozenset({ARM_L if gestos.normalize_side(name, side) == "left" else ARM_R})


@dataclass
class Hold:
    source: int
    name: str
    side: str
    channels: FrozenSet[str]
    start: float
    until: float


@dataclass
class Pending:
    source: int
    name: str
    side: str
    channels: FrozenSet[str]
    hold: float
    deadline: float


@dataclass
class Command:
    kind: str                  # "play" | "cancel"
    name: str = ""
    side: str = ""
    source: str = ""
    channels: Tuple[str, ...] = ()


class Orchestrator:
    def __init__(self, duration_of: Optional[Callable[[str], Optional[float]]] = None, ai_ttl: float = AI_TTL,
                 max_pending: int = MAX_PENDING):
        self._duration_of = duration_of or (lambda n: gestos.LIBRARY[n].duration if n in gestos.LIBRARY else None)
        self.ai_ttl, self.max_pending = float(ai_ttl), int(max_pending)
        self.mode = "autonomo"
        self.holds: Dict[str, Hold] = {}                  # canal -> quem o segura
        self.pending: Deque[Pending] = deque()
        self.out: Deque[Command] = deque(maxlen=MAX_OUT)
        self.last_activity = -1e9                         # último pedido do operador ou da IA (para o espontâneo)
        self._now = 0.0
        self._counters = dict.fromkeys(("started", "queued", "preempted", "dropped_busy", "dropped_mode", "dropped_invalid",
                                        "expired", "overflow", "cancelled"), 0)

    # ---- consulta ----------------------------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        return dict(self._counters, pending=len(self.pending), holds=len({id(h) for h in self.holds.values()}))

    def active(self, now: Optional[float] = None) -> Dict[str, Tuple[str, str, float]]:
        now = self._now if now is None else now
        return {ch: (SOURCE_NAMES[h.source], h.name, max(0.0, h.until - now)) for ch, h in self.holds.items()}

    def busy(self) -> bool:
        return bool(self.holds) or bool(self.pending)

    def drain(self) -> List[Command]:
        """Comandos prontos para o corpo (play/cancel), em ordem; esvazia a saída."""
        cmds = list(self.out)
        self.out.clear()
        return cmds

    # ---- modos -------------------------------------------------------------------------------
    def set_mode(self, mode: str, now: float) -> None:
        mode = str(mode or "")
        if mode not in MODES or mode == self.mode:
            return
        self.mode = mode
        if mode == "teleoperado":
            self._purge(lambda src: src < OPERATOR, now, cancel_active=True)

    def _allowed(self, prio: int) -> bool:
        return prio == OPERATOR or self.mode != "teleoperado"

    # ---- gestos ------------------------------------------------------------------------------
    def submit_gesture(self, source, name: str, now: float, side=None, hold: Optional[float] = None) -> str:
        """Devolve started | queued | dropped:<motivo>. Os comandos para o corpo saem em `drain()`."""
        self._tick(now)
        prio = SOURCES.get(str(source).lower()) if not isinstance(source, int) else (source if source in SOURCE_NAMES else None)
        name = str(name or "").strip().lower()
        channels = channels_for(name, side)
        if prio is None or not channels:
            self._counters["dropped_invalid"] += 1
            return "dropped:invalid"
        if not self._allowed(prio):
            self._counters["dropped_mode"] += 1
            return "dropped:mode"
        if prio >= AI:
            self.last_activity = now
        duration = float(hold) if hold else float(self._duration_of(name) or 2.0)
        duration = max(.2, min(duration, 30.0))
        norm_side = "left" if channels == frozenset({ARM_L}) and len(gestos.gesture_arms(name)) == 1 else ""
        blockers = {id(h): h for ch in channels if (h := self.holds.get(ch)) is not None and h.source > prio}
        if blockers:
            if prio == AI:
                return self._enqueue(Pending(prio, name, norm_side, channels, duration, now + self.ai_ttl))
            self._counters["dropped_busy"] += 1
            return "dropped:busy"
        self._start(prio, name, norm_side, channels, duration, now)
        return "started"

    def _enqueue(self, p: Pending) -> str:
        for old in list(self.pending):                    # o mesmo pedido repetido só renova o prazo
            if (old.source, old.name, old.side) == (p.source, p.name, p.side):
                self.pending.remove(old)
        self.pending.append(p)
        while len(self.pending) > self.max_pending:
            self.pending.popleft()
            self._counters["overflow"] += 1
        self._counters["queued"] += 1
        return "queued"

    def _start(self, prio, name, side, channels, duration, now) -> None:
        for h in {id(self.holds[ch]): self.holds[ch] for ch in channels if ch in self.holds}.values():
            for ch in h.channels:                         # quem perde, perde o gesto inteiro (como no animador)
                if self.holds.get(ch) is h:
                    del self.holds[ch]
            self._counters["preempted"] += 1
        hold = Hold(prio, name, side, channels, now, now + duration)
        for ch in channels:
            self.holds[ch] = hold
        self._counters["started"] += 1
        self.out.append(Command("play", name, side, SOURCE_NAMES[prio], tuple(sorted(channels))))
        self._trim_out()

    def _trim_out(self) -> None:
        if len(self.out) == self.out.maxlen:
            self._counters["overflow"] += 1               # deque descarta o mais antigo sozinho

    # ---- expressão/olhos ---------------------------------------------------------------------
    def claim_expression(self, source, now: float, hold: float = EXPRESSION_HOLD) -> bool:
        """Registra uma expressão pontual (operador/IA/espontâneo). False = bloqueada por quem tem prioridade."""
        self._tick(now)
        prio = SOURCES.get(str(source).lower())
        if prio is None or not self._allowed(prio):
            self._counters["dropped_mode" if prio is not None else "dropped_invalid"] += 1
            return False
        cur = self.holds.get(EXPRESSION)
        if cur is not None and cur.source > prio:
            self._counters["dropped_busy"] += 1
            return False
        if prio >= AI:
            self.last_activity = now
        self.holds[EXPRESSION] = Hold(prio, "expression", "", frozenset({EXPRESSION}), now, now + max(.1, float(hold)))
        return True

    def expression_allowed(self, source="ai", now: Optional[float] = None) -> bool:
        """A IA pode mudar a expressão agora? (portão consultado pelo assistente)"""
        if now is not None:
            self._tick(now)
        prio = SOURCES.get(str(source).lower())
        if prio is None or not self._allowed(prio):
            return False
        cur = self.holds.get(EXPRESSION)
        return cur is None or cur.source <= prio

    # ---- cancelamento / tempo ----------------------------------------------------------------
    def cancel(self, source=None, now: float = 0.0, channel: Optional[str] = None) -> int:
        """Cancela gestos ativos e pendentes de uma fonte (ou de todas) e/ou de um canal. Devolve quantos."""
        self._tick(now)
        prio = SOURCES.get(str(source).lower()) if source is not None else None
        return self._purge(lambda s: prio is None or s == prio, now, cancel_active=True, channel=channel)

    def _purge(self, match, now, cancel_active: bool, channel: Optional[str] = None) -> int:
        n = 0
        for p in list(self.pending):
            if match(p.source) and (channel is None or channel in p.channels):
                self.pending.remove(p)
                n += 1
        if cancel_active:
            for h in {id(h): h for h in self.holds.values()}.values():
                if match(h.source) and (channel is None or channel in h.channels):
                    for ch in h.channels:
                        if self.holds.get(ch) is h:
                            del self.holds[ch]
                    if h.name != "expression":
                        self.out.append(Command("cancel", h.name, h.side, SOURCE_NAMES[h.source], tuple(sorted(h.channels))))
                    n += 1
        self._counters["cancelled"] += n
        return n

    def _tick(self, now: float) -> None:
        self._now = max(self._now, float(now))
        for ch in [c for c, h in self.holds.items() if h.until <= now]:
            del self.holds[ch]

    def update(self, now: float) -> None:
        """Chamar uma vez por quadro: expira ocupações, descarta pedidos vencidos e promove os pendentes."""
        self._tick(now)
        for p in list(self.pending):
            if p.deadline <= now:
                self.pending.remove(p)
                self._counters["expired"] += 1
        if self.mode == "teleoperado":
            self.pending.clear()
            return
        for p in list(self.pending):                      # em ordem de chegada
            if any((h := self.holds.get(ch)) is not None and h.source > p.source for ch in p.channels):
                continue
            self.pending.remove(p)
            self._start(p.source, p.name, p.side, p.channels, p.hold, now)


# ---- exploração experimental (desligada por padrão) ----------------------------------------------
# Combinações fixas e seguras de gestos que o corpo já sabe fazer; nada é gerado nem editado em código, nada sai da máquina.
PRESETS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "aceno_e_sim": (("tchau", "right"), ("sim", "")),
    "duas_maos": (("tchau", "right"), ("joia", "left")),
    "ok_e_sim": (("ok", "left"), ("sim", "")),
    "paz_e_nao": (("paz", "right"), ("nao", "")),
    "joia_esq_sim": (("joia", "left"), ("sim", "")),
    "continencia_ok": (("continencia", "right"), ("ok", "left")),
    "pensar_nao": (("pensar", "right"), ("nao", "")),
    "apontar_sim": (("apontar", "right"), ("sim", "")),
}
MAX_SCORE = 3.0


class Explorer:
    """Escolhe, de vez em quando e só com o robô parado, uma combinação da lista acima (espontânea: perde para operador e IA).
    Estado só em memória e limitado (≤ 8 combinações); `reward()` ajusta a preferência. Não toca em rede nem em disco."""

    def __init__(self, orch: Orchestrator, enabled: bool = False, min_idle: float = 25.0, cooldown: float = 60.0,
                 epsilon: float = .3, rng: Optional[random.Random] = None):
        self.orch, self.enabled = orch, bool(enabled)
        self.min_idle, self.cooldown, self.epsilon = float(min_idle), float(cooldown), float(epsilon)
        self.rng = rng or random.Random()
        self.scores: Dict[str, float] = {k: 0.0 for k in PRESETS}
        self.runs: Dict[str, int] = {k: 0 for k in PRESETS}
        self.last_run = -1e9
        self.last_preset: Optional[str] = None
        self._first_tick: Optional[float] = None       # só explora depois de min_idle segundos de app aberto

    def tick(self, now: float, can_run: bool = True) -> Optional[str]:
        o = self.orch
        if self._first_tick is None:
            self._first_tick = now
        if (not self.enabled or not can_run or o.mode == "teleoperado" or o.busy()
                or now - max(o.last_activity, self._first_tick) < self.min_idle or now - self.last_run < self.cooldown):
            return None
        names = list(PRESETS)
        if self.rng.random() < self.epsilon:
            name = self.rng.choice(names)
        else:
            top = max(self.scores.values())
            name = self.rng.choice([n for n in names if self.scores[n] == top])
        results = [o.submit_gesture("spontaneous", g, now, side=side) for g, side in PRESETS[name]]
        self.last_run = now
        if all(r == "started" for r in results):
            self.runs[name] += 1
            self.last_preset = name
            return name
        return None

    def reward(self, name: str, value: float) -> bool:
        if name not in self.scores:
            return False
        self.scores[name] = max(-MAX_SCORE, min(MAX_SCORE, self.scores[name] + float(value)))
        return True
