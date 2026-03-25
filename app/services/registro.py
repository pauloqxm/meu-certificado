"""Registo de certificados emitidos em SQLite (código de verificação único)."""

from __future__ import annotations

import csv
import os
import re
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.sheets import normalize_email, normalize_evento, normalize_telefone

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


def _resolve_db_path() -> Path:
    """
    Caminho da base de dados.
    - Local: usa data/certificados.db
    - Railway/produção: definir CERT_DB_PATH para um volume persistente
      (ex.: /data/certificados.db).
    """
    env_path = (os.getenv("CERT_DB_PATH") or "").strip()
    if env_path:
        return Path(env_path)
    return PROJECT_ROOT / "data" / "certificados.db"


DB_PATH = _resolve_db_path()

# Exibição na validação (mesmo fuso do carimbo do certificado).
_EMITIDO_EM_EXIBICAO_TZ = ZoneInfo("America/Fortaleza")

# Sem O/0, I/1, L para reduzir ambiguidade na leitura do código.
_ALFABETO = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "O0I1L")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_emitido_em_utc(raw: str) -> datetime | None:
    """Interpreta o valor guardado na BD (ISO com Z ou offset)."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _emitido_em_texto_fortaleza(raw: str) -> str:
    """Data/hora de emissão para mostrar ao utilizador (Fortaleza/CE)."""
    dt = _parse_emitido_em_utc(raw)
    if dt is None:
        return (raw or "").strip()
    local = dt.astimezone(_EMITIDO_EM_EXIBICAO_TZ)
    return local.strftime("%d/%m/%Y às %H:%M:%S (Fortaleza/CE)")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        # Tenta criar a tabela (caso ainda não exista). Em bases antigas, migraremos
        # as colunas novas com ALTER TABLE logo a seguir.
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
                telefone_norm TEXT NOT NULL DEFAULT '',
                telefone TEXT NOT NULL DEFAULT '',
                carga_horaria TEXT NOT NULL DEFAULT '',
                emitido_em TEXT NOT NULL,
                UNIQUE (email_norm, evento_norm)
            )
            """
        )
        # Migração (compatível com versões anteriores da tabela):
        # - adiciona `telefone_norm` e `telefone` se faltarem.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(certificado_registro)").fetchall()}
        if "telefone_norm" not in cols:
            conn.execute(
                "ALTER TABLE certificado_registro ADD COLUMN telefone_norm TEXT NOT NULL DEFAULT ''"
            )
        if "telefone" not in cols:
            conn.execute("ALTER TABLE certificado_registro ADD COLUMN telefone TEXT NOT NULL DEFAULT ''")

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


def mensagem_se_telefone_nao_confere_bd(email: str, evento: str, telefone_digitado: str) -> str | None:
    """
    Se já existe registo na BD para (e-mail, evento), o telefone digitado tem de ser
    idêntico (só dígitos) ao `telefone_norm` guardado — evita reemitir com outro número.
    Registos antigos com `telefone_norm` vazio não bloqueiam (preenche na próxima emissão).
    """
    init_db()
    email_norm = normalize_email(email)
    evento_norm = normalize_evento(evento)
    tel_pedido = normalize_telefone(telefone_digitado)
    if not email_norm or not evento_norm or not tel_pedido:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT telefone_norm FROM certificado_registro WHERE email_norm = ? AND evento_norm = ?",
            (email_norm, evento_norm),
        ).fetchone()
        if not row:
            return None
        db_tel = (row[0] or "").strip()
        if not db_tel:
            return None
        if tel_pedido != db_tel:
            return (
                "Este e-mail já emitiu certificado neste evento. "
                "O telefone tem de ser exatamente o mesmo registado na base de dados na primeira emissão."
            )
    return None


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
    telefone = (participant.get("telefone") or "").strip()
    telefone_norm = normalize_telefone(telefone)
    if not telefone_norm:
        raise ValueError("Participante sem telefone para registo.")
    carga = participant.get("carga_horaria") or ""

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT codigo, telefone_norm FROM certificado_registro WHERE email_norm = ? AND evento_norm = ?",
            (email_norm, evento_norm),
        ).fetchone()
        if row:
            codigo = row["codigo"]
            db_tel = (row["telefone_norm"] or "").strip()
            if db_tel and telefone_norm != db_tel:
                raise ValueError(
                    "O telefone não coincide com o registo já existente para este e-mail e evento."
                )
            conn.execute(
                """UPDATE certificado_registro SET emitido_em = ?, nome = ?, email = ?, evento = ?,
                   data_evento = ?, local = ?, telefone_norm = ?, telefone = ?, carga_horaria = ? WHERE codigo = ?""",
                (
                    _utc_now_iso(),
                    nome,
                    email,
                    evento,
                    data_evento,
                    local,
                    telefone_norm,
                    telefone,
                    carga,
                    codigo,
                ),
            )
            conn.commit()
            return codigo

        for _ in range(30):
            codigo = _gerar_codigo()
            try:
                conn.execute(
                    """INSERT INTO certificado_registro (
                        codigo, email_norm, evento_norm, nome, email, evento,
                        data_evento, local, telefone_norm, telefone, carga_horaria, emitido_em
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        codigo,
                        email_norm,
                        evento_norm,
                        nome,
                        email,
                        evento,
                        data_evento,
                        local,
                        telefone_norm,
                        telefone,
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
                    "SELECT codigo, telefone_norm FROM certificado_registro WHERE email_norm = ? AND evento_norm = ?",
                    (email_norm, evento_norm),
                ).fetchone()
                if row2:
                    codigo_ex = row2["codigo"]
                    db_tel2 = (row2["telefone_norm"] or "").strip()
                    if db_tel2 and telefone_norm != db_tel2:
                        raise ValueError(
                            "O telefone não coincide com o registo já existente para este e-mail e evento."
                        )
                    conn.execute(
                        """UPDATE certificado_registro SET emitido_em = ?, nome = ?, email = ?, evento = ?,
                           data_evento = ?, local = ?, telefone_norm = ?, telefone = ?, carga_horaria = ? WHERE codigo = ?""",
                        (
                            _utc_now_iso(),
                            nome,
                            email,
                            evento,
                            data_evento,
                            local,
                            telefone_norm,
                            telefone,
                            carga,
                            codigo_ex,
                        ),
                    )
                    conn.commit()
                    return codigo_ex
                continue

    raise RuntimeError("Não foi possível gerar código de verificação único.")


_EXPORT_COLUMNS = (
    "id",
    "codigo",
    "email_norm",
    "evento_norm",
    "nome",
    "email",
    "evento",
    "data_evento",
    "local",
    "telefone_norm",
    "telefone",
    "carga_horaria",
    "emitido_em",
)


def listar_registros_export() -> list[dict[str, str | int]]:
    """Lista todos os registos (para export JSON/relatório)."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, codigo, email_norm, evento_norm, nome, email, evento,
                   data_evento, local, telefone_norm, telefone, carga_horaria, emitido_em
            FROM certificado_registro
            ORDER BY id
            """
        ).fetchall()
    return [dict(r) for r in rows]


def export_registros_csv_bytes() -> bytes:
    """CSV (UTF-8 com BOM) com os mesmos campos que o export JSON."""
    rows = listar_registros_export()
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in _EXPORT_COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


def _sqlite_tabela_existe(conn: sqlite3.Connection, nome: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (nome,),
    ).fetchone()
    return row is not None


def importar_merge_sqlite(origem: Path) -> dict[str, int]:
    """
    Lê certificado_registro de outro ficheiro .db e incorpora na base atual.
    Usa INSERT OR IGNORE: não apaga linhas; ignora duplicados por `codigo` ou (email_norm, evento_norm).
    """
    init_db()
    origem = Path(origem).resolve()
    if not origem.is_file():
        raise ValueError("O ficheiro de origem não existe.")

    with sqlite3.connect(DB_PATH) as dst:
        antes = int(dst.execute("SELECT COUNT(*) FROM certificado_registro").fetchone()[0])

        try:
            with sqlite3.connect(str(origem)) as src:
                if not _sqlite_tabela_existe(src, "certificado_registro"):
                    raise ValueError("O ficheiro não contém a tabela certificado_registro.")
                src.row_factory = sqlite3.Row
                rows = src.execute("SELECT * FROM certificado_registro").fetchall()
        except sqlite3.Error as e:
            raise ValueError(f"Não foi possível ler o SQLite enviado: {e}") from e

        for row in rows:
            d = dict(row)
            codigo = (d.get("codigo") or "").strip()
            if not codigo:
                continue
            emitido = (d.get("emitido_em") or "").strip() or _utc_now_iso()
            dst.execute(
                """
                INSERT OR IGNORE INTO certificado_registro (
                    codigo, email_norm, evento_norm, nome, email, evento,
                    data_evento, local, telefone_norm, telefone, carga_horaria, emitido_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    codigo,
                    (d.get("email_norm") or "").strip(),
                    (d.get("evento_norm") or "").strip(),
                    (d.get("nome") or "").strip(),
                    (d.get("email") or "").strip(),
                    (d.get("evento") or "").strip(),
                    (d.get("data_evento") or "").strip(),
                    (d.get("local") or "").strip(),
                    (d.get("telefone_norm") or "").strip(),
                    (d.get("telefone") or "").strip(),
                    (d.get("carga_horaria") or "").strip(),
                    emitido,
                ),
            )

        dst.commit()
        depois = int(dst.execute("SELECT COUNT(*) FROM certificado_registro").fetchone()[0])

    novos = depois - antes
    return {
        "registos_no_ficheiro": len(rows),
        "registos_na_base_antes": antes,
        "registos_na_base_depois": depois,
        "novos_inseridos": novos,
    }


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
            "telefone": d.get("telefone") or "",
            "telefone_norm": d.get("telefone_norm") or "",
            "carga_horaria": d["carga_horaria"] or "",
            "emitido_em": _emitido_em_texto_fortaleza(d["emitido_em"] or ""),
        }
