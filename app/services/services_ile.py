"""
Services sur l'île : audio-guide géolocalisé, marketplace en séquestre,
avis vérifiés, notifications de retard et tableau de bord des caisses.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erreurs import ErreurMetier, interdit, introuvable
from app.models import (Avis, Billet, Circuit, Commande, Coupon, Famille, LignePack,
                        MoyenPaiement, NiveauPack, Notification, Partenaire,
                        PointControle, Reservation, Rotation, StatutBillet,
                        StatutCommande, StatutCoupon, StatutReservation, TypePartenaire)

# ---------------------------------------------------------------------------
#  Audio-guide
# ---------------------------------------------------------------------------

LANGUES = ("fr", "en", "wo", "es", "pt")


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Formule de haversine, en mètres."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _est_entre(billet: Billet) -> bool:
    return billet.famille == Famille.ENTREE_SITE and billet.coupons[0].statut == \
        StatutCoupon.UTILISE


def position_audio(db: Session, billet: Billet, lat: float, lon: float,
                   langue: str) -> dict:
    if billet.famille != Famille.ENTREE_SITE:
        raise interdit("L'audio-guide est lié au billet d'entrée du site.")
    if not _est_entre(billet):
        raise interdit("L'audio-guide s'active une fois entré sur l'île.", "PAS_ENTRE")
    if billet.pack < NiveauPack.ESSENTIEL:
        raise interdit("L'audio-guide fait partie du pack Essentiel.", "PACK_INSUFFISANT")

    for circuit in db.scalars(select(Circuit)).all():
        for etape in circuit.etapes:
            d = distance_m(lat, lon, etape.lat, etape.lon)
            if d <= etape.rayon_m:
                pistes = {p.langue: p.url for p in etape.pistes}
                return {"circuit": circuit.nom, "etape": etape.nom,
                        "distance_m": round(d),
                        "piste": pistes.get(langue) or pistes.get("fr"),
                        "rappel_memoriel": circuit.ton_memoriel}
    return {"etape": None}


# ---------------------------------------------------------------------------
#  Marketplace (pack Premium) — paiement en séquestre
# ---------------------------------------------------------------------------

def reserver(db: Session, billet: Billet, partenaire_id: str, description: str,
             montant: int, moyen: MoyenPaiement) -> Reservation:
    if billet.famille != Famille.ENTREE_SITE or billet.statut != StatutBillet.ACTIF:
        raise interdit("Il faut un billet d'entrée du site actif.")
    if billet.pack < NiveauPack.PREMIUM:
        raise interdit("Réservé au pack Premium Téranga.", "PACK_INSUFFISANT")
    partenaire = db.get(Partenaire, partenaire_id)
    if partenaire is None or not partenaire.actif:
        raise introuvable("Partenaire")
    if partenaire.type == TypePartenaire.GUIDE and not partenaire.licence_verifiee:
        raise interdit("Licence de guide non vérifiée.", "LICENCE_NON_VERIFIEE")
    if montant <= 0:
        raise ErreurMetier("MONTANT", "Montant invalide.", 422)
    # Paiement : même passerelle que la billetterie (simulée ici).
    res = Reservation(billet_site_id=billet.id, visiteur_id=billet.visiteur_id,
                      partenaire_id=partenaire.id, description=description,
                      montant=montant)
    db.add(res)
    db.commit()
    return res


def confirmer_prestation(db: Session, res: Reservation) -> Reservation:
    if res.statut != StatutReservation.EN_SEQUESTRE:
        raise ErreurMetier("RESERVATION_CLOTUREE", "Réservation déjà clôturée.")
    res.commission = int(round(res.montant * res.partenaire.commission))
    res.net_partenaire = res.montant - res.commission
    res.statut = StatutReservation.LIBEREE
    db.commit()
    return res


def annuler_reservation(db: Session, res: Reservation) -> Reservation:
    if res.statut != StatutReservation.EN_SEQUESTRE:
        raise ErreurMetier("RESERVATION_CLOTUREE", "Réservation déjà clôturée.")
    res.statut = StatutReservation.REMBOURSEE
    db.commit()
    return res


def laisser_avis(db: Session, billet: Billet, reservation_id: str, note: int,
                 texte: str) -> Avis:
    """Avis VÉRIFIÉ : entré sur l'île + prestation réellement consommée."""
    if not 1 <= note <= 5:
        raise ErreurMetier("NOTE", "Note de 1 à 5.", 422)
    if not _est_entre(billet):
        raise interdit("Avis possible après ta visite (billet d'entrée scanné).", "PAS_ENTRE")
    res = db.get(Reservation, reservation_id)
    if res is None or res.billet_site_id != billet.id:
        raise introuvable("Réservation")
    if res.statut != StatutReservation.LIBEREE:
        raise interdit("Avis possible uniquement sur une prestation consommée.",
                       "PRESTATION_NON_CONSOMMEE")
    if db.scalar(select(Avis.id).where(Avis.reservation_id == res.id)):
        raise ErreurMetier("AVIS_EXISTANT", "Un avis existe déjà pour cette réservation.")
    avis = Avis(visiteur_id=billet.visiteur_id, partenaire_id=res.partenaire_id,
                reservation_id=res.id, note=note, texte=texte)
    db.add(avis)
    db.commit()
    return avis


# ---------------------------------------------------------------------------
#  Retards de chaloupe
# ---------------------------------------------------------------------------

def declarer_retard(db: Session, rotation: Rotation, minutes: int) -> int:
    """
    Notifie UNIQUEMENT les porteurs d'un billet CHALOUPE du jour ayant
    encore un coupon valide au poste de départ de cette rotation. Les
    visiteurs qui n'ont qu'un billet d'entrée ne reçoivent rien.
    """
    rotation.retard_minutes = minutes
    depart = rotation.heure_prevue + timedelta(minutes=minutes)
    poste = (PointControle.EMBARCADERE_DAKAR if rotation.depart == "Dakar"
             else PointControle.EMBARCADERE_GOREE)
    jour = rotation.heure_prevue.date()
    billets = db.scalars(select(Billet).join(Coupon).where(
        Billet.famille == Famille.CHALOUPE, Billet.statut == StatutBillet.ACTIF,
        Billet.date_visite == jour, Coupon.point_controle == poste,
        Coupon.statut == StatutCoupon.VALIDE)).unique().all()
    visiteurs = {b.visiteur_id for b in billets}
    for vid in visiteurs:
        db.add(Notification(visiteur_id=vid, message=(
            f"Chaloupe « {rotation.nom} » : +{minutes} min, nouveau départ "
            f"{depart:%H:%M}. Ton billet reste valable.")))
    db.commit()
    return len(visiteurs)


# ---------------------------------------------------------------------------
#  Tableau de bord : les trois caisses
# ---------------------------------------------------------------------------

def caisses(db: Session) -> dict:
    payee = Commande.statut == StatutCommande.PAYEE

    def somme(requete) -> int:
        return int(db.scalar(requete) or 0)

    bateau = somme(select(func.sum(Billet.prix)).join(Commande).where(
        payee, Billet.famille == Famille.CHALOUPE))
    site = somme(select(func.sum(Billet.prix)).join(Commande).where(
        payee, Billet.famille == Famille.ENTREE_SITE))
    packs = somme(select(func.sum(LignePack.prix)).join(Commande).where(payee))
    encaisse = somme(select(func.sum(Commande.montant)).where(payee))

    def res_somme(colonne, statut=None) -> int:
        q = select(func.sum(colonne))
        if statut is not None:
            q = q.where(Reservation.statut == statut)
        return somme(q)

    commissions = res_somme(Reservation.commission, StatutReservation.LIBEREE)
    return {
        "billetterie": {"encaisse": encaisse, "caisse_bateau": bateau,
                        "caisse_site": site, "packs": packs},
        "marketplace": {
            "encaisse": res_somme(Reservation.montant),
            "a_reverser_partenaires": res_somme(Reservation.net_partenaire,
                                                StatutReservation.LIBEREE),
            "commissions": commissions,
            "sequestre": res_somme(Reservation.montant, StatutReservation.EN_SEQUESTRE),
            "rembourse": res_somme(Reservation.montant, StatutReservation.REMBOURSEE),
        },
        "caisse_plateforme": packs + commissions,
    }
