"""
AutoCAD 自動標註 (Python + COM)

執行後持續監看 AutoCAD 目前圖面，
只要你畫出新的「點 / 線 / 圓 / 弧 / 聚合線」，就自動加上標註：

    點 (POINT)          -> X、Y 座標標註 (ordinate)
    線 (LINE)           -> 對齊長度標註 (aligned)
    圓 (CIRCLE)         -> 直徑標註 (diameter)
    弧 (ARC)            -> 半徑標註 (radial)
    聚合線 (LWPOLYLINE) -> 每一段直線段的長度標註

需求: Windows + AutoCAD (完整版，LT 不支援 COM) + Python
      pip install comtypes

用法:
    1. 先開啟 AutoCAD 和要畫的圖檔
    2. python autocad_auto_dim.py          # 只標註「之後」新畫的物件
       python autocad_auto_dim.py --all    # 先把圖上既有物件也全部標註
       python autocad_auto_dim.py --now    # 不等指令結束，畫一段就標一段
       python autocad_auto_dim.py -v       # 顯示除錯訊息（沒標註時用來查原因）
    3. 在 AutoCAD 裡正常畫圖，按 Ctrl+C 停止腳本

COM 部分用 comtypes 的早期繫結：每個物件都先用 GetBestInterface
轉成實際型別 (IAcadModelSpace、IAcadCircle、IAcadLine…) 再呼叫，
不靠名稱查詢，非英文版 AutoCAD 也能用。
"""

import argparse
import array
import math
import sys
import time
import traceback

import comtypes.client
import comtypes.client.dynamic
from comtypes.automation import IDispatch
from comtypes import COMError

# ---------------- 可調整的設定 ----------------
DIM_LAYER = "AUTO_DIM"        # 標註放在這個圖層
LINE_OFFSET = 5.0             # 長度標註離線的距離（圖面單位）
POINT_LEADER = 10.0           # 點座標標註的引線長度
CIRCLE_LEADER = 5.0           # 圓/弧標註的引線長度
POLL_INTERVAL = 0.3           # 每幾秒檢查一次新物件
WAIT_FOR_COMMAND_END = True   # True: 等指令(如 LINE)結束後才標註，避免干擾繪圖
VERBOSE = False               # 用 -v 參數開啟除錯訊息
# ----------------------------------------------

# AutoCAD 忙碌時 COM 會回傳這些錯誤碼，稍後重試即可
BUSY_ERRORS = (-2147418111, -2147417846)  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER


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


def typed(obj):
    """把 COM 物件轉成它實際的介面 (例如 IAcadCircle)。"""
    try:
        return comtypes.client.GetBestInterface(obj)
    except Exception:
        return obj


# 每種物件對應的 AutoCAD 介面
INTERFACES = {
    "AcDbLine": "IAcadLine",
    "AcDbCircle": "IAcadCircle",
    "AcDbArc": "IAcadArc",
    "AcDbPoint": "IAcadPoint",
    "AcDbPolyline": "IAcadLWPolyline",
}


def acad_typelib():
    """comtypes 為 AutoCAD 產生的型別庫模組（連線時自動產生）。"""
    for mod_name, mod in list(sys.modules.items()):
        if mod_name.startswith("comtypes.gen.") and hasattr(mod, "IAcadCircle"):
            return mod
    return None


def as_entity(ent, name):
    """把 space.Item() 拿到的 IAcadEntity 明確轉成 IAcadCircle、IAcadLine 等介面。"""
    return cast(ent, INTERFACES[name])


def cast(obj, iface_name):
    """明確轉成指定的 AutoCAD 介面；不行就改用動態呼叫。"""
    lib = acad_typelib()
    iface = getattr(lib, iface_name, None) if lib else None
    if iface is not None:
        try:
            return obj.QueryInterface(iface)
        except Exception as e:
            if VERBOSE:
                print(f"QueryInterface({iface.__name__}) 失敗: {e!r}，改用動態呼叫")
    elif VERBOSE:
        print("找不到 AutoCAD 型別庫模組，改用動態呼叫")
    return comtypes.client.dynamic.Dispatch(obj.QueryInterface(IDispatch))


def ensure_layer(doc):
    try:
        doc.Layers.Item(DIM_LAYER)
    except COMError:
        layer = doc.Layers.Add(DIM_LAYER)
        try:
            layer.Color = 3  # 綠色
        except Exception:
            pass


# ---------------- 各種物件的標註 ----------------

def dim_segment(space, p1, p2):
    """兩點之間的對齊標註，標註線往線段左側偏移 LINE_OFFSET。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    nx, ny = -dy / length, dx / length
    tx = (p1[0] + p2[0]) / 2 + nx * LINE_OFFSET
    ty = (p1[1] + p2[1]) / 2 + ny * LINE_OFFSET
    return [space.AddDimAligned(apt(p1[0], p1[1]), apt(p2[0], p2[1]), apt(tx, ty))]


def dim_line(space, ent):
    return dim_segment(space, ent.StartPoint, ent.EndPoint)


def dim_circle(space, ent):
    cx, cy = ent.Center[0], ent.Center[1]
    r = ent.Radius
    a = math.radians(45)
    p1 = apt(cx + r * math.cos(a), cy + r * math.sin(a))
    p2 = apt(cx - r * math.cos(a), cy - r * math.sin(a))
    return [space.AddDimDiameter(p1, p2, CIRCLE_LEADER)]


def dim_arc(space, ent):
    cx, cy = ent.Center[0], ent.Center[1]
    r = ent.Radius
    start, end = ent.StartAngle, ent.EndAngle
    mid = start + ((end - start) % (2 * math.pi)) / 2
    chord = apt(cx + r * math.cos(mid), cy + r * math.sin(mid))
    return [space.AddDimRadial(apt(cx, cy), chord, CIRCLE_LEADER)]


def dim_point(space, ent):
    x, y = ent.Coordinates[0], ent.Coordinates[1]
    dim_x = space.AddDimOrdinate(apt(x, y), apt(x, y + POINT_LEADER), True)   # X 座標
    dim_y = space.AddDimOrdinate(apt(x, y), apt(x + POINT_LEADER, y), False)  # Y 座標
    return [dim_x, dim_y]


def dim_lwpolyline(space, ent):
    c = list(ent.Coordinates)
    pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
    n = len(pts)
    seg_count = n if ent.Closed else n - 1
    dims = []
    for i in range(seg_count):
        if abs(ent.GetBulge(i)) > 1e-9:
            continue  # 弧形段先略過
        dims += dim_segment(space, pts[i], pts[(i + 1) % n])
    return dims


HANDLERS = {
    "AcDbLine": dim_line,
    "AcDbCircle": dim_circle,
    "AcDbArc": dim_arc,
    "AcDbPoint": dim_point,
    "AcDbPolyline": dim_lwpolyline,
}


# ---------------- 主迴圈 ----------------

def active_space(doc):
    """目前作用中的空間：模型空間或圖紙空間（配置）。"""
    if doc.ActiveSpace == 1:
        return cast(doc.ModelSpace, "IAcadModelSpace")
    return cast(doc.PaperSpace, "IAcadPaperSpace")


def process_range(space, start, end, done):
    for i in range(start, end):
        ent = retry(space.Item, i)
        handle, name, layer = retry(lambda: (ent.Handle, ent.ObjectName, ent.Layer))
        if handle in done:
            continue
        done.add(handle)
        if name not in HANDLERS or layer == DIM_LAYER:
            if VERBOSE and "Dimension" not in name:
                print(f"略過 {name}（不支援的物件類型）")
            continue
        try:
            ent = as_entity(ent, name)
            dims = retry(HANDLERS[name], space, ent)
            for d in dims:
                d.Layer = DIM_LAYER
            print(f"已標註 {name} (handle {handle})")
        except Exception as e:
            print(f"標註失敗 {name} (handle {handle}): {e!r}")
            if VERBOSE:
                traceback.print_exc()


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="AutoCAD 自動標註")
    parser.add_argument("--all", action="store_true", help="啟動時也標註圖上既有物件")
    parser.add_argument("--now", action="store_true", help="不等指令結束，畫一段就標一段")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示除錯訊息")
    args = parser.parse_args()
    VERBOSE = args.verbose
    wait_cmd = WAIT_FOR_COMMAND_END and not args.now

    try:
        # 不用 dynamic：全部走 AutoCAD 型別庫的早期繫結，不靠名稱查詢
        # （中文版等非英文 AutoCAD 用名稱查詢時會找不到 AddDimDiameter 等方法）
        acad = typed(comtypes.client.GetActiveObject("AutoCAD.Application"))
    except (OSError, COMError):
        print("找不到執行中的 AutoCAD。請先開啟 AutoCAD（LT 版不支援），再執行本程式。")
        return
    print(f"已連線 AutoCAD {acad.Version}，開始監看新物件… (Ctrl+C 停止)")

    counts = {}   # (圖面, 空間) -> 上次看到的物件數量
    done = {}     # 圖面 -> 已處理過的 handle
    waiting = False

    while True:
        try:
            doc = cast(acad.ActiveDocument, "IAcadDocument")
            doc_key = doc.Name

            if wait_cmd and doc.GetVariable("CMDACTIVE"):
                if VERBOSE and not waiting:
                    print("指令執行中，等指令結束後再標註…")
                waiting = True
            else:
                waiting = False
                space = active_space(doc)
                key = (doc_key, space.Name)
                count = space.Count

                if doc_key not in done:
                    ensure_layer(doc)
                    done[doc_key] = set()
                if key not in counts:
                    counts[key] = 0 if args.all else count
                    print(f"監看圖面: {doc_key} / {space.Name}（目前 {count} 個物件）")

                last = counts[key]
                if count > last:
                    if VERBOSE:
                        print(f"偵測到 {count - last} 個新物件")
                    process_range(space, last, count, done[doc_key])
                    # 我們新增的標註會在下一輪被掃到，但因類型不符會被自動略過
                    counts[key] = count
                elif count < last:
                    # 有物件被刪除（或 UNDO），重新設定基準
                    counts[key] = count

        except KeyboardInterrupt:
            print("停止自動標註")
            break
        except Exception as e:
            if is_busy(e):
                if VERBOSE:
                    print("AutoCAD 忙碌中，稍後重試…")
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
