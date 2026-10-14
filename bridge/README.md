# 在 AutoCAD 外執行論文的預訓練模型

`sketchconcept_bridge.py` 用作者的程式碼 ([yyuezhi/SketchConcept](https://github.com/yyuezhi/SketchConcept))
和預訓練權重，在 CPU 上解讀 AutoCAD 匯出的草圖，結果再讀回 AutoCAD 顯示。

```
AutoCAD  ACEXPORT  →  圖名_sketch.json     (L0 圖元 + 推論出的約束)
Python   sketchconcept_bridge.py            (作者的偵測/生成網路 + 匈牙利匹配)
AutoCAD  ACIMPORT  ←  圖名_concepts.lsp    (每個圖元屬於哪個概念實例、概念庫編號)
```
`ACMODEL` 會一次做完這三步（在 Windows 上透過 WScript.Shell 呼叫 Python 並等待）。

## 安裝（Windows，一次即可）

1. 安裝 **Python 3.11**（python.org，勾選 "Add to PATH"）。
2. 下載作者的程式碼（含 SketchGraphs 子模組）：
   ```
   cd C:\SketchConcept
   git clone --recursive https://github.com/yyuezhi/SketchConcept.git
   ```
3. 安裝套件（CPU 即可，不需要 GPU）：
   ```
   pip install -r C:\SketchConcept\bridge\requirements.txt
   pip install -e C:\SketchConcept\SketchConcept\SketchGraphs
   ```
4. 從作者 README 的 Google Drive 連結下載**預訓練模型**，放到
   `C:\SketchConcept\SketchConcept\experiment_directory\sample_pretrain_exp\result\network_param\`
   （作者的指令用 `-c 180`，檔名應為 `model_180.pth`；若不同，用 `--epoch` 指定數字）。
5. 把這個 `bridge` 資料夾複製到 `C:\SketchConcept\bridge\`。

路徑若不同，在 AutoCAD 載入 `autodim.lsp` 前（或之後）設定：
```lisp
(setq *ac:python* "C:\\Users\\你\\AppData\\Local\\Programs\\Python\\Python311\\python.exe")
(setq *ac:bridge* "C:\\SketchConcept\\bridge\\sketchconcept_bridge.py")
(setq *ac:repo*   "C:\\SketchConcept\\SketchConcept")
```

## 手動執行（不透過 AutoCAD 呼叫）

```
python sketchconcept_bridge.py --repo C:\SketchConcept\SketchConcept 圖名_sketch.json 圖名_concepts.lsp
```
`--random-init` 用未訓練的權重跑（只用來確認環境裝好了，結果沒有意義）。

## 注意

- 模型一次最多處理 16 個圖元、50 個元素（圖元 + 約束），這是作者的設定。大圖會依相連關係切塊，小的圖形會合併在同一塊。
- 模型是用 Onshape 的草圖（SketchGraphs）訓練的，那些草圖的約束是設計者加的；AutoCAD 圖沒有約束，這裡用的是幾何推論出的約束，所以結果可能和論文中的表現有落差。
- 模型輸出的是**概念庫編號**（1000 個學到的概念之一），沒有名稱。`ACIMPORT` 會把它和規則版的概念（矩形、長槽孔…）比對，標出一致的部分。
