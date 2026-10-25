"""用 PIL 把面板画成图（超采样后缩小，边缘/文字都是抗锯齿的）。"""
import os

from PIL import Image, ImageDraw, ImageFont

CARD = (32, 35, 41, 255)
CARD_HI = (43, 47, 55, 255)
LINE = (58, 63, 72, 255)
TRACK = (60, 66, 76, 255)
TXT = (232, 236, 243, 255)
SUB = (151, 160, 173, 255)
ACC = (58, 130, 247, 255)
GOOD = (74, 222, 128, 255)
WARN = (250, 190, 70, 255)
BAD = (248, 113, 113, 255)
OFF = (75, 82, 96, 255)
SLEEP = (118, 126, 140, 255)      # 休眠时的灰色电量条（斜纹）
DIM_HATCH = (86, 93, 106, 255)    # 休眠且没有上次读数时的整条淡斜纹

STATE_TEXT = {"ok": "", "sleep": "", "absent": "未连接", "error": "读取失败"}

FONT_DIR = r"C:\Windows\Fonts"
_FONT_CANDIDATES = ("msyh.ttc", "msyhbd.ttc", "simhei.ttf")


def _font(size, bold=False):
    names = ("msyhbd.ttc", "msyh.ttc") if bold else ("msyh.ttc", "simhei.ttf")
    for name in names:
        p = os.path.join(FONT_DIR, name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _trailing(state, level, last, good=80, warn=20):
    """设备名后面的文字：正常显示百分比；休眠显示上次读到的百分比（灰色）+ 斜纹条。

    good / warn 是颜色阈值（可在设置里改）：level >= good 绿，>= warn 黄，否则红。
    """
    if state == "ok" and level is not None:
        color = GOOD if level >= good else (WARN if level >= warn else BAD)
        return color, f"{level}%"
    if state == "sleep":
        return SUB, (f"{last}%" if last is not None else "--")
    if last is not None:
        return SUB, f"{last}%（{STATE_TEXT.get(state, '')}）"
    return SUB, STATE_TEXT.get(state, "--")


def _bar_fill(state, level, last):
    """电量条实心填充百分比（0 = 不填）；休眠那条走斜纹，不用这个。"""
    if state == "ok" and level:
        return float(level)
    return 0.0


def _hatch(img, box, radius, color, S, step=3.2, lw=1.2):
    """在 box 里画一条圆角斜纹带（斜纹留空，露出底下的轨道色）。"""
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    w, h = max(2, x2 - x1), max(2, y2 - y1)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w - 1, h - 1), radius=max(1, int(round(radius))), fill=255)
    st = max(3, int(round(step * S)))
    lwid = max(1, int(round(lw * S)))
    for k in range(-h, w + h, st):
        md.line((k, h, k + h, 0), fill=0, width=lwid)
    img.paste(Image.new("RGBA", (w, h), color), (x1, y1), mask)


def _sleep_bar(img, box, radius, last, S):
    """休眠态电量条：有上次读数 → 灰斜纹只到那个长度；一次都没读到 → 整条淡斜纹。

    这样既不会像「没电了」（空条），又能看出这是旧数据而不是实时电量。
    """
    x1, y1, x2, y2 = box
    if last:
        end = x1 + (x2 - x1) * max(0.0, min(100.0, float(last))) / 100.0
        if end - x1 > 2:
            _hatch(img, (x1, y1, end, y2), radius, SLEEP, S)
            return
    _hatch(img, (x1, y1, x2, y2), radius, DIM_HATCH, S, step=4.0, lw=1.1)


MDL2_FONT = r"C:\Windows\Fonts\segmdl2.ttf"     # Segoe MDL2 Assets
GLYPH_REFRESH = "\ue72c"


def _bolt(d, sc, x, y, h, color):
    """画一个闪电图标（设计坐标：左上角 x,y，高度 h）。用于「正在充电」。"""
    w = h * 0.62
    pts = [
        (x + w * 0.58, y),
        (x + w * 0.02, y + h * 0.58),
        (x + w * 0.46, y + h * 0.58),
        (x + w * 0.30, y + h),
        (x + w * 0.98, y + h * 0.38),
        (x + w * 0.54, y + h * 0.38),
    ]
    d.polygon([(sc(px), sc(py)) for px, py in pts], fill=color)


def _draw_refresh(d, sc, width, pad, hovered):
    """右上角圆形刷新按钮，返回它的设计坐标矩形（用于点击命中）。"""
    r = 11
    cx, cy = width - pad - r, 10 + r
    glyph = TXT if hovered else SUB
    d.ellipse((sc(cx - r), sc(cy - r), sc(cx + r), sc(cy + r)),
              fill=CARD_HI if hovered else None,
              outline=LINE, width=max(1, int(sc(1))))
    drawn = False
    try:
        f = ImageFont.truetype(MDL2_FONT, int(sc(13)))
        bb = d.textbbox((0, 0), GLYPH_REFRESH, font=f)
        d.text((sc(cx) - (bb[0] + bb[2]) / 2.0, sc(cy) - (bb[1] + bb[3]) / 2.0),
               GLYPH_REFRESH, font=f, fill=glyph)
        drawn = True
    except Exception:
        drawn = False
    if not drawn:                       # 没有 MDL2 字体就手画一个圆弧箭头
        gr = 5.2
        d.arc((sc(cx - gr), sc(cy - gr), sc(cx + gr), sc(cy + gr)),
              start=45, end=315, fill=glyph, width=max(1, int(sc(1.5))))
        ax, ay = cx + gr * 0.55, cy - gr * 0.80
        d.polygon([(sc(ax - 3.0), sc(ay - 0.4)), (sc(ax + 2.6), sc(ay - 2.0)),
                   (sc(ax + 1.0), sc(ay + 3.2))], fill=glyph)
    return (cx - r, cy - r, cx + r, cy + r)


def render_panel(devices, swap_on, hover, width, height, scale=1.0, ss=3, hover_refresh=False, good=80, warn=20):
    """devices: [{'key','label','state','level'}]；hover: 菜单项文案或 None。"""
    S = scale * ss
    W, H = int(width * S), int(height * S)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def sc(v):
        return v * S

    # 卡片底 + 细边
    d.rounded_rectangle((sc(0.5), sc(0.5), W - sc(0.5), H - sc(0.5)),
                        radius=sc(14), fill=CARD, outline=LINE, width=max(1, int(sc(1))))

    P = 16
    TOP = 34                     # 顶部留一条给右上角的刷新按钮
    y = TOP
    refresh_rect = _draw_refresh(d, sc, width, P, hover_refresh)
    rows = []
    for dev in devices:
        state, level = dev["state"], dev["level"]
        is_mouse = dev["key"] == "mouse"
        # 图标
        ix, iy, isz = P + 2, y + 5, 15
        if is_mouse:
            d.ellipse((sc(ix + 1), sc(iy), sc(ix + isz - 1), sc(iy + isz)),
                      outline=SUB, width=max(1, int(sc(1.2))))
            d.line((sc(ix + isz / 2), sc(iy + 1), sc(ix + isz / 2), sc(iy + isz * 0.42)),
                   fill=SUB, width=max(1, int(sc(1.2))))
        else:
            d.rounded_rectangle((sc(ix), sc(iy + 2), sc(ix + isz), sc(iy + isz - 2)),
                                radius=sc(2), outline=SUB, width=max(1, int(sc(1.2))))
            step = isz / 4.2
            for r in range(2):
                for c in range(3):
                    kx = ix + 2.4 + c * step
                    ky = iy + 4.4 + r * step
                    d.rectangle((sc(kx), sc(ky), sc(kx + step * 0.6), sc(ky + step * 0.6)), fill=SUB)

        d.text((sc(P + 26), sc(y + 4)), dev["label"], font=_font(int(sc(13))), fill=TXT)
        last = dev.get("last")
        color, text = _trailing(state, level, last, good, warn)
        if text:
            fw = d.textlength(text, font=_font(int(sc(13)), bold=True))
            d.text((sc(width - P) - fw, sc(y + 4)), text,
                   font=_font(int(sc(13)), bold=True), fill=color)
            if dev.get("charging"):
                tx = (width - P) - fw / S
                _bolt(d, sc, tx - 15, y + 3.5, 12.5, color)

        # 进度条
        bx1, bx2 = P + 2, width - P
        by = y + 30
        d.rounded_rectangle((sc(bx1), sc(by), sc(bx2), sc(by + 6)), radius=sc(3), fill=TRACK)
        if state == "sleep":
            _sleep_bar(img, (sc(bx1), sc(by), sc(bx2), sc(by + 6)), sc(3), last, S)
        else:
            fill = _bar_fill(state, level, last)
            if fill:
                bw = max(6, (bx2 - bx1) * fill / 100.0)
                d.rounded_rectangle((sc(bx1), sc(by), sc(bx1 + bw), sc(by + 6)),
                                    radius=sc(3), fill=color)
        rows.append(dev["key"])
        y += 56

    # 分隔线
    d.line((sc(P), sc(y + 2), sc(width - P), sc(y + 2)), fill=LINE, width=max(1, int(sc(1))))

    # 开关行
    ty = y + 16
    toggle_rect = (P - 6, ty - 8, width - P + 6, ty + 30)
    d.rounded_rectangle((sc(toggle_rect[0]), sc(toggle_rect[1]), sc(toggle_rect[2]), sc(toggle_rect[3])),
                        radius=sc(10), fill=CARD_HI)
    d.text((sc(P + 10), sc(ty + 1)), "WASD / 方向键 互换", font=_font(int(sc(13))), fill=TXT)
    px, py = width - P - 50, ty - 2
    track = ACC if swap_on else OFF
    d.rounded_rectangle((sc(px), sc(py), sc(px + 50), sc(py + 24)), radius=sc(12), fill=track)
    knob_x = px + (26 if swap_on else 3)
    d.ellipse((sc(knob_x), sc(py + 3), sc(knob_x + 18), sc(py + 21)), fill=(255, 255, 255, 255))

    # 菜单
    my = ty + 42
    d.line((sc(P), sc(my - 10), sc(width - P), sc(my - 10)), fill=LINE, width=max(1, int(sc(1))))
    return img, my, {"toggle": toggle_rect, "refresh": refresh_rect}


def compose(devices, swap_on, hover_label, menu_labels, width, height, scale=1.0, ss=3,
            hover_refresh=False, good=80, warn=20):
    img, my, geo = render_panel(devices, swap_on, hover_label, width, height, scale, ss,
                                hover_refresh=hover_refresh, good=good, warn=warn)
    d = ImageDraw.Draw(img)
    S = scale * ss
    P = 16
    items = []
    for i, label in enumerate(menu_labels):
        ry = my + i * 24
        color = ACC if (hover_label and hover_label == label) else TXT
        d.text((S * (P + 4), S * (ry + 1)), label, font=_font(int(S * 13)), fill=color)
        items.append((P - 6, ry - 4, width - P + 6, ry + 20))
    out_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return img.resize(out_size, Image.LANCZOS), items, geo



def render_tooltip(devices, width, height, scale=1.0, ss=3, good=80, warn=20):
    """悬停提示：两行设备卡片；电量数字紧跟在设备名后面，进度条长度按百分比来。"""
    S = scale * ss
    W, H = int(width * S), int(height * S)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def sc(v):
        return v * S

    d.rounded_rectangle((sc(0.5), sc(0.5), W - sc(0.5), H - sc(10)), radius=sc(10),
                        fill=CARD, outline=LINE, width=max(1, int(sc(1))))
    cx = width * 0.22
    d.polygon([(sc(cx), sc(H - 10)), (sc(cx + 16), sc(H - 10)), (sc(cx + 8), sc(H - 1))], fill=CARD)

    f_name = _font(int(sc(13)))
    f_pct = _font(int(sc(13)), bold=True)
    track_w = width - 32 - 14          # 进度条轨道宽度
    y = 10
    for dev in devices:
        state, level = dev["state"], dev["level"]
        is_mouse = dev["key"] == "mouse"
        ix, iy, isz = 12, y + 4, 14
        if is_mouse:
            d.ellipse((sc(ix + 1), sc(iy), sc(ix + isz - 1), sc(iy + isz)), outline=SUB,
                      width=max(1, int(sc(1.1))))
            d.line((sc(ix + isz / 2), sc(iy + 1), sc(ix + isz / 2), sc(iy + isz * 0.42)),
                   fill=SUB, width=max(1, int(sc(1.1))))
        else:
            d.rounded_rectangle((sc(ix), sc(iy + 2), sc(ix + isz), sc(iy + isz - 2)), radius=sc(2),
                                outline=SUB, width=max(1, int(sc(1.1))))
            step = isz / 4.2
            for r in range(2):
                for c in range(3):
                    kx = ix + 2.4 + c * step
                    ky = iy + 3.4 + r * step
                    d.rectangle((sc(kx), sc(ky), sc(kx + step * 0.6), sc(ky + step * 0.6)), fill=SUB)

        d.text((sc(32), sc(y + 1)), dev["label"], font=f_name, fill=TXT)
        name_w = d.textlength(dev["label"], font=f_name)

        color, text = _trailing(state, level, dev.get("last"), good, warn)
        if text:
            # 数字紧跟设备名（留 10px 间距）
            tx = 32 + name_w / S + 10
            d.text((sc(tx), sc(y + 1)), text, font=f_pct, fill=color)
            if dev.get("charging"):
                tw = d.textlength(text, font=f_pct) / S
                _bolt(d, sc, tx + tw + 4, y + 2, 11, color)

        # 进度条：填充宽度严格按百分比
        bx = 32
        by = y + 23
        d.rounded_rectangle((sc(bx), sc(by), sc(bx + track_w), sc(by + 5)), radius=sc(2.5), fill=TRACK)
        if state == "sleep":
            _sleep_bar(img, (sc(bx), sc(by), sc(bx + track_w), sc(by + 5)), sc(2.5),
                       dev.get("last"), S)
        else:
            fill = _bar_fill(state, level, dev.get("last"))
            if fill:
                fill_w = track_w * fill / 100.0
                d.rounded_rectangle((sc(bx), sc(by), sc(bx + fill_w), sc(by + 5)),
                                    radius=sc(2.5), fill=color)
        y += 42

    out_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return img.resize(out_size, Image.LANCZOS)
