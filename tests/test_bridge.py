"""End-to-end test: ACEXPORT (LISP, mocked AutoCAD) -> bridge (authors' network) -> ACIMPORT.

Needs the SketchConcept repo and a Python with torch; set
  SKETCHCONCEPT_REPO=/path/to/SketchConcept  SKETCHCONCEPT_PYTHON=/path/to/python
Without a checkpoint it runs with --random-init (checks the plumbing, not the concepts).
"""
import json, math, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from minilisp import *

HERE = os.path.dirname(os.path.abspath(__file__))
repo = os.environ.get("SKETCHCONCEPT_REPO")
py = os.environ.get("SKETCHCONCEPT_PYTHON", sys.executable)
if not repo:
    print("SKIP: set SKETCHCONCEPT_REPO"); sys.exit(0)

load(os.path.join(HERE, '..', 'autodim.lsp'))
tmp = tempfile.mkdtemp()
VARS['DWGPREFIX'] = tmp + os.sep
VARS['DIMZIN'] = 12  # leading-zero suppression on: export must still write valid JSON

slot = Poly([(0, 0), (0.6, 0), (0.6, 0.2), (0, 0.2)], [0, 1, 0, 1], closed=True)
rect = Poly([(1, 0), (2, 0), (2, 0.5), (1, 0.5)], closed=True)
hole = Circle((1.5, 0.25), 0.05)
run('ACEXPORT', [slot, rect, hole])
data = json.load(open(os.path.join(tmp, 'test_sketch.json')))
assert len(data['prims']) == 9 and data['constraints'], data
assert VARS['DIMZIN'] == 12
print('PASS export: %d prims, %d constraints' % (len(data['prims']), len(data['constraints'])))

args = [py, os.path.join(HERE, '..', 'bridge', 'sketchconcept_bridge.py'), '--repo', repo,
        os.path.join(tmp, 'test_sketch.json'), os.path.join(tmp, 'test_concepts.lsp')]
ckpt = os.path.join(repo, 'experiment_directory', 'sample_pretrain_exp', 'result', 'network_param', 'model_180.pth')
if not os.path.exists(ckpt):
    args.insert(2, '--random-init')
r = subprocess.run(args, capture_output=True, text=True)
print(r.stdout[-300:], r.stderr[-1500:] if r.returncode else '')
assert r.returncode == 0
print('PASS bridge')

_, out = run('ACIMPORT', [])
mk = lst(ENV['*ac:mk*'])
ids = [i for m in mk for i in m[2]]
assert all(0 <= i < 9 for i in ids) and len(ids) == len(set(ids)), mk
assert '預訓練模型的概念分解' in out
print('PASS import: %d instances, %d primitives assigned' % (len(mk), len(ids)))
