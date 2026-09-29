"""
Les deux parcours d'achat, la confirmation de paiement et le report.

PARCOURS 1 — CHALOUPE : tarif unique, nom + téléphone, AUCUNE identité.
PARCOURS 2 — ENTRÉE DU SITE : national (vérification préalable + quota
             1 CNI / jour, enfants accompagnants) ou international.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import tarifs
from app.config import settings
from app.erreurs import ErreurMetier, introuvable
from app.models import (Billet, Commande, Coupon, Famille, JetonRevoque, LignePack,
                        MoyenPaiement, Nationalite, NiveauPack, PointControle,
                        Rotation, StatutBillet, StatutCommande, StatutCoupon, Trajet,
                        VerificationIdentite, Visiteur, nouvel_id)
from app.securite import signer_jeton
from app.services import paiement

FUSEAU_DAKAR = ZoneInfo("Africa/Dakar")


def utc(dt: datetime) -> datetime:
    """SQLite rend des datetimes « naïfs » : on les considère en UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def aujourd_hui() -> date:
    """La journée de validité est celle de Dakar, pas celle du serveur."""
    return datetime.now(FUSEAU_DAKAR).date()


def _numero(prefixe: str) -> str:
    # Aléatoire plutôt que séquentiel : pas de course entre serveurs et
    # impossible de deviner le numéro du billet suivant.
    return f"{prefixe}-{aujourd_hui():%Y}-{secrets.token_hex(3).upper()}"


def _coupon(ordre: int, libelle: str, point: PointControle, jour: date) -> Coupon:
    ident = nouvel_id()
    return Coupon(id=ident, ordre=ordre, libelle=libelle, point_controle=point,
                  jeton=signer_jeton(ident, point, jour))


def _verifier_date(jour: date) -> None:
    if jour < aujourd_hui():
        raise ErreurMetier("DATE_PASSEE", "La date de visite est passée.", 422)
    if jour > aujourd_hui() + timedelta(days=90):
        raise ErreurMetier("DATE_TROP_LOINTAINE", "Réservation possible à 90 jours maximum.", 422)


def _creer_visiteur(db: Session, nom: str, telephone: str, email: str | None) -> Visiteur:
    visiteur = Visiteur(nom=nom.strip(), telephone=telephone.strip(), email=email)
    db.add(visiteur)
    return visiteur


# ---------------------------------------------------------------------------
#  PARCOURS 1 — CHALOUPE
# ---------------------------------------------------------------------------

def acheter_chaloupe(db: Session, *, nom: str, telephone: str, email: str | None,
                     date_visite: date, trajet: Trajet, passagers: int,
                     rotation_id: str | None, moyen: MoyenPaiement
                     ) -> tuple[Commande, str | None]:
    _verifier_date(date_visite)
    if not 1 <= passagers <= tarifs.PASSAGERS_MAX_PAR_BILLET:
        raise ErreurMetier("PASSAGERS", f"De 1 à {tarifs.PASSAGERS_MAX_PAR_BILLET} "
                           f"passagers par billet.", 422)
    if rotation_id:
        rotation = db.get(Rotation, rotation_id)
        if rotation is None or rotation.depart != "Dakar" or \
                utc(rotation.heure_prevue).astimezone(FUSEAU_DAKAR).date() != date_visite:
            raise ErreurMetier("ROTATION", "Départ invalide pour cette date.", 422)

    visiteur = _creer_visiteur(db, nom, telephone, email)
    prix = tarifs.prix_chaloupe(trajet, passagers)
    commande = Commande(visiteur=visiteur, montant=prix, moyen_paiement=moyen)
    billet = Billet(commande=commande, numero=_numero("CHL"), famille=Famille.CHALOUPE,
                    visiteur=visiteur, prix=prix, date_visite=date_visite,
                    date_visite_initiale=date_visite, trajet=trajet,
                    passagers=passagers, rotation_aller_id=rotation_id)
    billet.coupons.append(_coupon(0, "Traversée ALLER", PointControle.EMBARCADERE_DAKAR,
                                  date_visite))
    if trajet == Trajet.ALLER_RETOUR:
        billet.coupons.append(_coupon(1, "Traversée RETOUR",
                                      PointControle.EMBARCADERE_GOREE, date_visite))
    db.add_all([commande, billet])
    db.commit()
    return commande, _lancer_paiement(db, commande)


# ---------------------------------------------------------------------------
#  PARCOURS 2 — ENTRÉE DU SITE
# ---------------------------------------------------------------------------

def acheter_entree_site(db: Session, *, nom: str, telephone: str, email: str | None,
                        date_visite: date, verification_id: str | None,
                        adultes_supp: int, enfants: int, pack: NiveauPack,
                        moyen: MoyenPaiement) -> tuple[Commande, str | None]:
    _verifier_date(date_visite)
    if not 0 <= enfants <= tarifs.ENFANTS_MAX:
        raise ErreurMetier("ENFANTS", f"Maximum {tarifs.ENFANTS_MAX} enfants.", 422)
    if not 0 <= adultes_supp <= tarifs.ADULTES_SUPP_MAX:
        raise ErreurMetier("GROUPE", "Trop d'adultes : passer par la réservation groupe.", 422)

    verif: VerificationIdentite | None = None
    nationalite = Nationalite.INTERNATIONAL
    if verification_id:
        verif = db.get(VerificationIdentite, verification_id)
        if verif is None:
            raise introuvable("Vérification d'identité")
        if verif.utilisee:
            raise ErreurMetier("VERIF_DEJA_UTILISEE", "Cette vérification a déjà servi.")
        if utc(verif.expire_le) < datetime.now(timezone.utc):
            raise ErreurMetier("VERIF_EXPIREE", "Vérification expirée, recommence.", 422)
        if adultes_supp:
            raise ErreurMetier("ADULTE_NATIONAL_ACCOMPAGNANT",
                               "Tarif national : chaque adulte achète son billet avec "
                               "sa propre CNI. Seuls les enfants accompagnent.", 422)
        nationalite = Nationalite.NATIONAL
        # Contrôle « poli » avant l'index unique (qui reste le vrai garde-fou).
        deja = db.scalar(select(Billet.id).where(
            Billet.cni_hash_titulaire == verif.cni_hash,
            Billet.date_visite == date_visite, Billet.statut != StatutBillet.ANNULE))
        if deja:
            raise _erreur_quota()

    visiteur = _creer_visiteur(db, nom, telephone, email)
    prix = tarifs.prix_entree(nationalite, 1 + adultes_supp, enfants)
    prix_pack = tarifs.PRIX_PACKS[pack]
    commande = Commande(visiteur=visiteur, montant=prix + prix_pack, moyen_paiement=moyen)
    billet = Billet(id=nouvel_id(), commande=commande, numero=_numero("SIT"),
                    famille=Famille.ENTREE_SITE,
                    visiteur=visiteur, prix=prix, date_visite=date_visite,
                    date_visite_initiale=date_visite, nationalite=nationalite,
                    adultes_supp=adultes_supp, enfants=enfants, verification=verif,
                    cni_hash_titulaire=verif.cni_hash if verif else None)
    billet.coupons.append(_coupon(0, "Entrée du site", PointControle.ENTREE_SITE, date_visite))
    db.add_all([commande, billet])
    if pack != NiveauPack.DECOUVERTE:
        db.add(LignePack(commande=commande, billet_id=billet.id, niveau=pack.value,
                         prix=prix_pack))
    if verif:
        verif.utilisee = True
    try:
        db.commit()
    except IntegrityError as err:                 # deux achats simultanés
        db.rollback()
        raise _erreur_quota() from err
    return commande, _lancer_paiement(db, commande)


def _erreur_quota() -> ErreurMetier:
    return ErreurMetier("QUOTA_CNI", "Cette CNI a déjà un billet national pour ce jour "
                        "(1 billet par CNI et par jour).")


# ---------------------------------------------------------------------------
#  Paiement
# ---------------------------------------------------------------------------

def _lancer_paiement(db: Session, commande: Commande) -> str | None:
    session = paiement.initier(commande)
    commande.reference_paiement = session.reference
    db.commit()
    if settings.paiement_simule:
        confirmer_paiement(db, session.reference, succes=True)
    return session.url_paiement


def confirmer_paiement(db: Session, reference: str, succes: bool) -> Commande:
    """Appelé par le webhook. IDEMPOTENT : un 2e appel ne change rien."""
    commande = db.scalar(select(Commande).where(Commande.reference_paiement == reference))
    if commande is None:
        raise introuvable("Commande")
    if commande.statut != StatutCommande.EN_ATTENTE_PAIEMENT:
        return commande
    if succes:
        commande.statut = StatutCommande.PAYEE
        commande.payee_le = datetime.now(timezone.utc)
        for billet in commande.billets:
            billet.statut = StatutBillet.ACTIF
            for c in billet.coupons:
                c.statut = StatutCoupon.VALIDE
        for ligne in commande.packs:
            billet = db.get(Billet, ligne.billet_id)
            billet.pack = max(billet.pack, ligne.niveau)
    else:
        _annuler_commande(commande, StatutCommande.ECHOUEE)
    db.commit()
    return commande


def _annuler_commande(commande: Commande, statut: StatutCommande) -> None:
    commande.statut = statut
    for billet in commande.billets:
        billet.statut = StatutBillet.ANNULE          # libère le quota CNI
        for c in billet.coupons:
            c.statut = StatutCoupon.ANNULE
        if billet.verification:
            billet.verification.utilisee = False     # la vérif peut resservir


def expirer_commandes(db: Session) -> int:
    """Tâche périodique : paniers non payés au-delà du délai → libérés."""
    limite = datetime.now(timezone.utc) - timedelta(
        minutes=settings.delai_expiration_commande_min)
    commandes = db.scalars(select(Commande).where(
        Commande.statut == StatutCommande.EN_ATTENTE_PAIEMENT,
        )).all()
    commandes = [c for c in commandes if utc(c.cree_le) < limite]
    for c in commandes:
        _annuler_commande(c, StatutCommande.EXPIREE)
    db.commit()
    return len(commandes)


# ---------------------------------------------------------------------------
#  Accès visiteur, report, annonce de retour
# ---------------------------------------------------------------------------

def trouver_billet(db: Session, numero: str, telephone: str) -> Billet:
    """
    Mode invité : la preuve de possession est le couple (numéro, téléphone).
    Même réponse 404 si le numéro existe mais que le téléphone diffère,
    pour ne pas révéler l'existence du billet.
    """
    billet = db.scalar(select(Billet).where(Billet.numero == numero))
    if billet is None or billet.visiteur.telephone != telephone.strip():
        raise introuvable("Billet")
    return billet


REPORTS_AUTORISES = 1
DELAI_REPORT_JOURS = 7


def reporter(db: Session, billet: Billet, nouvelle_date: date) -> Billet:
    if billet.famille != Famille.ENTREE_SITE:
        raise ErreurMetier("REPORT_CHALOUPE", "Seul le billet d'entrée se reporte.", 422)
    coupon = billet.coupons[0]
    if coupon.statut != StatutCoupon.VALIDE:
        raise ErreurMetier("REPORT_IMPOSSIBLE", "Billet déjà utilisé ou annulé.")
    if billet.reports_utilises >= REPORTS_AUTORISES:
        raise ErreurMetier("REPORT_DEJA_FAIT", "Ce billet a déjà été reporté une fois.")
    if nouvelle_date < aujourd_hui():
        raise ErreurMetier("DATE_PASSEE", "Impossible de reporter vers le passé.", 422)
    if nouvelle_date > billet.date_visite_initiale + timedelta(days=DELAI_REPORT_JOURS):
        raise ErreurMetier("REPORT_TROP_TARD",
                           f"Report possible jusqu'à {DELAI_REPORT_JOURS} jours après "
                           f"la date initiale.")

    ancien_jeton = coupon.jeton
    billet.date_visite = nouvelle_date            # l'index unique suit la date
    billet.reports_utilises += 1
    coupon.jeton = signer_jeton(coupon.id, coupon.point_controle, nouvelle_date)
    db.add(JetonRevoque(jeton=ancien_jeton, coupon_id=coupon.id))
    try:
        db.commit()
    except IntegrityError as err:
        db.rollback()
        raise _erreur_quota() from err
    return billet


def annoncer_retour(db: Session, billet: Billet, rotation_id: str) -> Rotation:
    """Indicatif : le coupon retour reste valable sur toute rotation du jour."""
    if billet.famille != Famille.CHALOUPE or billet.trajet != Trajet.ALLER_RETOUR:
        raise ErreurMetier("PAS_DE_RETOUR", "Ce billet ne comporte pas de retour.", 422)
    rotation = db.get(Rotation, rotation_id)
    if rotation is None or rotation.depart != "Gorée":
        raise introuvable("Rotation de retour")
    if billet.rotation_retour_annoncee_id:
        ancienne = db.get(Rotation, billet.rotation_retour_annoncee_id)
        ancienne.retours_annonces -= billet.passagers
    rotation.retours_annonces += billet.passagers
    billet.rotation_retour_annoncee_id = rotation.id
    db.commit()
    return rotation
