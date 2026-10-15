"""
AutoCAD 自動標註 (Python + COM)

會幫圖上的「點 / 線 / 圓 / 弧 / 聚合線」自動加上標註：

    點 (POINT)          -> X、Y 座標標註 (ordinate)
    線 (LINE)           -> 對齊長度標註 (aligned)
    圓 (CIRCLE)         -> 直徑標註 + 十字中心線
    弧 (ARC)            -> 半徑標註 + 十字中心線
    聚合線 (LWPOLYLINE) -> 每一段直線段的長度標註
    相接的兩條線        -> 角度標註（線段端點相接、且不是一直線時）

另外會建立兩個圖層：
    中心線   線型 CENTER（一點鏈線），自動中心線畫在這裡
    虛線     線型 HIDDEN（虛線），把它設為目前圖層，畫出來的線就是虛線，一樣會自動標註

需求: Windows + AutoCAD (完整版，LT 不支援 COM) + Python
      pip install comtypes

用法:
    先開啟 AutoCAD，然後在 cmd 執行：

    python autocad_auto_dim.py                 # 監看目前圖面，之後新畫的物件自動標註
    python autocad_auto_dim.py --all           # 目前圖面上既有的物件也全部標註
    python autocad_auto_dim.py 圖檔.dwg         # 開啟圖檔 -> 全部標註 -> 另存成「圖檔_標註.dwg」-> 繼續監看
    python autocad_auto_dim.py 圖檔.dwg --once  # 同上，但標完就結束
    python autocad_auto_dim.py a.dwg b.dwg --once   # 一次處理多個圖檔
    python autocad_auto_dim.py --text 5        # 指定標註字高為 5（不指定就依圖形大小自動決定）

    其他參數:
      --now        不等指令結束，畫一段就標一段
      --no-save    讀圖檔標註後不要另存
      -v           顯示除錯訊息
    在 cmd 按 Ctrl+C 停止。

COM 部分用 comtypes 的早期繫結：每個物件都先用 QueryInterface
轉成實際型別 (IAcadModelSpace、IAcadCircle、IAcadLine…) 再呼叫，
不靠名稱查詢，非英文版 AutoCAD 也能用。
"""

import argparse
import array
import math
import os
import sys
import time
import traceback

import comtypes.client
import comtypes.client.dynamic
from comtypes.automation import IDispatch
from comtypes import COMError

# ---------------- 可調整的設定 ----------------
DIM_LAYER = "AUTO_DIM"        # 標註放在這個圖層
CENTER_LAYER = "中心線"        # 自動中心線放在這個圖層（線型 CENTER）
HIDDEN_LAYER = "虛線"          # 虛線圖層（線型 HIDDEN）
TEXT_RATIO = 1 / 40           # 自動字高 = 圖形大小 × 這個比例（字太小就調大，例如 1/25）
# 以下距離都以「字高」為單位，會跟著字高一起縮放
LINE_OFFSET = 2.5             # 長度標註離線的距離
POINT_LEADER = 4.0            # 點座標標註的引線長度
CIRCLE_LEADER = 2.0           # 圓/弧標註的引線長度
ANGLE_RADIUS = 5.0            # 角度標註弧線離頂點的距離（線太短時會自動縮小）
CENTER_EXTEND = 1.5           # 中心線超出圓的長度
DIM_ANGLES = True             # 是否標註相接兩線的角度
SKIP_RIGHT_ANGLE = True       # True: 90 度不標（改 False 就連直角也標）
DRAW_CENTERLINES = True       # 是否幫圓/弧畫中心線
SAVE_SUFFIX = "_標註"          # 讀圖檔標註後另存的檔名後綴
POLL_INTERVAL = 0.3           # 每幾秒檢查一次新物件
WAIT_FOR_COMMAND_END = True   # True: 等指令(如 LINE)結束後才標註，避免干擾繪圖
VERBOSE = False               # 用 -v 參數開啟除錯訊息
# ----------------------------------------------

# 這些圖層上的物件是本程式畫的，不再標註
OWN_LAYERS = {DIM_LAYER, CENTER_LAYER}
TOL = 1e-6

# AutoCAD 忙碌時 COM 會回傳這些錯誤碼，稍後重試即可
BUSY_ERRORS = (-2147418111, -2147417846)  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER


def log(msg):
    if VERBOSE:
        print(msg)


def apt(x, y, z=0.0):
    """把座標轉成 AutoCAD COM 需要的 double 陣列。"""
    return array.array("d", (float(x), float(y), float(z)))


def is_busy(err):
    return isinstance(err, COMError) and err.hresult in BUSY_ERRORS


def retry(fn, *args, tries=50, delay=0.1):
    """AutoCAD 忙碌時自動重試；其他錯誤直接拋出，保留原本的錯誤訊息。"""
    for _ in range(tries):
        try:
            return fn(*args)
        except COMError as e:
            if not is_busy(e):
                raise
            time.sleep(delay)
    return fn(*args)


# ---------------- COM 型別轉換 ----------------

# 每種物件對應的 AutoCAD 介面
INTERFACES = {
    "AcDbLine": "IAcadLine",
    "AcDbCircle": "IAcadCircle",
    "AcDbArc": "IAcadArc",
    "AcDbPoint": "IAcadPoint",
    "AcDbPolyline": "IAcadLWPolyline",
}


def typed(obj):
    """把 COM 物件轉成它實際的介面 (例如 IAcadApplication)。"""
    try:
        return comtypes.client.GetBestInterface(obj)
    except Exception:
        return obj


def acad_typelib():
    """comtypes 為 AutoCAD 產生的型別庫模組（連線時自動產生）。"""
    for mod_name, mod in list(sys.modules.items()):
        if mod_name.startswith("comtypes.gen.") and hasattr(mod, "IAcadCircle"):
            return mod
    return None


def cast(obj, iface_name):
    """明確轉成指定的 AutoCAD 介面；不行就改用動態呼叫。"""
    lib = acad_typelib()
    iface = getattr(lib, iface_name, None) if lib else None
    if iface is not None:
        try:
            return obj.QueryInterface(iface)
        except Exception as e:
            log(f"QueryInterface({iface.__name__}) 失敗: {e!r}，改用動態呼叫")
    else:
        log("找不到 AutoCAD 型別庫模組，改用動態呼叫")
    return comtypes.client.dynamic.Dispatch(obj.QueryInterface(IDispatch))


# 有些情況下 COM 方法用名稱找不到
# (AttributeError / NameError)。這時改從物件自己的型別資訊查出 DISPID，
# 直接用 IDispatch.Invoke 呼叫，不經過名稱查詢。
DISPATCH_METHOD = 1
DISPATCH_PROPERTYGET = 2
DISPATCH_PROPERTYPUT = 4


def _dispid(disp, name):
    errors = []
    try:
        return disp.GetTypeInfo(0).GetIDsOfNames(name)[0]
    except Exception as e:
        errors.append(e)
    try:
        return disp.GetIDsOfNames(name)[0]
    except Exception as e:
        errors.append(e)
    raise AttributeError(f"{name}（型別資訊和名稱查詢都找不到: {errors!r}）")


def _invoke(obj, name, kind, *args):
    disp = obj.QueryInterface(IDispatch)
    return disp.Invoke(_dispid(disp, name), *args, _invkind=kind)


def call(obj, method, *args):
    """呼叫 COM 方法，例如 call(space, "AddDimDiametric", p1, p2, 5.0)。"""
    try:
        fn = getattr(obj, method)
    except (AttributeError, NameError) as e:
        log(f"{method} 無法直接呼叫 ({e!r})，改用 IDispatch.Invoke")
        return _invoke(obj, method, DISPATCH_METHOD, *args)
    return fn(*args)


def put(obj, prop, value):
    """設定 COM 屬性，例如 put(dim, "Layer", "AUTO_DIM")。"""
    try:
        setattr(obj, prop, value)
    except (AttributeError, NameError) as e:
        log(f"設定 {prop} 失敗 ({e!r})，改用 IDispatch.Invoke")
        _invoke(obj, prop, DISPATCH_PROPERTYPUT, value)


def get(obj, prop):
    """讀取 COM 屬性。"""
    try:
        return getattr(obj, prop)
    except (AttributeError, NameError) as e:
        log(f"讀取 {prop} 失敗 ({e!r})，改用 IDispatch.Invoke")
        return _invoke(obj, prop, DISPATCH_PROPERTYGET)


# ---------------- 圖層與線型 ----------------

def ensure_linetype(doc, name):
    """載入線型（CENTER、HIDDEN…），已載入就略過。"""
    try:
        doc.Linetypes.Item(name)
        return True
    except COMError:
        pass
    metric = doc.GetVariable("MEASUREMENT") == 1
    files = ["acadiso.lin", "acad.lin"] if metric else ["acad.lin", "acadiso.lin"]
    for f in files:
        try:
            doc.Linetypes.Load(name, f)
            return True
        except COMError:
            continue
    print(f"無法載入線型 {name}，將使用實線")
    return False


def ensure_layer(doc, name, color, linetype=None):
    try:
        layer = doc.Layers.Item(name)
        return layer
    except COMError:
        pass
    layer = doc.Layers.Add(name)
    try:
        layer.color = color
    except Exception:
        pass
    if linetype and ensure_linetype(doc, linetype):
        try:
            layer.Linetype = linetype
        except Exception as e:
            log(f"圖層 {name} 設定線型失敗: {e!r}")
    return layer


def setup_layers(doc):
    ensure_layer(doc, DIM_LAYER, 3)                # 綠
    ensure_layer(doc, CENTER_LAYER, 1, "CENTER")   # 紅，一點鏈線
    ensure_layer(doc, HIDDEN_LAYER, 4, "HIDDEN")   # 青，虛線


def linetype_pattern_length(doc):
    """CENTER 線型一個循環的長度（公制 acadiso.lin 比英制 acad.lin 長很多）。"""
    return 50.0 if doc.GetVariable("MEASUREMENT") == 1 else 2.0


# ---------------- 幾何小工具 ----------------

def same_pt(a, b):
    return abs(a[0] - b[0]) <= TOL and abs(a[1] - b[1]) <= TOL


def pt_key(p):
    return (round(p[0], 6), round(p[1], 6))


def nice_number(x):
    """取整成 1、2、2.5、5 × 10^n 這類好看的數字。"""
    if x <= 0:
        return 2.5
    exp = math.floor(math.log10(x))
    base = x / 10 ** exp
    for step in (1, 2, 2.5, 5, 10):
        if base <= step * 1.25:
            return step * 10 ** exp
    return 10 ** (exp + 1)


def unit(dx, dy):
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > TOL else None


# ---------------- 一份圖面的標註器 ----------------

class Annotator:
    """一個空間（模型空間或某個配置）的標註器。"""

    def __init__(self, doc, space, text_height=None):
        self.doc = doc
        self.space = space
        self.done = set()          # 已處理過的物件 handle
        self.segments = {}         # 圖上的直線段 id -> (p1, p2)
        self.pt_index = {}         # 端點 -> 接在這點的線段 id
        self.angle_keys = set()    # 已標過的角度 (頂點, 兩段 id)
        self.bbox = None           # 圖形範圍 (xmin, ymin, xmax, ymax)
        setup_layers(doc)
        self.ltscale_base = linetype_pattern_length(doc)

        # 先讀一次既有物件的幾何（不標註），用來決定字高、判斷標註要放外側
        self.collect(0, space.Count)
        self.set_text_height(text_height)

    # ---- 字高 / 比例 ----
    def set_text_height(self, text_height):
        if text_height:
            h = text_height
            how = "指定"
        else:
            size = 0.0
            if self.bbox:
                size = max(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])
            if size <= TOL:
                size = float(self.doc.GetVariable("VIEWSIZE"))   # 空白圖：用目前畫面高度
            h = nice_number(size * TEXT_RATIO)
            how = "自動"
        dimtxt = float(self.doc.GetVariable("DIMTXT")) or 2.5
        self.h = h
        self.scale = h / dimtxt                # 標註的 ScaleFactor（字、箭頭一起放大）
        print(f"標註字高 {h:g}（{how}；可用 --text 指定）")

    def k(self, factor):
        """以字高為單位的距離。"""
        return factor * self.h

    # ---- 幾何記錄 ----
    def grow_bbox(self, x1, y1, x2=None, y2=None):
        x2 = x1 if x2 is None else x2
        y2 = y1 if y2 is None else y2
        if self.bbox is None:
            self.bbox = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        else:
            b = self.bbox
            self.bbox = (min(b[0], x1, x2), min(b[1], y1, y2), max(b[2], x1, x2), max(b[3], y1, y2))

    def add_segment(self, sid, p1, p2):
        if sid in self.segments:
            return
        self.segments[sid] = (p1, p2)
        for p in (p1, p2):
            self.pt_index.setdefault(pt_key(p), set()).add(sid)
        self.grow_bbox(p1[0], p1[1], p2[0], p2[1])

    def entities(self, start, end):
        """逐一取出第 start ~ end-1 個物件：(handle, 物件名, 轉型後的物件)。"""
        for i in range(start, end):
            ent = retry(self.space.Item, i)
            handle, name, layer = retry(lambda: (ent.Handle, ent.ObjectName, ent.Layer))
            if name not in INTERFACES or layer in OWN_LAYERS:
                yield handle, name, None
            else:
                yield handle, name, cast(ent, INTERFACES[name])

    def read_geometry(self, handle, name, ent):
        if name == "AcDbLine":
            self.add_segment(handle, ent.StartPoint, ent.EndPoint)
        elif name == "AcDbPolyline":
            for k, (p1, p2) in enumerate(self.poly_segments(ent)):
                self.add_segment(f"{handle}:{k}", p1, p2)
        elif name in ("AcDbCircle", "AcDbArc"):
            c, r = ent.Center, ent.Radius
            self.grow_bbox(c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        elif name == "AcDbPoint":
            c = ent.Coordinates
            self.grow_bbox(c[0], c[1])

    def collect(self, start, end):
        for handle, name, ent in self.entities(start, end):
            if ent is not None:
                try:
                    retry(self.read_geometry, handle, name, ent)
                except Exception as e:
                    log(f"讀取 {name} 失敗: {e!r}")

    # ---- 標註 ----
    def scan(self, start, end, annotate=True):
        """處理第 start ~ end-1 個物件。annotate=False 時只記錄，不標註。"""
        items = [x for x in self.entities(start, end) if x[0] not in self.done]
        for handle, name, ent in items:
            self.done.add(handle)
            if ent is not None:
                retry(self.read_geometry, handle, name, ent)   # 先把這批的線段都記下來
        if not annotate:
            return
        for handle, name, ent in items:
            if ent is None:
                if "Dimension" not in name:
                    log(f"略過 {name}（不支援或本程式畫的物件）")
                continue
            try:
                dims = retry(self.annotate_entity, handle, name, ent)
                for d in dims:
                    put(d, "Layer", DIM_LAYER)
                    try:
                        put(d, "ScaleFactor", self.scale)
                    except Exception as e:
                        log(f"設定標註比例失敗: {e!r}")
                print(f"已標註 {name} (handle {handle})")
            except Exception as e:
                print(f"標註失敗 {name} (handle {handle}): {e!r}")
                if VERBOSE:
                    traceback.print_exc()

    def annotate_entity(self, handle, name, ent):
        out = []
        if name == "AcDbLine":
            out += self.dim_segment(handle)
            out += self.dim_angles(handle)
        elif name == "AcDbPolyline":
            sids = [sid for sid in self.segments if sid.startswith(f"{handle}:")]
            for sid in sids:
                out += self.dim_segment(sid)
            for sid in sids:
                out += self.dim_angles(sid)
        elif name == "AcDbCircle":
            out += self.dim_circle(ent)
            self.centerline(ent.Center, ent.Radius)
        elif name == "AcDbArc":
            out += self.dim_arc(ent)
            self.centerline(ent.Center, ent.Radius)
        elif name == "AcDbPoint":
            out += self.dim_point(ent)
        return out

    @staticmethod
    def poly_segments(ent):
        c = list(ent.Coordinates)
        pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
        n = len(pts)
        seg_count = n if ent.Closed else n - 1
        for i in range(seg_count):
            if abs(ent.GetBulge(i)) > 1e-9:
                continue  # 弧形段先略過
            yield pts[i], pts[(i + 1) % n]

    def shape_center(self, sid):
        """和這段線相連的整個圖形（例如一個矩形）的中心，用來把標註放到外側。"""
        seen, stack = {sid}, [sid]
        sx = sy = total = 0.0
        while stack and len(seen) < 5000:
            cur = stack.pop()
            p1, p2 = self.segments[cur]
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            sx += (p1[0] + p2[0]) / 2 * length
            sy += (p1[1] + p2[1]) / 2 * length
            total += length
            for p in (p1, p2):
                for nxt in self.pt_index.get(pt_key(p), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
        return (sx / total, sy / total) if total > TOL else None

    def dim_segment(self, sid):
        """線段長度標註，放在圖形外側。"""
        p1, p2 = self.segments[sid]
        d = unit(p2[0] - p1[0], p2[1] - p1[1])
        if d is None:
            return []
        nx, ny = -d[1], d[0]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        c = self.shape_center(sid)
        side = (mx - c[0]) * nx + (my - c[1]) * ny if c else 0.0
        if abs(side) > TOL:
            if side < 0:
                nx, ny = -nx, -ny          # 往遠離圖形中心的那一側
        elif ny < -TOL or (abs(ny) <= TOL and nx > 0):
            nx, ny = -nx, -ny              # 單獨一條線：放上方（垂直線放左邊）
        off = self.k(LINE_OFFSET)
        return [call(self.space, "AddDimAligned", apt(p1[0], p1[1]), apt(p2[0], p2[1]),
                     apt(mx + nx * off, my + ny * off))]

    def dim_circle(self, ent):
        cx, cy = ent.Center[0], ent.Center[1]
        r = ent.Radius
        a = math.radians(45)
        p1 = apt(cx + r * math.cos(a), cy + r * math.sin(a))
        p2 = apt(cx - r * math.cos(a), cy - r * math.sin(a))
        return [call(self.space, "AddDimDiametric", p1, p2, self.k(CIRCLE_LEADER))]

    def dim_arc(self, ent):
        cx, cy = ent.Center[0], ent.Center[1]
        r = ent.Radius
        start, end = ent.StartAngle, ent.EndAngle
        mid = start + ((end - start) % (2 * math.pi)) / 2
        chord = apt(cx + r * math.cos(mid), cy + r * math.sin(mid))
        return [call(self.space, "AddDimRadial", apt(cx, cy), chord, self.k(CIRCLE_LEADER))]

    def dim_point(self, ent):
        x, y = ent.Coordinates[0], ent.Coordinates[1]
        lead = self.k(POINT_LEADER)
        dim_x = call(self.space, "AddDimOrdinate", apt(x, y), apt(x, y + lead), True)   # X 座標
        dim_y = call(self.space, "AddDimOrdinate", apt(x, y), apt(x + lead, y), False)  # Y 座標
        return [dim_x, dim_y]

    def centerline(self, center, r):
        """畫十字中心線（水平 + 垂直），放在中心線圖層。"""
        if not DRAW_CENTERLINES:
            return
        cx, cy = center[0], center[1]
        half = r + self.k(CENTER_EXTEND)
        # 讓線型在這個長度上至少出現約兩個循環，小圓也看得出一點鏈線
        lt_scale = max(2 * half / (self.ltscale_base * 2), 0.01)
        for (x1, y1, x2, y2) in ((cx - half, cy, cx + half, cy),
                                 (cx, cy - half, cx, cy + half)):
            line = call(self.space, "AddLine", apt(x1, y1), apt(x2, y2))
            put(line, "Layer", CENTER_LAYER)
            try:
                put(line, "LinetypeScale", lt_scale)
            except Exception:
                pass
            self.done.add(get(line, "Handle"))

    def dim_angles(self, sid):
        """這段線的兩個端點，和相接的線段之間加角度標註。"""
        if not DIM_ANGLES:
            return []
        dims = []
        for vertex in self.segments[sid]:
            # 在這個頂點相接的所有線段，記錄方向
            rays = []
            for qid in self.pt_index.get(pt_key(vertex), ()):
                q1, q2 = self.segments[qid]
                other = q2 if same_pt(q1, vertex) else q1
                u = unit(other[0] - vertex[0], other[1] - vertex[1])
                if u is not None:
                    rays.append((math.atan2(u[1], u[0]), u, other, qid))
            if len(rays) < 2:
                continue
            # 依方向排序，只標「相鄰」且小於 180 度的兩條
            rays.sort(key=lambda r: r[0])
            n = len(rays)
            for k in range(n):
                a, b = rays[k], rays[(k + 1) % n]
                if sid not in (a[3], b[3]) or a[3] == b[3]:
                    continue
                deg = math.degrees((b[0] - a[0]) % (2 * math.pi))
                if deg < 0.01 or deg > 179.99:
                    continue
                if SKIP_RIGHT_ANGLE and abs(deg - 90) < 0.01:
                    continue
                key = (pt_key(vertex), frozenset((a[3], b[3])))
                if key in self.angle_keys:
                    continue
                self.angle_keys.add(key)
                dims.append(self.add_angle(vertex, a, b))
        return dims

    def add_angle(self, vertex, ray_a, ray_b):
        ua, ub = ray_a[1], ray_b[1]
        len_a = math.hypot(ray_a[2][0] - vertex[0], ray_a[2][1] - vertex[1])
        len_b = math.hypot(ray_b[2][0] - vertex[0], ray_b[2][1] - vertex[1])
        radius = min(self.k(ANGLE_RADIUS), 0.6 * min(len_a, len_b))
        bis = unit(ua[0] + ub[0], ua[1] + ub[1])
        tx, ty = vertex[0] + bis[0] * radius, vertex[1] + bis[1] * radius
        return call(self.space, "AddDim3PointAngular",
                    apt(vertex[0], vertex[1]),
                    apt(ray_a[2][0], ray_a[2][1]),
                    apt(ray_b[2][0], ray_b[2][1]),
                    apt(tx, ty))


# ---------------- 圖面操作 ----------------

def active_space(doc):
    """目前作用中的空間：模型空間或圖紙空間（配置）。"""
    if doc.ActiveSpace == 1:
        return cast(doc.ModelSpace, "IAcadModelSpace")
    return cast(doc.PaperSpace, "IAcadPaperSpace")


def open_drawing(acad, path):
    """開啟圖檔；如果已經開著就直接切換過去。"""
    path = os.path.abspath(path)
    docs = cast(acad.Documents, "IAcadDocuments")
    for i in range(docs.Count):
        d = cast(docs.Item(i), "IAcadDocument")
        if os.path.normcase(d.FullName) == os.path.normcase(path):
            d.Activate()
            return d
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    d = cast(retry(docs.Open, path), "IAcadDocument")
    d.Activate()
    return d


def annotate_file(acad, path, save, text_height):
    print(f"開啟圖檔: {path}")
    doc = open_drawing(acad, path)
    space = cast(doc.ModelSpace, "IAcadModelSpace")
    count = space.Count
    print(f"模型空間共有 {count} 個物件，開始標註…")
    ann = Annotator(doc, space, text_height)
    ann.scan(0, count)
    if save:
        root, ext = os.path.splitext(os.path.abspath(path))
        out = f"{root}{SAVE_SUFFIX}{ext or '.dwg'}"
        retry(doc.SaveAs, out)
        print(f"已另存: {out}")
    return doc, space, ann


def diagnose(acad):
    """印出 CAD 環境資訊，以及模型空間認得哪些方法。"""
    def safe(fn):
        try:
            return fn()
        except Exception as e:
            return f"<{e!r}>"

    print("---- 診斷資訊 ----")
    print("Python:", sys.version)
    print("comtypes:", safe(lambda: comtypes.__version__))
    print("程式名稱:", safe(lambda: get(acad, "Name")))
    print("版本:", safe(lambda: get(acad, "Version")))
    print("路徑:", safe(lambda: get(acad, "FullName")))
    print("型別庫模組:", safe(lambda: acad_typelib().__name__))
    doc = cast(acad.ActiveDocument, "IAcadDocument")
    print("圖面:", safe(lambda: doc.Name), " ActiveSpace:", safe(lambda: doc.ActiveSpace))
    for label, getter, iface in (("ModelSpace", lambda: doc.ModelSpace, "IAcadModelSpace"),
                                 ("ActiveLayout.Block", lambda: doc.ActiveLayout.Block, "IAcadBlock")):
        obj = safe(getter)
        if isinstance(obj, str):
            print(label, "取得失敗:", obj)
            continue
        print(f"{label}: 原始型別 {type(obj).__name__}")
        disp = safe(lambda: obj.QueryInterface(IDispatch))
        if isinstance(disp, str):
            print("  QueryInterface(IDispatch) 失敗:", disp)
            continue
        print("  GetTypeInfo:", safe(lambda: disp.GetTypeInfo(0).GetDocumentation(-1)[0]))
        print("  ObjectName:", safe(lambda: _invoke(obj, "ObjectName", DISPATCH_PROPERTYGET)))
        print("  Count:", safe(lambda: _invoke(obj, "Count", DISPATCH_PROPERTYGET)))
        for m in ("AddLine", "AddCircle", "AddDimAligned", "AddDimRotated",
                  "AddDimDiametric", "AddDimRadial", "AddDim3PointAngular", "AddDimOrdinate"):
            print(f"  {m}: DISPID", safe(lambda: disp.GetIDsOfNames(m)[0]))
        typed_obj = safe(lambda: cast(obj, iface))
        print(f"  轉成 {iface}: {type(typed_obj).__name__}, 有 AddDimDiametric: "
              f"{hasattr(typed_obj, 'AddDimDiametric') if not isinstance(typed_obj, str) else typed_obj}")
    print("---- 診斷結束，請把以上全部貼給我 ----")


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="AutoCAD 自動標註")
    parser.add_argument("files", nargs="*", help="要開啟並標註的圖檔 (.dwg)")
    parser.add_argument("--all", action="store_true", help="目前圖面上既有的物件也標註")
    parser.add_argument("--once", action="store_true", help="處理完圖檔就結束，不繼續監看")
    parser.add_argument("--no-save", action="store_true", help="讀圖檔標註後不要另存")
    parser.add_argument("--now", action="store_true", help="不等指令結束，畫一段就標一段")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示除錯訊息")
    parser.add_argument("--text", type=float, help="標註字高（不指定就依圖形大小自動決定）")
    parser.add_argument("--diag", action="store_true", help="檢查 CAD 環境後結束（回報問題用）")
    args = parser.parse_args()
    VERBOSE = args.verbose
    wait_cmd = WAIT_FOR_COMMAND_END and not args.now

    try:
        # 不用 dynamic：全部走 AutoCAD 型別庫的早期繫結，不靠名稱查詢
        acad = typed(comtypes.client.GetActiveObject("AutoCAD.Application"))
    except (OSError, COMError):
        print("找不到執行中的 AutoCAD。請先開啟 AutoCAD（LT 版不支援），再執行本程式。")
        return
    acad = cast(acad, "IAcadApplication")
    print(f"已連線 AutoCAD {acad.Version}")
    if args.diag:
        diagnose(acad)
        return

    annotators = {}   # (圖面, 空間) -> Annotator
    counts = {}       # (圖面, 空間) -> 上次看到的物件數量

    for f in args.files:
        try:
            doc, space, ann = annotate_file(acad, f, not args.no_save, args.text)
            key = (doc.Name, space.Name)
            annotators[key] = ann
            counts[key] = space.Count
        except Exception as e:
            print(f"處理圖檔失敗 {f}: {e!r}")
            if VERBOSE:
                traceback.print_exc()

    if args.once:
        print("完成。")
        return

    print("開始監看新物件… (Ctrl+C 停止)")
    waiting = False
    while True:
        try:
            doc = cast(acad.ActiveDocument, "IAcadDocument")
            doc_key = doc.Name

            if wait_cmd and doc.GetVariable("CMDACTIVE"):
                if not waiting:
                    log("指令執行中，等指令結束後再標註…")
                waiting = True
            else:
                waiting = False
                space = active_space(doc)
                key = (doc_key, space.Name)
                count = space.Count

                if key not in annotators:
                    print(f"監看圖面: {doc_key} / {space.Name}（目前 {count} 個物件）")
                    annotators[key] = Annotator(doc, space, args.text)
                    # 既有物件：--all 就標註，否則只記錄（讓新線可以和舊線標角度）
                    annotators[key].scan(0, count, annotate=args.all)
                    counts[key] = count
                ann = annotators[key]
                ann.space = space

                last = counts[key]
                if count > last:
                    log(f"偵測到 {count - last} 個新物件")
                    ann.scan(last, count)
                    # 我們新增的標註和中心線會在下一輪被掃到，但會被自動略過
                    counts[key] = count
                elif count < last:
                    # 有物件被刪除（或 UNDO），重新設定基準
                    counts[key] = count

        except KeyboardInterrupt:
            print("停止自動標註")
            break
        except Exception as e:
            if is_busy(e):
                log("AutoCAD 忙碌中，稍後重試…")
            else:
                print(f"錯誤: {e!r}")
                if VERBOSE:
                    traceback.print_exc()

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("停止自動標註")
            break


if __name__ == "__main__":
    main()
