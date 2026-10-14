;;; ============================================================
;;; autodim.lsp  -  AutoCAD 自動標註工具
;;;
;;; 指令:
;;;   AUTODIM   選取物件後自動標註
;;;             - 直線 (LINE)         : 對齊尺寸
;;;             - 聚合線 (LWPOLYLINE) : 每一段直線加對齊尺寸、弧段加半徑尺寸
;;;                                     尺寸自動放在圖形「外側」
;;;             - 圓弧 (ARC)          : 半徑尺寸
;;;             - 圓 (CIRCLE)         : 直徑尺寸
;;;             - 可選擇加上整體的水平 / 垂直總尺寸
;;;   ADIMTOTAL 只加整體總尺寸 (水平 + 垂直)
;;;
;;; 使用的是「目前的標註型式」與「目前圖層」。
;;; 載入方式: APPLOAD 選取本檔，或把本檔拖進 AutoCAD 視窗。
;;; ============================================================

(vl-load-com)

(setq *ad:off* nil) ; 記住上次使用的偏移距離

;;; ---------- 小工具 ----------

(defun ad:z (p) (if (caddr p) (caddr p) 0.0))

(defun ad:pt (p) (vlax-3d-point (list (car p) (cadr p) (ad:z p))))

(defun ad:mid (a b) (mapcar '(lambda (x y) (/ (+ x y) 2.0)) a b))

(defun ad:space (doc)
  (if (and (= 0 (getvar "TILEMODE")) (= 1 (getvar "CVPORT")))
    (vla-get-PaperSpace doc)
    (vla-get-ModelSpace doc)
  )
)

;; 預設偏移距離 = 4 倍文字高度 x 標註比例
(defun ad:defoff (/ sc)
  (setq sc (getvar "DIMSCALE"))
  (if (<= sc 0.0) (setq sc 1.0))
  (* 4.0 (getvar "DIMTXT") sc)
)

;; 安全呼叫: 成功回傳建立的物件並累計數量，失敗回傳 nil
(defun ad:try (fn args / r)
  (setq r (vl-catch-all-apply fn args))
  (if (vl-catch-all-error-p r)
    (progn (princ (strcat "\n  略過: " (vl-catch-all-error-message r))) nil)
    (progn (setq *ad:cnt* (1+ *ad:cnt*)) r)
  )
)

;; 改寫尺寸文字，"<>" 代表量測值 (含 R / %%c 前綴)
(defun ad:override (o s)
  (if o (vl-catch-all-apply 'vla-put-TextOverride (list o s)))
  o
)

;; 以鞋帶公式判斷頂點方向: 逆時針 -> 外側在右邊 (-1)，順時針 -> 左邊 (1)
(defun ad:side (pts n / a i p1 p2)
  (setq a 0.0 i 0)
  (while (< i n)
    (setq p1 (nth i pts) p2 (nth (1+ i) pts)
          a  (+ a (- (* (car p1) (cadr p2)) (* (car p2) (cadr p1))))
          i  (1+ i))
  )
  (setq p1 (last pts) p2 (car pts)
        a  (+ a (- (* (car p1) (cadr p2)) (* (car p2) (cadr p1)))))
  (if (> a 0.0) -1 1)
)

;; 聚合線第 i 段 (凸度 b) 的圓弧: 回傳 (半徑 圓心 弧中點)
(defun ad:bulgearc (obj i p1 p2 / len r m cm dd)
  (setq len (distance p1 p2))
  (if (and (> len 1e-8) (> (abs (vla-GetBulge obj i)) 1e-8))
    (progn
      (setq r  (/ len (* 2.0 (sin (* 2.0 (atan (abs (vla-GetBulge obj i)))))))
            m  (vlax-curve-getPointAtParam obj (+ i 0.5))
            cm (ad:mid p1 p2)
            dd (distance cm m))
      (list r
            (mapcar '(lambda (a b) (+ a (* r (/ (- b a) dd)))) m cm)
            m)
    )
  )
)

;;; ---------- 各種尺寸 ----------

;; 對齊尺寸。side = 1 放在 p1->p2 的左側，-1 放在右側
(defun ad:aligned (spc p1 p2 side off / d nx ny m tp)
  (setq d (distance (list (car p1) (cadr p1)) (list (car p2) (cadr p2))))
  (if (> d 1e-8)
    (progn
      (setq nx (/ (- (cadr p1) (cadr p2)) d)   ; 左法向量 (-dy, dx)
            ny (/ (- (car p2) (car p1)) d)
            m  (ad:mid p1 p2)
            tp (list (+ (car m) (* side off nx))
                     (+ (cadr m) (* side off ny))
                     (ad:z m))
      )
      (ad:try 'vla-AddDimAligned (list spc (ad:pt p1) (ad:pt p2) (ad:pt tp)))
    )
  )
)

;; 半徑尺寸
(defun ad:radial (spc cen onarc off)
  (ad:try 'vla-AddDimRadial (list spc (ad:pt cen) (ad:pt onarc) off))
)

;;; ---------- 依物件類型處理 ----------

(defun ad:line (spc obj off / p1 p2 d nx ny side)
  (setq p1 (vlax-curve-getStartPoint obj)
        p2 (vlax-curve-getEndPoint obj)
        d  (distance p1 p2)
  )
  (if (> d 1e-8)
    (progn
      ;; 單獨直線: 尺寸放在上方; 垂直線放在左側
      (setq nx (/ (- (cadr p1) (cadr p2)) d)
            ny (/ (- (car p2) (car p1)) d)
            side (cond ((> ny 1e-8) 1)
                       ((< ny -1e-8) -1)
                       ((< nx 0) 1)
                       (T -1))
      )
      (ad:aligned spc p1 p2 side off)
    )
  )
)

;; 聚合線頂點 (含終點)
(defun ad:polypts (obj n / i pts)
  (setq i 0)
  (while (<= i n)
    (setq pts (cons (vlax-curve-getPointAtParam obj (float i)) pts) i (1+ i))
  )
  (reverse pts)
)

(defun ad:poly (spc obj off / n i pts side p1 p2 a)
  (setq n    (fix (+ 0.5 (vlax-curve-getEndParam obj)))
        pts  (ad:polypts obj n)
        side (ad:side pts n)
        i    0)
  ;; 逐段標註: 直線段加對齊尺寸，弧段加半徑尺寸
  (while (< i n)
    (setq p1 (nth i pts) p2 (nth (1+ i) pts))
    (if (< (abs (vla-GetBulge obj i)) 1e-8)
      (ad:aligned spc p1 p2 side off)
      (if (setq a (ad:bulgearc obj i p1 p2))
        (ad:radial spc (cadr a) (caddr a) off))
    )
    (setq i (1+ i))
  )
)

(defun ad:arc (spc obj off / cen m)
  (setq cen (vlax-safearray->list (vlax-variant-value (vla-get-Center obj)))
        m   (vlax-curve-getPointAtDist obj
              (/ (vlax-curve-getDistAtParam obj (vlax-curve-getEndParam obj)) 2.0))
  )
  (ad:radial spc cen m off)
)

(defun ad:circle (spc obj off / c r k p1 p2)
  (setq c  (vlax-safearray->list (vlax-variant-value (vla-get-Center obj)))
        r  (vla-get-Radius obj)
        k  (/ r (sqrt 2.0))
        p1 (list (+ (car c) k) (+ (cadr c) k) (ad:z c))
        p2 (list (- (car c) k) (- (cadr c) k) (ad:z c))
  )
  (ad:try 'vla-AddDimDiametric (list spc (ad:pt p1) (ad:pt p2) off))
)

;;; ---------- 總尺寸 ----------

(defun ad:bbox (ss / i a b mn mx)
  (setq i (sslength ss))
  (while (> i 0)
    (setq i (1- i))
    (vla-GetBoundingBox (vlax-ename->vla-object (ssname ss i)) 'a 'b)
    (setq a  (vlax-safearray->list a)
          b  (vlax-safearray->list b)
          mn (if mn (mapcar 'min mn a) a)
          mx (if mx (mapcar 'max mx b) b))
  )
  (list mn mx)
)

(defun ad:total (spc ss off / bb mn mx z)
  (setq bb (ad:bbox ss) mn (car bb) mx (cadr bb) z (ad:z mn))
  ;; 水平總尺寸: 放在下方
  (if (> (- (car mx) (car mn)) 1e-8)
    (ad:try 'vla-AddDimRotated
      (list spc
            (ad:pt (list (car mn) (cadr mn) z))
            (ad:pt (list (car mx) (cadr mn) z))
            (ad:pt (list (/ (+ (car mn) (car mx)) 2.0) (- (cadr mn) (* 2.0 off)) z))
            0.0))
  )
  ;; 垂直總尺寸: 放在右側
  (if (> (- (cadr mx) (cadr mn)) 1e-8)
    (ad:try 'vla-AddDimRotated
      (list spc
            (ad:pt (list (car mx) (cadr mn) z))
            (ad:pt (list (car mx) (cadr mx) z))
            (ad:pt (list (+ (car mx) (* 2.0 off)) (/ (+ (cadr mn) (cadr mx)) 2.0) z))
            (/ pi 2.0)))
  )
)

;;; ---------- 共用輸入 ----------

(defun ad:getoff (/ d)
  (if (not *ad:off*) (setq *ad:off* (ad:defoff)))
  (initget 6) ; 不可為 0 或負數
  (setq d (getdist (strcat "\n尺寸線偏移距離 <" (rtos *ad:off*) ">: ")))
  (if d (setq *ad:off* d))
  *ad:off*
)

;;; ---------- 指令 ----------

(defun c:AUTODIM (/ *error* doc spc ss off tot i obj typ)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (defun *error* (msg)
    (vla-EndUndoMark doc)
    (if (not (wcmatch (strcase msg T) "*cancel*,*quit*,*break*,*取消*"))
      (princ (strcat "\n錯誤: " msg)))
    (princ)
  )
  (princ "\n選取要標註的直線 / 聚合線 / 圓弧 / 圓:")
  (if (setq ss (ssget '((0 . "LINE,LWPOLYLINE,ARC,CIRCLE"))))
    (progn
      (setq off (ad:getoff))
      (initget "Y N")
      (setq tot (getkword "\n是否加上整體總尺寸? [是(Y)/否(N)] <Y>: "))
      (if (not tot) (setq tot "Y"))
      (vla-StartUndoMark doc)
      (setq spc (ad:space doc) *ad:cnt* 0 i 0)
      (while (< i (sslength ss))
        (setq obj (vlax-ename->vla-object (ssname ss i))
              typ (vla-get-ObjectName obj))
        (cond
          ((= typ "AcDbLine")     (ad:line spc obj off))
          ((= typ "AcDbPolyline") (ad:poly spc obj off))
          ((= typ "AcDbArc")      (ad:arc spc obj off))
          ((= typ "AcDbCircle")   (ad:circle spc obj off))
        )
        (setq i (1+ i))
      )
      (if (= tot "Y") (ad:total spc ss off))
      (vla-EndUndoMark doc)
      (princ (strcat "\n完成，共建立 " (itoa *ad:cnt*) " 個尺寸。(可用 U 一次復原)"))
    )
  )
  (princ)
)

(defun c:ADIMTOTAL (/ *error* doc ss off)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (defun *error* (msg)
    (vla-EndUndoMark doc)
    (if (not (wcmatch (strcase msg T) "*cancel*,*quit*,*break*,*取消*"))
      (princ (strcat "\n錯誤: " msg)))
    (princ)
  )
  (princ "\n選取要加總尺寸的物件:")
  (if (setq ss (ssget))
    (progn
      (setq off (ad:getoff))
      (vla-StartUndoMark doc)
      (setq *ad:cnt* 0)
      (ad:total (ad:space doc) ss off)
      (vla-EndUndoMark doc)
      (princ (strcat "\n完成，共建立 " (itoa *ad:cnt*) " 個尺寸。"))
    )
  )
  (princ)
)


;;; ============================================================
;;; 設計概念 (Sketch Concepts)
;;; 依據 Yuezhi Yang, Hao Pan, "Discovering Design Concepts for CAD
;;; Sketches", NeurIPS 2022   https://github.com/yyuezhi/SketchConcept
;;;
;;; 論文的 DSL (List 1):
;;;   L0 圖元   Line, Point, Circle, Arc
;;;   L0 約束   Coincident, Horizontal, Vertical, Parallel, Perpendicular,
;;;             Tangent, Equal, Concentric ...  (名稱同 SketchGraphs / 論文程式碼)
;;;   L1 概念   T -> lambda(引數).{L0 成員 t0..tk, R_T = 成員之間的約束}
;;;   草圖分解  S -> {c_i : T_i},  R_S = 跨概念的約束 (經由引數連接)
;;;   模組化程度 = 完全落在單一概念內的約束 / 全部約束 (論文 Sec.6 的指標)
;;;
;;; 論文用 transformer + 向量量化從 SketchGraphs 學出概念庫 L1。
;;; AutoCAD 裡無法執行該網路，所以本程式:
;;;   1. 從圖形建立 L0 草圖圖 (由幾何推論約束)
;;;   2. 用一組明確寫出的 L1 概念 (仿論文學到的: 矩形、長槽孔 / line-arcs、
;;;      對齊平行線、圓角...) 在約束圖上做子圖比對，把草圖分解成概念
;;;   3. 應用: 設計意圖解讀 (ACLIST)、依概念參數標註 (ACDIM)、
;;;      以概念為單位自動補全 (ACCOMPLETE)
;;; ============================================================

;;; ---------- 向量 ----------
(defun ac:2d (p) (list (car p) (cadr p)))
(defun ac:sub (a b) (list (- (car a) (car b)) (- (cadr a) (cadr b))))
(defun ac:add (a b) (list (+ (car a) (car b)) (+ (cadr a) (cadr b))))
(defun ac:scl (v s) (list (* (car v) s) (* (cadr v) s)))
(defun ac:dot (u v) (+ (* (car u) (car v)) (* (cadr u) (cadr v))))
(defun ac:cross (u v) (- (* (car u) (cadr v)) (* (cadr u) (car v))))
(defun ac:norm (v) (sqrt (ac:dot v v)))
(defun ac:unit (v / l) (setq l (ac:norm v)) (if (> l 0.0) (ac:scl v (/ 1.0 l)) v))
(defun ac:mid (a b) (ac:scl (ac:add a b) 0.5))
(defun ac:peq (p q) (< (ac:norm (ac:sub p q)) *ac:tol*))
(defun ac:eq (a b) (< (abs (- a b)) *ac:tol*))
(defun ac:zero (b) (< (abs b) 1e-8))
(defun ac:range (n / r) (repeat n (setq r (cons (setq n (1- n)) r))) r)
(defun ac:centroid (pts)
  (ac:scl (list (apply '+ (mapcar 'car pts)) (apply '+ (mapcar 'cadr pts)))
          (/ 1.0 (length pts)))
)

;;; ---------- L0 圖元 ----------
;;; 圖元 = (型別 幾何)
;;;   Line   (p1 p2)          Arc    (圓心 半徑 p1 p2 弧中點)
;;;   Circle (圓心 半徑)       Point  (p)

(defun ac:ty (i) (car (nth i *ac:P*)))
(defun ac:g (i) (cadr (nth i *ac:P*)))
(defun ac:addprim (ty g) (setq *ac:P* (append *ac:P* (list (list ty g)))))
(defun ac:addline (p1 p2)
  (if (> (distance (ac:2d p1) (ac:2d p2)) *ac:tol*)
    (ac:addprim "Line" (list (ac:2d p1) (ac:2d p2))))
)
(defun ac:ofty (ty) (vl-remove-if-not '(lambda (i) (= (ac:ty i) ty)) (ac:range (length *ac:P*))))

(defun ac:ends (i)
  (cond ((= (ac:ty i) "Line") (ac:g i))
        ((= (ac:ty i) "Arc") (list (nth 2 (ac:g i)) (nth 3 (ac:g i))))
        ((= (ac:ty i) "Point") (ac:g i)))
)
(defun ac:dir (i) (ac:unit (ac:sub (cadr (ac:g i)) (car (ac:g i)))))
(defun ac:len (i) (ac:norm (ac:sub (cadr (ac:g i)) (car (ac:g i)))))
(defun ac:touch (i p) (vl-some '(lambda (q) (ac:peq p q)) (ac:ends i)))
(defun ac:other (i p / e) (setq e (ac:ends i)) (if (ac:peq (car e) p) (cadr e) (car e)))
(defun ac:shared (i j / r)
  (foreach p (ac:ends i)
    (foreach q (ac:ends j) (if (and (not r) (ac:peq p q)) (setq r p))))
  r
)

;; 從選取集合取出 L0 圖元 (聚合線拆成線段與弧段)
(defun ac:extract (ss / i obj typ n k pts a)
  (setq i 0)
  (while (< i (sslength ss))
    (setq obj (vlax-ename->vla-object (ssname ss i))
          typ (vla-get-ObjectName obj))
    (cond
      ((= typ "AcDbLine")
       (ac:addline (vlax-curve-getStartPoint obj) (vlax-curve-getEndPoint obj)))
      ((= typ "AcDbPolyline")
       (setq n   (fix (+ 0.5 (vlax-curve-getEndParam obj)))
             pts (ad:polypts obj n)
             k   0)
       (while (< k n)
         (if (ac:zero (vla-GetBulge obj k))
           (ac:addline (nth k pts) (nth (1+ k) pts))
           (if (setq a (ad:bulgearc obj k (nth k pts) (nth (1+ k) pts)))
             (ac:addprim "Arc" (list (ac:2d (cadr a)) (car a) (ac:2d (nth k pts))
                                     (ac:2d (nth (1+ k) pts)) (ac:2d (caddr a))))))
         (setq k (1+ k))))
      ((= typ "AcDbArc")
       (ac:addprim "Arc"
         (list (ac:2d (vlax-safearray->list (vlax-variant-value (vla-get-Center obj))))
               (vla-get-Radius obj)
               (ac:2d (vlax-curve-getStartPoint obj))
               (ac:2d (vlax-curve-getEndPoint obj))
               (ac:2d (vlax-curve-getPointAtDist obj
                        (/ (vlax-curve-getDistAtParam obj (vlax-curve-getEndParam obj)) 2.0))))))
      ((= typ "AcDbCircle")
       (ac:addprim "Circle"
         (list (ac:2d (vlax-safearray->list (vlax-variant-value (vla-get-Center obj))))
               (vla-get-Radius obj))))
      ((= typ "AcDbPoint")
       (ac:addprim "Point"
         (list (ac:2d (vlax-safearray->list (vlax-variant-value (vla-get-Coordinates obj)))))))
    )
    (setq i (1+ i))
  )
)

;;; ---------- L0 約束 (由幾何推論) ----------
;;; 約束 = (型別 i) 或 (型別 i j)，i < j

(defun ac:addc (ty i j)
  (setq *ac:C* (cons (if j (list ty (min i j) (max i j)) (list ty i)) *ac:C*))
)
(defun ac:has (ty i j) (member (list ty (min i j) (max i j)) *ac:C*))
(defun ac:hasu (ty i) (member (list ty i) *ac:C*))
(defun ac:par (i j) (< (abs (ac:cross (ac:dir i) (ac:dir j))) 1e-6))

;; 直線 l 與圓弧 a 在 q 點相切: 直線方向垂直於半徑
(defun ac:tanla (l a q)
  (< (abs (ac:dot (ac:dir l) (ac:sub q (car (ac:g a))))) (* 10.0 *ac:tol*))
)
;; 兩圓弧在 q 點相切: q 在兩圓心連線上
(defun ac:tanaa (a b q / c1 c2 d)
  (setq c1 (car (ac:g a)) c2 (car (ac:g b)) d (ac:norm (ac:sub c2 c1)))
  (and (> d *ac:tol*)
       (< (abs (ac:cross (ac:sub q c1) (ac:scl (ac:sub c2 c1) (/ 1.0 d)))) (* 10.0 *ac:tol*)))
)

(defun ac:infer (/ n i j ti tj q comp same)
  (setq *ac:C* nil n (length *ac:P*) comp (ac:range n) i 0)
  ;; 第一輪: 單一圖元約束、端點重合；並依重合關係求相連元件
  (while (< i n)
    (if (= (ac:ty i) "Line")
      (progn
        (if (< (abs (cadr (ac:dir i))) 1e-6) (ac:addc "Horizontal" i nil))
        (if (< (abs (car (ac:dir i))) 1e-6) (ac:addc "Vertical" i nil))))
    (setq j (1+ i))
    (while (< j n)
      (if (ac:shared i j)
        (progn
          (ac:addc "Coincident" i j)
          (if (/= (nth i comp) (nth j comp)) (setq comp (subst (nth i comp) (nth j comp) comp)))))
      (setq j (1+ j)))
    (setq i (1+ i)))
  ;; 第二輪: 兩圖元約束。設計者通常只在相關的圖元之間加平行/等長，
  ;; 所以 Parallel、Equal(線/弧) 只在同一個相連元件內推論；
  ;; 圓的 Equal、Concentric 則不限 (孔群、同心圓)
  (setq i 0)
  (while (< i n)
    (setq ti (ac:ty i) j (1+ i))
    (while (< j n)
      (setq tj (ac:ty j) q (ac:shared i j) same (= (nth i comp) (nth j comp)))
      (cond
        ((and (= ti "Line") (= tj "Line"))
         (if (and same (ac:par i j)) (ac:addc "Parallel" i j))
         (if (and q (< (abs (ac:dot (ac:dir i) (ac:dir j))) 1e-6)) (ac:addc "Perpendicular" i j))
         (if (and same (or q (ac:par i j)) (ac:eq (ac:len i) (ac:len j))) (ac:addc "Equal" i j)))
        ((and q (= ti "Line") (= tj "Arc")) (if (ac:tanla i j q) (ac:addc "Tangent" i j)))
        ((and q (= ti "Arc") (= tj "Line")) (if (ac:tanla j i q) (ac:addc "Tangent" i j)))
        ((and q (= ti "Arc") (= tj "Arc")) (if (ac:tanaa i j q) (ac:addc "Tangent" i j)))
      )
      (if (and (member ti '("Arc" "Circle")) (member tj '("Arc" "Circle")))
        (progn
          (if (and (or same (= ti tj "Circle")) (ac:eq (cadr (ac:g i)) (cadr (ac:g j))))
            (ac:addc "Equal" i j))
          (if (ac:peq (car (ac:g i)) (car (ac:g j))) (ac:addc "Concentric" i j))))
      (setq j (1+ j)))
    (setq i (1+ i)))
  (setq *ac:C* (reverse *ac:C*))
)

;;; ---------- L1 概念庫 (子圖比對) ----------
;;; 概念實例 = (型別 成員圖元 額外資料)

(defun ac:free (i) (not (member i *ac:used*)))
(defun ac:take (ty ids ex)
  (setq *ac:K* (append *ac:K* (list (list ty ids ex)))
        *ac:used* (append ids *ac:used*))
)

;; 與直線 a 相接且垂直的自由直線
(defun ac:nbperp (a)
  (vl-remove-if-not '(lambda (j) (and (/= j a) (ac:free j) (ac:has "Perpendicular" a j))) *ac:lines*)
)

;; 矩形: a-b-c-d 依序相接且垂直，a//c、b//d
(defun ac:findrect (a / nb res)
  (setq nb (ac:nbperp a))
  (foreach b nb
    (foreach d nb
      (if (and (not res) (/= b d) (ac:has "Parallel" b d))
        (foreach c (ac:nbperp b)
          (if (and (not res) (/= c a) (/= c d)
                   (member c (ac:nbperp d)) (ac:has "Parallel" a c))
            (setq res (list a b c d)))))))
  res
)
(defun ac:rectverts (ids / a b c d)
  (setq a (nth 0 ids) b (nth 1 ids) c (nth 2 ids) d (nth 3 ids))
  (list (ac:shared d a) (ac:shared a b) (ac:shared b c) (ac:shared c d))
)

;; 長槽孔 (論文 Fig.1 的 line-arcs): 兩條平行等長直線，兩端各一圓弧與兩線相切
(defun ac:findslot (arc1 / ts res)
  (setq ts (vl-remove-if-not '(lambda (j) (and (ac:free j) (ac:has "Tangent" arc1 j))) *ac:lines*))
  (foreach l1 ts
    (foreach l2 ts
      (if (and (not res) (< l1 l2) (ac:has "Parallel" l1 l2) (ac:has "Equal" l1 l2))
        (foreach arc2 *ac:arcs*
          (if (and (not res) (/= arc2 arc1) (ac:free arc2)
                   (ac:has "Tangent" arc2 l1) (ac:has "Tangent" arc2 l2))
            (setq res (list l1 arc1 l2 arc2)))))))
  res
)

;; 沿相接的直線/圓弧走一圈；回傳 ((成員...) (頂點...))，不是閉合則 nil
(defun ac:walk (a / start p cur res pts cands ok)
  (setq start (car (ac:ends a)) p (cadr (ac:ends a))
        cur a res (list a) pts (list start) ok T)
  (while (and ok (not (ac:peq p start)))
    (setq cands (vl-remove-if-not
                  '(lambda (j) (and (not (member j res)) (ac:free j) (ac:touch j p)))
                  (append *ac:lines* *ac:arcs*)))
    (if (= (length cands) 1)
      (setq cur (car cands) res (cons cur res) pts (cons p pts) p (ac:other cur p))
      (setq ok nil))
  )
  (if (and ok (> (length res) 1)) (list (reverse res) (reverse pts)))
)

;; 正多邊形: 全為直線、等長、頂點共圓
(defun ac:regular (ids pts / c)
  (and (>= (length ids) 3)
       (vl-every '(lambda (i) (= (ac:ty i) "Line")) ids)
       (vl-every '(lambda (i) (ac:eq (ac:len i) (ac:len (car ids)))) ids)
       (progn
         (setq c (ac:centroid pts))
         (vl-every '(lambda (p) (ac:eq (distance c p) (distance c (car pts)))) pts)))
)

;; 對齊的平行線 (論文 Fig.3): 平行、不共線、兩端投影對齊
(defun ac:aligned2 (i j / u a1 a2 b1 b2)
  (setq u  (ac:dir i)
        a1 (ac:dot (car (ac:g i)) u) a2 (ac:dot (cadr (ac:g i)) u)
        b1 (ac:dot (car (ac:g j)) u) b2 (ac:dot (cadr (ac:g j)) u))
  (and (ac:eq (min a1 a2) (min b1 b2)) (ac:eq (max a1 a2) (max b1 b2))
       (> (abs (ac:cross (ac:sub (car (ac:g j)) (car (ac:g i))) u)) *ac:tol*))
)
(defun ac:findpair (a / res)
  (foreach b *ac:lines*
    (if (and (not res) (/= a b) (ac:free b) (ac:par a b) (ac:aligned2 a b))
      (setq res (list a b))))
  res
)

;; 與 c 有某種約束的自由圓
(defun ac:circlegroup (c ty)
  (cons c (vl-remove-if-not '(lambda (j) (and (/= j c) (ac:free j) (ac:has ty c j))) *ac:circles*))
)

;; 把整張草圖分解成概念實例 (每個圖元只屬於一個概念)
(defun ac:decompose (/ r w)
  (setq *ac:used* nil *ac:K* nil)
  (foreach a *ac:arcs*
    (if (and (ac:free a) (setq r (ac:findslot a))) (ac:take "Slot" r nil)))
  (foreach a *ac:lines*
    (if (and (ac:free a) (setq r (ac:findrect a)))
      (ac:take (if (ac:has "Equal" (car r) (cadr r)) "Square" "Rect") r nil)))
  (foreach a (append *ac:lines* *ac:arcs*)
    (if (and (ac:free a) (setq w (ac:walk a)))
      (ac:take (if (ac:regular (car w) (cadr w)) "NGon" "Profile") (car w) (cadr w))))
  (foreach a *ac:lines*
    (if (and (ac:free a) (setq r (ac:findpair a))) (ac:take "ParallelPair" r nil)))
  (foreach a *ac:arcs*
    (if (and (ac:free a) (vl-some '(lambda (l) (ac:has "Tangent" a l)) *ac:lines*))
      (ac:take "Fillet" (list a) nil)))
  (foreach c *ac:circles*
    (if (and (ac:free c) (> (length (setq r (ac:circlegroup c "Concentric"))) 1))
      (ac:take "Concentric" r nil)))
  (foreach c *ac:circles*
    (if (and (ac:free c) (> (length (setq r (ac:circlegroup c "Equal"))) 1))
      (ac:take "HolePattern" r nil)))
  ;; 剩下的圖元各自成為只有一個成員的概念
  (foreach i (ac:range (length *ac:P*))
    (if (ac:free i) (ac:take (ac:ty i) (list i) nil)))
)

(defun ac:build (ss / bb)
  (setq bb (ad:bbox ss)
        *ac:tol* (* 1e-7 (max 1.0 (distance (ac:2d (car bb)) (ac:2d (cadr bb)))))
        *ac:P* nil)
  (ac:extract ss)
  (setq *ac:lines* (ac:ofty "Line") *ac:arcs* (ac:ofty "Arc") *ac:circles* (ac:ofty "Circle"))
  (ac:infer)
  (ac:decompose)
)

;;; ---------- 設計意圖解讀: DSL 程式 + 模組化程度 ----------

;; 圖元 i 所在的 (概念編號 成員內編號)
(defun ac:where (i / k res)
  (setq k 0)
  (foreach c *ac:K*
    (if (and (not res) (member i (cadr c))) (setq res (list k (vl-position i (cadr c)))))
    (setq k (1+ k)))
  res
)
(defun ac:internal (con / w)
  (setq w (mapcar 'ac:where (cdr con)))
  (vl-every '(lambda (x) (= (car x) (car (car w)))) w)
)
(defun ac:join (lst sep / s)
  (setq s "")
  (foreach x lst (setq s (if (= s "") x (strcat s sep x))))
  s
)

;; 依論文 List 1 / Fig.1(b) 的格式產生程式文字
(defun ac:program (/ out seen k c ids loc args body cons-in cons-ex n-in n-all)
  (setq k 0 n-in 0 n-all 0)
  (setq out (list "// Sketch concept program (Yang & Pan, NeurIPS 2022, List 1)"
                  "// L1 composite types"))
  ;; 每種概念型別用第一個實例印出結構
  (foreach c *ac:K*
    (if (not (member (car c) seen))
      (progn
        (setq seen (cons (car c) seen) ids (cadr c) cons-in nil args nil)
        (foreach con *ac:C*
          (setq loc (mapcar '(lambda (i) (vl-position i ids)) (cdr con)))
          (cond
            ((vl-every 'numberp loc)
             (setq cons-in (cons (strcat (car con) "("
                                         (ac:join (mapcar '(lambda (x) (strcat "t" (itoa x))) loc) ",")
                                         ")") cons-in)))
            ((vl-some 'numberp loc) (setq args (cons con args)))))
        (setq body (ac:join (mapcar '(lambda (i) (strcat "t" (itoa (vl-position i ids)) ":" (ac:ty i))) ids) ", "))
        (setq out (append out
                    (list (strcat "T_" (car c) " -> lambda("
                                  (ac:join (mapcar '(lambda (x) (strcat "a" (itoa x))) (ac:range (length args))) ",")
                                  ").{" body
                                  (if cons-in (strcat ", R={" (ac:join (reverse cons-in) ", ") "}") "")
                                  "}")))))))
  ;; 草圖分解 S 與跨概念約束 R_S
  (setq out (append out (list "// Sketch decomposition"
                              (strcat "S -> {"
                                      (ac:join (mapcar '(lambda (c) (strcat "c" (itoa (vl-position c *ac:K*)) ":T_" (car c))) *ac:K*) ", ")
                                      "}"))))
  (foreach con *ac:C*
    (setq n-all (1+ n-all))
    (if (ac:internal con)
      (setq n-in (1+ n-in))
      (setq cons-ex (cons (strcat (car con) "("
                                  (ac:join (mapcar '(lambda (i / w) (setq w (ac:where i))
                                                      (strcat "c" (itoa (car w)) ".t" (itoa (cadr w))))
                                                   (cdr con)) ",")
                                  ")") cons-ex))))
  (setq out (append out
              (list (strcat "R_S={" (ac:join (reverse cons-ex) ", ") "}")
                    (strcat "// Modularity = " (rtos (if (> n-all 0) (* 100.0 (/ (float n-in) n-all)) 100.0) 2 1)
                            "% (" (itoa n-in) "/" (itoa n-all) " constraints inside concepts)"))))
  out
)

(setq *ac:names*
  '(("Rect" . "矩形") ("Square" . "正方形") ("Slot" . "長槽孔")
    ("NGon" . "正多邊形") ("Profile" . "閉合輪廓") ("ParallelPair" . "對齊平行線")
    ("Fillet" . "線-弧 (圓角)") ("Concentric" . "同心圓組") ("HolePattern" . "孔群")
    ("Line" . "單獨直線") ("Arc" . "單獨圓弧") ("Circle" . "單獨圓") ("Point" . "點")))

(defun ac:summary (/ cnt e)
  (foreach c *ac:K*
    (if (setq e (assoc (car c) cnt))
      (setq cnt (subst (cons (car c) (1+ (cdr e))) e cnt))
      (setq cnt (append cnt (list (cons (car c) 1))))))
  (princ (strcat "\n===== 設計概念: " (itoa (length *ac:P*)) " 個圖元、"
                 (itoa (length *ac:C*)) " 個約束 → " (itoa (length *ac:K*)) " 個概念實例 ====="))
  (foreach e cnt
    (princ (strcat "\n  " (cdr (assoc (car e) *ac:names*)) " (T_" (car e) ") × " (itoa (cdr e)))))
)

;;; ---------- 依概念上色 (grdraw 暫時顯示，REDRAW 清除) ----------

(defun ac:angmod (a) (while (< a 0.0) (setq a (+ a (* 2 pi)))) (while (>= a (* 2 pi)) (setq a (- a (* 2 pi)))) a)
(defun ac:arcpts (g n / c r a1 span st k pts)
  (setq c (car g) r (cadr g)
        a1 (angle c (nth 2 g))
        span (ac:angmod (- (angle c (nth 3 g)) a1)))
  (if (< (ac:angmod (- (angle c (nth 4 g)) a1)) span)
    (setq st (/ span n))
    (setq st (- (/ (- (* 2 pi) span) n))))
  (setq k 0)
  (while (<= k n) (setq pts (cons (polar c (+ a1 (* k st)) r) pts) k (1+ k)))
  pts
)
(defun ac:grpoly (pts col)
  (while (cdr pts) (grdraw (car pts) (cadr pts) col 1) (setq pts (cdr pts)))
)
(defun ac:drawprim (i col / g d)
  (setq g (ac:g i))
  (cond
    ((= (ac:ty i) "Line") (grdraw (car g) (cadr g) col 1))
    ((= (ac:ty i) "Arc") (ac:grpoly (ac:arcpts g 16) col))
    ((= (ac:ty i) "Circle")
     (ac:grpoly (ac:arcpts (list (car g) (cadr g) (polar (car g) 0.0 (cadr g))
                                 (polar (car g) (- (* 2 pi) 1e-9) (cadr g)) (polar (car g) pi (cadr g))) 32) col))
    ((= (ac:ty i) "Point")
     (setq d (* 0.01 (getvar "VIEWSIZE")) g (car g))
     (grdraw (ac:sub g (list d d)) (ac:add g (list d d)) col)
     (grdraw (ac:sub g (list d (- d))) (ac:add g (list d (- d))) col)))
)
(defun ac:drawall (/ k)
  (setq k 0)
  (foreach c *ac:K*
    (foreach i (cadr c) (ac:drawprim i (1+ (rem k 6))))
    (setq k (1+ k)))
)

;;; ---------- 依概念參數標註 ----------

;; 讓尺寸放在遠離 ref 的那一側
(defun ac:away (p1 p2 ref / m)
  (setq m (ac:mid p1 p2))
  (if (>= (ac:dot (list (- (cadr p1) (cadr p2)) (- (car p2) (car p1))) (ac:sub m ref)) 0.0) 1 -1)
)

(defun ac:diam (spc c r ang off / v)
  (setq v (list (* r (cos ang)) (* r (sin ang))))
  (ad:try 'vla-AddDimDiametric (list spc (ad:pt (ac:add c v)) (ad:pt (ac:sub c v)) off))
)

;; 孔是否等距排成一直線: 是則回傳排序後的圓心
(defun ac:pattern (cs / best a b d u srt ok gap i n)
  (setq best 0.0 n (length cs))
  (foreach p cs
    (foreach q cs
      (if (> (setq d (distance p q)) best) (setq best d a p b q))))
  (if (and (> n 1) (> best *ac:tol*))
    (progn
      (setq u   (ac:scl (ac:sub b a) (/ 1.0 best))
            srt (vl-sort (mapcar '(lambda (p) (list (ac:dot (ac:sub p a) u) p)) cs)
                         '(lambda (x y) (< (car x) (car y))))
            ok  (= (length srt) n)
            gap (/ best (1- n))
            i   0)
      (foreach x srt
        (if (> (abs (ac:cross (ac:sub (cadr x) a) u)) (* 10.0 *ac:tol*)) (setq ok nil)))
      (while (and ok (< i (1- n)))
        (if (not (< (abs (- (- (car (nth (1+ i) srt)) (car (nth i srt))) gap)) (* 10.0 *ac:tol*)))
          (setq ok nil))
        (setq i (1+ i)))
      (if ok (mapcar 'cadr srt))
    )
  )
)

;; 跨概念參照 (R_S): 圓心位於某個水平/垂直矩形內時，從最近的角標定位尺寸
(defun ac:locate (spc p / best v x0 x1 y0 y1 cx cy)
  (foreach c *ac:K*
    (if (and (not best) (member (car c) '("Rect" "Square"))
             (or (ac:hasu "Horizontal" (car (cadr c))) (ac:hasu "Vertical" (car (cadr c)))))
      (progn
        (setq v  (ac:rectverts (cadr c))
              x0 (apply 'min (mapcar 'car v)) x1 (apply 'max (mapcar 'car v))
              y0 (apply 'min (mapcar 'cadr v)) y1 (apply 'max (mapcar 'cadr v)))
        (if (and (< x0 (car p) x1) (< y0 (cadr p) y1)) (setq best (list x0 x1 y0 y1))))))
  (if best
    (progn
      (setq x0 (nth 0 best) x1 (nth 1 best) y0 (nth 2 best) y1 (nth 3 best)
            cx (if (< (- (car p) x0) (- x1 (car p))) x0 x1)
            cy (if (< (- (cadr p) y0) (- y1 (cadr p))) y0 y1))
      (ad:try 'vla-AddDimRotated
        (list spc (ad:pt (list cx cy)) (ad:pt p) (ad:pt (ac:mid (list cx cy) p)) 0.0))
      (ad:try 'vla-AddDimRotated
        (list spc (ad:pt (list cx cy)) (ad:pt p) (ad:pt (ac:mid (list cx cy) p)) (/ pi 2.0)))))
)

;; 單獨直線: 上方 / 左側
(defun ac:dimline (spc p1 p2 off / d nx ny)
  (setq d (distance p1 p2) nx (/ (- (cadr p1) (cadr p2)) d) ny (/ (- (car p2) (car p1)) d))
  (ad:aligned spc p1 p2 (cond ((> ny 1e-8) 1) ((< ny -1e-8) -1) ((< nx 0) 1) (T -1)) off)
)

(defun ac:dimconcept (spc c off / ty ids ex v cen g1 g2 r nv l1 l2 ft u n k pts side)
  (setq ty (car c) ids (cadr c) ex (caddr c))
  (cond
    ((= ty "Rect")
     (setq v (ac:rectverts ids) cen (ac:centroid v))
     (ad:aligned spc (nth 0 v) (nth 1 v) (ac:away (nth 0 v) (nth 1 v) cen) off)
     (ad:aligned spc (nth 1 v) (nth 2 v) (ac:away (nth 1 v) (nth 2 v) cen) off))
    ((= ty "Square")
     (setq v (ac:rectverts ids) cen (ac:centroid v))
     (ad:override (ad:aligned spc (nth 0 v) (nth 1 v) (ac:away (nth 0 v) (nth 1 v) cen) off) "□<>"))
    ((= ty "Slot")
     ;; 參數: 兩圓心距離、槽寬 (=2R)
     (setq g1 (ac:g (nth 1 ids)) g2 (ac:g (nth 3 ids)) r (cadr g1)
           cen (ac:mid (car g1) (car g2))
           nv  (ac:scl (list (- (cadr (ac:dir (nth 0 ids)))) (car (ac:dir (nth 0 ids)))) r))
     (ad:aligned spc (car g1) (car g2) 1 (+ off r))
     (ad:aligned spc (ac:add (car g1) nv) (ac:sub (car g1) nv)
                 (ac:away (ac:add (car g1) nv) (ac:sub (car g1) nv) cen) (+ off r)))
    ((member ty '("NGon" "Profile"))
     (setq pts ex n (length pts) side (ad:side (append pts (list (car pts))) n) k 0)
     (if (= ty "NGon")
       (ad:override (ad:aligned spc (nth 0 pts) (nth 1 pts) side off) (strcat (itoa n) "×<>"))
       (while (< k n)
         (if (= (ac:ty (nth k ids)) "Line")
           (ad:aligned spc (nth k pts) (nth (rem (1+ k) n) pts) side off)
           (setq *ac:pool* (cons (ac:g (nth k ids)) *ac:pool*)))
         (setq k (1+ k)))))
    ((= ty "ParallelPair")
     ;; 參數: 長度、間距
     (setq l1 (ac:g (nth 0 ids)) l2 (ac:g (nth 1 ids))
           cen (ac:centroid (append l1 l2))
           u (ac:dir (nth 1 ids))
           ft (ac:add (car l2) (ac:scl u (ac:dot (ac:sub (car l1) (car l2)) u))))
     (ad:aligned spc (car l1) (cadr l1) (ac:away (car l1) (cadr l1) cen) off)
     (ad:aligned spc (car l1) ft (ac:away (car l1) ft cen) off))
    ((= ty "Line") (ac:dimline spc (car (ac:g (car ids))) (cadr (ac:g (car ids))) off))
    ((member ty '("Fillet" "Arc")) (setq *ac:pool* (cons (ac:g (car ids)) *ac:pool*)))
    ((= ty "Concentric")
     (setq k 0)
     (foreach i ids
       (ac:diam spc (car (ac:g i)) (cadr (ac:g i)) (+ (/ pi 4.0) (* k (/ pi 9.0))) off)
       (setq k (1+ k)))
     (ac:locate spc (car (ac:g (car ids)))))
    ((= ty "HolePattern")
     ;; 參數: 孔徑 (n×)、節距 ((n-1)×)、第一孔位置
     (setq g1 (ac:g (car ids)) n (length ids))
     (ad:override (ac:diam spc (car g1) (cadr g1) (/ pi 4.0) off) (strcat (itoa n) "×<>"))
     (if (setq pts (ac:pattern (mapcar '(lambda (i) (car (ac:g i))) ids)))
       (progn
         (setq v (ad:aligned spc (car pts) (cadr pts) 1 (+ off (cadr g1))))
         (if (> n 2) (ad:override v (strcat (itoa (1- n)) "×<>")))
         (ac:locate spc (car pts)))
       (ac:locate spc (car g1))))
    ((= ty "Circle")
     (setq g1 (ac:g (car ids)))
     (ac:diam spc (car g1) (cadr g1) (/ pi 4.0) off)
     (ac:locate spc (car g1)))
  )
)

;; 相同半徑的圓弧合併成一個 "n×R"
(defun ac:dimpool (spc off / groups g o)
  (foreach a (reverse *ac:pool*)
    (if (setq g (vl-some '(lambda (gr) (if (ac:eq (cadr a) (cadr (car gr))) gr)) groups))
      (setq groups (subst (append g (list a)) g groups))
      (setq groups (append groups (list (list a))))))
  (foreach g groups
    (setq o (ad:radial spc (car (car g)) (nth 4 (car g)) off))
    (if (> (length g) 1) (ad:override o (strcat (itoa (length g)) "×<>"))))
)

;;; ---------- 以概念自動補全 (論文 Sec.6.2) ----------

;; p 只被一個圖元用到 (懸空端點)
(defun ac:dangling (p)
  (= 1 (length (vl-remove-if-not '(lambda (i) (ac:touch i p)) (ac:range (length *ac:P*)))))
)

(defun ac:complete (spc lay / n ea ec e1 e2 cen u o q1 q2)
  (setq n 0)
  ;; 矩形缺一邊: a ⊥ b ⊥ c，a // c 且等長，a、c 的另一端懸空
  (foreach b *ac:lines*
    (foreach a (ac:nbperp b)
      (foreach c (ac:nbperp b)
        (if (and (< a c) (ac:free a) (ac:free b) (ac:free c)
                 (ac:has "Parallel" a c) (ac:has "Equal" a c) (not (ac:shared a c)))
          (progn
            (setq ea (ac:other a (ac:shared a b)) ec (ac:other c (ac:shared c b)))
            ;; a、c 要在 b 的同一側 (U 形，不是 Z 形)
            (if (and (ac:dangling ea) (ac:dangling ec)
                     (> (ac:dot (ac:sub ea (ac:shared a b)) (ac:sub ec (ac:shared c b))) 0.0))
              (progn
                (setq o (vla-AddLine spc (ad:pt ea) (ad:pt ec)))
                (vla-put-Layer o lay)
                (setq *ac:used* (append (list a b c) *ac:used*) n (1+ n))
                (princ "\n  補全矩形 (T_Rect) 的第 4 邊"))))))))
  ;; 長槽孔缺一端: 兩條對齊平行線 + 一端相切圓弧
  (foreach arc1 *ac:arcs*
    (foreach l1 *ac:lines*
      (foreach l2 *ac:lines*
        (if (and (< l1 l2) (ac:free arc1) (ac:free l1) (ac:free l2)
                 (ac:has "Tangent" arc1 l1) (ac:has "Tangent" arc1 l2)
                 (ac:has "Parallel" l1 l2) (ac:aligned2 l1 l2))
          (progn
            (setq e1 (ac:other l1 (ac:shared l1 arc1)) e2 (ac:other l2 (ac:shared l2 arc1)))
            (if (and (ac:dangling e1) (ac:dangling e2))
              (progn
                (setq cen (ac:mid e1 e2)
                      u   (angle (car (ac:g arc1)) cen)
                      o   (vla-AddArc spc (ad:pt cen) (/ (distance e1 e2) 2.0)
                                      (- u (/ pi 2.0)) (+ u (/ pi 2.0))))
                (vla-put-Layer o lay)
                (setq *ac:used* (append (list arc1 l1 l2) *ac:used*) n (1+ n))
                (princ "\n  補全長槽孔 (T_Slot) 的另一端圓弧"))))))))
  n
)

;;; ---------- 指令 ----------

(setq *ac:filter* '((0 . "LINE,LWPOLYLINE,ARC,CIRCLE,POINT")))

(defun c:ACLIST (/ ss lines path f)
  (princ "\n選取要解讀設計意圖的圖形:")
  (if (setq ss (ssget *ac:filter*))
    (progn
      (ac:build ss)
      (ac:summary)
      (setq lines (ac:program))
      (princ "\n")
      (foreach l lines (princ (strcat "\n" l)))
      (setq path (strcat (getvar "DWGPREFIX") (vl-filename-base (getvar "DWGNAME")) "_concepts.txt"))
      (if (setq f (open path "w"))
        (progn (foreach l lines (write-line l f)) (close f)
               (princ (strcat "\n\nDSL 程式已存到: " path))))
      (ac:drawall)
      (princ "\n圖面上已依概念上色 (同一顏色 = 同一個概念實例)，輸入 REDRAW 清除。")))
  (princ)
)

(defun c:ACDIM (/ *error* doc spc ss off tot)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (defun *error* (msg)
    (vla-EndUndoMark doc)
    (if (not (wcmatch (strcase msg T) "*cancel*,*quit*,*break*,*取消*"))
      (princ (strcat "\n錯誤: " msg)))
    (princ)
  )
  (princ "\n選取要依設計概念標註的圖形:")
  (if (setq ss (ssget *ac:filter*))
    (progn
      (setq off (ad:getoff))
      (initget "Y N")
      (setq tot (getkword "\n是否加上整體總尺寸? [是(Y)/否(N)] <Y>: "))
      (if (not tot) (setq tot "Y"))
      (ac:build ss)
      (ac:summary)
      (vla-StartUndoMark doc)
      (setq spc (ad:space doc) *ad:cnt* 0 *ac:pool* nil)
      (foreach c *ac:K* (ac:dimconcept spc c off))
      (ac:dimpool spc off)
      (if (= tot "Y") (ad:total spc ss off))
      (vla-EndUndoMark doc)
      (princ (strcat "\n完成，共建立 " (itoa *ad:cnt*) " 個尺寸。(可用 U 一次復原)"))))
  (princ)
)

(defun c:ACCOMPLETE (/ *error* doc ss n lay)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (defun *error* (msg)
    (vla-EndUndoMark doc)
    (if (not (wcmatch (strcase msg T) "*cancel*,*quit*,*break*,*取消*"))
      (princ (strcat "\n錯誤: " msg)))
    (princ)
  )
  (princ "\n選取要自動補全的部分草圖:")
  (if (setq ss (ssget *ac:filter*))
    (progn
      (ac:build ss)
      ;; 只保留已完整的封閉概念，其餘圖元交給補全比對
      (setq *ac:used* nil)
      (foreach c *ac:K*
        (if (member (car c) '("Rect" "Square" "Slot" "NGon" "Profile"))
          (setq *ac:used* (append (cadr c) *ac:used*))))
      (vla-StartUndoMark doc)
      (setq lay "AC_COMPLETION")
      (vl-catch-all-apply
        '(lambda () (vla-put-Color (vla-Add (vla-get-Layers doc) lay) 1)))
      (setq n (ac:complete (ad:space doc) lay))
      (vla-EndUndoMark doc)
      (princ (strcat "\n完成，補全 " (itoa n) " 處 (紅色圖層 " lay "，不需要的可直接刪除或 U 復原)"))))
  (princ)
)

;;; ---------- 外部 Python: 執行論文的預訓練模型 ----------
;;; 依你的電腦修改這三個路徑 (見 bridge/README)
(if (not *ac:python*) (setq *ac:python* "python"))
(if (not *ac:bridge*) (setq *ac:bridge* "C:\\SketchConcept\\bridge\\sketchconcept_bridge.py"))
(if (not *ac:repo*)   (setq *ac:repo*   "C:\\SketchConcept\\SketchConcept"))

(defun ac:num (x) (rtos x 2 8))
(defun ac:jpt (p) (strcat "[" (ac:num (car p)) "," (ac:num (cadr p)) "]"))
(defun ac:jprim (i / g)
  (setq g (ac:g i))
  (strcat "{\"type\":\"" (ac:ty i) "\",\"g\":["
          (cond
            ((= (ac:ty i) "Line") (strcat (ac:jpt (car g)) "," (ac:jpt (cadr g))))
            ((= (ac:ty i) "Arc")
             (strcat (ac:jpt (nth 0 g)) "," (ac:num (nth 1 g)) "," (ac:jpt (nth 2 g)) ","
                     (ac:jpt (nth 3 g)) "," (ac:jpt (nth 4 g))))
            ((= (ac:ty i) "Circle") (strcat (ac:jpt (car g)) "," (ac:num (cadr g))))
            (T (ac:jpt (car g))))
          "]}")
)
(defun ac:jcons (c)
  (strcat "[\"" (car c) "\"," (ac:join (mapcar 'itoa (cdr c)) ",") "]")
)

(defun ac:files (/ base)
  (setq base (strcat (getvar "DWGPREFIX") (vl-filename-base (getvar "DWGNAME"))))
  (list (strcat base "_sketch.json") (strcat base "_concepts.lsp"))
)

;; 把目前的 L0 草圖圖寫成 JSON
(defun ac:export (path / f dz)
  (if (setq f (open path "w"))
    (progn
      ;; DIMZIN 會讓 rtos 省略前導零 (.5)，JSON 不接受
      (setq dz (getvar "DIMZIN"))
      (setvar "DIMZIN" 0)
      (write-line "{\"prims\":[" f)
      (write-line (ac:join (mapcar 'ac:jprim (ac:range (length *ac:P*))) ",\n") f)
      (write-line "],\"constraints\":[" f)
      (write-line (ac:join (mapcar 'ac:jcons *ac:C*) ",\n") f)
      (write-line "]}" f)
      (close f)
      (setvar "DIMZIN" dz)
      path))
)

;; 讀回模型結果: 每行 (圖元編號 實例編號 概念庫編號)
(defun ac:import (path / f l x e)
  (setq *ac:MK* nil)
  (if (setq f (open path "r"))
    (progn
      (while (setq l (read-line f))
        (if (= (substr l 1 1) "(")
          (progn
            (setq x (read l))
            (if (setq e (assoc (cadr x) *ac:MK*))
              (setq *ac:MK* (subst (list (car e) (cadr e) (append (caddr e) (list (car x)))) e *ac:MK*))
              (setq *ac:MK* (append *ac:MK* (list (list (cadr x) (caddr x) (list (car x))))))))))
      (close f)
      T))
)

(defun ac:sameset (a b)
  (and (= (length a) (length b)) (vl-every '(lambda (i) (member i b)) a))
)

;; 顯示模型的分解結果，並與規則版概念比對
(defun ac:showmodel (/ k rule n)
  (princ (strcat "\n===== 預訓練模型的概念分解: " (itoa (length *ac:MK*)) " 個概念實例 ====="))
  (setq k 0 n 0)
  (foreach m *ac:MK*
    (setq rule (vl-some '(lambda (c) (if (ac:sameset (caddr m) (cadr c)) (car c))) *ac:K*))
    (if rule (setq n (1+ n)))
    (princ (strcat "\n  實例 " (itoa (car m)) ": 概念庫 #" (itoa (cadr m))
                   "，" (itoa (length (caddr m))) " 個圖元 ("
                   (ac:join (mapcar 'ac:ty (caddr m)) ",") ")"
                   (if rule (strcat "  = 規則版 T_" rule) "")))
    (foreach i (caddr m) (ac:drawprim i (1+ (rem k 6))))
    (setq k (1+ k)))
  (princ (strcat "\n與規則版概念完全一致: " (itoa n) " / " (itoa (length *ac:MK*))))
  (princ "\n圖面上已依模型的概念上色，輸入 REDRAW 清除。")
)

(defun c:ACEXPORT (/ ss fs)
  (princ "\n選取要匯出給預訓練模型的圖形:")
  (if (setq ss (ssget *ac:filter*))
    (progn
      (ac:build ss)
      (setq fs (ac:files))
      (if (ac:export (car fs))
        (princ (strcat "\n已匯出 " (itoa (length *ac:P*)) " 個圖元、" (itoa (length *ac:C*))
                       " 個約束: " (car fs)
                       "\n執行 Python 後用 ACIMPORT 讀回 (期間不要重新選取其他圖形)")))))
  (princ)
)

(defun c:ACIMPORT (/ fs)
  (setq fs (ac:files))
  (cond
    ((not *ac:P*) (princ "\n請先執行 ACEXPORT 或 ACMODEL。"))
    ((ac:import (cadr fs)) (ac:showmodel))
    (T (princ (strcat "\n找不到結果檔: " (cadr fs)))))
  (princ)
)

;; 匯出 → 呼叫 Python 執行模型 (等待結束) → 讀回並顯示
(defun c:ACMODEL (/ ss fs sh rc)
  (princ "\n選取要用預訓練模型解讀的圖形:")
  (if (setq ss (ssget *ac:filter*))
    (progn
      (ac:build ss)
      (setq fs (ac:files))
      (ac:export (car fs))
      (if (findfile (cadr fs)) (vl-file-delete (cadr fs)))
      (princ "\n執行預訓練模型中 (CPU，約數秒到一分鐘)...")
      (setq sh (vlax-create-object "WScript.Shell")
            rc (vlax-invoke-method sh 'Run
                 (strcat "\"" *ac:python* "\" \"" *ac:bridge* "\" --repo \"" *ac:repo* "\" \""
                         (car fs) "\" \"" (cadr fs) "\"")
                 1 :vlax-true))
      (vlax-release-object sh)
      (if (ac:import (cadr fs))
        (ac:showmodel)
        (princ (strcat "\nPython 執行失敗 (代碼 " (itoa rc) ")。請檢查 *ac:python*、*ac:bridge*、*ac:repo* 的路徑，"
                       "或手動執行: python sketchconcept_bridge.py --repo ... \"" (car fs) "\" \"" (cadr fs) "\"")))))
  (princ)
)

(princ "\n自動標註已載入。指令: AUTODIM、ADIMTOTAL、ACLIST (解讀設計概念)、ACDIM (依概念標註)、ACCOMPLETE (概念補全)、ACMODEL / ACEXPORT / ACIMPORT (預訓練模型)")
(princ)
