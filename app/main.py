"""API de certificados: consulta planilha publicada e gera PNG/PDF."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.services.certificate import render_certificate_pdf, render_certificate_png, resolve_template_file
from app.services.registro import buscar_por_codigo, obter_ou_criar_codigo
from app.services.sheets import find_event_meta, find_participant_by_email, list_eventos

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Certificados COGERH", version="1.0.0")

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
        png = render_certificate_png(p, codigo)
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
        pdf = render_certificate_pdf(p, codigo)
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
    return {"status": "ok"}
