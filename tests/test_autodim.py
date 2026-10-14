import sys, math, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minilisp import *

load(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'autodim.lsp'))
fails = 0
def check(name, cond, info=''):
    global fails
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(info)))
    if not cond: fails += 1

def concepts():
    return [(c[0], len(lst(c[1]))) for c in lst(ENV['*ac:k*'])]
def kinds(created): return sorted(d.kind for d in created)
def ovr(created): return sorted(d.override for d in created if d.override)

rect = Poly([(0, 0), (100, 0), (100, 50), (0, 50)], closed=True)
# slot: CCW, caps bulge outward
slot = Poly([(200, 0), (260, 0), (260, 20), (200, 20)], [0, 1, 0, 1], closed=True)
sq = [Line((300, 0), (340, 0)), Line((340, 0), (340, 40)), Line((340, 40), (300, 40)), Line((300, 40), (300, 0))]
hexa = Poly([(500 + 20 * math.cos(k * math.pi / 3), 20 * math.sin(k * math.pi / 3)) for k in range(6)], closed=True)
holes = [Circle((20 + 20 * k, 25), 5) for k in range(4)]
conc = [Circle((600, 0), 10), Circle((600, 0), 20)]
lshape = Poly([(0, 200), (80, 200), (80, 230), (30, 230), (30, 260), (0, 260)], closed=True)
pair = [Line((0, 400), (50, 400)), Line((0, 420), (50, 420))]
# fillet: two lines joined by a tangent arc (open chain)
fil = Poly([(0, 500), (40, 500), (50, 510), (50, 550)], [0, math.tan(math.pi / 8), 0])

# ---------- ACLIST on everything ----------
created, out = run('ACLIST', [rect, slot] + sq + [hexa] + holes + conc + [lshape] + pair + [fil])
cs = concepts()
print(cs)
for want in [('Rect', 4), ('Slot', 4), ('Square', 4), ('NGon', 6), ('HolePattern', 4), ('Concentric', 2), ('Profile', 6), ('ParallelPair', 2)]:
    check('detect ' + want[0], want in cs, cs)
check('fillet chain', ('Fillet', 1) in cs and cs.count(('Line', 1)) == 2, cs)
prog = list(FILES.values())[-1].lines
print('\n'.join(prog))
check('program has T_Slot', any(l.startswith('T_Slot -> lambda(') and 'Tangent(' in l for l in prog))
check('modularity line', any('Modularity' in l for l in prog))
check('grdraw drew', len(GRDRAW) > 50)

# ---------- ACDIM: rect + holes inside ----------
created, out = run('ACDIM', [rect] + holes)
print(out[-300:])
print([(d.kind, d.override) for d in created])
check('rect: 2 aligned', sum(1 for d in created if d.kind == 'DimAligned' and d.override is None) == 2, kinds(created))
check('holes: 4x diam', '4×<>' in ovr(created), ovr(created))
check('holes: pitch 3x', '3×<>' in ovr(created), ovr(created))
check('holes: locate 2 rotated', kinds(created).count('DimRotated') == 2, kinds(created))
# rect dims placed outside
for d in created:
    if d.kind == 'DimAligned' and d.override is None:
        tp = d.args[2]
        check('rect dim outside', not (0 < tp[0] < 100 and 0 < tp[1] < 50), tp)

# ---------- ACDIM: slot ----------
created, out = run('ACDIM', [slot])
al = [d for d in created if d.kind == 'DimAligned']
lens = sorted(round(math.dist(d.args[0][:2], d.args[1][:2]), 6) for d in al)
check('slot dims: centre dist 60 & width 20', lens == [20.0, 60.0], lens)
check('slot: no radial', not any(d.kind == 'DimRadial' for d in created), kinds(created))

# ---------- ACDIM: square / hexagon / fillets ----------
created, _ = run('ACDIM', sq)
check('square one dim □', ovr(created) == ['□<>'] and len(created) == 1, [(d.kind, d.override) for d in created])
created, _ = run('ACDIM', [hexa])
check('hexagon one dim 6×', ovr(created) == ['6×<>'] and len(created) == 1, [(d.kind, d.override) for d in created])
f2 = Poly([(0, 0), (40, 0), (50, 10), (50, 50), (60, 60), (100, 60)], [0, math.tan(math.pi / 8), 0, -math.tan(math.pi / 8), 0])
created, _ = run('ACDIM', [f2])
check('equal fillets merged 2×R', ovr(created) == ['2×<>'] and kinds(created).count('DimRadial') == 1, [(d.kind, d.override) for d in created])

# ---------- ACDIM: L profile, outside placement ----------
created, _ = run('ACDIM', [lshape])
check('L profile 6 dims', len(created) == 6, kinds(created))

# ---------- ACCOMPLETE: partial rect ----------
part = [Line((0, 0), (0, 30)), Line((0, 0), (80, 0)), Line((80, 0), (80, 30))]
created, out = run('ACCOMPLETE', part)
nl = [d for d in created if d.kind == 'NewLine']
check('complete rect side', len(nl) == 1 and sorted([tuple(nl[0].p[:2]), tuple(nl[0].q[:2])]) == [(0, 30), (80, 30)], created)
check('completion layer', nl and nl[0].layer == 'AC_COMPLETION')
zs = [Line((0, 0), (0, 30)), Line((0, 0), (80, 0)), Line((80, 0), (80, -30))]
created, _ = run('ACCOMPLETE', zs)
check('Z shape not completed', not created, created)
full = sq
created, _ = run('ACCOMPLETE', full)
check('full square not completed', not created, created)

# ---------- ACCOMPLETE: partial slot ----------
ps = [Line((0, 0), (60, 0)), Line((0, 20), (60, 20)), Arc((60, 10), 10, -math.pi / 2, math.pi / 2)]
created, out = run('ACCOMPLETE', ps)
na = [d for d in created if d.kind == 'NewArc']
ok = False
if len(na) == 1:
    a = na[0]
    mid = (a.sa + a.ea) / 2
    ok = abs(a.c[0]) < 1e-9 and abs(a.c[1] - 10) < 1e-9 and abs(a.r - 10) < 1e-9 and abs(math.cos(mid) + 1) < 1e-9
check('complete slot cap (centre 0,10 r10 facing -x)', ok, created)

# ---------- old commands still work ----------
created, _ = run('AUTODIM', [rect])
check('AUTODIM rect 4 dims', len(created) == 4, kinds(created))

# ---------- extra edge cases ----------
cw = Poly([(200, 20), (260, 20), (260, 0), (200, 0)], [0, -1, 0, -1], closed=True)
created, _ = run('ACDIM', [cw])
check('CW slot detected', ('Slot', 4) in concepts(), concepts())
lens = sorted(round(math.dist(d.args[0][:2], d.args[1][:2]), 6) for d in created if d.kind == 'DimAligned')
check('CW slot dims', lens == [20.0, 60.0], lens)
t = math.tan(math.pi / 8)
rr = Poly([(10, 0), (90, 0), (100, 10), (100, 40), (90, 50), (10, 50), (0, 40), (0, 10)], [0, t, 0, t, 0, t, 0, t], closed=True)
created, _ = run('ACDIM', [rr])
check('rounded rect = Profile', ('Profile', 8) in concepts(), concepts())
check('rounded rect 4×R once', ovr(created) == ['4×<>'] and kinds(created).count('DimRadial') == 1, [(d.kind, d.override) for d in created])
# slot made of separate LINE + ARC entities, plus a POINT
sep = [Line((0, 0), (60, 0)), Line((60, 20), (0, 20)), Arc((60, 10), 10, -math.pi / 2, math.pi / 2), Arc((0, 10), 10, math.pi / 2, 3 * math.pi / 2), Point((30, 10))]
created, out = run('ACLIST', sep)
check('slot from separate entities', ('Slot', 4) in concepts() and ('Point', 1) in concepts(), concepts())

print('\nFAILURES:', fails)
sys.exit(1 if fails else 0)
