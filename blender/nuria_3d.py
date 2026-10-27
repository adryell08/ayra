"""NURIA 3D (base: AYRA V5) — malhas diretas, materiais portaveis e carenagem detalhada.
Blender: Scripting > Open > Run Script (Alt+P). F12: render.
Braços articulados (ombro, cotovelo, punho, dedos) com pivôs nomeados; o visor fica liso:
no app, os olhos atuais da Ayra aparecem nele em tempo real.
Exportar para o app: EXPORTAR = True grava nuria3d.npz em ~/AYRA_Modelo (copie para models/ da Ayra).
Sem abrir o Blender: NURIA_EXPORT=models/nuria3d.npz python -c "exec(open('blender/nuria_3d.py').read())" (modulo bpy).
Cria uma NOVA cena; não apaga a cena anterior. Frente = -Y; altura = Z.
Geometria conceitual aproximada da referencia, nao uma replica CAD.
Execute este arquivo completo no editor de texto do Blender.
Sem dependências, downloads ou texturas externas. Medidas visuais, não CAD.
SAVE_BLEND e opcional; exportacao pelo menu File > Export. Logomarca em texto é aproximação.
"""
import bpy
import bmesh
import math
import os
from mathutils import Vector, Matrix
from mathutils.geometry import tessellate_polygon
from math import sin, cos, pi, sqrt

SAVE_BLEND = False
EXPORTAR = False      # True: grava nuria3d.npz em OUTPUT_DIR (copie para a pasta models/ da Ayra). Blender 4.1+
OUTPUT_DIR = os.path.join(os.path.expanduser('~'), 'AYRA_Modelo')
SAMPLES = 64

scene = bpy.data.scenes.new('AYRA_V5_STUDIO')
if bpy.context.window:
    bpy.context.window.scene = scene
collection = bpy.data.collections.new('AYRA | Modelagem')
scene.collection.children.link(collection)
studio = bpy.data.collections.new('AYRA | Estudio')
scene.collection.children.link(studio)

def material(name, color, metal=0, rough=.3, emission=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    if hasattr(m, 'use_nodes'): m.use_nodes = True
    m.node_tree.nodes.clear()
    p = m.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    out = m.node_tree.nodes.new('ShaderNodeOutputMaterial')
    m.node_tree.links.new(p.outputs['BSDF'], out.inputs['Surface'])
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Metallic'].default_value = metal
    p.inputs['Roughness'].default_value = rough
    if 'Coat Weight' in p.inputs:
        p.inputs['Coat Weight'].default_value = .12
    if emission:
        socket = p.inputs.get('Emission Color') or p.inputs.get('Emission')
        if socket is not None: socket.default_value = (*color, 1)
        strength = p.inputs.get('Emission Strength')
        if strength is not None: strength.default_value = emission
    return m

WHITE = material('01 | Ceramica branca', (.83,.85,.89), .03, .28)
BLACK = material('02 | Visor obsidiana', (.0015,.003,.006), .12, .19)
DARK = material('03 | Juntas grafite', (.018,.023,.033), .6, .3)
NAVY = material('04 | Azul institucional', (.008,.025,.105), .4, .24)
PINK = material('05 | Rosa Afya', (.68,.001,.12), .02, .26)
LED = material('06 | LED azul', (.003,.055,1), .05, .23, 7)
CYAN = material('07 | Nucleo luminoso', (.02,.42,1), .1, .2, 3)
SEAM = material('08 | Linhas de montagem', (.25,.29,.36), .3, .36)
LENS = material('09 | Lente camera', (.005,.065,.12), .7, .1)

def empty(name, loc=(0,0,0), parent=None):
    o = bpy.data.objects.new(name, None)
    collection.objects.link(o)
    o.location = loc
    o.parent = parent
    o.empty_display_size = .12
    return o

root = empty('AYRA_ROOT')
head = empty('HEAD_PIVOT', (0,0,2.16), root)
torso = empty('TORSO', parent=root)

def attach(o, mat, parent=root, target=collection):
    # Data API only: works in Text Editor, Python Console and background.
    if target not in list(o.users_collection): target.objects.link(o)
    for c in list(o.users_collection):
        if c != target: c.objects.unlink(o)
    if mat: o.data.materials.append(mat)
    if parent:
        o.parent = parent
        # All model parent empties are unrotated: exact local inverse.
        offset = Vector((0,0,0)); ancestor = parent
        while ancestor:
            offset += ancestor.location
            ancestor = ancestor.parent
        o.matrix_parent_inverse = Matrix.Translation(-offset)
    return o

def mesh(name, verts, faces, mat, parent=root, sub=0):
    d = bpy.data.meshes.new(name)
    d.from_pydata(verts, [], faces)
    d.update()
    bm = bmesh.new()
    bm.from_mesh(d)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(d)
    bm.free()
    o = bpy.data.objects.new(name, d)
    collection.objects.link(o)
    attach(o, mat, parent)
    for p in d.polygons:
        p.use_smooth = True
    if sub:
        m = o.modifiers.new('Superficies suaves', 'SUBSURF')
        m.levels = sub
        m.render_levels = sub
    return o

def loft(name, rows, mat, parent=root, n=64, sub=2):
    # rows: z, half width, front/back radius, center Y, center X.
    v = [(cx+rx*sin(2*pi*j/n), cy-ry*cos(2*pi*j/n), z)
         for z,rx,ry,cy,cx in rows for j in range(n)]
    f = [tuple(reversed(range(n)))]
    for k in range(len(rows)-1):
        for j in range(n):
            a=k*n+j; b=k*n+(j+1)%n
            f.append((a,b,b+n,a+n))
    f.append(tuple((len(rows)-1)*n+j for j in range(n)))
    return mesh(name,v,f,mat,parent,sub)

def tube(name, points, radius, mat, parent=root, cyclic=False):
    d=bpy.data.curves.new(name,'CURVE'); d.dimensions='3D'
    d.resolution_u=16; d.bevel_depth=radius; d.bevel_resolution=3
    s=d.splines.new('POLY'); s.points.add(len(points)-1)
    for p, co in zip(s.points,points): p.co=(*co,1)
    s.use_cyclic_u=cyclic
    o=bpy.data.objects.new(name,d); collection.objects.link(o)
    return attach(o,mat,parent)

def box(name, loc, size, mat, bevel=.04, parent=root):
    vs=[(a*size[0]/2,b*size[1]/2,c*size[2]/2)
        for a,b,c in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                      (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]
    fs=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    o=mesh(name,vs,fs,mat,parent); o.location=loc
    b=o.modifiers.new('Bordas usinadas','BEVEL'); b.width=min(bevel,min(size)*.45); b.segments=4
    o.modifiers.new('Normais ponderadas','WEIGHTED_NORMAL')
    return o

def cylinder(name, loc, radius, depth, mat, axis='Z', parent=root):
    n=64; vs=[]
    for z in (-depth/2,depth/2):
        for i in range(n):
            a=2*pi*i/n; x=radius*cos(a); y=radius*sin(a)
            vs.append((z,x,y) if axis=='X' else ((x,z,y) if axis=='Y' else (x,y,z)))
    fs=[tuple(range(n-1,-1,-1)),tuple(range(n,2*n))]
    fs.extend((i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n))
    o=mesh(name,vs,fs,mat,parent); o.location=loc
    b=o.modifiers.new('Arestas suaves','BEVEL'); b.width=min(.012,depth*.25); b.segments=3
    o.modifiers.new('Normais','WEIGHTED_NORMAL')
    return o

def text(name, body, loc, size, mat, parent=root, rotation=(pi/2,0,0)):
    d=bpy.data.curves.new(name,'FONT'); d.body=body; d.align_x='CENTER'
    d.align_y='CENTER'; d.size=size; d.extrude=.001; d.bevel_depth=.0005
    o=bpy.data.objects.new(name,d); collection.objects.link(o)
    o.location=loc; o.rotation_euler=rotation
    return attach(o,mat,parent)

def emblem_A(name, surface, size, mat, parent):
    shapes=[ [(-.50,-.50),(-.025,.64),(.07,.64),(-.27,-.28)],
             [(-.025,.64),(.07,.64),(.50,-.50),(.31,-.35)],
             [(-.50,-.50),(-.16,-.18),(.09,-.12),(.39,-.35),(.10,-.24),(-.14,-.30)] ]
    verts=[]; faces=[]
    for poly in shapes:
        points=[Vector((u,v,0)) for u,v in poly]
        for tri in tessellate_polygon([points]):
            start=len(verts)
            coords=[points[p] if isinstance(p,int) else p for p in tri]
            verts.extend(surface(p.x*size,p.y*size) for p in coords)
            faces.append((start,start+1,start+2))
    obj=mesh(name,verts,faces,mat,parent)
    thick=obj.modifiers.new('Relevo do simbolo','SOLIDIFY'); thick.thickness=.0015
    return obj

# Main helmet: vertically shaped rings create a real pointed chin and dome.
PROFILE=[(2.23,.055,.035,-.34),(2.25,.13,.075,-.315),
 (2.32,.25,.16,-.25),(2.44,.385,.285,-.13),
 (2.60,.50,.415,-.015),(2.81,.575,.48,.012),
 (3.05,.595,.49,.018),(3.27,.565,.47,.035),
 (3.43,.485,.40,.048),(3.56,.33,.29,.06),
 (3.625,.14,.14,.06),(3.64,.005,.008,.06)]

def profile(z):
    # Cubic Hermite interpolation avoids visible horizontal profile bands.
    for k,(a,b) in enumerate(zip(PROFILE,PROFILE[1:])):
        if a[0]<=z<=b[0]:
            prev=PROFILE[max(0,k-1)]; nxt=PROFILE[min(len(PROFILE)-1,k+2)]
            t=(z-a[0])/(b[0]-a[0]); h=b[0]-a[0]
            out=[]
            for j in (1,2,3):
                m0=(b[j]-prev[j])/(b[0]-prev[0])
                m1=(nxt[j]-a[j])/(nxt[0]-a[0])
                out.append((2*t**3-3*t*t+1)*a[j]+(t**3-2*t*t+t)*h*m0
                    +(-2*t**3+3*t*t)*b[j]+(t**3-t*t)*h*m1)
            return out
    return PROFILE[0][1:] if z<PROFILE[0][0] else PROFILE[-1][1:]

def jaw_ridge(x,z):
    return .027*max(0,1-abs(z-2.43)/.23)*max(0,1-abs(x)/.32)

v=[]; f=[]; levels=92; segments=128
for k in range(levels):
    z=2.23+(3.64-2.23)*k/(levels-1); rx,ry,cy=profile(z)
    for j in range(segments):
        a=2*pi*j/segments; x=rx*sin(a); y=cy-ry*cos(a)
        if cos(a)>0: y-=jaw_ridge(x,z)*cos(a)**4
        v.append((x,y,z))
for k in range(levels-1):
    for j in range(segments):
        a=k*segments+j; b=k*segments+(j+1)%segments
        f.append((a,b,b+segments,a+segments))
f.extend([tuple(reversed(range(segments))),tuple((levels-1)*segments+j for j in range(segments))])
mesh('HEAD_SHELL | Queixo projetado e cranio',v,f,WHITE,head)

def front_y(x,z,offset=0):
    rx,ry,cy=profile(z)
    return cy-ry*sqrt(max(.025,1-(x/rx)**2))-offset-jaw_ridge(x,z)

def visor_point(u,v,offset=.017):
    # Rounded rectangular perimeter, tapered lower edge, curved in depth.
    z=2.89+.365*v
    width=.538*(.85+.15*(v+1)/2)
    x=width*u
    return (x,front_y(x,z,offset),z)

N=128; rings=14; verts=[visor_point(0,0)]; faces=[]; outline=[]
for k in range(1,rings+1):
    r=k/rings
    for i in range(N):
        a=2*pi*i/N
        u=math.copysign(abs(cos(a))**.52,cos(a))*r
        v=math.copysign(abs(sin(a))**.65,sin(a))*r
        p=visor_point(u,v); verts.append(p)
        if k==rings: outline.append(p)
for i in range(N): faces.append((0,1+i,1+(i+1)%N))
for k in range(rings-1):
    s=1+k*N; t=s+N
    for i in range(N): faces.append((s+i,s+(i+1)%N,t+(i+1)%N,t+i))
visor=mesh('FACE_VISOR',verts,faces,BLACK,head)
tube('VISOR | Junta perimetral',outline,.009,DARK,head,True)

# Olhos e boca: sem malha. O app projeta no visor o rosto de LEDs atual da Ayra.

# Crown seams and pink inserts follow the curved shell surface.
for s in (-1,1):
    points=[]
    for i in range(45):
        z=3.245+i*.008
        rx,ry,cy=profile(z); x=s*rx*.59
        points.append((x,front_y(x,z,.004),z))
    tube('HELMET | Junta frontal',points,.0028,SEAM,head)

def crown_patch(name, angle, z0,z1,width,mat):
    v=[]; f=[]
    for k in range(16):
        z=z0+(z1-z0)*k/15; rx,ry,cy=profile(z)
        for j in range(7):
            a=angle+(j/6-.5)*width
            v.append(((rx+.008)*sin(a),cy-(ry+.008)*cos(a),z))
    for k in range(15):
        for j in range(6):
            a=k*7+j; f.append((a,a+1,a+8,a+7))
    return mesh(name,v,f,mat,head)
crown_patch('HELMET | Faixa rosa superior',0,3.52,3.62,.34,PINK)
for s in (-1,1):
    crown_patch('HELMET | Rosa lateral',s*1.3,3.29,3.39,.43,PINK)
    crown_patch('HELMET | Rosa inferior',s*1.65,2.46,2.54,.33,PINK)

# Cheek-to-chin joints, following the lower face shell.
for side in (-1,1):
    pts=[]
    for i in range(50):
        t=i/49; z=2.255+.40*t; rx,ry,cy=profile(z)
        angle=.42+1.12*t
        x=side*rx*sin(angle); y=cy-ry*cos(angle)-.003
        pts.append((x,y,z))
    tube('HELMET | Recorte da mandibula',pts,.0022,SEAM,head)
# Crown stripe continues over the apex to the back of the helmet.
for angle in (0,pi): crown_patch('HELMET | Faixa central',angle,3.51,3.625,.25,PINK)

# Camera module mounted on forehead, with optical glass and internal lens.
camera=box('CAMERA', (0,-.422,3.385),(.155,.065,.185),DARK,.025,head)
camera.rotation_euler[0]=math.radians(-18)
cylinder('CAMERA | Aro',(0,-.463,3.386),.049,.018,BLACK,'Y',head)
cylinder('CAMERA | Vidro',(0,-.476,3.386),.033,.009,LENS,'Y',head)
cylinder('CAMERA | Pupila',(0,-.483,3.386),.015,.004,BLACK,'Y',head)

# Layered ear discs, silicone seams, glowing rings and white A emblem.
for s,label in [(-1,'LEFT'),(1,'RIGHT')]:
    for name,x,r,d,m in [('Carenagem',.538,.335,.07,WHITE),('Base',.565,.31,.075,DARK),('Aro',.591,.293,.06,NAVY),
                         ('Disco',.629,.258,.025,BLACK)]:
        cylinder('EAR_'+label+' | '+name,(s*x,.035,2.98),r,d,m,'X',head)
    pts=[(s*.647,.035+.239*cos(2*pi*i/128),2.98+.239*sin(2*pi*i/128)) for i in range(128)]
    tube('EAR_'+label+' | LED',pts,.009,LED,head,True)
    pts=[(s*.65,.035+.249*cos(2*pi*i/128),2.98+.249*sin(2*pi*i/128)) for i in range(128)]
    tube('EAR_'+label+' | Halo',pts,.003,CYAN,head,True)
    emblem_A('EAR_'+label+' | Emblema A',
        lambda u,v: (s*.654,.035+s*u,2.974+v),.26,WHITE,head)
    for i in range(12):
        a=i*2*pi/12
        tube('EAR | Recorte radial',[(s*.62,.035+.275*cos(a),2.98+.275*sin(a)),
             (s*.62,.035+.29*cos(a),2.98+.29*sin(a))],.003,DARK,head)
box('HELMET | Ventilacao traseira',(0,.454,2.97),(.24,.045,.34),DARK,.04,head)
for i in range(6):
    box('HELMET | Aleta traseira',(0,.482,2.86+i*.043),(.185,.013,.014),BLACK,.005,head)

# Compact neck, mechanical collar, and shaped breastplate.
loft('NECK',[(1.70,.23,.21,0,0),(1.75,.25,.23,0,0),
 (1.97,.19,.185,0,0),(2.08,.18,.17,0,0)],DARK,root,48,1)
for s in (-1,1):
    tube('NECK | Tendao',[(s*.19,-.09,1.8),(s*.17,-.10,1.96),(s*.23,.02,2.10)],.035,BLACK)
cylinder('NECK | Junta inferior',(0,0,1.77),.28,.09,BLACK)
# Wider chest and continuous shoulder shells, designed as surface meshes.
CHEST=[(.46,.48,.32,0,0),(.49,.53,.36,0,0),(.72,.64,.425,0,0),
       (1.04,.73,.455,0,0),(1.36,.79,.435,0,0),(1.55,.77,.37,.018,0),
       (1.67,.70,.315,.025,0),(1.73,.61,.285,.025,0),(1.78,.37,.25,.025,0)]

def row_at(rows,z):
    for a,b in zip(rows,rows[1:]):
        if a[0]<=z<=b[0]:
            t=(z-a[0])/(b[0]-a[0])
            return tuple(a[i]*(1-t)+b[i]*t for i in range(1,5))
    r=rows[0] if z<rows[0][0] else rows[-1]
    return tuple(r[1:])

loft('TORSO | Estrutura',[(z,rx*.97,ry*.96,cy,cx) for z,rx,ry,cy,cx in CHEST],DARK,torso)
loft('TORSO | Carenagem principal',CHEST,WHITE,torso,96,2)

def chest_y(x,z,offset=.015,back=False):
    rx,ry,cy,cx=row_at(CHEST,z)
    return cy+(1 if back else -1)*(ry*sqrt(max(.02,1-((x-cx)/rx)**2))+offset)

# Broad, hollow raised collar.
v=[]; f=[]
for z,rx,ry in [(1.67,.43,.31),(1.85,.32,.27),(1.855,.29,.24),(1.67,.38,.275)]:
    for i in range(80):
        a=2*pi*i/80; v.append((rx*sin(a),ry*cos(a)+.025,z))
for k in range(4):
    for i in range(80): f.append((k*80+i,k*80+(i+1)%80,((k+1)%4)*80+(i+1)%80,((k+1)%4)*80+i))
mesh('NECK | Gola elevada',v,f,WHITE,root,1)

# ---- Braços articulados: peças rígidas presas a pivôs (ombro > cotovelo > punho > dedos) ----
SHOULDER_PIVOT=1.30   # altura do pivô do ombro (centro da ombreira)
ELBOW_Z=.52; WRIST_Z=.03
HAND_SCALE=1.45      # mãos maiores que o real: gestos legíveis de longe

def sphere(name, center, r, mat, parent, n=32):
    cx,cy,cz=center
    rows=[(cz-r*cos(pi*k/12), max(r*sin(pi*k/12),.0005), max(r*sin(pi*k/12),.0005), cy, cx) for k in range(13)]
    return loft(name,rows,mat,parent,n,1)

def capsule(name, a, b, r, mat, parent, n=24):
    """Segmento arredondado de a até b, em coordenadas do mundo (pose de repouso)."""
    a=Vector(a); b=Vector(b); axis=(b-a); L=axis.length; axis.normalize()
    q=Vector((0,0,1)).rotation_difference(axis)
    rows=[]
    for k in range(7):                       # meia esfera de baixo
        t=pi/2*k/6; rows.append((-r*cos(t), max(r*sin(t),.0005)))
    for k in range(1,7):
        t=pi/2*k/6; rows.append((L+r*sin(t), max(r*cos(t),.0005)))
    v=[]; f=[]
    for z,rr in rows:
        for j in range(n):
            p=Vector((rr*cos(2*pi*j/n), rr*sin(2*pi*j/n), z))
            v.append(tuple(a+q@p))
    for k in range(len(rows)-1):
        for j in range(n):
            i0=k*n+j; i1=k*n+(j+1)%n; f.append((i0,i1,i1+n,i0+n))
    f.append(tuple(reversed(range(n)))); f.append(tuple((len(rows)-1)*n+j for j in range(n)))
    return mesh(name,v,f,mat,parent,1)

def hoop(name, center, r, thick, mat, parent, axis='Z'):
    cx,cy,cz=center; pts=[]
    for i in range(96):
        a=2*pi*i/96
        pts.append((cx+r*cos(a),cy+r*sin(a),cz) if axis=='Z' else (cx,cy+r*cos(a),cz+r*sin(a)))
    return tube(name,pts,thick,mat,parent,True)

FINGERS=[('INDICADOR',-.058,.020,.078,.062),('MEDIO',-.019,.021,.086,.066),
         ('ANELAR',.020,.020,.080,.062),('MINIMO',.057,.017,.066,.052)]

def build_forearm_and_hand(side, label, upper):
    x=side*1.06
    elbow=empty('ELBOW_'+label,(x-side*1.0,0,ELBOW_Z-SHOULDER_PIVOT),upper)
    cylinder('ELBOW_'+label+' | Dobradica',(x,0,ELBOW_Z),.135,.25,DARK,'X',elbow)
    for s2 in (-1,1):
        cylinder('ELBOW_'+label+' | Tampa',(x+s2*.128,0,ELBOW_Z),.09,.02,NAVY,'X',elbow)
    loft('FOREARM_'+label,[(.075,.105,.115,0,x),(.13,.135,.145,0,x),(.27,.165,.175,0,x),
         (.40,.17,.18,0,x),(.46,.15,.16,0,x),(.50,.10,.11,0,x)],WHITE,elbow,64,2)
    hoop('FOREARM_'+label+' | Faixa rosa',(x,0,.37),.172,.012,PINK,elbow)
    hoop('FOREARM_'+label+' | LED do punho',(x,0,.105),.118,.010,LED,elbow)
    wrist=empty('WRIST_'+label,(0,0,WRIST_Z-ELBOW_Z),elbow)
    k=HAND_SCALE
    sphere('WRIST_'+label+' | Junta',(x,0,WRIST_Z),.085,DARK,wrist)
    # Palma: espessura em X, largura em Y; a face interna (-side*X) é a palma.
    box('HAND_'+label+' | Dorso',(x,0,WRIST_Z-.105*k),(.075*k,.165*k,.15*k),WHITE,.03*k,wrist)
    box('HAND_'+label+' | Palma',(x-side*.04*k,0,WRIST_Z-.11*k),(.012*k,.135*k,.12*k),DARK,.006*k,wrist)
    base=WRIST_Z-.185*k
    for fname,y,r,l1,l2 in FINGERS:
        y,r,l1,l2=y*k,r*k,l1*k,l2*k
        p1=empty('FINGER_'+label+'_'+fname+'_1',(0,y,base-WRIST_Z),wrist)
        sphere('FINGER_'+label+'_'+fname+' | Junta 1',(x,y,base),r*1.08,DARK,p1,16)
        capsule('FINGER_'+label+'_'+fname+' | Falange 1',(x,y,base-r*.9),(x,y,base-l1+r*.6),r,WHITE,p1)
        p2=empty('FINGER_'+label+'_'+fname+'_2',(0,0,-l1),p1)
        sphere('FINGER_'+label+'_'+fname+' | Junta 2',(x,y,base-l1),r*.98,DARK,p2,16)
        capsule('FINGER_'+label+'_'+fname+' | Falange 2',(x,y,base-l1-r*.8),(x,y,base-l1-l2+r),r*.95,WHITE,p2)
    # Polegar: sai da borda frontal da palma, para baixo e para frente.
    t0=Vector((x-side*.02*k,-.085*k,WRIST_Z-.075*k)); d=Vector((-side*.22,-.55,-.80)).normalized()
    t1=empty('THUMB_'+label+'_1',tuple(t0-Vector((x,0,WRIST_Z))),wrist)
    sphere('THUMB_'+label+' | Junta 1',tuple(t0),.026*k,DARK,t1,16)
    capsule('THUMB_'+label+' | Falange 1',tuple(t0+d*.02*k),tuple(t0+d*.065*k),.023*k,WHITE,t1)
    t2=empty('THUMB_'+label+'_2',tuple(d*.075*k),t1)
    sphere('THUMB_'+label+' | Junta 2',tuple(t0+d*.075*k),.022*k,DARK,t2,16)
    capsule('THUMB_'+label+' | Falange 2',tuple(t0+d*.095*k),tuple(t0+d*.13*k),.021*k,WHITE,t2)

for side,label in [(-1,'LEFT'),(1,'RIGHT')]:
    cylinder('SHOULDER_'+label+' | Junta',(side*.795,0,1.40),.35,.09,BLACK,'X',torso)
    upper=empty('SHOULDER_'+label,(side*1.0,0,SHOULDER_PIVOT),torso)
    rows=[(.83,.14,.19,0,side*1.02),(.88,.205,.27,0,side*1.04),
          (1.04,.285,.355,0,side*1.045),(1.30,.305,.385,0,side*1.025),
          (1.57,.275,.35,0,side*.985),(1.74,.18,.25,0,side*.91),
          (1.81,.08,.135,0,side*.85)]
    loft('SHOULDER_'+label+' | Ombreira',rows,WHITE,upper,96,2)

    def shoulder_surface(u,v,offset=.014):
        # Graphic coordinates on the outer-front quadrant of the shoulder.
        y=-.105+side*u; z=1.23+v
        rx,ry,cy,cx=row_at(rows,z)
        x=cx+side*(rx*sqrt(max(.02,1-((y-cy)/ry)**2))+offset)
        return (x,y,z)

    # Dense radial patch stays curved and flush, including its centre.
    boundary=[(-.16,-.20),(-.235,-.01),(-.16,.215),(.135,.23),(.235,.015),(.16,-.20)]
    vs=[shoulder_surface(0,0,.016)]; fs=[]
    outline=[]
    for j in range(len(boundary)):
        a=boundary[j]; b=boundary[(j+1)%len(boundary)]
        for k in range(8):
            t=k/8; outline.append((a[0]*(1-t)+b[0]*t,a[1]*(1-t)+b[1]*t))
    n=len(outline)
    for ring in range(1,9):
        for u,v in outline: vs.append(shoulder_surface(u*ring/8,v*ring/8,.018))
    fs.extend((0,1+j,1+(j+1)%n) for j in range(n))
    for ring in range(7):
        k=1+ring*n
        fs.extend((k+j,k+(j+1)%n,k+n+(j+1)%n,k+n+j) for j in range(n))
    mesh('SHOULDER_'+label+' | Escudo curvo',vs,fs,NAVY,upper)
    tube('SHOULDER_'+label+' | Contorno', [shoulder_surface(u,v,.023) for u,v in outline],.005,NAVY,upper,True)
    emblem_A('SHOULDER_'+label+' | A da Afya',
             lambda u,v: shoulder_surface(u,v,.034),.245,WHITE,upper)

    # Pink stripe mapped onto upper shell, no floating rectangular block.
    vs=[]; fs=[]
    for k in range(12):
        z=1.57+k*.010
        rx,ry,cy,cx=row_at(rows,z)
        for j in range(8):
            a=-.77+j*.048
            vs.append((cx+side*(rx+.005)*cos(a),cy+(ry+.008)*sin(a),z))
    for k in range(11):
        for j in range(7):
            t=k*8+j; fs.append((t,t+1,t+9,t+8))
    mesh('SHOULDER_'+label+' | Faixa rosa',vs,fs,PINK,upper)

    # Broad angular white plate encloses the blue light on each chest side.
    edge=[(.70,1.56),(.70,1.36),(.68,1.22),(.55,1.08),(.50,.96),(.44,.61)]
    pts=[]
    for (x0,z0),(x1,z1) in zip(edge,edge[1:]):
        for k in range(12):
            t=k/12; x=side*(x0*(1-t)+x1*t); z=z0*(1-t)+z1*t
            pts.append((x,chest_y(x,z,.028),z))
    x,z=side*edge[-1][0],edge[-1][1]; pts.append((x,chest_y(x,z,.028),z))
    tube('TORSO | Canal '+label,pts,.028,NAVY,torso)
    tube('TORSO | LED '+label,[(x,y-.019,z) for x,y,z in pts],.010,LED,torso)
    tube('TORSO | Nucleo '+label,[(x,y-.025,z) for x,y,z in pts],.003,CYAN,torso)
    # Lower armor wing: a separate sculpted strip with rounded edges.
    vs=[]; fs=[]
    for k in range(20):
        z=.55+k*.032
        inner=.42+(z-.55)*.26
        rx,ry,cy,cx=row_at(CHEST,z)
        for j in range(7):
            x=side*(inner+(rx*.96-inner)*j/6)
            vs.append((x,chest_y(x,z,.03+sin(pi*j/6)*.018),z))
    for k in range(19):
        for j in range(6):
            t=k*7+j; fs.append((t,t+1,t+8,t+7))
    wing=mesh('TORSO | Placa lateral '+label,vs,fs,WHITE,torso,1)
    thick=wing.modifiers.new('Espessura','SOLIDIFY'); thick.thickness=.012
    pink=box('TORSO | Clavicula rosa',(side*.51,-.10,1.696),(.21,.115,.025),PINK,.01,torso)
    pink.rotation_euler[1]=side*.32
    loft('ARM_'+label,[(.59,.19,.23,0,side*1.06),(.62,.21,.24,0,side*1.06),
        (.77,.21,.25,0,side*1.055),(.97,.18,.23,0,side*1.045),(1.12,.16,.2,0,side*1.03)],WHITE,upper,64,2)
    # A quiet cuff seam gives the short upper arm a deliberate finish.
    cuff=[(side*1.06+.20*sin(2*pi*i/64),.23*cos(2*pi*i/64),.65) for i in range(64)]
    tube('ARM_'+label+' | Encaixe',cuff,.003,SEAM,upper,True)
    build_forearm_and_hand(side,label,upper)

emblem_A('BRANDING | A institucional',lambda u,v:(-.18+u,-.468,1.31+v),.23,PINK,torso)
brand=text('BRANDING | fya','fya',(.075,-.47,1.33),.255,PINK,torso)
brand.data.offset=.003
brand.data.shear=.035
uni=text('BRANDING | Universidade','UNIGRANRIO',(0,-.478,1.145),.094,NAVY,torso)
uni.data.offset=.001
text('BRANDING | AYRA','A Y R A',(0,-.405,.68),.057,SEAM,torso)
emblem_A('BRANDING | A traseiro',lambda u,v:(u,chest_y(u,1.34+v,.02,True),1.34+v),.30,NAVY,torso)
box('TORSO | Painel traseiro',(0,.433,.86),(.36,.04,.26),DARK,.03,torso)
for i in range(5): box('TORSO | Respiro',(0,.46,.79+i*.033),(.27,.012,.012),BLACK,.004,torso)

# Shorter neck brings the proportions closer to the concept sheet.
head.location.z -= .22
head.scale=(1.16,1.13,1.07)

# Display pedestal; separated from the model export.
ped=cylinder('STUDIO | Base',(0,0,.37),.72,.12,NAVY,parent=None)
attach(ped,None,None,studio)
floor=box('STUDIO | Piso',(0,0,.245),(200,200,.1),material('Fundo',(.26,.33,.43),0,.7),.01,None)
attach(floor,None,None,studio)

def aim(o,target): o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler()
def area(name,loc,power,color,size):
    d=bpy.data.lights.new(name,'AREA'); d.energy=power; d.color=color; d.shape='DISK'; d.size=size
    o=bpy.data.objects.new(name,d); studio.objects.link(o); o.location=loc; aim(o,(0,0,2))
area('LIGHTING | Softbox principal',(-3,-4,6),550,(.83,.91,1),4)
area('LIGHTING | Preenchimento',(3,-2,3.7),260,(1,.89,.87),3)
area('LIGHTING | Recorte',(1.5,2.7,4.8),650,(.45,.65,1),3)
area('LIGHTING | Topo',(-1,1,6),450,(1,1,1),2)

for name,loc in [('Tres_quartos',(4.8,-7.7,3.10)),('Frontal',(0,-8,2.8)),('Lateral',(7,-.2,2.9))]:
    d=bpy.data.cameras.new(name); o=bpy.data.objects.new('VIEW | '+name,d)
    studio.objects.link(o); o.location=loc; aim(o,(0,0,2.14))
    d.type='ORTHO'; d.ortho_scale=3.72; d.lens=60
    if name=='Tres_quartos': scene.camera=o

scene.world=bpy.data.worlds.new('AYRA | Ambiente')
# Explicit nodes: never look up an English/localized default node name.
if hasattr(scene.world, 'use_nodes'): scene.world.use_nodes=True
wt=scene.world.node_tree
wt.nodes.clear()
background=wt.nodes.new('ShaderNodeBackground')
world_output=wt.nodes.new('ShaderNodeOutputWorld')
background.inputs[0].default_value=(.17,.21,.3,1)
background.inputs[1].default_value=.3
wt.links.new(background.outputs[0],world_output.inputs['Surface'])
scene.render.engine='CYCLES'; scene.cycles.samples=SAMPLES
scene.cycles.use_denoising=True
scene.render.resolution_x=1200; scene.render.resolution_y=1200
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.view_settings.exposure=-.10
try: scene.view_settings.view_transform='AgX'
except TypeError: pass

# Emissive meshes work without version-specific compositor nodes.
# Add Glare/Fog Glow manually in Compositing if a stronger halo is desired.

# Color visible even in Solid mode; default preview uses Material Preview.
for screen in bpy.data.screens:
    for a in screen.areas:
        if a.type=='VIEW_3D':
            a.spaces.active.shading.color_type='MATERIAL'
            a.spaces.active.shading.type='MATERIAL'
            a.spaces.active.overlay.show_overlays=False
            a.spaces.active.region_3d.view_perspective='CAMERA'

# No selection operators: safe even if executed with a pinned editor.
head['descricao']='Pivo da cabeca. Frente -Y; Z = giro horizontal, X = inclinacao.'
root['referencia']='Busto conceitual AYRA. Nao e projeto mecanico validado.'
root['uso']='F12 renderiza. Cameras frontal e lateral disponiveis na colecao Estudio.'

# ---- Exportação para o app (models/nuria3d.npz): peças rígidas + pivôs, pose de repouso ----
def exportar(path):
    import numpy as np
    dg=bpy.context.evaluated_depsgraph_get()
    bones=[o for o in collection.objects if o.type=='EMPTY']
    bindex={o.name:i for i,o in enumerate(bones)}
    mats=[]; mindex={}
    P=[]; N=[]; Mt=[]; B=[]
    objs=[o for o in collection.objects if o.type in {'MESH','CURVE','FONT'}]
    objs.append(bpy.data.objects['STUDIO | Base'])
    for o in objs:
        owner=o.parent
        while owner is not None and owner.type!='EMPTY': owner=owner.parent
        bone=bindex.get(owner.name,0) if owner else 0
        mat=o.data.materials[0] if len(o.data.materials) else None
        key='10 | Visor do rosto' if o.name=='FACE_VISOR' else (mat.name if mat else 'Fundo')
        if key not in mindex:
            node=mat.node_tree.nodes.get('Principled BSDF') if mat else None
            node=node or (next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None) if mat else None)
            g=lambda s,d: (node.inputs[s].default_value if node and s in node.inputs else d)
            col=tuple(g('Base Color',(.5,.5,.5,1)))[:3]
            em=g('Emission Strength',0.0)
            mindex[key]=len(mats); mats.append((key,col,float(g('Metallic',0)),float(g('Roughness',.5)),float(em)))
        oe=o.evaluated_get(dg); me=oe.to_mesh()
        me.calc_loop_triangles()
        nt=len(me.loop_triangles)
        if nt==0:
            oe.to_mesh_clear(); continue
        loops=np.empty(nt*3,np.int32); me.loop_triangles.foreach_get('loops',loops)
        vidx=np.empty(len(me.loops),np.int32); me.loops.foreach_get('vertex_index',vidx)
        co=np.empty(len(me.vertices)*3,np.float32); me.vertices.foreach_get('co',co); co=co.reshape(-1,3)
        cn=np.empty(len(me.loops)*3,np.float32); me.corner_normals.foreach_get('vector',cn); cn=cn.reshape(-1,3)
        W=np.array(oe.matrix_world,np.float32); Wn=np.array(oe.matrix_world.inverted_safe().transposed().to_3x3(),np.float32)
        pos=co[vidx[loops]]@W[:3,:3].T+W[:3,3]
        nrm=cn[loops]@Wn.T; nrm/=np.maximum(np.linalg.norm(nrm,axis=1,keepdims=True),1e-8)
        P.append(pos); N.append(nrm); Mt.append(np.full(len(pos),mindex[key],np.uint8)); B.append(np.full(len(pos),bone,np.uint8))
        oe.to_mesh_clear()
    P=np.concatenate(P); N=np.concatenate(N); Mt=np.concatenate(Mt); B=np.concatenate(B)
    keyarr=np.concatenate([np.round(P*2e4),np.round(N*500),Mt[:,None],B[:,None]],axis=1).astype(np.int64)
    _,first,inv=np.unique(keyarr,axis=0,return_index=True,return_inverse=True)
    os.makedirs(os.path.dirname(path) or '.',exist_ok=True)
    np.savez_compressed(path,pos=P[first].astype(np.float32),nrm=N[first].astype(np.float16),mat=Mt[first],
        bone=B[first],idx=inv.reshape(-1).astype(np.uint32),
        bone_names=np.array([o.name for o in bones]),
        bone_parent=np.array([bindex.get(o.parent.name,-1) if o.parent else -1 for o in bones],np.int16),
        bone_pivot=np.array([tuple(o.matrix_world.translation) for o in bones],np.float32),
        mat_names=np.array([m[0] for m in mats]),mat_color=np.array([m[1] for m in mats],np.float32),
        mat_metal=np.array([m[2] for m in mats],np.float32),mat_rough=np.array([m[3] for m in mats],np.float32),
        mat_emission=np.array([m[4] for m in mats],np.float32))
    print('Exportado:',path,len(first),'vertices',len(inv)//3,'triangulos',len(bones),'pivos')

if EXPORTAR or os.environ.get('NURIA_EXPORT'):
    exportar(os.environ.get('NURIA_EXPORT') or os.path.join(OUTPUT_DIR,'nuria3d.npz'))

# Pose de vitrine para o render no Blender (o app anima os mesmos pivôs).
for label,side in (('LEFT',-1),('RIGHT',1)):
    bpy.data.objects['SHOULDER_'+label].rotation_euler=(math.radians(-12),0,0)
    bpy.data.objects['ELBOW_'+label].rotation_euler=(math.radians(-78),0,math.radians(side*18))

if SAVE_BLEND:
    os.makedirs(OUTPUT_DIR,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUTPUT_DIR,'NURIA_3D.blend'))
print('NURIA 3D pronta. F12 para renderizar. Cena anterior preservada.')
