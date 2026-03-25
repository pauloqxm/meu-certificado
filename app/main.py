"""API de certificados: consulta planilha publicada e gera PNG/PDF."""

from __future__ import annotations

import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.services.certificate import render_certificate_pdf, render_certificate_png, resolve_template_file
from app.services.registro import DB_PATH, buscar_por_codigo, init_db, listar_registros_export, obter_ou_criar_codigo
from app.services.sheets import find_event_meta, find_participant_by_email, list_eventos

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


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


def _get_participante(email: str, evento: str):
    try:
        return find_participant_by_email(email, evento=evento)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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
    evento: str = Query(..., min_length=1, description="Evento selecionado (coluna Evento)"),
):
    p = _get_participante(email, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este e-mail neste evento.",
        )
    return {
        "nome": p.get("nome"),
        "data": p.get("data"),
        "evento": p.get("evento"),
        "local": p.get("local"),
        "carga_horaria": p.get("carga_horaria"),
        "email": p.get("email"),
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
    evento: str = Query(..., min_length=1),
):
    p = _get_participante(email, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este e-mail neste evento.",
        )
    try:
        codigo = obter_ou_criar_codigo(p)
        verification_url = _build_verification_url(request, codigo)
        png = render_certificate_png(p, codigo, verification_url=verification_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
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
    evento: str = Query(..., min_length=1),
):
    p = _get_participante(email, evento)
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Participante não encontrado para este e-mail neste evento.",
        )
    try:
        codigo = obter_ou_criar_codigo(p)
        verification_url = _build_verification_url(request, codigo)
        pdf = render_certificate_pdf(p, codigo, verification_url=verification_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
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


def _require_export_token(
    x_cert_export_token: str | None,
    export_token: str | None,
) -> None:
    expected = (os.getenv("CERT_EXPORT_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Exportação desativada: defina a variável CERT_EXPORT_TOKEN no Railway (valor secreto e aleatório).",
        )
    got = (x_cert_export_token or export_token or "").strip()
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=403, detail="Token de exportação inválido ou em falta.")


@app.get("/api/admin/export")
def api_admin_export(
    export_format: Literal["sqlite", "json"] = Query(
        "sqlite",
        description="sqlite = ficheiro .db completo; json = todos os registos em JSON.",
    ),
    x_cert_export_token: str | None = Header(None, alias="X-Cert-Export-Token"),
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
    """
    _require_export_token(x_cert_export_token, export_token)

    if export_format == "json":
        return listar_registros_export()

    if not DB_PATH.is_file():
        raise HTTPException(status_code=404, detail=f"Ficheiro da base não encontrado: {DB_PATH}")
    return FileResponse(
        path=str(DB_PATH),
        filename="certificados_backup.db",
        media_type="application/vnd.sqlite3",
        content_disposition_type="attachment",
    )
