"""API de certificados: consulta planilha publicada e gera PNG/PDF."""

from __future__ import annotations

import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.services.certificate import render_certificate_pdf, render_certificate_png, resolve_template_file
from app.services.registro import (
    DB_PATH,
    buscar_por_codigo,
    export_registros_csv_bytes,
    importar_merge_sqlite,
    init_db,
    listar_registros_export,
    mensagem_se_telefone_nao_confere_bd,
    obter_ou_criar_codigo,
)
from app.services.sheets import find_event_meta, find_participant_by_email_or_telefone, list_eventos

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_ADMIN_IMPORT_MAX_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Garante que a tabela SQLite existe ao arrancar (ficheiro em data/certificados.db)."""
    init_db()
    yield


app = FastAPI(title="Certificados COGERH", version="1.0.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _slug_filename(s: str, max_len: int = 80) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s, flags=re.IGNORECASE)
    s = s.strip("_") or "certificado"
    return s[:max_len]


def _build_verification_url(request: Request, codigo: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/validar?codigo={quote(codigo)}"


@app.get("/")
def index_page() -> FileResponse:
    html = STATIC_DIR / "index.html"
    if not html.is_file():
        raise HTTPException(status_code=500, detail="Página index não encontrada.")
    return FileResponse(html)


@app.get("/validar")
def validar_page() -> FileResponse:
    html = STATIC_DIR / "validar.html"
    if not html.is_file():
        raise HTTPException(status_code=500, detail="Página de validação não encontrada.")
    return FileResponse(html)


@app.get("/export")
def export_page() -> FileResponse:
    html = STATIC_DIR / "export.html"
    if not html.is_file():
        raise HTTPException(status_code=500, detail="Página de exportação não encontrada.")
    return FileResponse(html)


def _get_participante(email: str, telefone: str, evento: str):
    try:
        return find_participant_by_email_or_telefone(email=email, telefone=telefone, evento=evento)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


def _get_participante_emitir(email: str, telefone: str, evento: str):
    """Planilha + telefone já registado na BD (se existir) têm de coincidir com o pedido."""
    msg_bd = mensagem_se_telefone_nao_confere_bd(email, evento, telefone)
    if msg_bd:
        raise HTTPException(status_code=403, detail=msg_bd)
    return _get_participante(email, telefone, evento)


@app.get("/api/eventos")
def api_eventos():
    try:
        return list_eventos()
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/template.png")
def api_template_png(evento: str = Query(..., min_length=1, description="Texto da coluna Evento")):
    try:
        meta = find_event_meta(evento)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not meta:
        raise HTTPException(status_code=404, detail="Evento não encontrado na planilha.")
    try:
        path = resolve_template_file(meta.get("template") or None)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return FileResponse(path, media_type="image/png")


@app.get("/api/participante")
def api_participante(
    email: str = Query(..., min_length=3, description="E-mail do participante"),
    telefone: str = Query(..., min_length=3, description="Telefone do participante"),
    evento: str = Query(..., min_length=1, description="Evento selecionado (coluna Evento)"),
):
    p = _get_participante_emitir(email, telefone, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este evento (e-mail ou telefone).",
        )
    return {
        "nome": p.get("nome"),
        "data": p.get("data"),
        "evento": p.get("evento"),
        "local": p.get("local"),
        "carga_horaria": p.get("carga_horaria"),
        "email": p.get("email"),
        "telefone": p.get("telefone"),
        "template": p.get("template"),
    }


@app.get("/api/validar")
def api_validar(codigo: str = Query(..., min_length=8, description="Código impresso no certificado")):
    r = buscar_por_codigo(codigo)
    if not r:
        return {
            "valido": False,
            "mensagem": "Código não encontrado ou inválido. Confira os caracteres (12 símbolos, sem O/0/I/1/L).",
        }
    return {"valido": True, **r}


@app.get("/api/certificado.png")
def api_certificado_png(
    request: Request,
    email: str = Query(..., min_length=3),
    telefone: str = Query(..., min_length=3),
    evento: str = Query(..., min_length=1),
):
    p = _get_participante_emitir(email, telefone, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este evento (e-mail ou telefone).",
        )
    try:
        codigo = obter_ou_criar_codigo(p)
        verification_url = _build_verification_url(request, codigo)
        png = render_certificate_png(p, codigo, verification_url=verification_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        msg = str(e)
        if "telefone não coincide" in msg.lower():
            raise HTTPException(status_code=403, detail=msg) from e
        raise HTTPException(status_code=500, detail=msg) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"X-Certificado-Codigo": codigo},
    )


@app.get("/api/certificado.pdf")
def api_certificado_pdf(
    request: Request,
    email: str = Query(..., min_length=3),
    telefone: str = Query(..., min_length=3),
    evento: str = Query(..., min_length=1),
):
    p = _get_participante_emitir(email, telefone, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este evento (e-mail ou telefone).",
        )
    try:
        codigo = obter_ou_criar_codigo(p)
        verification_url = _build_verification_url(request, codigo)
        pdf = render_certificate_pdf(p, codigo, verification_url=verification_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        msg = str(e)
        if "telefone não coincide" in msg.lower():
            raise HTTPException(status_code=403, detail=msg) from e
        raise HTTPException(status_code=500, detail=msg) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    name = _slug_filename(p.get("nome") or "certificado")
    filename = f"certificado_{name}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Certificado-Codigo": codigo,
        },
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "db_path": str(DB_PATH)}


def _normalize_export_token(raw: str | None) -> str:
    if not raw:
        return ""
    t = raw.strip()
    if t.startswith("\ufeff"):
        t = t.lstrip("\ufeff").strip()
    return t


def _require_export_token(
    x_cert_export_token: str | None,
    export_token: str | None,
    authorization: str | None,
) -> None:
    expected = _normalize_export_token(os.getenv("CERT_EXPORT_TOKEN"))
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Exportação desativada: defina a variável CERT_EXPORT_TOKEN no Railway (valor secreto e aleatório).",
        )
    bearer: str | None = None
    if authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            bearer = _normalize_export_token(auth[7:])

    got = _normalize_export_token(
        x_cert_export_token or export_token or bearer,
    )
    if len(got) != len(expected) or not secrets.compare_digest(got, expected):
        raise HTTPException(
            status_code=403,
            detail=(
                "Token de exportação inválido ou em falta. "
                "Confirme CERT_EXPORT_TOKEN no Railway (sem aspas em volta). "
                "Na URL, codifique o token: # como %23 (senão o navegador corta), + como %2B, & como %26. "
                "Preferível: header X-Cert-Export-Token ou Authorization: Bearer … (curl / Postman)."
            ),
        )


@app.get("/api/admin/export")
def api_admin_export(
    export_format: Literal["sqlite", "json", "csv"] = Query(
        "sqlite",
        description="sqlite = .db completo; json | csv = tabela de registos.",
    ),
    x_cert_export_token: str | None = Header(None, alias="X-Cert-Export-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    export_token: str | None = Query(
        None,
        description="Alternativa ao header (evite em proxies compartilhados — pode ficar em logs de URL).",
    ),
):
    """
    Backup dos dados de registo. Protegido por CERT_EXPORT_TOKEN.

    Exemplo (curl, SQLite):

        curl -f -L -o certificados.db -H "X-Cert-Export-Token: SEU_TOKEN" \\
          "https://SEU_DOMINIO/api/admin/export?export_format=sqlite"

    Exemplo (JSON):

        curl -f -H "X-Cert-Export-Token: SEU_TOKEN" \\
          "https://SEU_DOMINIO/api/admin/export?export_format=json"

    Exemplo (CSV):

        curl -f -L -o certificados.csv -H "Authorization: Bearer SEU_TOKEN" \\
          "https://SEU_DOMINIO/api/admin/export?export_format=csv"

    Ou com Bearer (evita problemas com + e & no token na URL):

        curl -f -H "Authorization: Bearer SEU_TOKEN" \\
          "https://SEU_DOMINIO/api/admin/export?export_format=sqlite" -o certificados.db
    """
    _require_export_token(x_cert_export_token, export_token, authorization)

    if export_format == "json":
        return listar_registros_export()

    if export_format == "csv":
        data = export_registros_csv_bytes()
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="certificados_export.csv"'},
        )

    if not DB_PATH.is_file():
        raise HTTPException(status_code=404, detail=f"Ficheiro da base não encontrado: {DB_PATH}")
    return FileResponse(
        path=str(DB_PATH),
        filename="certificados_backup.db",
        media_type="application/vnd.sqlite3",
        content_disposition_type="attachment",
    )


@app.post("/api/admin/import-db")
async def api_admin_import_db(
    file: UploadFile = File(..., description="Ficheiro .db exportado (mesma app)"),
    x_cert_export_token: str | None = Header(None, alias="X-Cert-Export-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    export_token: str | None = Form(None),
):
    """
    Mescla registos do .db enviado na base atual (INSERT OR IGNORE).
    Útil após redeploy com volume novo: não apaga linhas existentes.
    Mesmo token que CERT_EXPORT_TOKEN (header Bearer ou form export_token).
    """
    _require_export_token(x_cert_export_token, export_token, authorization)

    content = await file.read()
    if len(content) > _ADMIN_IMPORT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ficheiro demasiado grande (máximo {_ADMIN_IMPORT_MAX_BYTES // (1024 * 1024)} MB).",
        )
    if len(content) < 64:
        raise HTTPException(status_code=400, detail="Ficheiro vazio ou inválido.")

    with NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        stats = importar_merge_sqlite(tmp_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        tmp_path.unlink(missing_ok=True)

    return stats
