"""Run the pretrained SketchConcept model (Yang & Pan, NeurIPS 2022) on sketches exported from AutoCAD.

Pipeline:
  AutoCAD  --ACEXPORT-->  <drawing>_sketch.json   (L0 primitives + inferred constraints, from autodim.lsp)
  this script             builds SketchGraphs Sketch objects, splits them into model-sized pieces,
                          runs the authors' detection/generation network on CPU and matches the
                          generated elements back to the input primitives (the authors' own
                          prediction_to_sketch / Hungarian matching)
  AutoCAD  <--ACIMPORT--  <drawing>_concepts.lsp  (one line per primitive: (prim-id instance-key library-id))

Usage:
  python sketchconcept_bridge.py --repo PATH/TO/SketchConcept --epoch 180 in_sketch.json out_concepts.lsp
  (put model_180.pth in PATH/TO/SketchConcept/experiment_directory/sample_pretrain_exp/result/network_param/)

  --random-init  run with untrained weights (only to check that the pipeline works end to end)
"""
import argparse
import collections
import json
import math
import os
import sys

MAX_ELEMENTS = 50  # specs: global_posi_max_length (primitives + constraints per sketch)
MAX_PRIMS = 16      # specs: max_primitive_num
# constraint types we drop first when a piece is too long (least structural)
DROP_ORDER = ["Equal", "Parallel", "Concentric", "Horizontal", "Vertical"]


def setup_imports(repo):
    """Import the authors' code with the small patches needed for CPU-only inference."""
    sys.path.insert(0, repo)
    sys.path.insert(0, os.path.join(repo, "SketchGraphs"))
    import numpy as np
    if "object" not in np.__dict__:
        np.object = object  # removed in numpy >= 1.24, still used by the authors' code
    import torch
    import torch.nn as nn

    class NoDDP(nn.Module):
        """Stand-in for DistributedDataParallel: keeps the `.module` attribute and state-dict keys."""
        def __init__(self, module, device_ids=None):
            super().__init__()
            self.module = module

        def forward(self, *a, **k):
            return self.module(*a, **k)

    import torch.nn.parallel
    torch.nn.parallel.DistributedDataParallel = NoDDP
    import torch.utils.tensorboard

    class NoWriter:
        def __init__(self, *a, **k): pass
        def __getattr__(self, name): return lambda *a, **k: None

    torch.utils.tensorboard.SummaryWriter = NoWriter
    _load = torch.load
    torch.load = lambda f, map_location=None, **k: _load(f, map_location="cpu", **k)

    import TrainingHarness as TH
    TH.DDP = NoDDP
    TH.SummaryWriter = NoWriter
    return TH


# ---------------------------------------------------------------- sketch building

def arc_angles(g):
    """(centre, r, p1, p2, mid) -> (start, end) angles, counter-clockwise."""
    (cx, cy), r, p1, p2, m = g
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    a2 = math.atan2(p2[1] - cy, p2[0] - cx)
    am = math.atan2(m[1] - cy, m[0] - cx)
    span = (a2 - a1) % (2 * math.pi)
    if (am - a1) % (2 * math.pi) < span:
        return a1, a1 + span
    return a2, a2 + (2 * math.pi - span)


def components(n, constraints):
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for c in constraints:
        if len(c) == 3 and c[0] in ("Coincident", "Tangent", "Concentric"):
            parent[find(c[1])] = find(c[2])
    groups = collections.OrderedDict()
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _fit(ids, constraints):
    s = set(ids)
    cons = [c for c in constraints if all(r in s for r in c[1:])]
    for t in DROP_ORDER:
        if len(ids) + len(cons) <= MAX_ELEMENTS:
            break
        extra = len(ids) + len(cons) - MAX_ELEMENTS
        drop = set([i for i, c in enumerate(cons) if c[0] == t][:extra])
        cons = [c for i, c in enumerate(cons) if i not in drop]
    return cons[:MAX_ELEMENTS - len(ids)]


def split_pieces(prims, constraints):
    """Split the drawing into pieces the model can take (<= MAX_PRIMS primitives, <= MAX_ELEMENTS elements).

    Large connected components are cut into chunks; small ones are packed together, since the
    training sketches had 20-50 elements each. Pieces without any constraint cannot be encoded."""
    chunks = []
    for comp in components(len(prims), constraints):
        for k in range(0, len(comp), MAX_PRIMS):
            chunks.append(comp[k:k + MAX_PRIMS])
    pieces, cur = [], []
    for ch in chunks:
        if cur and len(cur) + len(ch) > MAX_PRIMS:
            pieces.append(cur)
            cur = []
        cur = cur + ch
    if cur:
        pieces.append(cur)
    out = []
    for ids in pieces:
        cons = _fit(ids, constraints)
        if cons:
            out.append((ids, cons))
    return out


def to_sketch(prims, ids, cons):
    """Build a SketchGraphs Sketch, normalised into [-1, 1] like the training data."""
    from sketchgraphs.data._entity import Line, Circle, Arc, Point
    from sketchgraphs.data._constraint import Constraint, ConstraintType, LocalReferenceParameter
    from sketchgraphs.data.sketch import Sketch

    pts = []
    for i in ids:
        t, g = prims[i]["type"], prims[i]["g"]
        if t == "Line":
            pts += g
        elif t in ("Arc", "Circle"):
            (cx, cy), r = g[0], g[1]
            pts += [[cx - r, cy - r], [cx + r, cy + r]]
        else:
            pts += g
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    scale = 0.9 / max(1e-12, (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2)

    def P(p):
        return ((p[0] - cx) * scale, (p[1] - cy) * scale)

    entities = collections.OrderedDict()
    for k, i in enumerate(ids):
        t, g, eid = prims[i]["type"], prims[i]["g"], "e%d" % k
        if t == "Line":
            (x1, y1), (x2, y2) = P(g[0]), P(g[1])
            L = math.hypot(x2 - x1, y2 - y1)
            e = Line(eid, pntX=x1, pntY=y1, dirX=(x2 - x1) / L, dirY=(y2 - y1) / L, startParam=0.0, endParam=L)
        elif t == "Circle":
            x, y = P(g[0])
            e = Circle(eid, xCenter=x, yCenter=y, radius=g[1] * scale)
        elif t == "Arc":
            x, y = P(g[0])
            a1, a2 = arc_angles(g)
            e = Arc(eid, xCenter=x, yCenter=y, radius=g[1] * scale, startParam=a1, endParam=a2)
        else:
            x, y = P(g[0])
            e = Point(eid, x=x, y=y)
        entities[eid] = e

    local = {i: "e%d" % k for k, i in enumerate(ids)}
    constraints = collections.OrderedDict()
    for k, c in enumerate(cons):
        cid = "c_%d" % (k + 1)  # the authors' code looks constraints up as "c_<n>"
        refs = [LocalReferenceParameter("local%d" % j, local[r]) for j, r in enumerate(c[1:])]
        constraints[cid] = Constraint(cid, ConstraintType[c[0]], refs)
    return Sketch(entities=entities, constraints=constraints)


# ---------------------------------------------------------------- model

def build_harness(TH, repo, exp, epoch):
    import json as _json
    specs = _json.load(open(os.path.join(exp, "specs.json")))
    args = {"dataset_dir": "", "train_file": "", "experiment": exp, "continue": None, "visualization": True}
    out = os.path.join(exp, "result")
    harness = TH.TrainingHarness([], None, out, args, specs, None, None, [], "cpu")
    if epoch is not None:
        # same as the authors' load_model_parameters, which assumes a CUDA device
        import torch
        path = os.path.join(out, "network_param", "model_%d.pth" % epoch)
        if not os.path.isfile(path):
            sys.exit("checkpoint not found: " + path)
        data = torch.load(path, map_location="cpu")
        harness.model.load_state_dict(data["model_state_dict"])
        harness.cad_embedding.load_state_dict(data["embedding_state_dict"])
        harness.epoch = data["epoch"] + 1
    return harness, specs


def run_model(harness, specs, sketches):
    import torch
    from data import CADDataset, collate_fn
    specs = dict(specs, test_data={"dataset_size": len(sketches)})
    ds = CADDataset({"GT_sketch": sketches}, specs, True, "cpu")
    batch = collate_fn([ds[(i, i)] for i in range(len(sketches))])
    sketch_GT, sketch_length, encoding_info, _ = batch
    with torch.no_grad():
        encoding_info = harness._to(encoding_info)
        param_enc = harness.param_encoding(encoding_info)
        inputs = harness.sketch_to_tensor2(sketch_length, encoding_info, param_enc)
        pred = harness.model(inputs, harness.cad_embedding, "visualize")
        _, belong, ttype, corr, _ = harness.prediction_to_sketch(pred, sketch_GT, param_enc, encoding_info)
    L = harness.max_abstruction
    results = []
    for b, sk in enumerate(sketches):
        n_ent = len(sk.entities)
        assign = {}
        for k in range(len(corr[b])):
            gt = int(corr[b][k])
            if 0 <= gt < n_ent and int(ttype[b][k][0]) < 4:  # generated primitive matched to input primitive gt
                assign[gt] = (int(belong[b][k]) // L, int(ttype[b][k][1]))
        results.append(assign)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--repo", required=True, help="path to the SketchConcept repository (with SketchGraphs inside)")
    ap.add_argument("--exp", default=None, help="experiment directory (default: <repo>/experiment_directory/sample_pretrain_exp)")
    ap.add_argument("--epoch", type=int, default=180)
    ap.add_argument("--random-init", action="store_true")
    a = ap.parse_args()

    exp = a.exp or os.path.join(a.repo, "experiment_directory", "sample_pretrain_exp")
    data = json.load(open(a.input, encoding="utf-8"))
    prims, constraints = data["prims"], data["constraints"]

    TH = setup_imports(a.repo)
    import torch
    torch.manual_seed(0)
    harness, specs = build_harness(TH, a.repo, exp, None if a.random_init else a.epoch)

    pieces = split_pieces(prims, constraints)
    sketches = [to_sketch(prims, ids, cons) for ids, cons in pieces]
    results = run_model(harness, specs, sketches) if sketches else []

    with open(a.output, "w", encoding="utf-8") as f:
        f.write(";; SketchConcept result: (prim-id instance-key library-id)\n")
        for p, ((ids, _), assign) in enumerate(zip(pieces, results)):
            for local, (inst, lib) in sorted(assign.items()):
                f.write("(%d %d %d)\n" % (ids[local], p * 100 + inst, lib))
    n = sum(len(r) for r in results)
    print("pieces: %d, primitives: %d, assigned to concepts: %d -> %s" % (len(pieces), len(prims), n, a.output))


if __name__ == "__main__":
    main()
