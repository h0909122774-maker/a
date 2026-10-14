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

;; 安全呼叫: 成功回傳 T 並累計數量，失敗回傳 nil
(defun ad:try (fn args / r)
  (setq r (vl-catch-all-apply fn args))
  (if (vl-catch-all-error-p r)
    (progn (princ (strcat "\n  略過: " (vl-catch-all-error-message r))) nil)
    (progn (setq *ad:cnt* (1+ *ad:cnt*)) T)
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

(defun ad:poly (spc obj off / n i pts a side p1 p2 b m cm r v len ang)
  (setq n (fix (+ 0.5 (vlax-curve-getEndParam obj))))
  ;; 收集頂點 (含終點)
  (setq i 0 pts nil)
  (while (<= i n)
    (setq pts (cons (vlax-curve-getPointAtParam obj i) pts) i (1+ i))
  )
  (setq pts (reverse pts))
  ;; 以鞋帶公式判斷方向: 逆時針 -> 外側在右邊
  (setq a 0.0 i 0)
  (while (< i n)
    (setq p1 (nth i pts) p2 (nth (1+ i) pts)
          a  (+ a (- (* (car p1) (cadr p2)) (* (car p2) (cadr p1))))
          i  (1+ i))
  )
  (setq p1 (last pts) p2 (car pts)
        a  (+ a (- (* (car p1) (cadr p2)) (* (car p2) (cadr p1)))))
  (setq side (if (> a 0.0) -1 1))
  ;; 逐段標註
  (setq i 0)
  (while (< i n)
    (setq p1 (nth i pts) p2 (nth (1+ i) pts)
          b  (vla-GetBulge obj i))
    (if (< (abs b) 1e-8)
      (ad:aligned spc p1 p2 side off)
      ;; 弧段: 由凸度計算半徑與圓心
      (progn
        (setq len (distance p1 p2))
        (if (> len 1e-8)
          (progn
            (setq ang (* 4.0 (atan (abs b)))
                  r   (/ len (* 2.0 (sin (/ ang 2.0))))
                  m   (vlax-curve-getPointAtParam obj (+ i 0.5)) ; 弧中點
                  cm  (ad:mid p1 p2)                             ; 弦中點
                  v   (mapcar '- cm m)
                  v   (mapcar '(lambda (x) (/ x (distance cm m))) v)
            )
            (ad:radial spc (mapcar '(lambda (p d) (+ p (* d r))) m v) m off)
          )
        )
      )
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

(princ "\n自動標註已載入。指令: AUTODIM (自動標註)、ADIMTOTAL (只加總尺寸)")
(princ)
