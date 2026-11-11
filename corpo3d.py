"""Corpo 3D da Ayra em tempo real (OpenGL 3.3 via moderngl, desenho fora da tela -> pygame).

O visor recebe, a cada quadro, o rosto de LEDs atual (o mesmo da tela 2D). Sem moderngl ou sem OpenGL
o app continua no 2D e explica o motivo.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pygame

from gestos import Animator, Rig

MODEL = Path(__file__).resolve().parent / "models" / "nuria3d.npz"

VERT = """
#version 330
uniform mat4 bones[32];
uniform mat4 vp;
uniform vec4 visor_box;
in vec3 in_pos; in vec3 in_nrm; in float in_bone; in float in_mat;
out vec3 v_pos; out vec3 v_nrm; out vec2 v_uv; flat out int v_mat;
void main() {
    mat4 B = bones[int(in_bone + 0.5)];
    vec4 wp = B * vec4(in_pos, 1.0);
    v_pos = wp.xyz;
    v_nrm = mat3(B) * in_nrm;
    v_mat = int(in_mat + 0.5);
    v_uv = vec2((in_pos.x - visor_box.x) / visor_box.z, (in_pos.z - visor_box.y) / visor_box.w);
    gl_Position = vp * wp;
}
"""

FRAG = """
#version 330
uniform vec3 mcol[16]; uniform float mmet[16]; uniform float mrough[16]; uniform float memit[16];
uniform int visor_id; uniform sampler2D face; uniform vec3 cam; uniform vec4 face_crop; uniform float face_gain;
in vec3 v_pos; in vec3 v_nrm; in vec2 v_uv; flat in int v_mat;
out vec4 frag;
vec3 sky(vec3 d) { return mix(vec3(.035,.04,.05), vec3(.30,.36,.48), clamp(d.z*.5+.5, 0., 1.)); }
vec3 light(vec3 N, vec3 V, vec3 L, vec3 c, vec3 base, float metal, float rough) {
    float nl = max(dot(N, L), 0.);
    vec3 H = normalize(L + V);
    float sh = 2. / max(pow(rough, 4.), 1e-4) - 2.;
    vec3 F0 = mix(vec3(.04), base, metal);
    vec3 spec = F0 * pow(max(dot(N, H), 0.), sh) * (sh + 8.) / 25.13;
    return c * nl * (base * (1. - metal) + spec);
}
void main() {
    vec3 N = normalize(v_nrm);
    if (!gl_FrontFacing) N = -N;
    vec3 V = normalize(cam - v_pos);
    vec3 base = mcol[v_mat]; float metal = mmet[v_mat]; float rough = max(mrough[v_mat], .08);
    vec3 col = vec3(0.);
    col += light(N, V, normalize(vec3(-.45, -.70, .60)), vec3(1.0, .97, .93) * 2.7, base, metal, rough);
    col += light(N, V, normalize(vec3(.65, -.45, .25)), vec3(.80, .86, 1.0) * 1.0, base, metal, rough);
    col += light(N, V, normalize(vec3(.35, .90, .45)), vec3(.45, .62, 1.0) * 1.7, base, metal, rough);
    vec3 F0 = mix(vec3(.04), base, metal);
    float fres = pow(1. - max(dot(N, V), 0.), 5.);
    vec3 F = F0 + (1. - F0) * fres;
    col += sky(N) * base * (1. - metal) * .55;
    col += sky(reflect(-V, N)) * F * (1. - rough) * 1.1;
    col += base * memit[v_mat] * .35;
    if (v_mat == visor_id) {
        vec2 uv = face_crop.xy + clamp(v_uv, 0., 1.) * face_crop.zw;
        vec3 led = pow(texture(face, vec2(uv.x, 1. - uv.y)).rgb, vec3(2.2));
        col += led * face_gain;
    }
    col = col * (2.51 * col + .03) / (col * (2.43 * col + .59) + .14);   // ACES (aprox.)
    frag = vec4(pow(clamp(col, 0., 1.), vec3(1. / 2.2)), 1.);
}
"""


def _look_at(eye, target, up=(0, 0, 1)) -> np.ndarray:
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    M = np.eye(4)
    M[0, :3], M[1, :3], M[2, :3] = s, u, -f
    M[:3, 3] = -M[:3, :3] @ eye
    return M


def _perspective(fovy_deg, aspect, near, far) -> np.ndarray:
    f = 1 / math.tan(math.radians(fovy_deg) / 2)
    M = np.zeros((4, 4))
    M[0, 0], M[1, 1] = f / aspect, f
    M[2, 2], M[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    M[3, 2] = -1
    return M


def available() -> Tuple[bool, str]:
    """(pode usar o 3D, motivo se não)."""
    if not MODEL.exists():
        return False, "modelo 3D ausente (models/nuria3d.npz)"
    try:
        import moderngl  # noqa: F401
    except ImportError:
        return False, "instale o pacote moderngl (o iniciar.bat instala)"
    return True, ""


_IDENT32 = np.tile(np.eye(4, dtype=np.float32), (32, 1, 1))


class Body3D:
    def __init__(self, model: Path = MODEL, samples: int = 4):
        import moderngl
        self.mgl = moderngl
        self._bones = np.zeros((32, 4, 4), np.float32)
        self._bones_t = np.zeros((32, 4, 4), np.float32)
        self._readbuf = None
        try:
            self.ctx = moderngl.create_standalone_context(require=330)
        except Exception:                                   # Linux sem tela (testes): EGL
            self.ctx = moderngl.create_standalone_context(require=330, backend="egl")
        data = np.load(model)
        self.rig = Rig.load(data)
        self.anim = Animator()
        pos = data["pos"].astype(np.float32)
        mat = data["mat"].astype(np.float32)
        names = [str(n) for n in data["mat_names"]]
        self.visor_id = names.index("10 | Visor do rosto")
        vis = pos[data["mat"] == self.visor_id]
        lo, hi = vis.min(0), vis.max(0)
        self.visor_aspect = float((hi[0] - lo[0]) / (hi[2] - lo[2]))
        verts = np.concatenate([pos, data["nrm"].astype(np.float32), data["bone"].astype(np.float32)[:, None],
                                mat[:, None]], axis=1).astype(np.float32)
        self.prog = self.ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        vbo = self.ctx.buffer(verts.tobytes())
        ibo = self.ctx.buffer(data["idx"].astype(np.uint32).tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(vbo, "3f 3f 1f 1f", "in_pos", "in_nrm", "in_bone", "in_mat")],
                                         index_buffer=ibo, index_element_size=4)
        n = len(names)
        pad = lambda a, k: np.concatenate([a, np.zeros((16 - n,) + a.shape[1:], np.float32)]).astype(np.float32)
        self.prog["mcol"].write(pad(data["mat_color"], 3).tobytes())
        self.prog["mmet"].write(pad(data["mat_metal"], 1).tobytes())
        self.prog["mrough"].write(pad(data["mat_rough"], 1).tobytes())
        self.prog["memit"].write(pad(data["mat_emission"], 1).tobytes())
        self.prog["visor_id"].value = self.visor_id
        self.prog["visor_box"].value = (float(lo[0]), float(lo[2]), float(hi[0] - lo[0]), float(hi[2] - lo[2]))
        self.prog["face"].value = 0
        self.prog["face_gain"].value = 1.6
        self.samples = samples
        # Qualidade adaptativa (ligada pelo app): do mais bonito ao mais leve, conforme o tempo real de desenho
        # (suavização de borda MSAA, escala da imagem interna). Placa fraca ou Raspberry: desce sozinho.
        self.adaptive = False
        self.ladder = ((samples, 1.0), (min(samples, 2), 0.85), (0, 0.7), (0, 0.55))
        self.q, self.scale = 0, 1.0
        self.render_ms, self._renders, self._q_changed = 0.0, 0, 0.0
        self.size = (0, 0)
        self.face_tex = None
        self.face_size = (0, 0)
        self.triangles = len(data["idx"]) // 3

    # ---- alvos de desenho -----------------------------------------------------
    def _targets(self, size):
        if size == self.size:
            return
        for name in ("fbo", "fbo_ms", "rb_c", "rb_d", "rb_cm", "rb_dm"):
            obj = getattr(self, name, None)
            if obj is not None:
                obj.release()
        w, h = size
        self.rb_c = self.ctx.renderbuffer((w, h), 4)
        self.rb_d = self.ctx.depth_renderbuffer((w, h))
        self.fbo = self.ctx.framebuffer([self.rb_c], self.rb_d)
        self.fbo_ms = self.rb_cm = self.rb_dm = None
        if self.samples > 1:
            try:
                self.rb_cm = self.ctx.renderbuffer((w, h), 4, samples=self.samples)
                self.rb_dm = self.ctx.depth_renderbuffer((w, h), samples=self.samples)
                self.fbo_ms = self.ctx.framebuffer([self.rb_cm], self.rb_dm)
            except Exception:
                self.fbo_ms = None
        self.size = size

    def _face(self, face: Optional[pygame.Surface]):
        if face is None:
            return
        fw, fh = face.get_size()
        if self.face_tex is None or self.face_size != (fw, fh):
            if self.face_tex is not None:
                self.face_tex.release()
            self.face_tex = self.ctx.texture((fw, fh), 3)
            self.face_tex.filter = (self.mgl.LINEAR, self.mgl.LINEAR)
            self.face_size = (fw, fh)
            # recorte do rosto (16:9) para a proporção do visor, centrado nos olhos
            fa = fw / fh
            if fa > self.visor_aspect:
                cw = self.visor_aspect / fa
                self.prog["face_crop"].value = ((1 - cw) / 2, 0.0, cw, 1.0)
            else:
                ch = fa / self.visor_aspect
                self.prog["face_crop"].value = (0.0, (1 - ch) / 2, 1.0, ch)
        self.face_tex.write(pygame.image.tobytes(face, "RGB"))

    # ---- quadro -------------------------------------------------------------
    def render(self, size: Tuple[int, int], face: Optional[pygame.Surface], now: float, gaze=(0.0, 0.0),
               talk: float = 0.0, bg=(10, 12, 16), reduce_motion: bool = False) -> pygame.Surface:
        t_start = time.perf_counter()
        w, h = max(16, int(size[0] * self.scale)), max(16, int(size[1] * self.scale))
        self._targets((w, h))
        self._face(face)
        pose = self.anim.pose(now, gaze, talk, reduce_motion)
        bob = 0.0 if reduce_motion else .006 * math.sin(now * 1.3)
        bones = self._bones                                  # buffer reutilizado (sem alocar 32 matrizes por quadro)
        bones[:] = _IDENT32
        M = self.rig.matrices(pose, bob)
        bones[:len(M)] = M
        np.copyto(self._bones_t, bones.transpose(0, 2, 1))
        self.prog["bones"].write(self._bones_t)
        eye, target = (0.0, -8.6, 2.30), (0.0, 0.0, 1.92)
        aspect = w / h
        half = max(1.95, 2.1 / aspect)                     # cabe dos braços abertos ao topo da cabeça
        fov = math.degrees(2 * math.atan(half / 8.6))
        vp = _perspective(fov, aspect, .5, 40) @ _look_at(eye, target)
        self.prog["vp"].write(vp.T.astype(np.float32).tobytes())
        self.prog["cam"].value = eye
        fb = self.fbo_ms or self.fbo
        fb.use()
        self.ctx.viewport = (0, 0, w, h)
        r, g, b = (c / 255.0 for c in bg[:3])
        fb.clear(r, g, b, 1.0, depth=1.0)
        self.ctx.enable(self.mgl.DEPTH_TEST)
        if self.face_tex is not None:
            self.face_tex.use(0)
        self.vao.render()
        if self.fbo_ms is not None:
            self.ctx.copy_framebuffer(self.fbo, self.fbo_ms)
        need = w * h * 3
        if self._readbuf is None or len(self._readbuf) != need:
            self._readbuf = bytearray(need)                  # buffer de leitura reutilizado enquanto o tamanho não muda
        self.fbo.read_into(self._readbuf, components=3, alignment=1)
        img = pygame.image.frombuffer(self._readbuf, (w, h), "RGB")
        img = pygame.transform.flip(img, False, True)        # OpenGL lê de baixo para cima; o flip já devolve uma Surface independente do buffer
        self._adapt((time.perf_counter() - t_start) * 1000.0)
        return img

    def _adapt(self, ms: float) -> None:
        self.render_ms = ms if self._renders == 0 else 0.9 * self.render_ms + 0.1 * ms
        self._renders += 1
        if not self.adaptive or self._renders % 20:
            return
        now = time.perf_counter()
        if self.render_ms > 14.0 and self.q < len(self.ladder) - 1:
            self._set_quality(self.q + 1, now)                       # pesado: desce um degrau
        elif (self.q > 0 and self.render_ms * (self.scale / self.ladder[self.q - 1][1]) ** 2 * 1.6 < 14.0
              and now - self._q_changed > 15.0):
            self._set_quality(self.q - 1, now)                       # folga de sobra e estável: sobe um degrau

    def set_adaptive(self, on: bool) -> None:
        """Liga/desliga o ajuste automático. Desligado = sempre a qualidade máxima, custe o que custar."""
        self.adaptive = bool(on)
        if not self.adaptive and self.q != 0:
            self._set_quality(0, time.perf_counter())

    def _set_quality(self, q: int, now: float) -> None:
        self.q, self._q_changed = q, now
        self.samples, self.scale = self.ladder[q]
        self.size = (0, 0)                                           # recria os alvos de desenho
        self._renders = 0

    def play(self, name: str, now: float, side: str = "right") -> bool:
        return self.anim.play(name, now, side)

    def cancel(self, now: float, arms=None, head: bool = True) -> int:
        return self.anim.cancel(now, arms, head)

    def release(self):
        try:
            self.ctx.release()
        except Exception:
            pass
