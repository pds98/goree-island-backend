"""Routes publiques utilisées par l'appli visiteur (mode invité)."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.erreurs import ErreurMetier, interdit
from app.models import (Billet, Circuit, Commande, NiveauPack, Partenaire, Rotation,
                        TypePartenaire)
from app.schemas import (AchatChaloupe, AchatEntreeSite, AnnonceRetour, BilletOut,
                         CommandeOut, CouponOut, NouvelAvis, NouvelleReservation,
                         PositionAudio, Report, VerificationOut, WebhookPaiement)
from app.services import billetterie, identite, paiement, services_ile

router = APIRouter(prefix="/api/v3", tags=["Visiteurs"])


# --- Sérialisation ----------------------------------------------------------

def billet_out(b: Billet) -> BilletOut:
    return BilletOut(
        numero=b.numero, famille=b.famille, date_visite=b.date_visite, prix=b.prix,
        statut=b.statut.value, passagers=b.passagers,
        nationalite=b.nationalite.value if b.nationalite else None,
        enfants=b.enfants, adultes_supp=b.adultes_supp,
        pack=NiveauPack(b.pack).name if b.famille.value == "ENTREE_SITE" else None,
        coupons=[CouponOut(libelle=c.libelle, point_controle=c.point_controle,
                           statut=c.statut.value, jeton=c.jeton) for c in b.coupons])


def commande_out(c: Commande, url: str | None) -> CommandeOut:
    return CommandeOut(commande_id=c.id, statut=c.statut.value, montant=c.montant,
                       url_paiement=url, billets=[billet_out(b) for b in c.billets])


# --- Parcours 1 : chaloupe --------------------------------------------------

@router.get("/rotations")
def rotations(jour: date | None = None, db: Session = Depends(get_db)):
    """Horaires du jour avec retard en direct (écran « Départs de Dakar »)."""
    jour = jour or billetterie.aujourd_hui()
    return [{"id": r.id, "nom": r.nom, "depart": r.depart,
             "heure_prevue": r.heure_prevue, "retard_minutes": r.retard_minutes,
             "capacite": r.capacite}
            for r in db.scalars(select(Rotation).order_by(Rotation.heure_prevue)).all()
            if r.heure_prevue.date() == jour]


@router.post("/chaloupe/commandes", response_model=CommandeOut, status_code=201,
             summary="Acheter une traversée (tarif unique, sans identité)")
def acheter_chaloupe(achat: AchatChaloupe, db: Session = Depends(get_db)):
    commande, url = billetterie.acheter_chaloupe(
        db, nom=achat.nom, telephone=achat.telephone, email=achat.email,
        date_visite=achat.date_visite, trajet=achat.trajet, passagers=achat.passagers,
        rotation_id=achat.rotation_id, moyen=achat.moyen_paiement)
    return commande_out(commande, url)


# --- Parcours 2 : entrée du site ---------------------------------------------

@router.post("/identite/verifications", response_model=VerificationOut, status_code=201,
             summary="Tarif national : scan CNI + selfie vivant")
async def verifier_identite(image_cni: UploadFile = File(...), selfie: UploadFile = File(...),
                            db: Session = Depends(get_db)):
    verif = identite.verifier(db, await image_cni.read(), await selfie.read())
    return VerificationOut(verification_id=verif.id, nom_carte=verif.nom_carte,
                           cni_4_derniers=verif.cni_4_derniers, expire_le=verif.expire_le)


@router.post("/site/commandes", response_model=CommandeOut, status_code=201,
             summary="Acheter l'entrée du site (national ou international) + pack")
def acheter_entree(achat: AchatEntreeSite, db: Session = Depends(get_db)):
    commande, url = billetterie.acheter_entree_site(
        db, nom=achat.nom, telephone=achat.telephone, email=achat.email,
        date_visite=achat.date_visite, verification_id=achat.verification_id,
        adultes_supp=achat.adultes_supp, enfants=achat.enfants,
        pack=NiveauPack[achat.pack], moyen=achat.moyen_paiement)
    return commande_out(commande, url)


# --- Paiement -----------------------------------------------------------------

@router.post("/paiements/webhook", tags=["Paiement"],
             summary="Appelé par l'agrégateur de paiement (signature HMAC)")
async def webhook(request: Request, x_signature: str = Header(default=""),
                  db: Session = Depends(get_db)):
    corps = await request.body()
    if not paiement.webhook_authentique(corps, x_signature):
        raise ErreurMetier("SIGNATURE_WEBHOOK", "Signature invalide.", 401)
    donnees = WebhookPaiement(**json.loads(corps))
    commande = billetterie.confirmer_paiement(db, donnees.reference,
                                              succes=donnees.statut == "REUSSI")
    return {"commande_id": commande.id, "statut": commande.statut.value}


# --- Mon billet -----------------------------------------------------------------

@router.get("/billets/{numero}", response_model=BilletOut)
def mon_billet(numero: str, telephone: str, db: Session = Depends(get_db)):
    return billet_out(billetterie.trouver_billet(db, numero, telephone))


@router.post("/billets/{numero}/report", response_model=BilletOut)
def reporter(numero: str, corps: Report, db: Session = Depends(get_db)):
    billet = billetterie.trouver_billet(db, numero, corps.telephone)
    return billet_out(billetterie.reporter(db, billet, corps.nouvelle_date))


@router.post("/billets/{numero}/retour")
def annoncer_retour(numero: str, corps: AnnonceRetour, db: Session = Depends(get_db)):
    billet = billetterie.trouver_billet(db, numero, corps.telephone)
    r = billetterie.annoncer_retour(db, billet, corps.rotation_id)
    return {"rotation": r.nom, "retours_annonces": r.retours_annonces,
            "taux_remplissage": round(100 * r.retours_annonces / r.capacite, 1)}


# --- Sur l'île -------------------------------------------------------------------

@router.get("/circuits", tags=["Sur l'île"])
def circuits(db: Session = Depends(get_db)):
    return [{"id": c.id, "nom": c.nom, "theme": c.theme, "duree_min": c.duree_min,
             "ton_memoriel": c.ton_memoriel,
             "etapes": [{"nom": e.nom, "lat": e.lat, "lon": e.lon} for e in c.etapes]}
            for c in db.scalars(select(Circuit)).all()]


@router.post("/audio/position", tags=["Sur l'île"],
             summary="Position GPS → piste audio de l'étape la plus proche")
def audio(pos: PositionAudio, db: Session = Depends(get_db)):
    billet = billetterie.trouver_billet(db, pos.numero, pos.telephone)
    return services_ile.position_audio(db, billet, pos.lat, pos.lon, pos.langue)


@router.get("/partenaires", tags=["Marketplace"])
def partenaires(type: TypePartenaire | None = None, db: Session = Depends(get_db)):
    """Seuls les guides à licence vérifiée sont visibles."""
    q = select(Partenaire).where(Partenaire.actif.is_(True))
    if type:
        q = q.where(Partenaire.type == type)
    return [{"id": p.id, "type": p.type.value, "nom": p.nom, "langues": p.langues,
             "licence_verifiee": p.licence_verifiee}
            for p in db.scalars(q).all()
            if p.type != TypePartenaire.GUIDE or p.licence_verifiee]


@router.post("/reservations", status_code=201, tags=["Marketplace"])
def reserver(corps: NouvelleReservation, db: Session = Depends(get_db)):
    billet = billetterie.trouver_billet(db, corps.numero, corps.telephone)
    res = services_ile.reserver(db, billet, corps.partenaire_id, corps.description,
                                corps.montant, corps.moyen_paiement)
    return {"reservation_id": res.id, "statut": res.statut.value, "montant": res.montant}


@router.post("/avis", status_code=201, tags=["Marketplace"])
def avis(corps: NouvelAvis, db: Session = Depends(get_db)):
    billet = billetterie.trouver_billet(db, corps.numero, corps.telephone)
    a = services_ile.laisser_avis(db, billet, corps.reservation_id, corps.note, corps.texte)
    return {"avis_id": a.id, "note": a.note}
