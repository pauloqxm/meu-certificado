"""Carrega participantes a partir da planilha publicada no Google Sheets (CSV)."""

from __future__ import annotations

import csv
import io
import time
import unicodedata
from typing import Any

import httpx

DEFAULT_SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSIm01pwU110987AqjvaenSke6cHpDHlWgb1VMGm0h6PrjD5qQpKjzdm7lTDCSqUXYrGj-BvYKzol3s/pub"
    "?output=csv"
)

_CACHE_ROWS: list[dict[str, str]] | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL_SEC = 90.0


def _norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return s.strip().lower()


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def normalize_email(s: str) -> str:
    """E-mail normalizado para chaves (registo / validação)."""
    return _norm_email(s)


def normalize_telefone(s: str) -> str:
    """Telefone normalizado apenas com dígitos (ex.: '(88) 98142-8918' -> '88981428918')."""
    return "".join(c for c in (s or "") if c.isdigit())


def telefone_digitos_iguais(telefone_lido: str, telefone_planilha: str) -> bool:
    """Exige o mesmo número de dígitos e a mesma sequência (só após normalizar para dígitos)."""
    a = normalize_telefone(telefone_lido)
    b = normalize_telefone(telefone_planilha)
    if not a or not b:
        return False
    return a == b


def normalize_evento(s: str) -> str:
    """Comparação estável entre células (espaços, quebras de linha)."""
    t = unicodedata.normalize("NFKC", s or "")
    t = " ".join(t.split())
    return t.strip().lower()


def _decode_csv_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _row_to_participant(row: dict[str, Any]) -> dict[str, str]:
    """Mapeia cabeçalhos da planilha para chaves fixas."""
    m: dict[str, str] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = _norm_key(str(k))
        val = "" if v is None else str(v).strip()
        m[key] = val

    def pick(*candidates: str) -> str:
        for c in candidates:
            t = _norm_key(c)
            if t in m and m[t]:
                return m[t]
        return ""

    return {
        "ord": pick("ord.", "ord"),
        "data": pick("data"),
        "evento": pick("evento"),
        "local": pick("local"),
        "carga_horaria": pick("carga_horária", "carga_horaria", "carga horária"),
        "nome": pick("nome"),
        "email": pick("e-mail", "email", "e_mail"),
        "telefone": pick("telefone", "tel"),
        "link": pick("link"),
        "template": pick("template"),
    }


def fetch_sheet_rows(csv_url: str = DEFAULT_SHEET_CSV_URL) -> list[dict[str, str]]:
    global _CACHE_ROWS, _CACHE_AT
    now = time.monotonic()
    if _CACHE_ROWS is not None and (now - _CACHE_AT) < _CACHE_TTL_SEC:
        return _CACHE_ROWS

    try:
        with httpx.Client(timeout=45.0, follow_redirects=True) as client:
            r = client.get(csv_url)
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError("Não foi possível carregar a planilha publicada. Tente novamente em instantes.") from e

    text = _decode_csv_bytes(r.content)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for raw in reader:
        if not raw:
            continue
        p = _row_to_participant(raw)
        if p.get("email"):
            rows.append(p)

    _CACHE_ROWS = rows
    _CACHE_AT = now
    return rows


def list_eventos(csv_url: str = DEFAULT_SHEET_CSV_URL) -> list[dict[str, str]]:
    """Um item por evento distinto (primeira linha define data/local/template)."""
    seen: dict[str, dict[str, str]] = {}
    for p in fetch_sheet_rows(csv_url):
        ev = (p.get("evento") or "").strip()
        if not ev:
            continue
        key = normalize_evento(ev)
        if key in seen:
            continue
        seen[key] = {
            "evento": ev,
            "data": p.get("data") or "",
            "local": p.get("local") or "",
            "carga_horaria": p.get("carga_horaria") or "",
            "template": p.get("template") or "",
        }
    return sorted(seen.values(), key=lambda x: (x["evento"] or "").lower())


def find_participant_by_email(
    email: str,
    telefone: str | None = None,
    evento: str | None = None,
    csv_url: str = DEFAULT_SHEET_CSV_URL,
) -> dict[str, str] | None:
    target = _norm_email(email)
    if not target:
        return None
    ev_key = normalize_evento(evento) if evento else None
    for p in fetch_sheet_rows(csv_url):
        if _norm_email(p.get("email", "")) != target:
            continue
        if ev_key is not None:
            if normalize_evento(p.get("evento", "")) != ev_key:
                continue
        if telefone is not None:
            if not telefone_digitos_iguais(telefone, p.get("telefone", "")):
                continue
        return p
    return None


def find_event_meta(evento: str, csv_url: str = DEFAULT_SHEET_CSV_URL) -> dict[str, str] | None:
    """Metadados do evento (primeira linha com esse Evento)."""
    key = normalize_evento(evento)
    if not key:
        return None
    for p in fetch_sheet_rows(csv_url):
        if normalize_evento(p.get("evento", "")) == key:
            return {
                "evento": (p.get("evento") or "").strip(),
                "data": p.get("data") or "",
                "local": p.get("local") or "",
                "carga_horaria": p.get("carga_horaria") or "",
                "template": p.get("template") or "",
            }
    return None
