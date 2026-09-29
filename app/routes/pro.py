"""Routes des professionnels : agents de contrôle, partenaires et admin."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependances import agent_courant, exiger_admin, partenaire_courant
from app.erreurs import interdit, introuvable
from app.models import (Agent, ConflitScan, Partenaire, Reservation, Rotation,
                        VerificationIdentite)
from app.schemas import (NouveauPartenaire, NouvelAgent, NouvelleRotation, Retard, Scan,
                         ScanOut, Synchronisation)
from app.securite import generer_cle_api, lire_photo
from app.services import billetterie, scan, services_ile

agents = APIRouter(prefix="/api/v3", tags=["Agents"])
partenaires = APIRouter(prefix="/api/v3/reservations", tags=["Partenaires"])
admin = APIRouter(prefix="/api/v3/admin", tags=["Admin"],
                  dependencies=[Depends(exiger_admin)])


# --- Agents -----------------------------------------------------------------

@agents.post("/scan", response_model=ScanOut, summary="Scanner un QR (en ligne)")
def scanner(corps: Scan, agent: Agent = Depends(agent_courant),
            db: Session = Depends(get_db)):
    return ScanOut(**scan.scanner(db, agent, corps.jeton).__dict__)


@agents.get("/agents/manifeste", summary="Manifeste du jour pour le mode hors ligne")
def manifeste(agent: Agent = Depends(agent_courant), db: Session = Depends(get_db)):
    return scan.manifeste(db, agent)


@agents.post("/scan/sync", summary="Remonter les scans faits hors ligne")
def synchroniser(corps: Synchronisation, agent: Agent = Depends(agent_courant),
                 db: Session = Depends(get_db)):
    return scan.synchroniser(db, agent, [(s.coupon_id, s.horodatage) for s in corps.scans])


@agents.get("/agents/photos/{verification_id}", response_class=Response,
            summary="Photo de contrôle (déchiffrée pour l'agent du site)")
def photo(verification_id: str, agent: Agent = Depends(agent_courant),
          db: Session = Depends(get_db)):
    if agent.poste.value != "ENTREE_SITE":
        raise interdit("Photo réservée au contrôle de l'entrée du site.")
    verif = db.get(VerificationIdentite, verification_id)
    if verif is None:
        raise introuvable("Photo")
    return Response(lire_photo(verif.photo_controle_ref), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


# --- Partenaires ----------------------------------------------------------------

def _sa_reservation(db: Session, rid: str, p: Partenaire) -> Reservation:
    res = db.get(Reservation, rid)
    if res is None or res.partenaire_id != p.id:
        raise introuvable("Réservation")
    return res


@partenaires.post("/{reservation_id}/confirmer", summary="Prestation rendue → paiement libéré")
def confirmer(reservation_id: str, p: Partenaire = Depends(partenaire_courant),
              db: Session = Depends(get_db)):
    res = services_ile.confirmer_prestation(db, _sa_reservation(db, reservation_id, p))
    return {"statut": res.statut.value, "net_partenaire": res.net_partenaire,
            "commission": res.commission}


@partenaires.post("/{reservation_id}/annuler", summary="Annulation → visiteur remboursé")
def annuler(reservation_id: str, p: Partenaire = Depends(partenaire_courant),
            db: Session = Depends(get_db)):
    res = services_ile.annuler_reservation(db, _sa_reservation(db, reservation_id, p))
    return {"statut": res.statut.value}


# --- Admin ------------------------------------------------------------------------

@admin.post("/agents", status_code=201)
def creer_agent(corps: NouvelAgent, db: Session = Depends(get_db)):
    cle, empreinte = generer_cle_api()
    agent = Agent(nom=corps.nom, poste=corps.poste, cle_api_hash=empreinte)
    db.add(agent)
    db.commit()
    return {"id": agent.id, "cle_api": cle,
            "avertissement": "Clé affichée une seule fois : à enrôler dans l'app agent."}


@admin.post("/rotations", status_code=201)
def creer_rotation(corps: NouvelleRotation, db: Session = Depends(get_db)):
    r = Rotation(**corps.model_dump())
    db.add(r)
    db.commit()
    return {"id": r.id, "nom": r.nom}


@admin.post("/rotations/{rotation_id}/retard")
def retard(rotation_id: str, corps: Retard, db: Session = Depends(get_db)):
    r = db.get(Rotation, rotation_id)
    if r is None:
        raise introuvable("Rotation")
    return {"notifies": services_ile.declarer_retard(db, r, corps.minutes)}


@admin.post("/partenaires", status_code=201)
def creer_partenaire(corps: NouveauPartenaire, db: Session = Depends(get_db)):
    cle, empreinte = generer_cle_api()
    p = Partenaire(**corps.model_dump(), cle_api_hash=empreinte)
    db.add(p)
    db.commit()
    return {"id": p.id, "cle_api": cle}


@admin.post("/partenaires/{partenaire_id}/licence-verifiee",
            summary="Licence contrôlée auprès du registre officiel des guides")
def valider_licence(partenaire_id: str, db: Session = Depends(get_db)):
    p = db.get(Partenaire, partenaire_id)
    if p is None or not p.licence:
        raise introuvable("Partenaire avec licence")
    p.licence_verifiee = True
    db.commit()
    return {"id": p.id, "licence_verifiee": True}


@admin.get("/caisses", summary="Les trois caisses : bateau, site, plateforme")
def caisses(db: Session = Depends(get_db)):
    return services_ile.caisses(db)


@admin.get("/conflits", summary="Scans hors ligne en conflit")
def conflits(db: Session = Depends(get_db)):
    return [{"coupon_id": c.coupon_id, "agent_initial": c.agent_initial_id,
             "agent_conflit": c.agent_conflit_id, "detecte_le": c.detecte_le}
            for c in db.scalars(select(ConflitScan)).all()]


@admin.post("/maintenance/expirer-commandes")
def expirer(db: Session = Depends(get_db)):
    return {"expirees": billetterie.expirer_commandes(db)}
