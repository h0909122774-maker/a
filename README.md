# AutoCAD 自動標註 (AutoLISP)

`autodim.lsp`（UTF-8，AutoCAD 2021+）／`autodim_big5.lsp`（Big5，舊版 AutoCAD）內容相同。
用 `APPLOAD` 載入，或把檔案拖進 AutoCAD 視窗。

## 指令

| 指令 | 功能 |
|---|---|
| `AUTODIM` | 逐段標註：直線段 → 對齊尺寸（自動放外側）、弧 → 半徑、圓 → 直徑，可加總尺寸 |
| `ADIMTOTAL` | 只加整體水平 + 垂直總尺寸 |
| `ACLIST` | **設計意圖解讀**：把草圖分解成設計概念，依概念上色 (REDRAW 清除)，輸出 DSL 程式與模組化程度 |
| `ACDIM` | **依設計概念標註**：每個概念只標它的參數，重複的實例合併 |
| `ACCOMPLETE` | **以概念自動補全**：補上缺一邊的矩形、缺一端的長槽孔（畫在紅色圖層 `AC_COMPLETION`） |

## 與論文的對應

Yuezhi Yang, Hao Pan, *Discovering Design Concepts for CAD Sketches*, NeurIPS 2022
（[論文](https://arxiv.org/abs/2210.14451)、[程式碼](https://github.com/yyuezhi/SketchConcept)）

| 論文 | 本程式 |
|---|---|
| L0 圖元 Line / Point / Circle / Arc | 由 LINE、LWPOLYLINE（拆成線段/弧段）、ARC、CIRCLE、POINT 取出 |
| L0 約束 (SketchGraphs 型別) | 由幾何推論：Coincident、Horizontal、Vertical、Parallel、Perpendicular、Tangent、Equal、Concentric |
| L1 概念 `T -> λ(args).{t_i, R_T}` | 明確寫出的概念庫，在約束圖上做子圖比對（見下表） |
| 草圖分解 `S -> {t_i}`, `R_S` | 每個圖元只屬於一個概念；跨概念的約束列為 `R_S` |
| Modularity 指標 (Sec. 6) | 完全落在單一概念內的約束比例 |
| 設計意圖解讀 (Sec. 6.1, Fig. 3) | `ACLIST`：依概念上色 + 輸出 Fig. 1(b) 格式的程式，另存成 `<圖名>_concepts.txt` |
| 概念是「參數化結構」(Sec. 3) | `ACDIM`：依概念參數標註 |
| 自動補全 (Sec. 6.2, Fig. 5) | `ACCOMPLETE`：以概念為單位補全 |

**不同之處**：論文用 transformer + 向量量化從 SketchGraphs（約 100 萬張草圖）**學出**概念庫；
預訓練模型需要 PyTorch/CUDA，無法在 AutoCAD 內執行。本程式的概念庫是**人工寫定**的，
挑選的概念仿照論文中學到的結果（矩形 Fig. 4、line-arcs/長槽孔 Fig. 1、對齊平行線 Fig. 3）。

### 概念庫

| 概念 | 結構 (成員 + 內部約束) | `ACDIM` 標註的參數 |
|---|---|---|
| `T_Rect` | 4 Line，相鄰 Coincident+Perpendicular，對邊 Parallel | 長、寬 |
| `T_Square` | T_Rect 且鄰邊 Equal | `□邊長` |
| `T_Slot` | 2 Line (Parallel, Equal) + 2 Arc，各弧與兩線 Tangent | 圓心距、槽寬 |
| `T_NGon` | 閉合直線環，等長且頂點共圓 | `n×邊長` |
| `T_Profile` | 其他閉合的線/弧環 | 每段直線（放外側），弧併入半徑群組 |
| `T_ParallelPair` | 2 Line 平行、兩端對齊 | 長度、間距 |
| `T_Fillet` | Arc 與直線 Tangent（直線經由引數連到外部） | 併入半徑群組 `n×R` |
| `T_Concentric` | 同心圓 | 各直徑 + 定位 |
| `T_HolePattern` | 等徑圓 | `n×Ø`；等距排成一直線時加 `(n-1)×節距`；定位 |

跨概念參照：孔/同心圓位於水平矩形內時，從最近的矩形角標出水平與垂直定位尺寸。

## 測試

沒有 AutoCAD 也能測試邏輯：`tests/minilisp.py` 是一個迷你 AutoLISP 直譯器，並模擬 AutoCAD ActiveX 物件。

```
python3 tests/test_autodim.py        # 35 個情境：概念偵測、標註、補全、DSL 輸出
python3 tests/parencheck.py autodim.lsp
```

模擬環境不等於真正的 AutoCAD，請先在測試圖上試用。

## 限制
- 只處理 2D、世界座標。
- 約束是由幾何推論的（AutoCAD 一般圖面沒有存約束），容差為圖面大小的 1e-7。
- 概念庫是固定的，不會從你的圖學習新概念。
