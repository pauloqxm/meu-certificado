"""Registo de certificados emitidos em SQLite (código de verificação único)."""

from __future__ import annotations

import re
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from pathlib import Path

from app.services.sheets import normalize_email, normalize_evento

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "certificados.db"

# Sem O/0, I/1, L para reduzir ambiguidade na leitura do código.
_ALFABETO = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "O0I1L")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS certificado_registro (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL UNIQUE,
                email_norm TEXT NOT NULL,
                evento_norm TEXT NOT NULL,
                nome TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                evento TEXT NOT NULL DEFAULT '',
                data_evento TEXT NOT NULL DEFAULT '',
                local TEXT NOT NULL DEFAULT '',
                carga_horaria TEXT NOT NULL DEFAULT '',
                emitido_em TEXT NOT NULL,
                UNIQUE (email_norm, evento_norm)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_certificado_codigo ON certificado_registro (codigo)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_certificado_email_evento ON certificado_registro (email_norm, evento_norm)"
        )
        conn.commit()


def _gerar_codigo() -> str:
    raw = "".join(secrets.choice(_ALFABETO) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def normalizar_codigo_digitado(s: str) -> str | None:
    """Aceita com ou sem hífens / espaços; devolve forma CANÓNICA ou None."""
    raw = re.sub(r"[^A-Za-z0-9]", "", (s or "").upper())
    if len(raw) != 12:
        return None
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def obter_ou_criar_codigo(participant: dict[str, str]) -> str:
    """
    Um código por par (e-mail, evento). Reemissões reutilizam o mesmo código
    e atualizam a data de emissão.
    """
    init_db()
    email_norm = normalize_email(participant.get("email", ""))
    evento_norm = normalize_evento(participant.get("evento", ""))
    if not email_norm or not evento_norm:
        raise ValueError("Participante sem e-mail ou evento para registo.")

    nome = participant.get("nome") or ""
    email = (participant.get("email") or "").strip()
    evento = (participant.get("evento") or "").strip()
    data_evento = participant.get("data") or ""
    local = participant.get("local") or ""
    carga = participant.get("carga_horaria") or ""

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT codigo FROM certificado_registro WHERE email_norm = ? AND evento_norm = ?",
            (email_norm, evento_norm),
        ).fetchone()
        if row:
            codigo = row["codigo"]
            conn.execute(
                """UPDATE certificado_registro SET emitido_em = ?, nome = ?, email = ?, evento = ?,
                   data_evento = ?, local = ?, carga_horaria = ? WHERE codigo = ?""",
                (_utc_now_iso(), nome, email, evento, data_evento, local, carga, codigo),
            )
            conn.commit()
            return codigo

        for _ in range(30):
            codigo = _gerar_codigo()
            try:
                conn.execute(
                    """INSERT INTO certificado_registro (
                        codigo, email_norm, evento_norm, nome, email, evento,
                        data_evento, local, carga_horaria, emitido_em
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        codigo,
                        email_norm,
                        evento_norm,
                        nome,
                        email,
                        evento,
                        data_evento,
                        local,
                        carga,
                        _utc_now_iso(),
                    ),
                )
                conn.commit()
                return codigo
            except sqlite3.IntegrityError:
                conn.rollback()
                # colisão rara em `codigo`; tenta outro. Se for UNIQUE(email_norm, evento_norm), relê.
                row2 = conn.execute(
                    "SELECT codigo FROM certificado_registro WHERE email_norm = ? AND evento_norm = ?",
                    (email_norm, evento_norm),
                ).fetchone()
                if row2:
                    codigo_ex = row2["codigo"]
                    conn.execute(
                        """UPDATE certificado_registro SET emitido_em = ?, nome = ?, email = ?, evento = ?,
                           data_evento = ?, local = ?, carga_horaria = ? WHERE codigo = ?""",
                        (_utc_now_iso(), nome, email, evento, data_evento, local, carga, codigo_ex),
                    )
                    conn.commit()
                    return codigo_ex
                continue

    raise RuntimeError("Não foi possível gerar código de verificação único.")


def buscar_por_codigo(codigo_digitado: str) -> dict[str, str] | None:
    init_db()
    canon = normalizar_codigo_digitado(codigo_digitado)
    if not canon:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM certificado_registro WHERE codigo = ?",
            (canon,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        return {
            "codigo": d["codigo"],
            "nome": d["nome"] or "",
            "email": d["email"] or "",
            "evento": d["evento"] or "",
            "data_evento": d["data_evento"] or "",
            "local": d["local"] or "",
            "carga_horaria": d["carga_horaria"] or "",
            "emitido_em": d["emitido_em"] or "",
        }
