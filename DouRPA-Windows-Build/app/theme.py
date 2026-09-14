APP_STYLESHEET = r"""
* {
    font-family: "Microsoft YaHei UI", "Segoe UI";
    outline: none;
}

QMainWindow {
    background: #EAF0FF;
}

QWidget#Root {
    color: #17203A;
    background: transparent;
}

/* ===== Sidebar: translucent sapphire glass ===== */
QFrame#Sidebar {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(35, 55, 126, 225),
        stop:0.42 rgba(53, 72, 153, 212),
        stop:0.72 rgba(55, 75, 160, 202),
        stop:1 rgba(60, 78, 151, 212)
    );
    border: 1px solid rgba(255,255,255,72);
    border-left: 1px solid rgba(255,255,255,105);
    border-top: 1px solid rgba(255,255,255,115);
}

QLabel#BrandIcon {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,74),
        stop:0.45 rgba(167,210,255,58),
        stop:1 rgba(190,157,255,52)
    );
    border: 1px solid rgba(255,255,255,128);
    border-radius: 14px;
}

QLabel#BrandName {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 700;
}

QLabel#BrandSub {
    color: rgba(231,239,255,182);
    font-size: 9px;
    letter-spacing: 1px;
}

QFrame#SideDivider {
    background: rgba(255,255,255,32);
    border: 0;
}

QPushButton#NavButton {
    color: rgba(240,246,255,205);
    background: rgba(255,255,255,0);
    border: 1px solid rgba(255,255,255,0);
    border-radius: 15px;
    padding: 12px 14px;
    text-align: left;
    font-size: 12px;
    font-weight: 500;
}

QPushButton#NavButton:hover {
    background: rgba(255,255,255,30);
    color: #FFFFFF;
    border: 1px solid rgba(255,255,255,62);
}

QPushButton#NavButton[active="true"] {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(255,255,255,74),
        stop:0.35 rgba(145,182,255,88),
        stop:1 rgba(177,131,255,68)
    );
    color: #FFFFFF;
    border: 1px solid rgba(255,255,255,142);
}

QFrame#SidebarMiniCard {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,26),
        stop:1 rgba(168,194,255,18)
    );
    border: 1px solid rgba(255,255,255,52);
    border-radius: 16px;
}

QLabel#SidebarMiniTitle {
    color: #FFFFFF;
    font-size: 10px;
    font-weight: 700;
}

QLabel#SidebarMiniText {
    color: rgba(236,242,255,202);
    font-size: 10px;
}

QLabel#SidebarMiniSub {
    color: rgba(215,226,250,142);
    font-size: 9px;
}

/* ===== Top area ===== */
QFrame#Topbar {
    background: transparent;
    border: 0;
}

QLabel#PageTitle {
    font-size: 26px;
    font-weight: 700;
    color: #12182A;
}

QLabel#PageSub {
    font-size: 11px;
    color: #747F9A;
}

QFrame#ConnectionPill {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,190),
        stop:1 rgba(235,241,255,150)
    );
    border: 1px solid rgba(255,255,255,230);
    border-radius: 15px;
}

QLabel#EngineStatus {
    color: #4E5C79;
    font-size: 10px;
    font-weight: 600;
}

/* ===== Main liquid-glass cards ===== */
QFrame#Card {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,214),
        stop:0.36 rgba(255,255,255,180),
        stop:0.72 rgba(243,247,255,154),
        stop:1 rgba(255,248,255,170)
    );
    border: 1px solid rgba(255,255,255,238);
    border-top: 1px solid rgba(255,255,255,255);
    border-left: 1px solid rgba(255,255,255,248);
    border-radius: 22px;
}

QLabel#MetricTitle {
    color: #76819B;
    font-size: 11px;
}

QLabel#MetricValue {
    color: #111728;
    font-size: 30px;
    font-weight: 700;
}

QLabel#MetricHint {
    color: #9BA5B9;
    font-size: 10px;
}

QLabel#SectionTitle {
    color: #141B2D;
    font-size: 16px;
    font-weight: 700;
}

QLabel#SectionSub {
    color: #7D89A4;
    font-size: 10px;
}

QLabel#SelectionCount {
    color: #64708C;
    font-size: 10px;
    padding: 0 6px;
}

/* ===== Buttons ===== */
QPushButton#PrimaryButton {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 #458CFF,
        stop:0.44 #5D6FFF,
        stop:0.74 #7A5EFF,
        stop:1 #B653F1
    );
    color: #FFFFFF;
    border: 1px solid rgba(255,255,255,125);
    border-top: 1px solid rgba(255,255,255,178);
    border-radius: 13px;
    padding: 10px 18px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton#PrimaryButton:hover {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 #5197FF,
        stop:0.45 #6677FF,
        stop:0.78 #8A68FF,
        stop:1 #C25AF6
    );
    border-color: rgba(255,255,255,205);
}

QPushButton#PrimaryButton:pressed {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 #3979E8,
        stop:1 #844CDE
    );
}

QPushButton#PrimaryButton:disabled {
    background: rgba(168,178,210,95);
    color: rgba(255,255,255,150);
    border-color: rgba(255,255,255,75);
}

QPushButton#SecondaryButton {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,206),
        stop:1 rgba(238,243,255,164)
    );
    color: #34415F;
    border: 1px solid rgba(255,255,255,230);
    border-bottom: 1px solid rgba(190,202,230,155);
    border-radius: 13px;
    padding: 9px 15px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton#SecondaryButton:hover {
    background: rgba(255,255,255,238);
    border: 1px solid rgba(141,163,228,155);
    color: #273654;
}

QPushButton#DangerButton {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,247,250,216),
        stop:1 rgba(255,222,233,175)
    );
    color: #C53A59;
    border: 1px solid rgba(255,182,203,185);
    border-radius: 13px;
    padding: 9px 14px;
    font-size: 11px;
    font-weight: 600;
}

QPushButton#DangerButton:hover {
    background: rgba(255,229,238,232);
    border-color: rgba(243,126,164,195);
}

QPushButton#DangerButton:disabled {
    color: rgba(167,123,137,130);
    background: rgba(246,238,243,135);
    border-color: rgba(224,206,214,130);
}

/* ===== Inputs ===== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    min-height: 38px;
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(255,255,255,202),
        stop:1 rgba(239,245,255,165)
    );
    color: #1D2943;
    border: 1px solid rgba(255,255,255,236);
    border-bottom: 1px solid rgba(190,202,229,155);
    border-radius: 12px;
    padding: 0 11px;
    selection-background-color: #D8E1FF;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    background: rgba(255,255,255,238);
    border: 1px solid rgba(111,134,255,205);
}

QComboBox::drop-down {
    border: 0;
    width: 28px;
}

/* ===== Tables ===== */
QTableWidget {
    background: rgba(255,255,255,106);
    alternate-background-color: rgba(244,247,255,112);
    border: 1px solid rgba(255,255,255,218);
    border-radius: 15px;
    gridline-color: rgba(218,226,244,128);
    color: #34415C;
    selection-background-color: rgba(210,222,255,192);
    selection-color: #17213A;
}

QHeaderView::section {
    background: qlineargradient(
        x1:0,y1:0,x2:0,y2:1,
        stop:0 rgba(255,255,255,205),
        stop:1 rgba(237,242,255,168)
    );
    color: #67738E;
    border: 0;
    border-bottom: 1px solid rgba(206,216,239,155);
    padding: 10px 8px;
    font-size: 10px;
    font-weight: 600;
}

QTableWidget::item {
    border-bottom: 1px solid rgba(225,231,245,138);
    padding: 8px;
}

QTableWidget::item:selected {
    background: rgba(214,224,255,190);
    color: #17213A;
}

/* ===== Progress ===== */
QProgressBar {
    border: 1px solid rgba(255,255,255,165);
    background: rgba(206,217,239,145);
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 #35B6FF,
        stop:0.46 #557DFF,
        stop:1 #A55AF1
    );
    border-radius: 4px;
}

/* ===== Log / text panels ===== */
QPlainTextEdit, QTextEdit {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(25,37,70,232),
        stop:1 rgba(47,43,92,224)
    );
    color: #D8E4FF;
    border: 1px solid rgba(178,198,255,68);
    border-top: 1px solid rgba(255,255,255,52);
    border-radius: 16px;
    padding: 12px;
    font-family: Consolas, "Microsoft YaHei UI";
    font-size: 10px;
    selection-background-color: rgba(92,111,213,175);
}

/* ===== Checkboxes / tabs ===== */
QCheckBox {
    color: #35425F;
    spacing: 8px;
    font-size: 11px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid rgba(160,176,216,175);
    border-radius: 5px;
    background: rgba(255,255,255,196);
}

QCheckBox::indicator:checked {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:1,
        stop:0 #4F8BFF,
        stop:1 #765DFF
    );
    border-color: rgba(90,104,241,210);
}

QTabWidget::pane {
    background: rgba(255,255,255,55);
    border: 1px solid rgba(255,255,255,125);
    border-radius: 14px;
    top: -1px;
}

QTabBar::tab {
    background: rgba(255,255,255,64);
    color: #75809A;
    padding: 9px 16px;
    margin-right: 5px;
    border: 1px solid rgba(255,255,255,92);
    border-radius: 11px;
}

QTabBar::tab:hover {
    background: rgba(255,255,255,132);
    color: #55627D;
}

QTabBar::tab:selected {
    background: qlineargradient(
        x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(97,135,255,190),
        stop:1 rgba(144,94,255,175)
    );
    color: #FFFFFF;
    border: 1px solid rgba(255,255,255,172);
    font-weight: 600;
}

/* ===== Scroll bars ===== */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(117,132,172,105);
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(99,116,162,150);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
}
QScrollBar::handle:horizontal {
    background: rgba(117,132,172,105);
    border-radius: 4px;
    min-width: 30px;
}

/* ===== Misc ===== */
QToolTip {
    background: rgba(24,30,50,244);
    color: #FFFFFF;
    border: 1px solid rgba(255,255,255,42);
    border-radius: 7px;
    padding: 6px;
}

QMessageBox {
    background: #F2F5FF;
}
"""
