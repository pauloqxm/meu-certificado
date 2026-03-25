"""Gera imagem do certificado sobre o template PNG e exporta PDF."""

from __future__ import annotations

import os
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
import qrcode
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
PROJECT_ROOT = BASE_DIR.parent

DEFAULT_TEMPLATE_NAMES = ("template_geoprocessamento.png",)

FONT_PATH = STATIC_DIR / "fonts" / "DejaVuSans.ttf"
FONT_BOLD_PATH = STATIC_DIR / "fonts" / "DejaVuSans-Bold.ttf"

LINE_SPACING_BODY = 2.0

# Horário de Fortaleza (Ceará); IANA America/Fortaleza — mesmo fuso que Brasília.
CERT_TIMEZONE = ZoneInfo("America/Fortaleza")

# Página pública de validação (texto no certificado abaixo do código). Sobrescreva com PUBLIC_VALIDAR_URL.
_DEFAULT_PUBLIC_VALIDAR = "https://meu-certificado.up.railway.app/validar"


def public_validar_page_url() -> str:
    return (os.getenv("PUBLIC_VALIDAR_URL") or _DEFAULT_PUBLIC_VALIDAR).strip() or _DEFAULT_PUBLIC_VALIDAR


def resolve_template_file(basename: str | None) -> Path:
    """
    Resolve o ficheiro PNG do template. `basename` vem da coluna Template (só nome do ficheiro).
    Procura em: static/, raiz do projeto/, static/templates/.
    """
    name: str | None = None
    if basename and str(basename).strip():
        raw = str(basename).strip()
        name = Path(raw).name
        if name in (".", "..", ""):
            name = None

    search_dirs: list[Path] = [STATIC_DIR, PROJECT_ROOT, STATIC_DIR / "templates"]

    if name:
        for folder in search_dirs:
            cand = folder / name
            if cand.is_file():
                return cand
        raise FileNotFoundError(
            f'Template "{name}" não encontrado. Coloque o PNG em {STATIC_DIR}, em {STATIC_DIR / "templates"} ou na raiz do projeto.'
        )

    for n in DEFAULT_TEMPLATE_NAMES:
        for folder in (STATIC_DIR, PROJECT_ROOT):
            cand = folder / n
            if cand.is_file():
                return cand

    raise FileNotFoundError(
        f"Nenhum template padrão encontrado. Coloque um PNG em {STATIC_DIR} ou na raiz do projeto."
    )


def _resolve_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    order = []
    if bold:
        order.append(FONT_BOLD_PATH)
    order.append(FONT_PATH)
    for p in order:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _word_width(draw: ImageDraw.ImageDraw, word: str, bold: bool, font_r: ImageFont.ImageFont, font_b: ImageFont.ImageFont) -> int:
    font = font_b if bold else font_r
    bbox = draw.textbbox((0, 0), word, font=font)
    return bbox[2] - bbox[0]


def _space_width(draw: ImageDraw.ImageDraw, font_r: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), " ", font=font_r)
    return max(1, bbox[2] - bbox[0])


def _no_space_before_token(word: str) -> bool:
    """Vírgula/ponto/etc. como token separado: sem espaço entre a palavra anterior e a pontuação."""
    if not word:
        return True
    w = word.strip()
    if not w:
        return True
    if len(w) == 1 and w in ",.;:!?…":
        return True
    return False


def _inter_word_space_slots(line_tokens: list[tuple[str, bool]]) -> int:
    n = len(line_tokens)
    if n < 2:
        return 0
    return sum(
        1
        for i in range(n - 1)
        if not _no_space_before_token(line_tokens[i + 1][0])
    )


def _line_width(
    line_tokens: list[tuple[str, bool]],
    draw: ImageDraw.ImageDraw,
    font_r: ImageFont.ImageFont,
    font_b: ImageFont.ImageFont,
) -> float:
    if not line_tokens:
        return 0.0
    sw = _space_width(draw, font_r)
    total = sum(_word_width(draw, w, b, font_r, font_b) for w, b in line_tokens)
    total += sw * _inter_word_space_slots(line_tokens)
    return float(total)


def build_body_word_tokens(p: dict[str, str]) -> list[tuple[str, bool]]:
    """Palavras do corpo: (texto, negrito). Nome e carga horária em negrito."""
    nome = ((p.get("nome") or "").strip() or "—").replace("\n", " ")
    evento = ((p.get("evento") or "").strip()).replace("\n", " ")
    local = ((p.get("local") or "").strip()).replace("\n", " ")
    data = ((p.get("data") or "").strip()).replace("\n", " ")
    carga = ((p.get("carga_horaria") or "").strip()).replace("\n", " ")

    runs: list[tuple[str, bool]] = [
        ("Certificamos que ", False),
        (nome, True),
        (" participou do evento ", False),
        (evento, True),
        (", realizado em ", False),
        (local, False),
        (" no dia ", False),
        (data, False),
        (", com carga horária total de ", False),
        (carga, True),
        (".", False),
    ]

    tokens: list[tuple[str, bool]] = []
    for text, bold in runs:
        if not text:
            continue
        parts = text.split()
        for w in parts:
            if w:
                tokens.append((w, bold))
    return tokens


def _wrap_tokens(
    tokens: list[tuple[str, bool]],
    draw: ImageDraw.ImageDraw,
    font_r: ImageFont.ImageFont,
    font_b: ImageFont.ImageFont,
    max_width: float,
) -> list[list[tuple[str, bool]]]:
    lines: list[list[tuple[str, bool]]] = []
    i = 0
    n = len(tokens)
    while i < n:
        line: list[tuple[str, bool]] = []
        while i < n:
            trial = line + [tokens[i]]
            tw = _line_width(trial, draw, font_r, font_b)
            if tw <= max_width or not line:
                line.append(tokens[i])
                i += 1
                if tw > max_width and len(line) == 1:
                    break
            else:
                break
        lines.append(line)
    return lines


def _body_line_step(
    draw: ImageDraw.ImageDraw,
    font_r: ImageFont.ImageFont,
    font_b: ImageFont.ImageFont,
    line_spacing: float,
) -> int:
    br = draw.textbbox((0, 0), "Ágf", font=font_r)
    bb = draw.textbbox((0, 0), "Ágf", font=font_b)
    h = max(br[3] - br[1], bb[3] - bb[1])
    return max(1, int(h * line_spacing))


def _municipio_from_local(local: str) -> str:
    """Município = texto antes da primeira vírgula (ex.: 'Quixeramobim, na FATEC...' → Quixeramobim)."""
    s = " ".join((local or "").replace("\n", " ").split()).strip()
    if not s:
        return ""
    before_comma = s.split(",", maxsplit=1)[0].strip()
    before_comma = re.sub(r"\s*[-–/]\s*[A-Z]{2}\s*$", "", before_comma, flags=re.I).strip()
    return before_comma


def _format_generation_stamp(participant: dict[str, str] | None) -> str:
    dt = datetime.now(CERT_TIMEZONE)
    ts = dt.strftime("%d/%m/%Y às %H:%M:%S")
    mun = _municipio_from_local((participant or {}).get("local") or "")
    if mun:
        return f"Certificado gerado em {ts} — {mun} (fuso horário Fortaleza/CE)"
    return f"Certificado gerado em {ts} — Fortaleza/CE"


def _font_line_height(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
    b = draw.textbbox((0, 0), "Ág", font=font)
    return max(1, b[3] - b[1])


def _draw_justified_line(
    draw: ImageDraw.ImageDraw,
    line_tokens: list[tuple[str, bool]],
    y: float,
    margin_left: float,
    max_width: float,
    font_r: ImageFont.ImageFont,
    font_b: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    justify: bool,
) -> None:
    n = len(line_tokens)
    if n == 0:
        return
    space_w = _space_width(draw, font_r)
    widths = [_word_width(draw, w, b, font_r, font_b) for w, b in line_tokens]
    slots = _inter_word_space_slots(line_tokens)
    content = sum(widths) + slots * space_w
    extra = max_width - content
    if justify and slots > 0 and extra > 0:
        gap_extra = extra / slots
    else:
        gap_extra = 0.0

    x = margin_left
    for i, ((word, bold), tw) in enumerate(zip(line_tokens, widths)):
        font = font_b if bold else font_r
        draw.text((x, y), word, font=font, fill=fill, anchor="ls")
        x += tw
        if i < n - 1 and not _no_space_before_token(line_tokens[i + 1][0]):
            x += space_w + gap_extra


def _draw_body_paragraph(
    draw: ImageDraw.ImageDraw,
    tokens: list[tuple[str, bool]],
    margin_left: float,
    max_width: float,
    start_y: float,
    font_r: ImageFont.ImageFont,
    font_b: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    if not tokens:
        return
    lines = _wrap_tokens(tokens, draw, font_r, font_b, max_width)
    line_step = _body_line_step(draw, font_r, font_b, LINE_SPACING_BODY)
    y = start_y
    for li, line_toks in enumerate(lines):
        last_line = li == len(lines) - 1
        _draw_justified_line(
            draw,
            line_toks,
            y,
            margin_left,
            max_width,
            font_r,
            font_b,
            fill,
            justify=not last_line,
        )
        y += line_step


def _build_qr_image(data: str, target_size_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img_qr.resize((target_size_px, target_size_px), Image.Resampling.NEAREST)


def render_certificate_png(
    participant: dict[str, str],
    codigo_verificacao: str,
    verification_url: str | None = None,
) -> bytes:
    path = resolve_template_file(participant.get("template") or None)

    img = Image.open(path).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    nome = (participant.get("nome") or "").strip() or "—"
    tokens = build_body_word_tokens(participant)

    name_size = max(28, int(h * 0.038))
    body_size = max(24, int(h * 0.027))
    code_size = max(13, int(h * 0.017))
    font_name = _resolve_font(name_size, bold=True)
    font_body = _resolve_font(body_size, bold=False)
    font_body_bold = _resolve_font(body_size, bold=True)
    font_code = _resolve_font(code_size, bold=False)

    fill = (0, 0, 0)
    margin_x = w * 0.11
    max_text_w = w - 2 * margin_x

    line_step_body = _body_line_step(draw, font_body, font_body_bold, LINE_SPACING_BODY)
    # Subir todo o bloco de texto (nome, corpo, rodapé, QR) duas linhas do corpo
    offset_up = 2 * line_step_body

    name_y = int(h * 0.36) - offset_up + line_step_body
    draw.text((w / 2, name_y), nome, fill=fill, font=font_name, anchor="mm")

    # Duas linhas (entrelinha do corpo) entre o nome e o texto justificado
    start_body_y = float(int(h * 0.42)) + 2 * line_step_body - offset_up
    _draw_body_paragraph(draw, tokens, margin_x, max_text_w, start_body_y, font_body, font_body_bold, fill)

    codigo = (codigo_verificacao or "").strip()
    code_y: float | None = None
    if codigo:
        label = f"Código de verificação: {codigo}"
        # Sobe uma linha (altura da fonte do código) + bloco global
        code_y = float(int(h * 0.82) - _font_line_height(draw, font_code) - offset_up)
        draw.text((w / 2, code_y), label, fill=fill, font=font_code, anchor="mm")

        url_txt = public_validar_page_url()
        url_size = max(11, int(h * 0.013))
        font_url = _resolve_font(url_size, bold=False)
        gap = int(h * 0.012)
        link_y = code_y + _font_line_height(draw, font_code) + gap
        draw.text((w / 2, link_y), url_txt, fill=fill, font=font_url, anchor="mm")

    if verification_url:
        qr_size = max(96, int(h * 0.15))
        qr_img = _build_qr_image(verification_url, qr_size)
        qr_x = int(w * 0.79)
        if code_y is not None:
            qr_y = int(round(code_y - qr_size / 2))
        else:
            qr_y = int(h * 0.61) - offset_up
        img.paste(qr_img, (qr_x, qr_y))
        legend = "Validar QR"
        draw.text((qr_x + qr_size / 2, qr_y + qr_size + int(h * 0.014)), legend, fill=fill, font=font_code, anchor="mm")

    stamp_text = _format_generation_stamp(participant)
    stamp_size = max(11, int(h * 0.012))
    font_stamp = _resolve_font(stamp_size, bold=False)
    pad_bottom = int(h * 0.018)
    draw.text(
        (margin_x, h - pad_bottom),
        stamp_text,
        fill=(72, 72, 72),
        font=font_stamp,
        anchor="lb",
    )

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def png_bytes_to_pdf(png_bytes: bytes) -> bytes:
    im = Image.open(BytesIO(png_bytes))
    w_px, h_px = im.size
    w_pt = w_px * 72.0 / 96.0
    h_pt = h_px * 72.0 / 96.0
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(w_pt, h_pt))
    c.drawImage(ImageReader(BytesIO(png_bytes)), 0, 0, width=w_pt, height=h_pt, mask="auto")
    c.showPage()
    c.save()
    return out.getvalue()


def render_certificate_pdf(
    participant: dict[str, str],
    codigo_verificacao: str,
    verification_url: str | None = None,
) -> bytes:
    png = render_certificate_png(participant, codigo_verificacao, verification_url=verification_url)
    return png_bytes_to_pdf(png)
