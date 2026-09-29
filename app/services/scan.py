"""
Contrôle terrain : scan en ligne, manifeste hors ligne, synchronisation.

Ordre des contrôles (le premier échec arrête tout, SANS toucher au statut) :
  1. signature Ed25519 du jeton   → faux QR
  2. bon poste                    → un agent du site ne « brûle » jamais
                                    un coupon de chaloupe
  3. bonne date (heure de Dakar)  → hors fenêtre de validité
  4. jeton courant, non révoqué   → ancien QR après report
  5. coupon VALIDE, non utilisé   → anti-réutilisation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Agent, Billet, ConflitScan, Coupon, Famille, JetonRevoque,
                        Nationalite, NiveauPack, StatutBillet, StatutCoupon)
from app.securite import cle_publique_pem, verifier_jeton
from app.services.billetterie import aujourd_hui


@dataclass
class ResultatScan:
    autorise: bool
    motif: str
    billet: dict | None = None
    coupon: dict | None = None
    restants: list[str] = field(default_factory=list)


def infos_affichage(billet: Billet) -> dict:
    """
    Ce que l'écran de l'agent affiche. Pour un national : la photo de
    contrôle et la consigne « CNI se terminant par XXXX ».
    """
    infos: dict = {"numero": billet.numero, "famille": billet.famille.value,
                   "titulaire": billet.visiteur.nom}
    if billet.famille == Famille.CHALOUPE:
        infos.update(passagers=billet.passagers,
                     consigne="Tarif unique : aucune pièce d'identité à demander. "
                              f"Compter {billet.passagers} passager(s).")
        return infos
    infos.update(nationalite=billet.nationalite.value, adultes_supp=billet.adultes_supp,
                 enfants=billet.enfants, pack=NiveauPack(billet.pack).name)
    if billet.nationalite == Nationalite.NATIONAL and billet.verification:
        infos.update(photo_url=f"/api/v3/agents/photos/{billet.verification.id}",
                     cni_4_derniers=billet.verification.cni_4_derniers,
                     consigne=f"Demander la CNI : même visage, numéro se terminant par "
                              f"{billet.verification.cni_4_derniers}.")
    if billet.enfants:
        infos["consigne_enfants"] = (f"{billet.enfants} enfant(s) accompagnant(s) : "
                                     "le parent titulaire doit être présent.")
    return infos


def scanner(db: Session, agent: Agent, jeton: str) -> ResultatScan:
    contenu = verifier_jeton(jeton)
    if contenu is None:
        return ResultatScan(False, "SIGNATURE_INVALIDE")
    if contenu.point != agent.poste:
        return ResultatScan(False, "MAUVAIS_GUICHET",
                            coupon={"point_controle": contenu.point.value})
    if contenu.date_visite != aujourd_hui():
        return ResultatScan(False, "HORS_DATE",
                            coupon={"date_visite": contenu.date_visite.isoformat()})

    # Verrou de ligne (PostgreSQL) : deux agents qui scannent le même QR à la
    # même seconde ne peuvent pas le valider tous les deux.
    coupon = db.scalar(select(Coupon).where(Coupon.id == contenu.coupon_id)
                       .with_for_update())
    if coupon is None:
        return ResultatScan(False, "INCONNU")
    billet = coupon.billet
    if coupon.jeton != jeton or db.get(JetonRevoque, jeton) is not None:
        return ResultatScan(False, "JETON_REVOQUE", billet=infos_affichage(billet))
    if coupon.statut == StatutCoupon.UTILISE:
        return ResultatScan(False, "DEJA_UTILISE", billet=infos_affichage(billet),
                            coupon={"libelle": coupon.libelle,
                                    "scanne_le": coupon.scanne_le.isoformat(),
                                    "agent_id": coupon.agent_id})
    if coupon.statut != StatutCoupon.VALIDE or billet.statut != StatutBillet.ACTIF:
        return ResultatScan(False, "NON_VALIDE", billet=infos_affichage(billet))

    coupon.statut = StatutCoupon.UTILISE
    coupon.scanne_le = datetime.now(timezone.utc)
    coupon.agent_id = agent.id
    db.commit()
    return ResultatScan(True, "OK", billet=infos_affichage(billet),
                        coupon={"libelle": coupon.libelle, "statut": coupon.statut.value},
                        restants=[c.libelle for c in billet.coupons
                                  if c.statut == StatutCoupon.VALIDE])


def manifeste(db: Session, agent: Agent, jour: date | None = None) -> dict:
    """
    Téléchargé avant la prise de poste : tout ce qu'il faut pour contrôler
    SANS réseau, limité au poste de l'agent et à la journée.
    """
    jour = jour or aujourd_hui()
    coupons = db.scalars(select(Coupon).join(Billet).where(
        Coupon.point_controle == agent.poste, Coupon.statut == StatutCoupon.VALIDE,
        Billet.date_visite == jour, Billet.statut == StatutBillet.ACTIF)).all()
    revoques = db.scalars(select(JetonRevoque.jeton)).all()
    return {
        "poste": agent.poste.value,
        "date": jour.isoformat(),
        "cle_publique_pem": cle_publique_pem(),
        "coupons": {c.id: {"libelle": c.libelle, **infos_affichage(c.billet)}
                    for c in coupons},
        "jetons_revoques": list(revoques),
    }


def synchroniser(db: Session, agent: Agent, scans: list[tuple[str, datetime]]) -> dict:
    """Retour du réseau : on applique les scans hors ligne, on relève les conflits."""
    appliques, conflits = 0, []
    for coupon_id, quand in scans:
        coupon = db.get(Coupon, coupon_id)
        if coupon is None or coupon.point_controle != agent.poste:
            continue
        if coupon.statut == StatutCoupon.UTILISE:
            if coupon.agent_id != agent.id:
                db.add(ConflitScan(coupon_id=coupon.id, agent_initial_id=coupon.agent_id,
                                   agent_conflit_id=agent.id))
                conflits.append(coupon.billet.numero)
            continue
        if coupon.statut == StatutCoupon.VALIDE:
            coupon.statut = StatutCoupon.UTILISE
            coupon.scanne_le, coupon.agent_id = quand, agent.id
            coupon.scanne_hors_ligne = True
            appliques += 1
    db.commit()
    return {"appliques": appliques, "conflits": conflits}
