"""Word-level captions written as ASS subtitles, burned in by libass.

Styles are built from the presets in presets.py. Pure Python, so the
generated file can be unit-tested without ffmpeg.
"""
from __future__ import annotations

from .models import Segment
from .presets import ACCENT_COLORS

WHITE = "#FFFFFF"
BLACK = "#000000"

# Each style: font size (for a 1080px-wide frame), bold, border style,
# outline width, shadow, and whether it uses the accent colour.
STYLES: dict[str, dict] = {
    "highlight": {"size": 70, "bold": 1, "border": 1, "outline": 6, "shadow": 1, "active": True},
    "classic": {"size": 66, "bold": 1, "border": 1, "outline": 5, "shadow": 0},
    "box": {"size": 58, "bold": 1, "border": 3, "outline": 16, "shadow": 0, "box": True},
    "neon": {"size": 66, "bold": 1, "border": 1, "outline": 7, "shadow": 0, "accent_outline": True},
    "minimal": {"size": 50, "bold": 0, "border": 1, "outline": 2, "shadow": 0},
}

_ALIGN = {"top": 8, "middle": 5, "bottom": 2}


def _ass_hex(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS byte order)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _tag(hex_color: str) -> str:
    return _ass_hex(hex_color) + "&"


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(token: str) -> str:
    return token.replace("\\", "").replace("{", "(").replace("}", ")")


def _collect_words(units: list[Segment], start: float, end: float) -> list[tuple[float, float, str]]:
    """Words inside [start, end], with times relative to the clip start."""
    out: list[tuple[float, float, str]] = []
    for u in units:
        if u.end <= start or u.start >= end:
            continue
        words = u.words or _even_split(u)
        for s, e, tok in words:
            if e <= start or s >= end:
                continue
            out.append((max(s, start) - start, min(e, end) - start, str(tok)))
    return out


def _even_split(u: Segment) -> list[tuple[float, float, str]]:
    """Fallback for transcripts saved before word timings existed."""
    toks = u.text.split()
    if not toks:
        return []
    step = (u.end - u.start) / len(toks)
    return [(u.start + i * step, u.start + (i + 1) * step, t) for i, t in enumerate(toks)]


def group_phrases(words: list[tuple[float, float, str]], max_words: int = 3,
                  max_gap: float = 0.6) -> list[list[tuple[float, float, str]]]:
    """Group words into on-screen phrases of up to max_words words."""
    phrases: list[list[tuple[float, float, str]]] = []
    cur: list[tuple[float, float, str]] = []
    for w in words:
        if cur:
            gap = w[0] - cur[-1][1]
            ends_sentence = cur[-1][2].endswith((".", "?", "!", "…"))
            if len(cur) >= max_words or gap > max_gap or ends_sentence:
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return phrases


def build_ass(units: list[Segment], window_start: float, window_end: float, *,
              width: int, height: int, style: str = "highlight", position: str = "bottom",
              uppercase: bool = False, accent: str = "yellow", bottom_margin: int = 330) -> str:
    """Return the full ASS file text for one clip."""
    st = STYLES.get(style, STYLES["highlight"])
    scale = width / 1080
    accent_hex = ACCENT_COLORS.get(accent, ACCENT_COLORS["yellow"])

    if position == "bottom":
        margin_v = round(bottom_margin * height / 1920)
    elif position == "top":
        margin_v = round(220 * height / 1920)
    else:
        margin_v = 0
    align = _ALIGN.get(position, 2)

    outline_color = _ass_hex(accent_hex) if st.get("accent_outline") else _ass_hex(BLACK)
    back = _ass_hex(BLACK, 0x66) if st.get("box") else _ass_hex(BLACK, 0x00)
    font_size = round(st["size"] * scale)
    outline = round(st["outline"] * scale) if st["border"] == 1 else round(st["outline"] * scale)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{font_size},{_ass_hex(WHITE)},&H000000FF,{outline_color},{back},"
        f"{st['bold'] and -1 or 0},0,0,0,100,100,0,0,{st['border']},{outline},{st['shadow']},"
        f"{align},60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[tuple[float, float, str]] = []
    for ph in group_phrases(_collect_words(units, window_start, window_end)):
        if st.get("active"):
            # Word highlight: one event per word, the spoken word in accent colour.
            for idx, (s, e, _tok) in enumerate(ph):
                end = ph[idx + 1][0] if idx + 1 < len(ph) else e
                if end <= s:
                    end = s + 0.05
                parts = []
                for j, (_, _, tok) in enumerate(ph):
                    text = _esc(tok.upper() if uppercase else tok)
                    if j == idx:
                        parts.append("{\\c" + _tag(accent_hex) + "}" + text + "{\\c" + _tag(WHITE) + "}")
                    else:
                        parts.append(text)
                events.append((s, end, " ".join(parts)))
        else:
            text = " ".join(_esc(tok.upper() if uppercase else tok) for _, _, tok in ph)
            end = ph[-1][1] if ph[-1][1] > ph[0][0] else ph[0][0] + 0.5
            events.append((ph[0][0], end, text))

    for s, e, text in events:
        lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"
