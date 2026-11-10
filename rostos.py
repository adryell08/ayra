"""Memória de rostos da Ayra: só com consentimento, só no computador, com validade.

* Guarda apenas **números** (vetor de 128 valores do SFace), nunca fotos. Arquivo `rostos.local.json`
  (fora do Git, como os demais `*.local.json`).
* Cadastro em dois passos: a IA pergunta, a pessoa confirma, só então o vetor é gravado (`Enrollment`).
* Validade: `NURIA_ROSTOS_DIAS` (padrão 7). Cada vez que a pessoa é reconhecida a validade renova; sem
  aparecer por esse prazo, o cadastro é apagado sozinho. "Esqueça meu rosto" apaga na hora.
* Custo zero sem cadastro: com a lista vazia a thread só dorme. Com cadastro, ~1 reconhecimento a cada 2 s
  e só se há rosto na câmera (a imagem nunca vai para a nuvem; a IA recebe só uma nota de texto com o nome).
"""
from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

PATH = Path(__file__).resolve().parent / "rostos.local.json"
MAX_SAMPLES = 8


def ttl_days() -> float:
    try:
        return max(0.0, float(os.environ.get("NURIA_ROSTOS_DIAS", "7").replace(",", ".")))
    except ValueError:
        return 7.0


def clean_name(name: str) -> str:
    name = "".join(c for c in (name or "") if c.isalnum() or c in " -'.")
    return " ".join(name.split())[:40]


def _key(name: str) -> str:
    plain = unicodedata.normalize("NFD", name.lower())
    return "".join(c for c in plain if unicodedata.category(c) != "Mn")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.reshape(-1).astype(np.float32), b.reshape(-1).astype(np.float32)
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / d if d > 0 else 0.0


class FaceBook:
    """Cadastro persistente (JSON atômico). Thread-safe."""

    def __init__(self, path: Path = PATH, days: Optional[float] = None, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.days = ttl_days() if days is None else float(days)
        self._clock = clock
        self._lock = threading.Lock()
        self._people: list = []
        self.version = 0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._people = [p for p in data.get("pessoas", []) if p.get("nome") and p.get("amostras")]
        except (OSError, ValueError, AttributeError):
            self._people = []
        self.purge()

    def _save(self) -> None:
        self.version += 1
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pessoas": self._people}, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def _expiry(self) -> float:
        return self._clock() + self.days * 86400 if self.days > 0 else 0.0     # 0 = não expira

    def purge(self) -> int:
        now = self._clock()
        with self._lock:
            keep = [p for p in self._people if not p.get("expira") or p["expira"] > now]
            gone = len(self._people) - len(keep)
            if gone:
                self._people = keep
                self._save()
            return gone

    def people(self) -> List[dict]:
        with self._lock:
            return [dict(id=p["id"], nome=p["nome"], criado=p.get("criado", 0), visto=p.get("visto", 0),
                         expira=p.get("expira", 0), amostras=len(p["amostras"])) for p in self._people]

    def __len__(self) -> int:
        return len(self._people)

    def add(self, name: str, samples: List[np.ndarray]) -> dict:
        name = clean_name(name)
        if not name or not samples:
            raise ValueError("nome ou amostras vazios")
        vecs = [[round(float(x), 5) for x in np.asarray(s).reshape(-1)] for s in samples]
        now = self._clock()
        with self._lock:
            person = next((p for p in self._people if _key(p["nome"]) == _key(name)), None)
            if person is None:
                person = dict(id=uuid.uuid4().hex[:8], nome=name, criado=now, amostras=[])
                self._people.append(person)
            person["amostras"] = (person["amostras"] + vecs)[-MAX_SAMPLES:]
            person["visto"], person["expira"] = now, self._expiry()
            self._save()
            return dict(id=person["id"], nome=person["nome"], expira=person["expira"])

    def forget(self, name: str) -> int:
        k = _key(clean_name(name))
        with self._lock:
            keep = [p for p in self._people if _key(p["nome"]) != k]
            gone = len(self._people) - len(keep)
            if gone:
                self._people = keep
                self._save()
            return gone

    def forget_all(self) -> int:
        with self._lock:
            gone = len(self._people)
            self._people = []
            if gone:
                self._save()
            return gone

    def match(self, feat: np.ndarray, threshold: float = 0.40):
        """(pessoa, escore) da melhor semelhança acima do limiar; (None, melhor escore) se ninguém passa."""
        best, best_score = None, -1.0
        with self._lock:
            for p in self._people:
                score = max(cosine(feat, np.asarray(v, dtype=np.float32)) for v in p["amostras"])
                if score > best_score:
                    best, best_score = p, score
            if best is not None and best_score >= threshold:
                best["visto"], best["expira"] = self._clock(), self._expiry()     # reconhecida: renova a validade
                return dict(id=best["id"], nome=best["nome"]), best_score
        return None, best_score


class SFaceEmbedder:
    """YuNet (já usado no rastreio) + SFace. Carrega só na primeira vez que precisa (~37 MB)."""

    MIN_FACE = 70            # px de largura (no quadro reduzido): menor que isso o vetor não é confiável

    def __init__(self):
        self._det = self._rec = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            import cv2
            from preparar_modelos import model_path, valid_model
            return hasattr(cv2, "FaceRecognizerSF") and valid_model("faces") and Path(self._det_path()).is_file()
        except Exception:
            return False

    @staticmethod
    def _det_path() -> str:
        from tracker import DEFAULT_MODEL
        return str(DEFAULT_MODEL)

    def _load(self):
        import cv2
        from preparar_modelos import model_path
        self._det = cv2.FaceDetectorYN.create(self._det_path(), "", (320, 320), 0.8, 0.3, 20)
        self._rec = cv2.FaceRecognizerSF.create(str(model_path("faces")), "")

    def __call__(self, frame) -> List[np.ndarray]:
        """Vetores dos rostos do quadro, do maior para o menor."""
        import cv2
        with self._lock:
            if self._det is None:
                self._load()
            h, w = frame.shape[:2]
            scale = min(1.0, 640.0 / max(w, 1))
            img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame
            self._det.setInputSize((img.shape[1], img.shape[0]))
            _, faces = self._det.detect(img)
            if faces is None:
                return []
            rows = sorted((f for f in faces if f[2] >= self.MIN_FACE), key=lambda f: -float(f[2] * f[3]))
            return [self._rec.feature(self._rec.alignCrop(img, row)).reshape(-1).copy() for row in rows[:3]]


class FaceRecognizer:
    """Reconhece em segundo plano e avisa quando a pessoa na frente muda (nunca por quadro)."""

    PERIOD = 2.0
    CONFIRM = 2              # leituras seguidas iguais para trocar a identidade (sem piscar)
    LOST_AFTER = 12.0
    ASK_TTL = 120.0          # tempo para a pessoa responder ao pedido de confirmação

    def __init__(self, book: FaceBook, frame_fn: Callable[[], object], embedder=None,
                 on_change: Optional[Callable[[Optional[str]], None]] = None, threshold: Optional[float] = None):
        self.book = book
        self.frame_fn = frame_fn
        self.embedder = embedder or SFaceEmbedder()
        self.on_change = on_change
        try:
            self.threshold = float(threshold if threshold is not None else os.environ.get("NURIA_ROSTOS_LIMIAR", "0.40"))
        except ValueError:
            self.threshold = 0.40
        self.current: Optional[str] = None       # nome reconhecido agora
        self.seen_unknown = False                 # há rosto, mas ninguém cadastrado
        self.error = ""
        self._cand, self._streak, self._last_face = None, 0, 0.0
        self._pending: Optional[dict] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---- ciclo de vida ----
    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="rostos", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            from tracker import lower_thread_priority
            lower_thread_priority()
        except Exception:
            pass
        last_purge = 0.0
        while not self._stop.wait(self.PERIOD):
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                self.book.purge()
            if len(self.book) == 0:
                self._set_current(None)
                continue                           # sem cadastro: custo zero
            try:
                self.step()
            except Exception as error:             # modelo ausente etc.: desliga em silêncio e explica
                self.error = str(error)[:120]
                self._stop.set()

    # ---- um ciclo (também usado nos testes) ----
    def step(self) -> Optional[str]:
        frame = self.frame_fn()
        feats = self.embedder(frame) if frame is not None else []
        now = time.time()
        if not feats:
            if now - self._last_face > self.LOST_AFTER:
                self._set_current(None)
            return self.current
        self._last_face = now
        person, _ = self.book.match(feats[0], self.threshold)
        name = person["nome"] if person else ""
        self.seen_unknown = not person
        if name == self._cand:
            self._streak += 1
        else:
            self._cand, self._streak = name, 1
        if self._streak >= self.CONFIRM:
            self._set_current(name or None)
        return self.current

    def _set_current(self, name: Optional[str]) -> None:
        if name != self.current:
            self.current = name
            if name is None:
                self._cand, self._streak = None, 0
            if self.on_change is not None and name:
                try:
                    self.on_change(name)
                except Exception:
                    pass

    # ---- cadastro em dois passos ----
    def ask(self, name: str, turn: str) -> dict:
        """Passo 1: registra o pedido. Nada é gravado; a IA precisa perguntar à pessoa."""
        name = clean_name(name)
        if not name:
            return dict(ok=False, motivo="nome vazio")
        self._pending = dict(nome=name, turno=turn, t=time.time())
        days = self.book.days
        prazo = f"por {int(days) if days == int(days) else days} dias" if days > 0 else "até ela pedir para esquecer"
        return dict(ok=False, pendente=True, instrucao=(
            f"Nada foi gravado. Pergunte agora à pessoa, em uma frase, se ela autoriza guardar o rosto dela como "
            f"'{name}' neste computador {prazo} (só números, sem foto). Se ela disser sim, chame lembrar_rosto de novo "
            f"com confirmado=true; se disser não, não faça nada."))

    def confirm(self, name: str, turn: str, samples: int = 3) -> dict:
        """Passo 2: só vale em outro turno, para o mesmo nome e dentro do prazo."""
        name = clean_name(name)
        p = self._pending
        if not p or _key(p["nome"]) != _key(name) or time.time() - p["t"] > self.ASK_TTL:
            return self.ask(name, turn)
        if p["turno"] == turn:
            return dict(ok=False, pendente=True,
                        instrucao="A pessoa ainda não respondeu. Pergunte e espere a resposta dela antes de confirmar.")
        got: List[np.ndarray] = []
        for i in range(samples):
            frame = self.frame_fn()
            feats = self.embedder(frame) if frame is not None else []
            if len(feats) > 1:
                return dict(ok=False, motivo="Há mais de um rosto na câmera. Peça para ficar só a pessoa que será cadastrada.")
            if feats:
                got.append(feats[0])
            if i < samples - 1:
                time.sleep(0.25)
        if len(got) < 2:
            return dict(ok=False, motivo="Não consegui ver o rosto com nitidez. Peça para olhar para a câmera, de frente e perto.")
        info = self.book.add(name, got)
        self._pending = None
        self._cand, self._streak = info["nome"], self.CONFIRM
        self._set_current(info["nome"])
        return dict(ok=True, nome=info["nome"], validade_dias=self.book.days)

    def forget(self, name: str) -> dict:
        n = self.book.forget(name)
        if self.current and _key(self.current) == _key(clean_name(name)):
            self.current = None
        return dict(ok=bool(n), apagados=n)
