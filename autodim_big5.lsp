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
;;; 設計概念標註 (參考 Yang & Pan, "Discovering Design Concepts
;;; for CAD Sketches", NeurIPS 2022)
;;;
;;; 論文把草圖看成「圖元 + 約束」組成的圖，並把反覆出現的圖元/約束
;;; 組合歸納成可重複使用的「設計概念」(例如矩形、長槽孔、孔群)，
;;; 用來解讀設計意圖。論文是用神經網路從資料集中「學」出概念；
;;; 這裡改用一組固定規則的概念庫來偵測，再依概念標註:
;;;   - 每個概念只標決定它的參數 (矩形: 長、寬；長槽孔: 中心距、寬)
;;;   - 相同概念的重複實例合併成一個尺寸 (4×%%c10、3×R5、(n-1)×節距)
;;;
;;; 指令:
;;;   ACLIST  只解讀設計意圖，在指令列列出偵測到的概念
;;;   ACDIM   依設計概念自動標註
;;; ============================================================

(defun ad:eq (a b) (<= (abs (- a b)) (* 1e-6 (max 1.0 (abs a) (abs b)))))
(defun ad:2d (p) (list (car p) (cadr p)))
(defun ad:sub (a b) (mapcar '- (ad:2d a) (ad:2d b)))
(defun ad:dot (u v) (+ (* (car u) (car v)) (* (cadr u) (cadr v))))
(defun ad:cross (u v) (- (* (car u) (cadr v)) (* (cadr u) (car v))))
(defun ad:peq (p q) (< (distance (ad:2d p) (ad:2d q)) 1e-6))
(defun ad:zero (b) (< (abs b) 1e-8))
(defun ad:semi (b) (< (abs (- (abs b) 1.0)) 1e-4)) ; 凸度 ±1 = 半圓

(defun ad:seglens (pts n / i l)
  (setq i 0)
  (while (< i n)
    (setq l (cons (distance (ad:2d (nth i pts)) (ad:2d (nth (1+ i) pts))) l) i (1+ i)))
  (reverse l)
)

(defun ad:alleq (l) (vl-every '(lambda (x) (ad:eq x (car l))) l))

;; 約束: 相鄰邊互相垂直
(defun ad:rightangles (pts n / i u v ok)
  (setq i 0 ok T)
  (while (and ok (< i n))
    (setq u (ad:sub (nth (rem (1+ i) n) pts) (nth i pts))
          v (ad:sub (nth (rem (+ i 2) n) pts) (nth (rem (1+ i) n) pts)))
    (if (> (abs (ad:dot u v)) (* 1e-6 (distance '(0 0) u) (distance '(0 0) v)))
      (setq ok nil))
    (setq i (1+ i))
  )
  ok
)

;; 約束: 所有頂點到中心等距 (正多邊形)
(defun ad:cyclic (pts n / c l)
  (setq l (reverse (cdr (reverse pts))) ; 去掉重複的終點
        c (list (/ (apply '+ (mapcar 'car l)) n) (/ (apply '+ (mapcar 'cadr l)) n)))
  (ad:alleq (mapcar '(lambda (p) (distance c (ad:2d p))) l))
)

;; 長槽孔: 直線-半圓-直線-半圓。回傳第一條直線段的索引，否則 nil
(defun ad:slotstart (bs)
  (cond
    ((and (ad:zero (nth 0 bs)) (ad:zero (nth 2 bs))
          (ad:semi (nth 1 bs)) (ad:semi (nth 3 bs))
          (> (* (nth 1 bs) (nth 3 bs)) 0.0)) 0)
    ((and (ad:zero (nth 1 bs)) (ad:zero (nth 3 bs))
          (ad:semi (nth 0 bs)) (ad:semi (nth 2 bs))
          (> (* (nth 0 bs) (nth 2 bs)) 0.0)) 1)
  )
)

;; 把聚合線解讀成一個概念: (類型 物件 頂點 方向 [長槽孔起始段])
;; 一般聚合線的弧段放入圓弧池，之後依半徑分組
(defun ad:classify (obj / n pts bs side lens closed straight k i a)
  (setq n    (fix (+ 0.5 (vlax-curve-getEndParam obj)))
        pts  (ad:polypts obj n)
        side (ad:side pts n)
        lens (ad:seglens pts n)
        closed (ad:peq (car pts) (last pts))
        i 0)
  (while (< i n) (setq bs (cons (vla-GetBulge obj i) bs) i (1+ i)))
  (setq bs (reverse bs)
        straight (vl-every 'ad:zero bs))
  (cond
    ((and closed straight (= n 4) (ad:rightangles pts n))
     (list (if (ad:eq (nth 0 lens) (nth 1 lens)) "SQUARE" "RECT") obj pts side))
    ((and closed (= n 4) (setq k (ad:slotstart bs))
          (ad:eq (nth k lens) (nth (+ k 2) lens)))
     (list "SLOT" obj pts side k))
    ((and closed straight (>= n 3) (ad:alleq lens) (ad:cyclic pts n))
     (list "NGON" obj pts side))
    (T
     (setq i 0)
     (while (< i n)
       (if (setq a (ad:bulgearc obj i (nth i pts) (nth (1+ i) pts)))
         (setq *ad:arcs* (cons a *ad:arcs*)))
       (setq i (1+ i)))
     (list "POLY" obj pts side))
  )
)

;; 依半徑分組: items 的第一個元素是半徑
(defun ad:groupr (items / groups g)
  (foreach it (reverse items)
    (if (setq g (vl-some '(lambda (gr) (if (ad:eq (car it) (car (car gr))) gr)) groups))
      (setq groups (subst (append g (list it)) g groups))
      (setq groups (append groups (list (list it))))
    )
  )
  groups
)

;; 孔群是否等距排成一直線: 是則回傳依序排好的圓心，否則 nil
(defun ad:pattern (cs / best a b d u srt ok gap i n)
  (setq best 0.0 n (length cs))
  (foreach p cs
    (foreach q cs
      (if (> (setq d (distance (ad:2d p) (ad:2d q))) best) (setq best d a p b q))))
  (if (and (> n 1) (> best 1e-8))
    (progn
      (setq u   (mapcar '(lambda (x) (/ x best)) (ad:sub b a))
            srt (vl-sort (mapcar '(lambda (p) (list (ad:dot (ad:sub p a) u) p)) cs)
                         '(lambda (x y) (< (car x) (car y))))
            ok  T
            gap (/ best (1- n))
            i   0)
      (foreach x srt
        (if (> (abs (ad:cross (ad:sub (cadr x) a) u)) (* 1e-6 (max 1.0 best))) (setq ok nil)))
      (while (and ok (< i (1- n)))
        (if (not (ad:eq (- (car (nth (1+ i) srt)) (car (nth i srt))) gap)) (setq ok nil))
        (setq i (1+ i)))
      (if ok (mapcar 'cadr srt))
    )
  )
)

;; 解讀選取集合，回傳概念清單；圓與圓弧分別放入 *ad:circs* / *ad:arcs*
(defun ad:analyze (ss / i obj typ concepts c)
  (setq *ad:arcs* nil *ad:circs* nil i 0)
  (while (< i (sslength ss))
    (setq obj (vlax-ename->vla-object (ssname ss i))
          typ (vla-get-ObjectName obj))
    (cond
      ((= typ "AcDbLine") (setq concepts (cons (list "LINE" obj) concepts)))
      ((= typ "AcDbPolyline") (setq concepts (cons (ad:classify obj) concepts)))
      ((= typ "AcDbArc")
       (setq c (vlax-safearray->list (vlax-variant-value (vla-get-Center obj))))
       (setq *ad:arcs*
              (cons (list (vla-get-Radius obj) c
                          (vlax-curve-getPointAtDist obj
                            (/ (vlax-curve-getDistAtParam obj (vlax-curve-getEndParam obj)) 2.0)))
                    *ad:arcs*)))
      ((= typ "AcDbCircle")
       (setq *ad:circs*
              (cons (list (vla-get-Radius obj)
                          (vlax-safearray->list (vlax-variant-value (vla-get-Center obj))))
                    *ad:circs*)))
    )
    (setq i (1+ i))
  )
  (setq *ad:arcs* (reverse *ad:arcs*) *ad:circs* (reverse *ad:circs*))
  (reverse concepts)
)

;; 設計意圖解讀報告
(defun ad:report (concepts / names cnt e g)
  (setq names '(("RECT" . "矩形") ("SQUARE" . "正方形") ("SLOT" . "長槽孔")
                ("NGON" . "正多邊形") ("POLY" . "一般聚合線") ("LINE" . "直線")))
  (foreach c concepts
    (if (setq e (assoc (car c) cnt))
      (setq cnt (subst (cons (car c) (1+ (cdr e))) e cnt))
      (setq cnt (append cnt (list (cons (car c) 1))))))
  (princ "\n===== 偵測到的設計概念 =====")
  (foreach e cnt
    (princ (strcat "\n  " (cdr (assoc (car e) names)) " × " (itoa (cdr e)))))
  (foreach g (ad:groupr *ad:circs*)
    (princ (strcat "\n  孔: " (itoa (length g)) " × 直徑 " (rtos (* 2.0 (car (car g))))
                   (if (and (> (length g) 1) (ad:pattern (mapcar 'cadr g)))
                     "  (等距排成一直線)" ""))))
  (foreach g (ad:groupr *ad:arcs*)
    (princ (strcat "\n  圓弧: " (itoa (length g)) " × 半徑 " (rtos (car (car g))))))
  (princ "\n============================")
)

;; 依概念標註
(defun ad:dimconcept (spc c off / typ obj pts side k n i)
  (setq typ (car c) obj (cadr c) pts (caddr c) side (cadddr c))
  (cond
    ((= typ "LINE") (ad:line spc obj off))
    ((= typ "RECT")
     (ad:aligned spc (nth 0 pts) (nth 1 pts) side off)
     (ad:aligned spc (nth 1 pts) (nth 2 pts) side off))
    ((= typ "SQUARE")
     (ad:override (ad:aligned spc (nth 0 pts) (nth 1 pts) side off) "□<>"))
    ((= typ "NGON")
     (ad:override (ad:aligned spc (nth 0 pts) (nth 1 pts) side off)
                  (strcat (itoa (1- (length pts))) "×<>")))
    ((= typ "SLOT")
     (setq k (nth 4 c))
     ;; 中心距 = 直線段長；寬 = 半圓的弦長 (放在圓弧外側)
     (ad:aligned spc (nth k pts) (nth (1+ k) pts) side off)
     (ad:aligned spc (nth (1+ k) pts) (nth (+ k 2) pts) side
                 (+ off (/ (distance (nth (1+ k) pts) (nth (+ k 2) pts)) 2.0))))
    ((= typ "POLY")
     ;; 只標直線段；弧段已放入圓弧池統一標註
     (setq n (1- (length pts)) i 0)
     (while (< i n)
       (if (ad:zero (vla-GetBulge obj i))
         (ad:aligned spc (nth i pts) (nth (1+ i) pts) side off))
       (setq i (1+ i))))
  )
)

;; 孔群: 一個直徑尺寸 "n×%%c..."，等距直線排列再加一個節距尺寸
(defun ad:dimholes (spc g off / n r c k o cs)
  (setq n (length g) r (car (car g)) c (cadr (car g)) k (/ r (sqrt 2.0)))
  (setq o (ad:try 'vla-AddDimDiametric
            (list spc
                  (ad:pt (list (+ (car c) k) (+ (cadr c) k) (ad:z c)))
                  (ad:pt (list (- (car c) k) (- (cadr c) k) (ad:z c)))
                  off)))
  (if (> n 1)
    (progn
      (ad:override o (strcat (itoa n) "×<>"))
      (if (setq cs (ad:pattern (mapcar 'cadr g)))
        (progn
          (setq o (ad:aligned spc (car cs) (cadr cs) 1 (+ off r)))
          (if (> n 2) (ad:override o (strcat (itoa (1- n)) "×<>")))))
    )
  )
)

;; 相同半徑的圓弧: 只標一個 "n×R..."
(defun ad:dimarcs (spc g off / o)
  (setq o (ad:radial spc (cadr (car g)) (caddr (car g)) off))
  (if (> (length g) 1) (ad:override o (strcat (itoa (length g)) "×<>")))
)

(defun c:ACLIST (/ ss)
  (princ "\n選取要解讀的直線 / 聚合線 / 圓弧 / 圓:")
  (if (setq ss (ssget '((0 . "LINE,LWPOLYLINE,ARC,CIRCLE"))))
    (ad:report (ad:analyze ss)))
  (princ)
)

(defun c:ACDIM (/ *error* doc spc ss off tot concepts)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (defun *error* (msg)
    (vla-EndUndoMark doc)
    (if (not (wcmatch (strcase msg T) "*cancel*,*quit*,*break*,*取消*"))
      (princ (strcat "\n錯誤: " msg)))
    (princ)
  )
  (princ "\n選取要依設計概念標註的直線 / 聚合線 / 圓弧 / 圓:")
  (if (setq ss (ssget '((0 . "LINE,LWPOLYLINE,ARC,CIRCLE"))))
    (progn
      (setq off (ad:getoff))
      (initget "Y N")
      (setq tot (getkword "\n是否加上整體總尺寸? [是(Y)/否(N)] <Y>: "))
      (if (not tot) (setq tot "Y"))
      (setq concepts (ad:analyze ss))
      (ad:report concepts)
      (vla-StartUndoMark doc)
      (setq spc (ad:space doc) *ad:cnt* 0)
      (foreach c concepts (ad:dimconcept spc c off))
      (foreach g (ad:groupr *ad:circs*) (ad:dimholes spc g off))
      (foreach g (ad:groupr *ad:arcs*) (ad:dimarcs spc g off))
      (if (= tot "Y") (ad:total spc ss off))
      (vla-EndUndoMark doc)
      (princ (strcat "\n完成，共建立 " (itoa *ad:cnt*) " 個尺寸。(可用 U 一次復原)"))
    )
  )
  (princ)
)

(princ "\n自動標註已載入。指令: AUTODIM (自動標註)、ADIMTOTAL (只加總尺寸)、")
(princ "\n  ACLIST (解讀設計概念)、ACDIM (依設計概念標註)")
(princ)
