"""
AutoCAD 自動標註 (Python + COM)

執行後持續監看 AutoCAD 目前圖面的模型空間，
只要你畫出新的「點 / 線 / 圓 / 弧 / 聚合線」，就自動加上標註：

    點 (POINT)          -> X、Y 座標標註 (ordinate)
    線 (LINE)           -> 對齊長度標註 (aligned)
    圓 (CIRCLE)         -> 直徑標註 (diameter)
    弧 (ARC)            -> 半徑標註 (radial)
    聚合線 (LWPOLYLINE) -> 每一段直線段的長度標註

需求: Windows + AutoCAD (完整版，LT 不支援 COM) + Python
      pip install pywin32

用法:
    1. 先開啟 AutoCAD 和要畫的圖檔
    2. python autocad_auto_dim.py          # 只標註「之後」新畫的物件
       python autocad_auto_dim.py --all    # 先把圖上既有物件也全部標註
    3. 在 AutoCAD 裡正常畫圖，按 Ctrl+C 停止腳本
"""

import argparse
import math
import time

import pythoncom
import win32com.client
from pywintypes import com_error

# ---------------- 可調整的設定 ----------------
DIM_LAYER = "AUTO_DIM"        # 標註放在這個圖層
LINE_OFFSET = 5.0             # 長度標註離線的距離（圖面單位）
POINT_LEADER = 10.0           # 點座標標註的引線長度
CIRCLE_LEADER = 5.0           # 圓/弧標註的引線長度
POLL_INTERVAL = 0.3           # 每幾秒檢查一次新物件
WAIT_FOR_COMMAND_END = True   # True: 等指令(如 LINE)結束後才標註，避免干擾繪圖
# ----------------------------------------------

# AutoCAD 忙碌時 COM 會回傳這些錯誤碼，稍後重試即可
BUSY_ERRORS = (-2147418111, -2147417846)  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER


def vpt(x, y, z=0.0):
    """把座標轉成 AutoCAD COM 需要的 VARIANT double 陣列。"""
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z))
    )


def is_busy(err):
    return isinstance(err, com_error) and err.hresult in BUSY_ERRORS


def retry(fn, *args, tries=50, delay=0.1):
    """AutoCAD 忙碌時自動重試。"""
    for _ in range(tries):
        try:
            return fn(*args)
        except com_error as e:
            if is_busy(e):
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("AutoCAD 一直處於忙碌狀態")


def ensure_layer(doc):
    try:
        doc.Layers.Item(DIM_LAYER)
    except com_error:
        layer = doc.Layers.Add(DIM_LAYER)
        try:
            layer.color = 3  # 綠色
        except com_error:
            pass


# ---------------- 各種物件的標註 ----------------

def dim_segment(ms, p1, p2):
    """兩點之間的對齊標註，標註線往線段左側偏移 LINE_OFFSET。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return []
    nx, ny = -dy / length, dx / length
    tx = (p1[0] + p2[0]) / 2 + nx * LINE_OFFSET
    ty = (p1[1] + p2[1]) / 2 + ny * LINE_OFFSET
    return [ms.AddDimAligned(vpt(p1[0], p1[1]), vpt(p2[0], p2[1]), vpt(tx, ty))]


def dim_line(ms, ent):
    return dim_segment(ms, ent.StartPoint, ent.EndPoint)


def dim_circle(ms, ent):
    cx, cy, _ = ent.Center
    r = ent.Radius
    a = math.radians(45)
    p1 = vpt(cx + r * math.cos(a), cy + r * math.sin(a))
    p2 = vpt(cx - r * math.cos(a), cy - r * math.sin(a))
    return [ms.AddDimDiameter(p1, p2, CIRCLE_LEADER)]


def dim_arc(ms, ent):
    cx, cy, _ = ent.Center
    r = ent.Radius
    start, end = ent.StartAngle, ent.EndAngle
    mid = start + ((end - start) % (2 * math.pi)) / 2
    chord = vpt(cx + r * math.cos(mid), cy + r * math.sin(mid))
    return [ms.AddDimRadial(vpt(cx, cy), chord, CIRCLE_LEADER)]


def dim_point(ms, ent):
    x, y, _ = ent.Coordinates
    dim_x = ms.AddDimOrdinate(vpt(x, y), vpt(x, y + POINT_LEADER), True)   # X 座標
    dim_y = ms.AddDimOrdinate(vpt(x, y), vpt(x + POINT_LEADER, y), False)  # Y 座標
    return [dim_x, dim_y]


def dim_lwpolyline(ms, ent):
    c = list(ent.Coordinates)
    pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
    n = len(pts)
    seg_count = n if ent.Closed else n - 1
    dims = []
    for i in range(seg_count):
        if abs(ent.GetBulge(i)) > 1e-9:
            continue  # 弧形段先略過
        dims += dim_segment(ms, pts[i], pts[(i + 1) % n])
    return dims


HANDLERS = {
    "AcDbLine": dim_line,
    "AcDbCircle": dim_circle,
    "AcDbArc": dim_arc,
    "AcDbPoint": dim_point,
    "AcDbPolyline": dim_lwpolyline,
}


def annotate(ms, ent):
    """幫一個物件加標註，回傳是否有處理。"""
    handler = HANDLERS.get(ent.ObjectName)
    if handler is None:
        return False
    if ent.Layer == DIM_LAYER:
        return False
    for d in handler(ms, ent):
        d.Layer = DIM_LAYER
    return True


# ---------------- 主迴圈 ----------------

def process_range(doc, start, end, done):
    ms = doc.ModelSpace
    for i in range(start, end):
        ent = retry(ms.Item, i)
        handle = ent.Handle
        if handle in done:
            continue
        done.add(handle)
        try:
            if retry(annotate, ms, ent):
                print(f"已標註 {ent.ObjectName} (handle {handle})")
        except com_error as e:
            print(f"標註失敗 {ent.ObjectName} (handle {handle}): {e}")


def main():
    parser = argparse.ArgumentParser(description="AutoCAD 自動標註")
    parser.add_argument("--all", action="store_true", help="啟動時也標註圖上既有物件")
    args = parser.parse_args()

    acad = win32com.client.GetActiveObject("AutoCAD.Application")
    print("已連線 AutoCAD，開始監看新物件… (Ctrl+C 停止)")

    counts = {}   # 每份圖面上次看到的物件數量
    done = {}     # 每份圖面已處理過的 handle

    while True:
        try:
            doc = acad.ActiveDocument
            key = doc.FullName or doc.Name

            if WAIT_FOR_COMMAND_END and doc.GetVariable("CMDACTIVE"):
                time.sleep(POLL_INTERVAL)
                continue

            count = doc.ModelSpace.Count

            if key not in counts:
                ensure_layer(doc)
                done[key] = set()
                counts[key] = 0 if args.all else count
                print(f"監看圖面: {doc.Name}")

            last = counts[key]
            if count > last:
                process_range(doc, last, count, done[key])
                # 我們新增的標註會在下一輪被掃到，但因類型不符會被自動略過
                counts[key] = count
            elif count < last:
                # 有物件被刪除（或 UNDO），重新設定基準
                counts[key] = count

        except com_error as e:
            if not is_busy(e):
                print(f"COM 錯誤: {e}")

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("停止自動標註")
            break


if __name__ == "__main__":
    main()
