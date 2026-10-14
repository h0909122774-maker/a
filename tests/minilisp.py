"""Minimal AutoLISP interpreter + AutoCAD ActiveX mocks, for testing autodim.lsp offline."""
import math, fnmatch, re, sys, os

class Sym(str):
    pass

class Pair:
    def __init__(s, a, d): s.car, s.cdr = a, d
    def __eq__(s, o): return isinstance(o, Pair) and s.car == o.car and s.cdr == o.cdr
    def __repr__(s): return f"({s.car!r} . {s.cdr!r})"

T = True
NIL = None

# ---------------- reader ----------------
def tokenize(src):
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in ' \t\r\n': i += 1
        elif c == ';':
            while i < n and src[i] != '\n': i += 1
        elif c in "()'": toks.append(c); i += 1
        elif c == '"':
            i += 1; s = ''
            while src[i] != '"':
                if src[i] == '\\':
                    i += 1; e = src[i]
                    s += {'n': '\n', 't': '\t', '\\': '\\', '"': '"'}.get(e, e)
                else: s += src[i]
                i += 1
            i += 1; toks.append(('str', s))
        else:
            j = i
            while j < n and src[j] not in ' \t\r\n()\';"': j += 1
            toks.append(src[i:j]); i = j
    return toks

def parse(toks):
    pos = 0
    def atom(t):
        if isinstance(t, tuple): return t[1]
        if re.fullmatch(r'[-+]?\d+', t): return int(t)
        if re.fullmatch(r'[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?', t): return float(t)
        return Sym(t.lower())
    def read():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t == ')': raise SyntaxError('unexpected )')
        if t == "'": return [Sym('quote'), read()]
        if t == '(':
            lst = []
            while toks[pos] != ')':
                if toks[pos] == '.':
                    pos += 1; d = read(); pos += 1  # skip ')'
                    assert len(lst) == 1
                    return Pair(lst[0], d)
                lst.append(read())
            pos += 1
            return lst if lst else None
        return atom(t)
    out = []
    while pos < len(toks): out.append(read())
    return out

# ---------------- interpreter ----------------
class LispError(Exception): pass

class Lambda:
    def __init__(s, params, body, name='lambda'):
        s.args, s.locals = [], []
        tgt = s.args
        for p in (params or []):
            if p == '/': tgt = s.locals; continue
            tgt.append(p)
        s.body, s.name = body, name

ENV = {}
BUILTIN = {}

def truthy(v): return v is not None

def lookup(sym):
    if sym == 't': return T
    if sym == 'nil': return None
    if sym in ENV: return ENV[sym]
    if sym in BUILTIN: return BUILTIN[sym]
    return None

def as_fn(f):
    if isinstance(f, Sym): f = lookup(f)
    if isinstance(f, list) and f and f[0] == 'lambda': return Lambda(f[1], f[2:])
    if f is None: raise LispError('no function')
    return f

def call(f, args):
    f = as_fn(f)
    if callable(f) and not isinstance(f, Lambda): return f(*args)
    saved = {}
    names = f.args + f.locals
    for nm in names: saved[nm] = ENV.get(nm, '__unbound__')
    try:
        for k, nm in enumerate(f.args): ENV[nm] = args[k] if k < len(args) else None
        for nm in f.locals: ENV[nm] = None
        r = None
        for e in f.body: r = ev(e)
        return r
    finally:
        for nm, v in saved.items():
            if v == '__unbound__': ENV.pop(nm, None)
            else: ENV[nm] = v

def ev(x):
    if isinstance(x, Sym): return lookup(x)
    if not isinstance(x, list): return x
    h = x[0]
    if h == 'quote': return x[1]
    if h == 'setq':
        v = None
        for k in range(1, len(x), 2): v = ev(x[k + 1]); ENV[x[k]] = v
        return v
    if h == 'defun':
        ENV[x[1]] = Lambda(x[2], x[3:], x[1]); return x[1]
    if h == 'lambda': return Lambda(x[1], x[2:])
    if h == 'function': return ev(x[1]) if isinstance(x[1], list) else lookup(x[1])
    if h == 'if':
        if truthy(ev(x[1])): return ev(x[2])
        return ev(x[3]) if len(x) > 3 else None
    if h == 'cond':
        for cl in x[1:]:
            v = ev(cl[0])
            if truthy(v):
                for e in cl[1:]: v = ev(e)
                return v
        return None
    if h == 'progn':
        v = None
        for e in x[1:]: v = ev(e)
        return v
    if h == 'and':
        for e in x[1:]:
            if not truthy(ev(e)): return None
        return T
    if h == 'or':
        for e in x[1:]:
            if truthy(ev(e)): return T
        return None
    if h == 'while':
        v = None
        while truthy(ev(x[1])):
            for e in x[2:]: v = ev(e)
        return v
    if h == 'repeat':
        v = None
        for _ in range(ev(x[1])):
            for e in x[2:]: v = ev(e)
        return v
    if h == 'foreach':
        nm = x[1]; old = ENV.get(nm, '__unbound__'); v = None
        for it in (ev(x[2]) or []):
            ENV[nm] = it
            for e in x[3:]: v = ev(e)
        if old == '__unbound__': ENV.pop(nm, None)
        else: ENV[nm] = old
        return v
    f = ev(h) if isinstance(h, Sym) else ev(h)
    if f is None: raise LispError(f'undefined function {h}')
    return call(f, [ev(a) for a in x[1:]])

def L(lst): return lst if lst else None
def lst(v): return [] if v is None else v

def b(name):
    def deco(fn): BUILTIN[name] = fn; return fn
    return deco

def num_div(*a):
    r = a[0]
    for v in a[1:]:
        if isinstance(r, int) and isinstance(v, int): r = int(r / v)
        else: r = r / v
    return r

def cmp_chain(op):
    def f(*a): return T if all(op(a[i], a[i + 1]) for i in range(len(a) - 1)) else None
    return f

BUILTIN.update({
    '+': lambda *a: sum(a) if a else 0, '-': lambda *a: -a[0] if len(a) == 1 else a[0] - sum(a[1:]),
    '*': lambda *a: math.prod(a), '/': num_div, '1+': lambda a: a + 1, '1-': lambda a: a - 1,
    '=': cmp_chain(lambda a, b: a == b), '/=': lambda a, b: T if a != b else None,
    '<': cmp_chain(lambda a, b: a < b), '>': cmp_chain(lambda a, b: a > b),
    '<=': cmp_chain(lambda a, b: a <= b), '>=': cmp_chain(lambda a, b: a >= b),
    'abs': abs, 'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos,
    'atan': lambda a, b=None: math.atan(a) if b is None else math.atan2(a, b),
    'max': max, 'min': min, 'rem': lambda a, b: math.fmod(a, b) if isinstance(a, float) or isinstance(b, float) else int(math.fmod(a, b)),
    'fix': lambda a: int(a), 'float': float,
    'not': lambda a: T if a is None else None, 'null': lambda a: T if a is None else None,
    'eq': lambda a, b: T if a == b else None, 'equal': lambda a, b, *f: T if a == b else None,
    'numberp': lambda a: T if isinstance(a, (int, float)) and not isinstance(a, bool) else None,
    'car': lambda a: a.car if isinstance(a, Pair) else (a[0] if a else None),
    'cdr': lambda a: a.cdr if isinstance(a, Pair) else (L(a[1:]) if a else None),
    'cadr': lambda a: a[1] if a and len(a) > 1 else None,
    'caddr': lambda a: a[2] if a and len(a) > 2 else None,
    'cadddr': lambda a: a[3] if a and len(a) > 3 else None,
    'nth': lambda i, a: a[i] if a and 0 <= i < len(a) else None,
    'cons': lambda a, d: [a] + d if isinstance(d, list) else ([a] if d is None else Pair(a, d)),
    'list': lambda *a: L(list(a)), 'append': lambda *a: L([x for l in a for x in lst(l)]),
    'reverse': lambda a: L(list(reversed(lst(a)))), 'length': lambda a: len(lst(a)),
    'last': lambda a: a[-1] if a else None,
    'member': lambda x, a: next((L(a[i:]) for i in range(len(lst(a))) if a[i] == x), None),
    'assoc': lambda k, a: next((e for e in lst(a) if (e.car if isinstance(e, Pair) else e[0]) == k), None),
    'subst': lambda new, old, a: L([new if e == old else e for e in lst(a)]),
    'vl-position': lambda x, a: next((i for i, e in enumerate(lst(a)) if e == x), None),
    'pi': math.pi,
    'distance': lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1]) if len(p) < 3 or len(q) < 3 else math.dist(p[:3], q[:3]),
    'angle': lambda p, q: math.atan2(q[1] - p[1], q[0] - p[0]) % (2 * math.pi),
    'polar': lambda p, a, d: [p[0] + d * math.cos(a), p[1] + d * math.sin(a)] + list(p[2:3]),
    'strcat': lambda *a: ''.join(a), 'itoa': lambda i: str(int(i)),
    'rtos': lambda x, m=2, p=4: (lambda t: re.sub(r'^(-?)0\.', r'\1.', t) if VARS.get('DIMZIN', 0) & 4 else t)(f"{x:.{p}f}"),
    'strcase': lambda s, low=None: s.lower() if low else s.upper(),
    'wcmatch': lambda s, pat: T if any(fnmatch.fnmatchcase(s, p) for p in pat.split(',')) else None,
    'vl-load-com': lambda: None, 'vl-filename-base': lambda s: s.rsplit('.', 1)[0],
    'initget': lambda *a: None, 'redraw': lambda *a: None,
})
ENV['pi'] = math.pi

@b('mapcar')
def _mapcar(f, *ls):
    ls = [lst(l) for l in ls]
    return L([call(f, list(t)) for t in zip(*ls)])
@b('apply')
def _apply(f, a): return call(f, lst(a))
@b('vl-some')
def _some(f, *ls):
    for t in zip(*[lst(l) for l in ls]):
        v = call(f, list(t))
        if truthy(v): return v
    return None
@b('vl-every')
def _every(f, *ls):
    return T if all(truthy(call(f, list(t))) for t in zip(*[lst(l) for l in ls])) else None
@b('vl-remove-if-not')
def _rin(f, a): return L([e for e in lst(a) if truthy(call(f, [e]))])
@b('vl-sort')
def _sort(a, f):
    import functools
    return L(sorted(lst(a), key=functools.cmp_to_key(lambda x, y: -1 if truthy(call(f, [x, y])) else (1 if truthy(call(f, [y, x])) else 0))))

class CatchErr:
    def __init__(s, m): s.m = m
@b('vl-catch-all-apply')
def _cap(f, a=None):
    try: return call(f, lst(a))
    except Exception as e: return CatchErr(str(e))
BUILTIN['vl-catch-all-error-p'] = lambda r: T if isinstance(r, CatchErr) else None
BUILTIN['vl-catch-all-error-message'] = lambda r: r.m

OUT = []
@b('princ')
def _princ(x=None):
    if x is not None: OUT.append(x if isinstance(x, str) else repr(x))
    return x
@b('eval')
def _eval(x): return ev(x)

# ---------------- AutoCAD mocks ----------------
class Ent:
    def __init__(s, kind, **kw): s.kind = kind; s.__dict__.update(kw); s.layer = '0'; s.override = None
    def __repr__(s): return f"<{s.kind} {s.__dict__}>"

def bulge_mid(p1, p2, bl):
    c = math.dist(p1, p2); mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    rx, ry = (p2[1] - p1[1]) / c, -(p2[0] - p1[0]) / c
    s = bl * c / 2
    return [mx + rx * s, my + ry * s, 0.0]

def Line(p, q): return Ent('AcDbLine', p=list(p) + [0.0], q=list(q) + [0.0])
def Poly(verts, bulges=None, closed=False): return Ent('AcDbPolyline', v=[list(v) + [0.0] for v in verts], bl=(list(bulges or []) + [0] * len(verts))[:len(verts)], closed=closed)
def Arc(c, r, sa, ea): return Ent('AcDbArc', c=list(c) + [0.0], r=r, sa=sa, ea=ea)
def Circle(c, r): return Ent('AcDbCircle', c=list(c) + [0.0], r=r)
def Point(p): return Ent('AcDbPoint', c=list(p) + [0.0])

CREATED = []
SEL = []
GRDRAW = []
DOC = Ent('Doc'); SPACE = Ent('Space'); LAYERS = Ent('Layers')

def arc_end(e): return e.ea if e.ea > e.sa else e.ea + 2 * math.pi
def endparam(e):
    if e.kind == 'AcDbPolyline': return float(len(e.v) if e.closed else len(e.v) - 1)
    if e.kind == 'AcDbArc': return arc_end(e)
    if e.kind == 'AcDbLine': return math.dist(e.p, e.q)
def ptparam(e, t):
    if e.kind == 'AcDbPolyline':
        n = len(e.v); k = int(math.floor(t)); f = t - k
        p1 = e.v[k % n]
        if f < 1e-12: return list(p1)
        p2 = e.v[(k + 1) % n]
        if e.bl[k] == 0: return [p1[i] + (p2[i] - p1[i]) * f for i in range(3)]
        assert abs(f - 0.5) < 1e-9
        return bulge_mid(p1, p2, e.bl[k])
    if e.kind == 'AcDbArc': return [e.c[0] + e.r * math.cos(t), e.c[1] + e.r * math.sin(t), 0.0]
def startpt(e): return list(e.p) if e.kind == 'AcDbLine' else ptparam(e, e.sa if e.kind == 'AcDbArc' else 0)
def endpt(e): return list(e.q) if e.kind == 'AcDbLine' else ptparam(e, endparam(e))
def bbox(e):
    if e.kind == 'AcDbLine': pts = [e.p, e.q]
    elif e.kind == 'AcDbPolyline': pts = e.v + [bulge_mid(e.v[i], e.v[(i + 1) % len(e.v)], e.bl[i]) for i in range(len(e.v)) if e.bl[i]]
    elif e.kind in ('AcDbArc', 'AcDbCircle'): pts = [[e.c[0] - e.r, e.c[1] - e.r, 0], [e.c[0] + e.r, e.c[1] + e.r, 0]]
    else: pts = [e.c]
    return [min(p[0] for p in pts), min(p[1] for p in pts), 0.0], [max(p[0] for p in pts), max(p[1] for p in pts), 0.0]

def mkdim(kind):
    def f(spc, *a):
        d = Ent(kind, args=a); CREATED.append(d); return d
    return f

def getbb(o, sa, sb):
    mn, mx = bbox(o); ENV[sa] = mn; ENV[sb] = mx

VARS = {'TILEMODE': 1, 'CVPORT': 2, 'DIMSCALE': 1.0, 'DIMTXT': 2.5, 'DWGPREFIX': '', 'DWGNAME': 'test.dwg', 'VIEWSIZE': 100.0, 'DIMZIN': 8}

class FakeFile:
    def __init__(s, p): s.p = p; s.lines = []
FILES = {}
def _open(p, m):
    if m == 'r':
        if not os.path.exists(p): return None
        f = FakeFile(p); f.lines = open(p, encoding='utf-8').read().splitlines(); f.pos = 0; return f
    f = FakeFile(p); FILES[p] = f; return f
def _wl(l, f):
    f.lines.append(l)
    with open(f.p, 'w', encoding='utf-8') as h: h.write('\n'.join(f.lines) + '\n')
    return l
def _rl(f):
    if f.pos >= len(f.lines): return None
    f.pos += 1; return f.lines[f.pos - 1]
def _read(s):
    r = parse(tokenize(s)); return r[0] if r else None

BUILTIN.update({
    'vlax-ename->vla-object': lambda e: e, 'vla-get-objectname': lambda e: e.kind,
    'vlax-curve-getendparam': endparam, 'vlax-curve-getpointatparam': ptparam,
    'vlax-curve-getstartpoint': startpt, 'vlax-curve-getendpoint': endpt,
    'vlax-curve-getdistatparam': lambda e, t: e.r * (t - e.sa),
    'vlax-curve-getpointatdist': lambda e, d: ptparam(e, e.sa + d / e.r),
    'vla-getbulge': lambda e, i: e.bl[i],
    'vla-get-center': lambda e: list(e.c), 'vla-get-coordinates': lambda e: list(e.c),
    'vla-get-radius': lambda e: e.r,
    'vlax-safearray->list': lambda a: a, 'vlax-variant-value': lambda a: a,
    'vlax-3d-point': lambda p: list(p), 'vla-getboundingbox': getbb,
    'vla-adddimaligned': mkdim('DimAligned'), 'vla-adddimrotated': mkdim('DimRotated'),
    'vla-adddimradial': mkdim('DimRadial'), 'vla-adddimdiametric': mkdim('DimDiametric'),
    'vla-addline': lambda s, p, q: (CREATED.append(Ent('NewLine', p=p, q=q)) or CREATED[-1]),
    'vla-addarc': lambda s, c, r, a1, a2: (CREATED.append(Ent('NewArc', c=c, r=r, sa=a1, ea=a2)) or CREATED[-1]),
    'vla-put-textoverride': lambda o, s: setattr(o, 'override', s),
    'vla-put-layer': lambda o, s: setattr(o, 'layer', s),
    'vla-put-color': lambda o, c: None, 'vla-get-layers': lambda d: LAYERS, 'vla-add': lambda l, n: Ent('Layer', name=n),
    'vlax-get-acad-object': lambda: Ent('Acad'), 'vla-get-activedocument': lambda a: DOC,
    'vla-get-modelspace': lambda d: SPACE, 'vla-get-paperspace': lambda d: SPACE,
    'vla-startundomark': lambda d: None, 'vla-endundomark': lambda d: None,
    'getvar': lambda v: VARS[v.upper()], 'getkword': lambda *a: 'N', 'getdist': lambda *a: None,
    'ssget': lambda *a: (list(SEL) if SEL else None), 'sslength': lambda s: len(s), 'ssname': lambda s, i: s[i],
    'grdraw': lambda p, q, c, h=None: GRDRAW.append((p, q, c)),
    'open': _open, 'write-line': _wl, 'close': lambda f: None, 'read-line': _rl, 'read': _read,
    'substr': lambda s, i, n=None: s[i - 1:] if n is None else s[i - 1:i - 1 + n],
    'setvar': lambda v, x: VARS.__setitem__(v.upper(), x), 'findfile': lambda p: p if os.path.exists(p) else None,
    'vl-file-delete': lambda p: (os.remove(p), T)[1] if os.path.exists(p) else None,
})

def load(path):
    for form in parse(tokenize(open(path, encoding='utf-8').read())): ev(form)

def run(cmd, sel):
    SEL[:] = sel; CREATED.clear(); OUT.clear(); GRDRAW.clear()
    call(Sym('c:' + cmd.lower()), [])
    return list(CREATED), ''.join(OUT)
