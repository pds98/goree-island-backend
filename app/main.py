"""
Point d'entrée FastAPI.

    uvicorn app.main:app --reload
    → documentation interactive : http://localhost:8000/docs
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (enregistre les tables)
from app.db import Base, SessionLocal, engine
from app.erreurs import ErreurMetier
from app.routes import pro, visiteurs
from app.seed import charger_circuits


@asynccontextmanager
async def cycle_de_vie(_: FastAPI):
    # MVP : création directe des tables. En production : migrations Alembic.
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DEMO", "true").lower() == "true":
        with SessionLocal() as db:
            charger_circuits(db)
    yield


app = FastAPI(
    title="Gorée Island API",
    version="3.0.0",
    description=("Billetterie en deux parcours (chaloupe à tarif unique / entrée du "
                 "site national-international), jetons QR signés Ed25519 vérifiables "
                 "hors ligne, services sur l'île et marketplace en séquestre."),
    lifespan=cycle_de_vie,
)


@app.exception_handler(ErreurMetier)
async def erreur_metier(_: Request, err: ErreurMetier) -> JSONResponse:
    return JSONResponse(status_code=err.statut_http,
                        content={"code": err.code, "message": err.message})


@app.get("/sante", tags=["Technique"])
def sante() -> dict:
    return {"statut": "ok", "version": app.version}


app.include_router(visiteurs.router)
app.include_router(pro.agents)
app.include_router(pro.partenaires)
app.include_router(pro.admin)
