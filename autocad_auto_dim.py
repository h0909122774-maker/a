"""
AutoCAD 自動標註 (Python + COM)

會幫圖上的「點 / 線 / 圓 / 弧 / 聚合線」自動加上標註：

    點 (POINT)          -> X、Y 座標標註 (ordinate)
    線 (LINE)           -> 對齊長度標註 (aligned)
    圓 (CIRCLE)         -> 直徑標註 + 十字中心線
    弧 (ARC)            -> 半徑標註 + 十字中心線
    聚合線 (LWPOLYLINE) -> 每一段直線段的長度標註
    相接的兩條線        -> 角度標註（線段端點相接、且不是一直線時）
    立體 (3D 實體/曲面/網格) -> 外框的長、寬、高（圓柱、球、圓錐的長改標直徑 Ø）

字高預設用標註型式的標準字高 (DIMTXT)，只有特別大的物件才放大；用 --text 可改成固定字高。

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
    python autocad_auto_dim.py --views         # 三視圖：目前圖面每個視圖標總長、總高、主要尺寸
    python autocad_auto_dim.py 圖檔.dwg --views # 三視圖：讀圖檔標註後另存
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
from comtypes.automation import IDispatch, VARIANT, VT_I2
from comtypes import COMError

# ---------------- 可調整的設定 ----------------
DIM_LAYER = "AUTO_DIM"        # 標註放在這個圖層
CENTER_LAYER = "中心線"        # 自動中心線放在這個圖層（線型 CENTER）
HIDDEN_LAYER = "虛線"          # 虛線圖層（線型 HIDDEN）
# 字高：一般大小的零件用標註型式本來的字高（DIMTXT，通常 2.5 或 3.5），
# 只有物件大於 NORMAL_SIZE 時才按比例放大，避免正常尺寸的圖標註變超大
SIZE_BY_OBJECT = True         # True: 特別大的物件字跟著放大；False: 全部用標準字高
NORMAL_SIZE = 200.0           # 物件大小在這以內都用標準字高（單位同圖面，例如 mm）
MAX_TEXT_FACTOR = 20.0        # 放大最多到標準字高的幾倍
# 以下距離都以「字高」為單位，會跟著字高一起縮放
LINE_OFFSET = 2.5             # 長度標註離線的距離
POINT_LEADER = 4.0            # 點座標標註的引線長度
CIRCLE_LEADER = 2.0           # 圓/弧標註的引線長度
ANGLE_RADIUS = 5.0            # 角度標註弧線離頂點的距離（線太短時會自動縮小）
CENTER_EXTEND = 1.5           # 中心線超出圓的長度
DIM_ANGLES = True             # 是否標註相接兩線的角度
SKIP_RIGHT_ANGLE = True       # True: 90 度不標（改 False 就連直角也標）
DRAW_CENTERLINES = True       # 是否幫圓畫中心線
ARC_CENTERLINE_MIN_DEG = 180  # 弧要大於這個角度才畫中心線（小圓角 R5 之類的不畫）
VIEW_GAP_RATIO = 0.01         # 三視圖：相距小於圖寬 1% 的東西算同一個視圖
VIEW_MIN_SEGMENT = 0.05       # 三視圖：短於視圖大小 5% 的線段不標
VIEW_SEGMENTS = False         # 三視圖：是否標每段線長（工程圖通常不標，預設關）
LABEL_LEADERS = (2, 4, 7, 11)  # 圓/弧標註引線長度候選（字高的倍數），擠的時候會拉長
VIEW_EACH_ARC = False         # 三視圖：True 每個弧都標 R；False 相同半徑只標一次
VIEW_OVERALL = False          # 三視圖：有孔時是否另外標總長、總高
VIEWS_VAR = "USERI4"          # AutoCAD 裡輸入 ADVIEWS（設 USERI4 = 1）就對目前圖面做工程圖式標註
SYNC_CHANGES = True           # 刪除物件時一起刪標註、修改物件時重新標註
CHECK_INTERVAL = 1.0          # 每幾秒檢查一次刪除/修改
SKIP_LAYERS = set()           # 這些圖層上的東西不標註，例如 {"組合件", "標準件"}
                              # （另外，名稱以 NODIM 開頭的圖層也一律不標）
PAUSE_VAR = "USERI5"          # 開關：AutoCAD 裡 USERI5 = 1 暫停自動標註，0 恢復（存在圖檔裡）
DEDUPE = True                 # 重疊的線/同位置同大小的圓只標一次（畫組合件時避免重複標註）
SAVE_SUFFIX = "_標註"          # 讀圖檔標註後另存的檔名後綴
POLL_INTERVAL = 0.3           # 每幾秒檢查一次新物件
WAIT_FOR_COMMAND_END = True   # True: 等指令(如 LINE)結束後才標註，避免干擾繪圖
VERBOSE = False               # 用 -v 參數開啟除錯訊息
# ----------------------------------------------

# 這些圖層上的物件是本程式畫的，不再標註
OWN_LAYERS = {DIM_LAYER, CENTER_LAYER}


def skip_layer(layer):
    """不標註的圖層：本程式自己的圖層、SKIP_LAYERS、名稱以 NODIM 開頭的圖層。"""
    return layer in OWN_LAYERS or layer in SKIP_LAYERS or layer.upper().startswith("NODIM")
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
    "AcDb3dSolid": "IAcad3DSolid",
}

# 其他立體物件（曲面、網格）：用外框標長、寬、高
SOLID_NAMES = ("AcDb3dSolid", "AcDbSubDMesh", "AcDbPolyFaceMesh", "AcDbPolygonMesh")


def is_solid(name):
    return name in SOLID_NAMES or name.endswith("Surface")


def iface_for(name):
    if name in INTERFACES:
        return INTERFACES[name]
    return "IAcadEntity" if is_solid(name) else None


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


class PolyArc:
    """聚合線的圓弧段，提供和 AcDbArc 一樣的 Center / Radius / StartAngle / EndAngle。"""

    def __init__(self, center, radius, start, end, handle):
        self.Center = (center[0], center[1], 0.0)
        self.Radius = radius
        self.StartAngle = start
        self.EndAngle = end
        self.Handle = handle


def poly_arcs(ent):
    """取出聚合線裡有凸度 (bulge) 的弧段。"""
    c = list(ent.Coordinates)
    pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
    n = len(pts)
    seg_count = n if ent.Closed else n - 1
    handle = ent.Handle
    for i in range(seg_count):
        b = ent.GetBulge(i)
        if abs(b) <= 1e-9:
            continue
        p1, p2 = pts[i], pts[(i + 1) % n]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        chord = math.hypot(dx, dy)
        if chord <= TOL:
            continue
        lx, ly = -dy / chord, dx / chord                 # 弦的左側法向量
        h = (chord / 2) * (1 - b * b) / (2 * b)          # 弦中點到圓心的距離（帶正負）
        cx = (p1[0] + p2[0]) / 2 + lx * h
        cy = (p1[1] + p2[1]) / 2 + ly * h
        r = math.hypot(p1[0] - cx, p1[1] - cy)
        a1 = math.atan2(p1[1] - cy, p1[0] - cx)
        a2 = math.atan2(p2[1] - cy, p2[0] - cx)
        start, end = (a1, a2) if b > 0 else (a2, a1)     # 統一成逆時針
        yield PolyArc((cx, cy), r, start, end, f"{handle}:{i}")


def owner(sid):
    """線段 id（handle 或 handle:段號）所屬的物件 handle。"""
    return str(sid).split(":")[0]


def geometry_sig(name, ent):
    """物件的幾何特徵；改過圖形之後這個值會不同。"""
    def r(v):
        return tuple(round(x, 6) for x in v)
    if name == "AcDbLine":
        return r(ent.StartPoint) + r(ent.EndPoint)
    if name in ("AcDbCircle", "AcDbArc"):
        extra = (round(ent.StartAngle, 6), round(ent.EndAngle, 6)) if name == "AcDbArc" else ()
        return r(ent.Center) + (round(ent.Radius, 6),) + extra
    if name == "AcDbPoint":
        return r(ent.Coordinates)
    if name == "AcDbPolyline":
        return r(ent.Coordinates) + (bool(ent.Closed),)
    lo, hi = call(ent, "GetBoundingBox")
    return r(lo) + r(hi)


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
        self.round_items = {}      # 圓/弧 handle -> (物件名, 物件, 外框)
        self.created = {}          # 原物件 handle -> 本程式為它畫的標註/中心線
        self.tracked = {}          # 原物件 handle -> (物件名, 幾何特徵)，用來偵測刪除/修改
        self._made = []            # 標註一個物件時順便畫的中心線
        self.has_old_dims = False  # 圖裡已經有本程式之前畫的標註
        self.claims = {}           # 幾何位置 -> 先標它的物件 handle（避免重複標註）
        self.occupied = []         # 已放好的標註文字範圍（避免擠在一起）
        setup_layers(doc)
        self.ltscale_base = linetype_pattern_length(doc)

        # 先讀一次既有物件的幾何（不標註），用來決定字高、判斷標註要放外側
        self.collect(0, space.Count)
        self.set_text_height(text_height)

    # ---- 字高 / 比例 ----
    def set_text_height(self, text_height):
        self.dimtxt = float(self.doc.GetVariable("DIMTXT")) or 2.5
        self.fixed = bool(text_height)
        h = text_height or self.dimtxt      # 預設：標註型式的標準字高
        self.base_h = h
        self.use_height(h)
        if self.fixed or not SIZE_BY_OBJECT:
            print(f"標註字高 {h:g}（可用 --text 指定）")
        else:
            print(f"標註字高 {h:g}；大於 {NORMAL_SIZE:g} 的物件會等比例放大（用 --text 可固定字高）")

    def use_height(self, h):
        self.h = h
        self.scale = h / self.dimtxt           # 標註的 ScaleFactor（字、箭頭一起放大）

    def height_for(self, size):
        """依物件大小決定這個物件的字高。"""
        if self.fixed or not SIZE_BY_OBJECT or size <= NORMAL_SIZE:
            return self.base_h
        h = nice_number(self.base_h * size / NORMAL_SIZE)
        return min(h, self.base_h * MAX_TEXT_FACTOR)

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
            iface = iface_for(name)
            if layer == DIM_LAYER:
                self.has_old_dims = True
            if iface is None or skip_layer(layer):
                yield handle, name, None
            else:
                yield handle, name, cast(ent, iface)

    def read_geometry(self, handle, name, ent):
        if name == "AcDbLine":
            self.add_segment(handle, ent.StartPoint, ent.EndPoint)
        elif name == "AcDbPolyline":
            for k, (p1, p2) in enumerate(self.poly_segments(ent)):
                self.add_segment(f"{handle}:{k}", p1, p2)
            # 聚合線裡的圓弧段（例如對聚合線做 FILLET 產生的圓角）也當成弧
            for k, arc in enumerate(poly_arcs(ent)):
                c, r = arc.Center, arc.Radius
                box = (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
                self.round_items[f"{handle}:a{k}"] = ("AcDbArc", arc, box)
                self.grow_bbox(*box)
        elif name in ("AcDbCircle", "AcDbArc"):
            c, r = ent.Center, ent.Radius
            self.round_items[handle] = (name, ent, (c[0] - r, c[1] - r, c[0] + r, c[1] + r))
            self.grow_bbox(c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        elif name == "AcDbPoint":
            c = ent.Coordinates
            self.grow_bbox(c[0], c[1])
        elif is_solid(name):
            lo, hi = self.bounding_box(ent)
            self.grow_bbox(lo[0], lo[1], hi[0], hi[1])

    @staticmethod
    def bounding_box(ent):
        lo, hi = call(ent, "GetBoundingBox")
        return tuple(lo), tuple(hi)

    def object_size(self, handle, name, ent):
        """物件大小：圖形（相連線段）外框的長邊、圓的直徑、立體外框的最長邊。"""
        if name == "AcDbLine":
            return self.shape_info(handle)[1]
        if name == "AcDbPolyline":
            sids = [sid for sid in self.segments if sid.startswith(f"{handle}:")]
            return self.shape_info(sids[0])[1] if sids else 0.0
        if name in ("AcDbCircle", "AcDbArc"):
            return 2 * ent.Radius
        if is_solid(name):
            lo, hi = self.bounding_box(ent)
            return max(hi[i] - lo[i] for i in range(3))
        return 0.0

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
                try:
                    retry(self.read_geometry, handle, name, ent)   # 先把這批的線段都記下來
                except Exception as e:
                    # 早期繫結讀不到時，改用動態呼叫再試一次
                    try:
                        dyn = comtypes.client.dynamic.Dispatch(ent.QueryInterface(IDispatch))
                        retry(self.read_geometry, handle, name, dyn)
                        log(f"{name} (handle {handle}) 改用動態呼叫讀取成功")
                    except Exception as e2:
                        print(f"讀取 {name} (handle {handle}) 失敗，略過: {e!r} / {e2!r}")
        if not annotate:
            return
        for handle, name, ent in items:
            if ent is None:
                if "Dimension" not in name:
                    log(f"略過 {name}（不支援或本程式畫的物件）")
                continue
            self.annotate_one(handle, name, ent)

    def annotate_one(self, handle, name, ent, verb="已標註"):
        try:
            h = self.height_for(retry(self.object_size, handle, name, ent))
            self.use_height(h)
            self._made = []
            dims = retry(self.annotate_entity, handle, name, ent)
            for d in dims:
                put(d, "Layer", DIM_LAYER)
                try:
                    put(d, "ScaleFactor", self.scale)
                except Exception as e:
                    log(f"設定標註比例失敗: {e!r}")
            self.created.setdefault(handle, []).extend(dims + self._made)
            self.tracked[handle] = (name, geometry_sig(name, ent))
            print(f"{verb} {name} (handle {handle}，字高 {h:g})")
        except Exception as e:
            print(f"標註失敗 {name} (handle {handle}): {e!r}")
            if VERBOSE:
                traceback.print_exc()

    def purge_own(self):
        """刪掉這個空間裡本程式之前畫的標註和中心線（AUTO_DIM、中心線圖層）。"""
        removed = failed = 0
        for i in range(self.space.Count - 1, -1, -1):
            try:
                ent = retry(self.space.Item, i)
                if retry(lambda: ent.Layer) in OWN_LAYERS:
                    retry(ent.Delete)
                    removed += 1
            except Exception as e:
                failed += 1
                log(f"刪除第 {i} 個物件失敗: {e!r}")
        if failed:
            print(f"有 {failed} 個舊標註刪不掉，略過")
        self.done = set()
        self.has_old_dims = False
        return removed

    # ---- 刪除 / 修改後同步 ----
    def forget(self, handle):
        """刪掉這個物件的標註和中心線，並清掉它的幾何記錄。"""
        for obj in self.created.pop(handle, []):
            try:
                call(obj, "Delete")
            except Exception:
                pass            # 可能已經被刪了（例如兩條線共用的角度標註）
        sids = [sid for sid in self.segments if owner(sid) == handle]
        for sid in sids:
            for p in self.segments.pop(sid):
                self.pt_index.get(pt_key(p), set()).discard(sid)
        self.angle_keys = {k for k in self.angle_keys if not (k[1] & set(sids))}
        self.round_items.pop(handle, None)
        self.tracked.pop(handle, None)
        self.claims = {k: v for k, v in self.claims.items() if v != handle}

    def check_changes(self):
        """檢查標註過的物件有沒有被刪除或修改：刪除就一起刪標註，修改就重新標註。

        直接掃一遍目前空間裡的物件（不用 HandleToObject，那個在某些情況會失敗，
        讓還在的物件被誤當成已刪除）。
        """
        if not self.tracked:
            return
        present = {}
        for i in range(self.space.Count):
            ent = retry(self.space.Item, i)
            handle = retry(lambda: ent.Handle)
            if handle in self.tracked:
                present[handle] = ent
        for handle, (name, sig) in list(self.tracked.items()):
            if handle not in present:
                self.forget(handle)
                self.done.discard(handle)
                print(f"{name} (handle {handle}) 已刪除，一併刪除它的標註")
                continue
            try:
                ent = cast(present[handle], iface_for(name))
                new_sig = retry(geometry_sig, name, ent)
            except Exception as e:
                log(f"讀取 {name} (handle {handle}) 失敗，稍後再試: {e!r}")
                continue
            if new_sig != sig:
                log(f"{name} (handle {handle}) 圖形改變: {sig} -> {new_sig}")
                self.forget(handle)
                try:
                    retry(self.read_geometry, handle, name, ent)
                except Exception as e:
                    print(f"讀取 {name} 新圖形失敗: {e!r}")
                self.annotate_one(handle, name, ent, verb="已更新標註")

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
        elif name in ("AcDbCircle", "AcDbArc"):
            d = self.dim_circle(ent) if name == "AcDbCircle" else self.dim_arc(ent)
            out += d
            if d and self.wants_centerline(name, ent):
                self.centerline(ent.Center, ent.Radius, owner_handle=ent.Handle)
        elif name == "AcDbPoint":
            out += self.dim_point(ent)
        elif is_solid(name):
            out += self.dim_solid(ent)
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
        return self.shape_info(sid)[0]

    def shape_info(self, sid):
        """和這段線相連的整個圖形（例如一個矩形）的中心，用來把標註放到外側。"""
        seen, stack = {sid}, [sid]
        sx = sy = total = 0.0
        xs, ys = [], []
        while stack and len(seen) < 5000:
            cur = stack.pop()
            p1, p2 = self.segments[cur]
            xs += [p1[0], p2[0]]
            ys += [p1[1], p2[1]]
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            sx += (p1[0] + p2[0]) / 2 * length
            sy += (p1[1] + p2[1]) / 2 * length
            total += length
            for p in (p1, p2):
                for nxt in self.pt_index.get(pt_key(p), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
        size = max(max(xs) - min(xs), max(ys) - min(ys))
        return ((sx / total, sy / total) if total > TOL else None), size

    def claim(self, key, handle):
        """同一個幾何位置只讓第一個物件標註；回傳 False 表示已經有別的物件標過。"""
        if not DEDUPE:
            return True
        holder = self.claims.setdefault(key, handle)
        if holder != handle:
            log(f"略過重複標註（和 handle {holder} 重疊）")
            return False
        return True

    def dim_segment(self, sid):
        """線段長度標註，放在圖形外側。"""
        p1, p2 = self.segments[sid]
        d = unit(p2[0] - p1[0], p2[1] - p1[1])
        if d is None:
            return []
        if not self.claim(("seg",) + tuple(sorted((pt_key(p1), pt_key(p2)))), owner(sid)):
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
        if not self.claim(("circle", pt_key((cx, cy)), round(r, 6)), ent.Handle):
            return []
        a = math.radians(45)
        p1 = apt(cx + r * math.cos(a), cy + r * math.sin(a))
        p2 = apt(cx - r * math.cos(a), cy - r * math.sin(a))
        return [call(self.space, "AddDimDiametric", p1, p2, self.k(CIRCLE_LEADER))]

    def dim_arc(self, ent):
        cx, cy = ent.Center[0], ent.Center[1]
        r = ent.Radius
        if not self.claim(("arc", pt_key((cx, cy)), round(r, 6)), ent.Handle):
            return []
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

    def dim_solid(self, ent):
        """立體物件：依外框標長 (X)、寬 (Y)、高 (Z)。圓柱、球、圓錐的長改標直徑 Ø。"""
        (x0, y0, z0), (x1, y1, z1) = self.bounding_box(ent)
        off = self.k(LINE_OFFSET)
        dims = []
        try:
            solid_type = str(get(ent, "SolidType"))
        except Exception:
            solid_type = ""
        round_solid = solid_type.lower() in ("cylinder", "sphere", "cone", "torus")

        if x1 - x0 > TOL:      # 長：前面底邊，往 -Y 偏移
            d = call(self.space, "AddDimAligned", apt(x0, y0, z0), apt(x1, y0, z0),
                     apt((x0 + x1) / 2, y0 - off, z0))
            if round_solid:
                put(d, "TextOverride", "%%c<>")
            dims.append(d)
        if y1 - y0 > TOL and not round_solid:   # 寬：右側底邊，往 +X 偏移
            dims.append(call(self.space, "AddDimAligned", apt(x1, y0, z0), apt(x1, y1, z0),
                             apt(x1 + off, (y0 + y1) / 2, z0)))
        if z1 - z0 > TOL:      # 高：先在 XY 平面畫好，再繞 Y 軸轉 -90° 立起來
            hgt = z1 - z0
            d = call(self.space, "AddDimAligned", apt(x0, y0, z0), apt(x0 + hgt, y0, z0),
                     apt(x0 + hgt / 2, y0 - off, z0))
            call(d, "Rotate3D", apt(x0, y0, z0), apt(x0, y0 + 1, z0), -math.pi / 2)
            dims.append(d)
        return dims

    # ---- 三視圖 ----
    def find_views(self):
        """把相連或重疊的線/圓分成一群一群，每一群就是一個視圖（前視、俯視、側視…）。"""
        boxes = []   # (外框, 種類, id)
        for sid, (p1, p2) in self.segments.items():
            boxes.append(((min(p1[0], p2[0]), min(p1[1], p2[1]),
                           max(p1[0], p2[0]), max(p1[1], p2[1])), "seg", sid))
        for handle, (_, _, box) in self.round_items.items():
            boxes.append((box, "round", handle))
        if not boxes:
            return []
        b = self.bbox
        gap = max(b[2] - b[0], b[3] - b[1]) * VIEW_GAP_RATIO
        parent = list(range(len(boxes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        order = sorted(range(len(boxes)), key=lambda i: boxes[i][0][0])
        for a_pos, i in enumerate(order):     # 依 x 排序，只比對 x 範圍重疊的
            bi = boxes[i][0]
            for j in order[a_pos + 1:]:
                bj = boxes[j][0]
                if bj[0] > bi[2] + gap:
                    break
                if bj[1] <= bi[3] + gap and bi[1] <= bj[3] + gap:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(len(boxes)):
            groups.setdefault(find(i), []).append(boxes[i])
        views = []
        for members in groups.values():
            x0 = min(m[0][0] for m in members); y0 = min(m[0][1] for m in members)
            x1 = max(m[0][2] for m in members); y1 = max(m[0][3] for m in members)
            views.append(((x0, y0, x1, y1), members))
        # 在外框裡面、沒碰到外框線的東西（例如矩形中間的孔）併進同一個視圖
        merged = True
        while merged:
            merged = False
            for i in range(len(views)):
                for j in range(i + 1, len(views)):
                    a, b2 = views[i][0], views[j][0]
                    if a[0] <= b2[2] and b2[0] <= a[2] and a[1] <= b2[3] and b2[1] <= a[3]:
                        box = (min(a[0], b2[0]), min(a[1], b2[1]), max(a[2], b2[2]), max(a[3], b2[3]))
                        views[i] = (box, views[i][1] + views[j][1])
                        del views[j]
                        merged = True
                        break
                if merged:
                    break
        views.sort(key=lambda v: (-v[0][3], v[0][0]))   # 由上到下、由左到右
        return views

    def step(self, label, fn, default=None):
        """執行一個標註步驟；失敗就印出原因，繼續做下一步（不讓一個錯誤害整張圖都沒標）。"""
        try:
            return retry(fn)
        except Exception as e:
            print(f"  {label} 失敗: {e!r}")
            traceback.print_exc()
            return default

    def chain_dims(self, centers, box, horizontal):
        """孔中心位置的串連尺寸（像 40 | 45 | 12）：水平的標在視圖下方，垂直的標在左方。"""
        x0, y0, x1, y1 = box
        off = self.k(LINE_OFFSET) * 2
        merge = max(x1 - x0, y1 - y0) * 0.005
        axis = 0 if horizontal else 1
        stops = sorted(centers, key=lambda c: c[axis])
        uniq = []
        for c in stops:
            if not uniq or c[axis] - uniq[-1][axis] > merge:
                uniq.append(c)
        dims = []
        for a, b in zip(uniq, uniq[1:]):
            if horizontal:
                dims.append(call(self.space, "AddDimRotated", apt(*a), apt(*b),
                                 apt((a[0] + b[0]) / 2, y0 - off), 0.0))
            else:
                dims.append(call(self.space, "AddDimRotated", apt(*a), apt(*b),
                                 apt(x0 - off, (a[1] + b[1]) / 2), math.pi / 2))
        return dims

    # ---- 避免標註擠在一起 ----
    def text_rect(self, x, y, text):
        """估計文字佔的範圍（中心 x, y）。"""
        w = max(len(text), 2) * self.h * 0.75 + self.h
        hh = self.h * 1.8
        return (x - w / 2, y - hh / 2, x + w / 2, y + hh / 2)

    def overlap(self, rect):
        """和已經放好的標註重疊的總面積。"""
        total = 0.0
        for o in self.occupied:
            dx = min(rect[2], o[2]) - max(rect[0], o[0])
            dy = min(rect[3], o[3]) - max(rect[1], o[1])
            if dx > 0 and dy > 0:
                total += dx * dy
        return total

    def place_label(self, cx, cy, r, text, angles, view_box):
        """在候選方向、引線長度中，挑一個不和其他標註重疊、盡量在圖形外面的位置。"""
        best = None
        for lead_k in LABEL_LEADERS:
            lead = self.k(lead_k)
            for ang in angles:
                ux, uy = math.cos(ang), math.sin(ang)
                half_w = (self.text_rect(0, 0, text)[2])
                dist = r + lead + half_w
                tx, ty = cx + dist * ux, cy + dist * uy
                rect = self.text_rect(tx, ty, text)
                score = self.overlap(rect) * 1000
                vx0, vy0, vx1, vy1 = view_box
                if vx0 < tx < vx1 and vy0 < ty < vy1:
                    score += self.h * self.h * 4       # 在圖形裡面：不是不行，但盡量放外面
                score += lead * 0.01                   # 同樣條件下引線短一點比較好
                if best is None or score < best[0]:
                    best = (score, ang, lead, rect)
            if best[0] < 1e-9:
                break
        self.occupied.append(best[3])
        return best[1], best[2]

    def size_label(self, name, ent, count, view_center, spread, view_box=None):
        """一組相同大小的圓/弧只標一個：引線往外拉、自動避開其他標註，數量寫成 2XØ4。"""
        cx, cy = ent.Center[0], ent.Center[1]
        r = ent.Radius
        view_box = view_box or (cx, cy, cx, cy)
        u = unit(cx - view_center[0], cy - view_center[1])
        base = math.atan2(u[1], u[0]) if u else math.radians(45)
        base += math.radians(25) * spread
        if name == "AcDbArc":
            start, end = ent.StartAngle, ent.EndAngle
            sweep = (end - start) % (2 * math.pi) or 2 * math.pi
            # 引線一定要指在弧上：在弧的範圍內取幾個候選角度，離「往外」方向近的先試
            cands = [start + sweep * f for f in (0.5, 0.3, 0.7, 0.15, 0.85)]
            cands.sort(key=lambda a: abs(math.atan2(math.sin(a - base), math.cos(a - base))))
            text = f"R{r:g}"
            ang, lead = self.place_label(cx, cy, r, text, cands, view_box)
            return call(self.space, "AddDimRadial", apt(cx, cy),
                        apt(cx + r * math.cos(ang), cy + r * math.sin(ang)), lead)
        text = (f"{count}X" if count > 1 else "") + f"Ø{2 * r:g}"
        cands = [base + math.radians(d) for d in (0, 30, -30, 60, -60, 90, -90, 135, -135, 180)]
        ang, lead = self.place_label(cx, cy, r, text, cands, view_box)
        ux, uy = math.cos(ang), math.sin(ang)
        d = call(self.space, "AddDimDiametric", apt(cx + r * ux, cy + r * uy),
                 apt(cx - r * ux, cy - r * uy), lead)
        if count > 1:
            put(d, "TextOverride", f"{count}X%%c<>")
        return d

    def main_centerlines(self, center, box):
        """主圓心的長中心線，橫跨整個視圖。"""
        x0, y0, x1, y1 = box
        ext = self.k(CENTER_EXTEND) * 2
        cx, cy = center
        lt_scale = max((x1 - x0 + y1 - y0) / (self.ltscale_base * 8), 0.01)
        for (a, b) in (((x0 - ext, cy), (x1 + ext, cy)), ((cx, y0 - ext), (cx, y1 + ext))):
            line = call(self.space, "AddLine", apt(*a), apt(*b))
            put(line, "Layer", CENTER_LAYER)
            try:
                put(line, "LinetypeScale", lt_scale)
            except Exception:
                pass
            self.done.add(get(line, "Handle"))

    def annotate_views(self):
        """工程圖式標註（每個視圖）：
        - 孔位置：各圓心之間的串連尺寸（下方水平、左方垂直）
        - 孔大小：相同直徑只標一次，寫成 2XØ4；圓角相同半徑只標一次 R5
        - 主圓心畫長中心線，每個圓畫十字中心線
        - 不標每一段線長（VIEW_SEGMENTS = True 可打開）
        """
        views = self.find_views()
        print(f"找到 {len(views)} 個視圖（線段 {len(self.segments)} 段、圓/弧 {len(self.round_items)} 個）")
        for n, (box, members) in enumerate(views, 1):
            x0, y0, x1, y1 = box
            size = max(x1 - x0, y1 - y0)
            if size <= TOL:
                continue
            self.use_height(self.height_for(size))
            dims = []
            try:
                rounds = [self.round_items[h] for _, kind, h in members if kind == "round"]
                circles = [(nm, e) for nm, e, _ in rounds if nm == "AcDbCircle"]
                arcs = [(nm, e) for nm, e, _ in rounds if nm == "AcDbArc"]
                view_center = ((x0 + x1) / 2, (y0 + y1) / 2)

                # 孔中心（圓心；大弧的圓心也算，例如繞著孔的凸耳）
                centers = {}
                for nm, e in circles + [(nm, e) for nm, e in arcs if self.wants_centerline(nm, e)]:
                    centers.setdefault(pt_key(e.Center), (e.Center[0], e.Center[1]))
                centers = list(centers.values())
                if len(centers) >= 2:
                    dims += self.step("水平位置尺寸", lambda: self.chain_dims(centers, box, True), [])
                    dims += self.step("垂直位置尺寸", lambda: self.chain_dims(centers, box, False), [])

                if VIEW_OVERALL or not centers:
                    off = self.k(LINE_OFFSET) * (4 if centers else 1)
                    dims.append(call(self.space, "AddDimRotated", apt(x0, y0), apt(x1, y0),
                                     apt((x0 + x1) / 2, y0 - off), 0.0))
                    dims.append(call(self.space, "AddDimRotated", apt(x0, y0), apt(x0, y1),
                                     apt(x0 - off, (y0 + y1) / 2), math.pi / 2))

                # 孔大小：同直徑一組，挑離視圖中心最遠的那個標（引線比較好往外拉）
                groups = {}
                for nm, e in circles:
                    groups.setdefault((nm, round(e.Radius, 4)), []).append(e)
                for nm, e in arcs:
                    if VIEW_EACH_ARC:      # 每個弧各自標 R（同位置同半徑的重複弧只標一次）
                        groups.setdefault((nm, round(e.Radius, 4), pt_key(e.Center)), []).append(e)
                    else:
                        groups.setdefault((nm, round(e.Radius, 4)), []).append(e)
                # 已經被佔用的地方：位置尺寸那兩排
                off2 = self.k(LINE_OFFSET) * 2
                self.occupied = [(x0 - self.h * 4, y0 - off2 - self.h * 1.5, x1 + self.h * 4, y0 - off2 + self.h * 1.5),
                                 (x0 - off2 - self.h * 1.5, y0 - self.h * 4, x0 - off2 + self.h * 1.5, y1 + self.h * 4)]
                per_center = {}
                for (nm, _, *_rest), ents in sorted(groups.items(), key=lambda kv: -kv[0][1]):
                    rep = max(ents, key=lambda e: math.hypot(e.Center[0] - view_center[0],
                                                             e.Center[1] - view_center[1]))
                    k = pt_key(rep.Center)
                    spread = per_center.get(k, 0)
                    per_center[k] = spread + 1
                    d = self.step(f"{nm} R{rep.Radius:g} 大小標註",
                                  lambda: self.size_label(nm, rep, len(ents), view_center, spread, box))
                    if d is not None:
                        dims.append(d)

                # 中心線：最大的圓當主圓心畫長中心線，其他圓畫小十字
                if circles:
                    main = max(circles, key=lambda t: t[1].Radius)[1]
                    self.step("主中心線", lambda: self.main_centerlines((main.Center[0], main.Center[1]), box))
                    self.claims[("center", pt_key(main.Center))] = "views"
                for nm, e in circles:
                    self.step("中心線", lambda: self.centerline(e.Center, e.Radius, owner_handle=e.Handle))

                if VIEW_SEGMENTS:
                    for _, kind, sid in members:
                        if kind == "seg":
                            p1, p2 = self.segments[sid]
                            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) >= size * VIEW_MIN_SEGMENT:
                                dims += self.dim_segment(sid)

                for d in dims:
                    put(d, "Layer", DIM_LAYER)
                    try:
                        put(d, "ScaleFactor", self.scale)
                    except Exception as e:
                        log(f"設定標註比例失敗: {e!r}")
                print(f"視圖 {n}: {x1 - x0:g} × {y1 - y0:g}，{len(dims)} 個標註，字高 {self.h:g}")
            except Exception as e:
                print(f"視圖 {n} 標註失敗: {e!r}")
                traceback.print_exc()

    @staticmethod
    def wants_centerline(name, ent):
        """圓都畫；弧只有大弧才畫（圓角這種小弧畫了只會滿圖都是中心線）。"""
        if name == "AcDbCircle":
            return True
        sweep = math.degrees((ent.EndAngle - ent.StartAngle) % (2 * math.pi))
        return sweep >= ARC_CENTERLINE_MIN_DEG

    def centerline(self, center, r, owner_handle=None):
        """畫十字中心線（水平 + 垂直），放在中心線圖層。同一個圓心只畫一組。"""
        if not DRAW_CENTERLINES:
            return
        if owner_handle is not None and not self.claim(("center", pt_key(center)), owner_handle):
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
            self._made.append(line)

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
                d = self.add_angle(vertex, a, b)
                dims.append(d)
                # 角度標註也掛在另一條線底下：那條線刪掉/修改時一起清掉
                other = owner(b[3] if a[3] == sid else a[3])
                if other != owner(sid):
                    self.created.setdefault(other, []).append(d)
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


def annotate_file(acad, path, save, text_height, views=False):
    print(f"開啟圖檔: {path}")
    doc = open_drawing(acad, path)
    space = cast(doc.ModelSpace, "IAcadModelSpace")
    count = space.Count
    print(f"模型空間共有 {count} 個物件，開始標註…")
    ann = Annotator(doc, space, text_height)
    if views:
        ann = run_views(doc, space, text_height)
    else:
        ann.scan(0, count)
    if save:
        root, ext = os.path.splitext(os.path.abspath(path))
        out = f"{root}{SAVE_SUFFIX}{ext or '.dwg'}"
        retry(doc.SaveAs, out)
        print(f"已另存: {out}")
    return doc, space, ann


def set_int_var(doc, name, value):
    """設定整數系統變數。AutoCAD 的 USERIx 要 16 位元整數 (VT_I2)，
    直接傳 Python int 會被當成 32 位元而回「無效的類別」。"""
    v = VARIANT()
    v.vt = VT_I2
    v._.VT_I2 = int(value)
    try:
        doc.SetVariable(name, v)
        return
    except Exception as e:
        log(f"SetVariable({name}) 失敗: {e!r}，改用指令設定")
    doc.SendCommand(f'(setvar "{name}" {int(value)}) ')


def run_views(doc, space, text_height):
    """工程圖式標註：先清掉之前的自動標註/中心線，再整張重標。"""
    ann = Annotator(doc, space, text_height)
    removed = ann.purge_own()
    if removed:
        print(f"清除之前的自動標註 {removed} 個")
    print("讀取圖形…")
    ann = Annotator(doc, space, text_height)
    ann.scan(0, space.Count, annotate=False)
    print("開始標註…")
    ann.annotate_views()
    return ann


def read_pause(doc):
    try:
        return int(doc.GetVariable(PAUSE_VAR)) == 1
    except Exception:
        return False


def poll_key_toggle(paused):
    """在 cmd 視窗按 P 切換暫停/恢復（只有 Windows 有 msvcrt）。"""
    try:
        import msvcrt
    except ImportError:
        return paused
    while msvcrt.kbhit():
        if msvcrt.getwch().lower() == "p":
            paused = not paused
    return paused


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
    parser.add_argument("--all", action="store_true",
                        help="開啟/切換到的每張圖，圖上既有的物件也標註（已標過的圖會略過）")
    parser.add_argument("--once", action="store_true", help="處理完圖檔就結束，不繼續監看")
    parser.add_argument("--no-save", action="store_true", help="讀圖檔標註後不要另存")
    parser.add_argument("--now", action="store_true", help="不等指令結束，畫一段就標一段")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示除錯訊息")
    parser.add_argument("--views", action="store_true",
                        help="三視圖模式：每個視圖標總長、總高、主要尺寸，標完就結束")
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
            doc, space, ann = annotate_file(acad, f, not args.no_save, args.text, args.views)
            key = (doc.Name, space.Name)
            annotators[key] = ann
            counts[key] = space.Count
        except Exception as e:
            print(f"處理圖檔失敗 {f}: {e!r}")
            if VERBOSE:
                traceback.print_exc()

    if args.views and not args.files:
        doc = cast(acad.ActiveDocument, "IAcadDocument")
        run_views(doc, active_space(doc), args.text)
    if args.once or args.views:
        print("完成。")
        return

    print("開始監看新物件… (Ctrl+C 停止)")
    waiting = False
    last_check = 0.0
    local_pause = False
    was_paused = False
    print("開關：AutoCAD 裡輸入 ADOFF / ADON（需載入 autodim_toggle.lsp），或在這個視窗按 P")
    while True:
        local_pause = poll_key_toggle(local_pause)
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
                    new_ann = Annotator(doc, space, args.text)
                    annotators[key] = new_ann
                    counts[key] = count     # 先記下來：就算下面標註出錯，下一輪也不會 KeyError
                    # 既有物件：--all 就標註，否則只記錄（讓新線可以和舊線標角度）
                    if args.all and new_ann.has_old_dims:
                        # 之前標過的標註和原物件已經對不起來（改圖、縮放時不會跟著變），
                        # 所以先刪掉舊的自動標註/中心線，再全部重標並重新追蹤
                        removed = new_ann.purge_own()
                        print(f"刪除之前的自動標註 {removed} 個，重新標註…")
                        count = space.Count
                    elif args.all and count > 0:
                        print("標註圖上既有物件…")
                    new_ann.scan(0, count, annotate=args.all)
                    counts[key] = space.Count
                ann = annotators[key]
                ann.space = space

                # AutoCAD 裡輸入 ADVIEWS：整張圖改成工程圖式標註
                try:
                    want_views = int(doc.GetVariable(VIEWS_VAR)) == 1
                except Exception:
                    want_views = False
                if want_views:
                    set_int_var(doc, VIEWS_VAR, 0)
                    print("收到 ADVIEWS：工程圖式標註…")
                    annotators[key] = run_views(doc, space, args.text)
                    counts[key] = space.Count
                    continue

                paused = local_pause or read_pause(doc)
                if paused != was_paused:
                    print("⏸ 自動標註已暫停（ADON 或在這裡按 P 恢復）" if paused
                          else "▶ 自動標註已恢復")
                    was_paused = paused

                last = counts.setdefault(key, count)
                if count > last:
                    log(f"偵測到 {count - last} 個新物件")
                    # 暫停時只記錄，不標註；恢復後這些物件也不會被補標
                    ann.scan(last, count, annotate=not paused)
                    # 我們新增的標註和中心線會在下一輪被掃到，但會被自動略過
                    counts[key] = count
                elif count < last:
                    # 有物件被刪除（或 UNDO），重新設定基準
                    counts[key] = count

                # 每隔一段時間檢查：刪掉的物件連標註一起刪、改過的物件重新標註
                if SYNC_CHANGES and not paused and time.time() - last_check >= CHECK_INTERVAL:
                    ann.check_changes()
                    counts[key] = space.Count
                    last_check = time.time()

        except KeyboardInterrupt:
            print("停止自動標註")
            break
        except Exception as e:
            if is_busy(e):
                log("AutoCAD 忙碌中，稍後重試…")
            else:
                print(f"錯誤: {e!r}")
                traceback.print_exc()

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("停止自動標註")
            break


if __name__ == "__main__":
    main()
