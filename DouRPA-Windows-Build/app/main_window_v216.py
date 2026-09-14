from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QPushButton,
    QWidget,
)

from app.main_window_v215 import MainWindow as V215MainWindow


class AmbientGlassLayer(QWidget):
    """Purely decorative liquid-glass background. No interaction or business logic."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Soft pearl base.
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0.00, QColor(231, 239, 255))
        base.setColorAt(0.28, QColor(242, 239, 255))
        base.setColorAt(0.58, QColor(235, 247, 255))
        base.setColorAt(0.80, QColor(249, 239, 255))
        base.setColorAt(1.00, QColor(241, 246, 255))
        p.fillRect(self.rect(), base)

        def glow(cx, cy, radius, inner, outer):
            g = QRadialGradient(cx, cy, radius)
            g.setColorAt(0.0, inner)
            g.setColorAt(0.45, QColor(inner.red(), inner.green(), inner.blue(), max(0, inner.alpha() // 2)))
            g.setColorAt(1.0, outer)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(int(cx-radius), int(cy-radius), int(radius*2), int(radius*2))

        w, h = max(1, self.width()), max(1, self.height())
        glow(w * 0.10, h * 0.05, w * 0.42,
             QColor(73, 128, 255, 105), QColor(73, 128, 255, 0))
        glow(w * 0.74, h * 0.02, w * 0.35,
             QColor(144, 101, 255, 92), QColor(144, 101, 255, 0))
        glow(w * 0.98, h * 0.32, w * 0.34,
             QColor(255, 121, 218, 92), QColor(255, 121, 218, 0))
        glow(w * 0.52, h * 0.83, w * 0.36,
             QColor(88, 207, 255, 62), QColor(88, 207, 255, 0))

        # Two soft "liquid glass" light sweeps inspired by the reference,
        # intentionally kept subtle so tables and controls stay readable.
        path = QPainterPath()
        path.moveTo(w * 0.18, h * 0.05)
        path.cubicTo(w * 0.36, h * 0.00, w * 0.42, h * 0.21, w * 0.61, h * 0.14)
        path.cubicTo(w * 0.78, h * 0.08, w * 0.84, h * 0.17, w * 0.95, h * 0.12)
        path.lineTo(w * 0.95, h * 0.27)
        path.cubicTo(w * 0.78, h * 0.34, w * 0.65, h * 0.20, w * 0.47, h * 0.27)
        path.cubicTo(w * 0.30, h * 0.34, w * 0.24, h * 0.18, w * 0.12, h * 0.22)
        path.closeSubpath()

        sweep = QLinearGradient(w * 0.15, h * 0.05, w * 0.90, h * 0.30)
        sweep.setColorAt(0.0, QColor(255, 255, 255, 18))
        sweep.setColorAt(0.50, QColor(255, 255, 255, 96))
        sweep.setColorAt(1.0, QColor(255, 255, 255, 10))
        p.setBrush(sweep)
        p.setPen(Qt.NoPen)
        p.drawPath(path)

        p.end()


class MainWindow(V215MainWindow):
    """V2.1.6 visual-only liquid glass upgrade.

    No RPA, queue, task, store, cache, delay, login, delete, stop-task or
    stop-run behavior is changed. This class only paints and styles the UI.
    """

    def __init__(self, root: Path):
        super().__init__(root)
        self.setWindowTitle("DouRPA Pro · 抖店相似品批量发布")
        self._install_ambient_layer()
        self._apply_liquid_glass_effects()
        self._refresh_nav_glow()

    def _install_ambient_layer(self):
        rootw = self.centralWidget()
        if not rootw:
            return
        self._ambient_glass = AmbientGlassLayer(rootw)
        self._ambient_glass.setGeometry(rootw.rect())
        self._ambient_glass.lower()
        self._ambient_glass.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        ambient = getattr(self, "_ambient_glass", None)
        if ambient is not None and self.centralWidget() is not None:
            ambient.setGeometry(self.centralWidget().rect())
            ambient.lower()

    @staticmethod
    def _shadow(widget, blur: int, y: int, color: QColor):
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, y)
        effect.setColor(color)
        widget.setGraphicsEffect(effect)

    def _apply_liquid_glass_effects(self):
        # Replace earlier card shadows with softer blue-violet refraction shadows.
        for frame in self.findChildren(QFrame):
            name = frame.objectName()
            if name == "Card":
                self._shadow(frame, 42, 8, QColor(77, 83, 171, 38))
            elif name == "ConnectionPill":
                self._shadow(frame, 24, 4, QColor(80, 103, 220, 35))
            elif name == "SidebarMiniCard":
                self._shadow(frame, 24, 5, QColor(32, 55, 130, 42))

        for btn in self.findChildren(QPushButton):
            if btn.objectName() == "PrimaryButton":
                self._shadow(btn, 24, 5, QColor(92, 82, 255, 76))

    def _refresh_nav_glow(self):
        for btn in getattr(self, "nav", []):
            if bool(btn.property("active")):
                self._shadow(btn, 28, 3, QColor(118, 137, 255, 92))
            else:
                # Remove only the decorative nav glow; QSS still handles hover/normal.
                btn.setGraphicsEffect(None)

    def switch_page(self, idx: int):
        super().switch_page(idx)
        self._refresh_nav_glow()
