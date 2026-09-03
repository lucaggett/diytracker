import random as _r
from collections import defaultdict
from datetime import datetime, timedelta
from datetime import time as time_type
from pathlib import Path

from diytracker.models import Event
from diytracker.utils import clean_genre_tokens, parent_for_token, parent_genres

_GENRE_PALETTE = [
    (239, 68, 68),
    (249, 115, 22),
    (234, 179, 8),
    (34, 197, 94),
    (20, 184, 166),
    (6, 182, 212),
    (59, 130, 246),
    (139, 92, 246),
    (236, 72, 153),
    (132, 204, 22),
    (245, 158, 11),
    (16, 185, 129),
]


def _load_font(size, bold=False):
    from PIL import ImageFont

    candidates = (
        [
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    if Path("/System/Library/Fonts/Helvetica.ttc").exists():
        try:
            return ImageFont.truetype(
                "/System/Library/Fonts/Helvetica.ttc", size, index=0
            )
        except OSError:
            pass
    return ImageFont.load_default()


def generate_weekly_calendar_image(monday_date, parent_genre=None):
    from PIL import Image, ImageDraw

    GERMAN_MONTHS = [
        "JANUAR",
        "FEBRUAR",
        "MÄRZ",
        "APRIL",
        "MAI",
        "JUNI",
        "JULI",
        "AUGUST",
        "SEPTEMBER",
        "OKTOBER",
        "NOVEMBER",
        "DEZEMBER",
    ]
    GERMAN_DAYS = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]

    sunday_date = monday_date + timedelta(days=6)

    query = Event.query.filter(
        Event.date >= datetime.combine(monday_date, time_type.min),
        Event.date <= datetime.combine(sunday_date, time_type.max),
    )
    if parent_genre:
        query = query.filter(Event.parent_genres.like(f"%,{parent_genre},%"))
    events = query.order_by(Event.date.asc()).all()

    by_day = defaultdict(list)
    for e in events:
        by_day[e.date.weekday()].append(e)

    if parent_genre:
        # When filtered, the legend shows sub-genre tokens within the chosen
        # parent (e.g. 'Metalcore', 'Beatdown' for Hardcore) so unrelated
        # parents (Metal on a Hardcore-filtered image) don't get a chip.
        legend_genres = {}
        for e in events:
            for tok in clean_genre_tokens(e.genre):
                if parent_for_token(tok) == parent_genre and tok not in legend_genres:
                    legend_genres[tok] = _GENRE_PALETTE[
                        len(legend_genres) % len(_GENRE_PALETTE)
                    ]

        def event_primary_color(evt):
            for tok in clean_genre_tokens(evt.genre):
                if tok in legend_genres:
                    return legend_genres[tok]
            return (100, 100, 110)
    else:
        genre_color = {}
        for e in events:
            for p in parent_genres(e.genre):
                if p not in genre_color:
                    genre_color[p] = _GENRE_PALETTE[
                        len(genre_color) % len(_GENRE_PALETTE)
                    ]

        def event_primary_color(evt):
            for p in parent_genres(evt.genre):
                if p in genre_color:
                    return genre_color[p]
            return (100, 100, 110)

        legend_genres = {}
        for e in events:
            for p in parent_genres(e.genre):
                if p in genre_color and p not in legend_genres:
                    legend_genres[p] = genre_color[p]
                    break

    W, H = 1080, 1350
    BG = (12, 12, 13)
    RED = (208, 20, 20)
    RED_DARK = (158, 12, 12)
    WHITE = (246, 243, 238)
    LGRAY = (182, 178, 172)
    MGRAY = (98, 95, 90)
    SEP_COL = (72, 70, 76)
    MARGIN = 36
    USABLE_W = W - 2 * MARGIN

    def load_black(size):
        for path in [
            "/Library/Fonts/Arial Black.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]:
            if Path(path).exists():
                try:
                    from PIL import ImageFont

                    return ImageFont.truetype(path, size)
                except OSError:
                    pass
        return _load_font(size, bold=True)

    f_hero = load_black(108)
    f_datenum = load_black(82)
    f_month = load_black(92)
    f_pill = _load_font(23, bold=True)
    f_act = _load_font(21, bold=True)
    f_venue = _load_font(16)
    f_genre = _load_font(17)
    f_footer = _load_font(18, bold=True)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def wrap_text(text, font, max_w):
        if draw.textlength(text, font=font) <= max_w:
            return [text]
        words = text.split()
        lines, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if draw.textlength(test, font=font) <= max_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [text]

    def rough_vline(x, y0, y1):
        y = y0
        while y < y1:
            dot = _r.randint(3, 7)
            gap = _r.randint(4, 10)
            jx = _r.randint(-1, 1)
            w = _r.choice([1, 1, 1, 2])
            draw.line([x + jx, y, x + jx, min(y + dot, y1)], fill=SEP_COL, width=w)
            y += dot + gap

    def rough_hline(x0, x1, y):
        x = x0
        while x < x1:
            dot = _r.randint(3, 7)
            gap = _r.randint(4, 10)
            jy = _r.randint(-1, 1)
            w = _r.choice([1, 1, 1, 2])
            draw.line([x, y + jy, min(x + dot, x1), y + jy], fill=SEP_COL, width=w)
            x += dot + gap

    def rough_hrule(y, color, thickness=7):
        draw.rectangle([0, y, W, y + thickness], fill=color)
        for _ in range(W // 5):
            rx = _r.randint(0, W - 1)
            ry = _r.choice([y, y + thickness - 1])
            draw.point((rx, ry), fill=BG)

    def rough_pill(x0, y0, x1, y1, color):
        draw.rounded_rectangle([x0, y0, x1, y1], radius=5, fill=color)
        for _ in range((x1 - x0) // 3):
            px = _r.randint(x0 + 2, x1 - 3)
            py = _r.randint(y0 + 1, y1 - 2)
            draw.point((px, py), fill=RED_DARK)

    rough_hrule(0, RED, thickness=7)

    ev_y = 14
    hero_text = parent_genre.upper().replace("/", " ") if parent_genre else "EVENTS"
    draw.text((MARGIN, ev_y), hero_text, font=f_hero, fill=WHITE)

    dr = f"{monday_date.day}.-{sunday_date.day}."
    dr_w = draw.textlength(dr, font=f_datenum)
    draw.text((W - MARGIN - dr_w, ev_y + 14), dr, font=f_datenum, fill=WHITE)

    month_str = GERMAN_MONTHS[monday_date.month - 1]
    m_w = draw.textlength(month_str, font=f_month)
    draw.text((W - MARGIN - m_w, ev_y + 100), month_str, font=f_month, fill=RED)

    LEG_TOP = 240
    DOT_D = 15
    ITEM_W = USABLE_W // 3
    ROW_H = 30

    for idx, (genre, color) in enumerate(legend_genres.items()):
        row_i = idx // 3
        col_i = idx % 3
        lx = MARGIN + col_i * ITEM_W
        ly = LEG_TOP + row_i * ROW_H
        if ly + DOT_D > LEG_TOP + 4 * ROW_H:
            break
        draw.ellipse([lx, ly, lx + DOT_D, ly + DOT_D], fill=color)
        tw_g = ITEM_W - DOT_D - 10
        label = genre
        while label and draw.textlength(label, font=f_genre) > tw_g:
            label = label[:-1]
        draw.text((lx + DOT_D + 7, ly - 1), label, font=f_genre, fill=LGRAY)

    num_leg_rows = max(1, -(-len(legend_genres) // 3))
    HDR_BOT = LEG_TOP + num_leg_rows * ROW_H + 18
    rough_hrule(HDR_BOT, RED, thickness=7)

    GRID_TOP = HDR_BOT + 7 + 18
    FOOTER_H = 44
    GRID_BOT = H - FOOTER_H

    ALL_GROUPS = [[0, 1, 2, 3], [4], [5], [6]]
    col_groups = [g for g in ALL_GROUPS if any(by_day.get(d) for d in g)]

    if not col_groups:
        msg = "Keine Events diese Woche"
        mw = draw.textlength(msg, font=f_act)
        draw.text(
            ((W - mw) / 2, GRID_TOP + (GRID_BOT - GRID_TOP) // 2),
            msg,
            font=f_act,
            fill=MGRAY,
        )
    else:
        n_cols = len(col_groups)
        SEP_W = 4
        col_w = (USABLE_W - SEP_W * (n_cols - 1)) // n_cols

        for i in range(1, n_cols):
            sx = MARGIN + i * (col_w + SEP_W) - SEP_W
            rough_vline(sx, GRID_TOP, GRID_BOT)

        for col_i, day_group in enumerate(col_groups):
            cx = MARGIN + col_i * (col_w + SEP_W)
            cy = GRID_TOP
            tw = col_w - 28

            active_days = [d for d in day_group if by_day.get(d)]

            for day_i, weekday in enumerate(active_days):
                day_events = by_day[weekday]
                day_date = monday_date + timedelta(days=weekday)

                if day_i > 0:
                    rough_hline(cx, cx + col_w, cy)
                    cy += 16

                pill_label = f"{GERMAN_DAYS[weekday]} {day_date.strftime('%d.%m.')}"
                pill_h = 34
                pill_pad = 12
                pill_w = min(
                    int(draw.textlength(pill_label, font=f_pill)) + pill_pad * 2,
                    col_w - 4,
                )
                rough_pill(cx + 2, cy, cx + 2 + pill_w, cy + pill_h, RED)
                draw.text(
                    (cx + 2 + pill_pad, cy + 6), pill_label, font=f_pill, fill=WHITE
                )
                cy += pill_h + 10

                for evt in day_events:
                    if cy > GRID_BOT - 28:
                        draw.text((cx + 20, cy), "…", font=f_venue, fill=MGRAY)
                        break

                    color = event_primary_color(evt)
                    DOT_D_EV = 14
                    draw.ellipse(
                        [cx + 4, cy + 4, cx + 4 + DOT_D_EV, cy + 4 + DOT_D_EV],
                        fill=color,
                    )

                    tx = cx + DOT_D_EV + 12
                    acts_raw = (evt.acts or evt.name or "").strip()
                    act_lines = [
                        a.strip()
                        for a in acts_raw.replace("\n", ",").split(",")
                        if a.strip()
                    ] or [evt.name or "?"]

                    for act in act_lines:
                        for line in wrap_text(act, f_act, tw):
                            if cy > GRID_BOT - 26:
                                break
                            draw.text((tx, cy), line, font=f_act, fill=WHITE)
                            cy += 25

                    if evt.venue and cy <= GRID_BOT - 20:
                        vstr = evt.venue.name
                        if evt.venue.city:
                            vstr += f", {evt.venue.city}"
                        for line in wrap_text(vstr, f_venue, tw)[:1]:
                            draw.text((tx, cy), line, font=f_venue, fill=MGRAY)
                        cy += 20

                    cy += 10

    year_str = str(monday_date.year)
    yw = draw.textlength(year_str, font=f_footer)
    draw.text(((W - yw) / 2, H - FOOTER_H + 12), year_str, font=f_footer, fill=MGRAY)

    light_dots = [(_r.randint(0, W - 1), _r.randint(0, H - 1)) for _ in range(16000)]
    draw.point(light_dots, fill=(32, 30, 35))
    dark_dots = [(_r.randint(0, W - 1), _r.randint(0, H - 1)) for _ in range(8000)]
    draw.point(dark_dots, fill=(4, 4, 5))

    return img
