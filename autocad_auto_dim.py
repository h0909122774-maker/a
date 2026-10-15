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

字高會跟著物件大小變化（大物件大字、小物件小字），用 --text 可改成固定字高。

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
from comtypes.automation import IDispatch
from comtypes import COMError

# ---------------- 可調整的設定 ----------------
DIM_LAYER = "AUTO_DIM"        # 標註放在這個圖層
CENTER_LAYER = "中心線"        # 自動中心線放在這個圖層（線型 CENTER）
HIDDEN_LAYER = "虛線"          # 虛線圖層（線型 HIDDEN）
TEXT_RATIO = 1 / 40           # 整張圖的基準字高 = 圖形大小 × 這個比例
SIZE_BY_OBJECT = True         # True: 字高跟著每個物件的大小變（大物件大字、小物件小字）
OBJECT_TEXT_RATIO = 1 / 10    # 物件字高 = 物件大小 × 這個比例（字太小就調大，例如 1/6）
MIN_TEXT_FACTOR = 0.3         # 物件字高最小不小於基準字高的幾倍（避免小東西的字小到看不見）
MAX_TEXT_FACTOR = 3.0         # 物件字高最大不超過基準字高的幾倍
# 以下距離都以「字高」為單位，會跟著字高一起縮放
LINE_OFFSET = 2.5             # 長度標註離線的距離
POINT_LEADER = 4.0            # 點座標標註的引線長度
CIRCLE_LEADER = 2.0           # 圓/弧標註的引線長度
ANGLE_RADIUS = 5.0            # 角度標註弧線離頂點的距離（線太短時會自動縮小）
CENTER_EXTEND = 1.5           # 中心線超出圓的長度
DIM_ANGLES = True             # 是否標註相接兩線的角度
SKIP_RIGHT_ANGLE = True       # True: 90 度不標（改 False 就連直角也標）
DRAW_CENTERLINES = True       # 是否幫圓/弧畫中心線
VIEW_GAP_RATIO = 0.01         # 三視圖：相距小於圖寬 1% 的東西算同一個視圖
VIEW_MIN_SEGMENT = 0.05       # 三視圖：短於視圖大小 5% 的線段不標
SYNC_CHANGES = True           # 刪除物件時一起刪標註、修改物件時重新標註
CHECK_INTERVAL = 1.0          # 每幾秒檢查一次刪除/修改
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
        self.dimtxt = float(self.doc.GetVariable("DIMTXT")) or 2.5
        self.fixed = bool(text_height)
        self.base_h = h
        self.use_height(h)
        if self.fixed or not SIZE_BY_OBJECT:
            print(f"標註字高 {h:g}（{how}；可用 --text 指定）")
        else:
            print(f"標註字高依物件大小自動調整（基準 {h:g}；用 --text 可固定字高）")

    def use_height(self, h):
        self.h = h
        self.scale = h / self.dimtxt           # 標註的 ScaleFactor（字、箭頭一起放大）

    def height_for(self, size):
        """依物件大小決定這個物件的字高。"""
        if self.fixed or not SIZE_BY_OBJECT or size <= TOL:
            return self.base_h
        h = nice_number(size * OBJECT_TEXT_RATIO)
        return min(max(h, self.base_h * MIN_TEXT_FACTOR), self.base_h * MAX_TEXT_FACTOR)

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
            if iface is None or layer in OWN_LAYERS:
                yield handle, name, None
            else:
                yield handle, name, cast(ent, iface)

    def read_geometry(self, handle, name, ent):
        if name == "AcDbLine":
            self.add_segment(handle, ent.StartPoint, ent.EndPoint)
        elif name == "AcDbPolyline":
            for k, (p1, p2) in enumerate(self.poly_segments(ent)):
                self.add_segment(f"{handle}:{k}", p1, p2)
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
                retry(self.read_geometry, handle, name, ent)   # 先把這批的線段都記下來
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

    def check_changes(self):
        """檢查標註過的物件有沒有被刪除或修改：刪除就一起刪標註，修改就重新標註。"""
        for handle, (name, sig) in list(self.tracked.items()):
            try:
                ent = cast(retry(self.doc.HandleToObject, handle), iface_for(name))
                new_sig = retry(geometry_sig, name, ent)
            except Exception:
                self.forget(handle)
                self.done.discard(handle)
                print(f"{name} (handle {handle}) 已刪除，一併刪除它的標註")
                continue
            if new_sig != sig:
                self.forget(handle)
                retry(self.read_geometry, handle, name, ent)
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
        elif name == "AcDbCircle":
            out += self.dim_circle(ent)
            self.centerline(ent.Center, ent.Radius)
        elif name == "AcDbArc":
            out += self.dim_arc(ent)
            self.centerline(ent.Center, ent.Radius)
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

    def chain_dims(self, rounds, box, size, horizontal):
        """孔中心位置的串連尺寸：水平方向標在視圖下方，垂直方向標在視圖右方。"""
        x0, y0, x1, y1 = box
        if not rounds:
            return []
        off = self.k(LINE_OFFSET)
        merge = size * 0.005
        if horizontal:
            stops = [(x0, (x0, y0)), (x1, (x1, y0))]
            stops += [(e.Center[0], (e.Center[0], e.Center[1])) for _, e, _ in rounds]
        else:
            stops = [(y0, (x1, y0)), (y1, (x1, y1))]
            stops += [(e.Center[1], (e.Center[0], e.Center[1])) for _, e, _ in rounds]
        stops.sort(key=lambda t: t[0])
        uniq = []
        for v, p in stops:
            if not uniq or v - uniq[-1][0] > merge:
                uniq.append((v, p))
        if len(uniq) <= 2:
            return []      # 和總長/總高一樣，不用另外標
        dims = []
        for (va, pa), (vb, pb) in zip(uniq, uniq[1:]):
            if horizontal:
                dims.append(call(self.space, "AddDimRotated", apt(*pa), apt(*pb),
                                 apt((va + vb) / 2, y0 - off), 0.0))
            else:
                dims.append(call(self.space, "AddDimRotated", apt(*pa), apt(*pb),
                                 apt(x1 + off, (va + vb) / 2), math.pi / 2))
        return dims

    def annotate_views(self):
        """三視圖標註：每個視圖標總長、總高、主要線段長度、圓的直徑與中心線。"""
        views = self.find_views()
        print(f"找到 {len(views)} 個視圖")
        for n, ((x0, y0, x1, y1), members) in enumerate(views, 1):
            size = max(x1 - x0, y1 - y0)
            if size <= TOL:
                continue
            self.use_height(self.height_for(size))
            off = self.k(LINE_OFFSET)
            dims = []
            try:
                rounds = [self.round_items[h] for _, kind, h in members if kind == "round"]
                # 孔位置的串連尺寸（像 40 | 45 | 12 這樣，從邊到各孔中心）
                chain_x = self.chain_dims(rounds, (x0, y0, x1, y1), size, horizontal=True)
                chain_y = self.chain_dims(rounds, (x0, y0, x1, y1), size, horizontal=False)
                dims += chain_x + chain_y
                # 總長（下方）、總高（左方），有串連尺寸時放在更外面一層
                ox = off * (2 if chain_x else 1)
                oy = off * (2 if chain_y else 1)
                if x1 - x0 > TOL:
                    dims.append(call(self.space, "AddDimRotated", apt(x0, y0), apt(x1, y0),
                                     apt((x0 + x1) / 2, y0 - ox), 0.0))
                if y1 - y0 > TOL:
                    dims.append(call(self.space, "AddDimRotated", apt(x0, y0), apt(x0, y1),
                                     apt(x0 - oy, (y0 + y1) / 2), math.pi / 2))
                # 主要線段：同一視圖裡相同方向、相同長度的只標一次，太短的不標
                seen = set()
                for _, kind, sid in members:
                    if kind != "seg":
                        continue
                    p1, p2 = self.segments[sid]
                    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                    if length < size * VIEW_MIN_SEGMENT or abs(length - (x1 - x0)) < TOL \
                            and abs(p1[1] - p2[1]) < TOL or abs(length - (y1 - y0)) < TOL \
                            and abs(p1[0] - p2[0]) < TOL:
                        continue   # 太短，或和總長/總高重複
                    direction = round(math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180, 1)
                    key = (round(length, 4), direction)
                    if key in seen:
                        continue
                    seen.add(key)
                    dims += self.dim_segment(sid)
                # 圓與弧：同一視圖相同大小只標一次；有好幾個時寫成「4X Ø6」
                counts = {}
                for name, ent, _ in rounds:
                    key = (name, round(ent.Radius, 4))
                    counts[key] = counts.get(key, 0) + 1
                seen_r = set()
                for name, ent, _ in rounds:
                    key = (name, round(ent.Radius, 4))
                    if key not in seen_r:
                        seen_r.add(key)
                        if name == "AcDbCircle":
                            d = self.dim_circle(ent)[0]
                            if counts[key] > 1:
                                put(d, "TextOverride", f"{counts[key]}X%%c<>")
                        else:
                            d = self.dim_arc(ent)[0]
                        dims.append(d)
                    if name == "AcDbCircle":
                        self.centerline(ent.Center, ent.Radius)
                for d in dims:
                    put(d, "Layer", DIM_LAYER)
                    try:
                        put(d, "ScaleFactor", self.scale)
                    except Exception as e:
                        log(f"設定標註比例失敗: {e!r}")
                print(f"視圖 {n}: {x1 - x0:g} × {y1 - y0:g}，{len(dims)} 個標註，字高 {self.h:g}")
            except Exception as e:
                print(f"視圖 {n} 標註失敗: {e!r}")
                if VERBOSE:
                    traceback.print_exc()

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
        ann.scan(0, count, annotate=False)
        ann.annotate_views()
    else:
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
        space = active_space(doc)
        ann = Annotator(doc, space, args.text)
        ann.scan(0, space.Count, annotate=False)
        ann.annotate_views()
    if args.once or args.views:
        print("完成。")
        return

    print("開始監看新物件… (Ctrl+C 停止)")
    waiting = False
    last_check = 0.0
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

                # 每隔一段時間檢查：刪掉的物件連標註一起刪、改過的物件重新標註
                if SYNC_CHANGES and time.time() - last_check >= CHECK_INTERVAL:
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
                if VERBOSE:
                    traceback.print_exc()

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("停止自動標註")
            break


if __name__ == "__main__":
    main()
