;;; autodim_toggle.lsp -- autocad_auto_dim.py on/off switch
;;; APPLOAD this file, then:
;;;   ADOFF  pause auto dimensioning (USERI5 = 1, saved in the drawing)
;;;   ADON   resume auto dimensioning (USERI5 = 0)
;;;   ADSW   toggle
;;;   ADVIEWS  redo the whole drawing in engineering-drawing style
;;;            (hole positions chained from centers, 2XØ4 hole labels, R once per size)
(defun c:ADOFF () (setvar "USERI5" 1) (princ "\nAuto dimension: OFF") (princ))
(defun c:ADON  () (setvar "USERI5" 0) (princ "\nAuto dimension: ON") (princ))
(defun c:ADSW  ()
  (if (= (getvar "USERI5") 1) (c:ADON) (c:ADOFF)))
(defun c:ADVIEWS () (setvar "USERI4" 1) (princ "\nAuto dimension: drawing-style re-dimension requested") (princ))
(princ "\nautodim_toggle loaded: ADON / ADOFF / ADSW / ADVIEWS")
(princ)
