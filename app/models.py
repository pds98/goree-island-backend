"""
Modèles ORM (SQLAlchemy 2.0).

Correspondance directe avec le schéma de `architecture_billetterie_v3.md` :
deux familles de billets (CHALOUPE / ENTREE_SITE), le coupon comme unité
scannable, l'identité réduite à une empreinte, la marketplace en séquestre.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, Date, DateTime, Enum, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def nouvel_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
#  Énumérations
# ---------------------------------------------------------------------------

class Famille(str, enum.Enum):
    CHALOUPE = "CHALOUPE"
    ENTREE_SITE = "ENTREE_SITE"


class Nationalite(str, enum.Enum):
    NATIONAL = "NATIONAL"
    INTERNATIONAL = "INTERNATIONAL"


class Trajet(str, enum.Enum):
    ALLER_SIMPLE = "ALLER_SIMPLE"
    ALLER_RETOUR = "ALLER_RETOUR"


class PointControle(str, enum.Enum):
    EMBARCADERE_DAKAR = "EMBARCADERE_DAKAR"
    EMBARCADERE_GOREE = "EMBARCADERE_GOREE"
    ENTREE_SITE = "ENTREE_SITE"


class StatutCommande(str, enum.Enum):
    EN_ATTENTE_PAIEMENT = "EN_ATTENTE_PAIEMENT"
    PAYEE = "PAYEE"
    ECHOUEE = "ECHOUEE"
    EXPIREE = "EXPIREE"


class StatutBillet(str, enum.Enum):
    EN_ATTENTE = "EN_ATTENTE"   # compte déjà dans le quota (évite 2 paniers en parallèle)
    ACTIF = "ACTIF"
    ANNULE = "ANNULE"           # libère le quota


class StatutCoupon(str, enum.Enum):
    EN_ATTENTE = "EN_ATTENTE"
    VALIDE = "VALIDE"
    UTILISE = "UTILISE"
    ANNULE = "ANNULE"


class MoyenPaiement(str, enum.Enum):
    WAVE = "WAVE"
    ORANGE_MONEY = "ORANGE_MONEY"
    CARTE = "CARTE"


class NiveauPack(int, enum.Enum):
    DECOUVERTE = 0
    ESSENTIEL = 1
    PREMIUM = 2


class TypePartenaire(str, enum.Enum):
    GUIDE = "GUIDE"
    RESTAURANT = "RESTAURANT"
    HOTEL = "HOTEL"


class StatutReservation(str, enum.Enum):
    EN_SEQUESTRE = "EN_SEQUESTRE"
    LIBEREE = "LIBEREE"
    REMBOURSEE = "REMBOURSEE"


# ---------------------------------------------------------------------------
#  Visiteurs et identité
# ---------------------------------------------------------------------------

class Visiteur(Base):
    __tablename__ = "visiteur"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    nom: Mapped[str] = mapped_column(String(120))
    telephone: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(160))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class VerificationIdentite(Base):
    """
    Résultat d'une vérification CNI + selfie. Ne contient AUCUNE image ni
    numéro en clair : seulement l'empreinte HMAC, les 4 derniers chiffres
    et la référence de la photo de contrôle chiffrée.
    """
    __tablename__ = "verification_identite"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    nom_carte: Mapped[str] = mapped_column(String(120))
    cni_hash: Mapped[str] = mapped_column(String(64), index=True)
    cni_4_derniers: Mapped[str] = mapped_column(String(4))
    photo_controle_ref: Mapped[str] = mapped_column(String(200))
    score_visage: Mapped[float] = mapped_column(Float)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    utilisee: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
#  Rotations de chaloupe et notifications
# ---------------------------------------------------------------------------

class Rotation(Base):
    __tablename__ = "rotation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    nom: Mapped[str] = mapped_column(String(80))
    depart: Mapped[str] = mapped_column(String(40))          # "Dakar" | "Gorée"
    heure_prevue: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retard_minutes: Mapped[int] = mapped_column(Integer, default=0)
    capacite: Mapped[int] = mapped_column(Integer)
    retours_annonces: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    visiteur_id: Mapped[str] = mapped_column(ForeignKey("visiteur.id"), index=True)
    canal: Mapped[str] = mapped_column(String(10), default="SMS")
    message: Mapped[str] = mapped_column(Text)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


# ---------------------------------------------------------------------------
#  Commandes, billets, coupons
# ---------------------------------------------------------------------------

class Commande(Base):
    __tablename__ = "commande"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    visiteur_id: Mapped[str] = mapped_column(ForeignKey("visiteur.id"))
    montant: Mapped[int] = mapped_column(Integer)
    moyen_paiement: Mapped[MoyenPaiement] = mapped_column(Enum(MoyenPaiement))
    statut: Mapped[StatutCommande] = mapped_column(
        Enum(StatutCommande), default=StatutCommande.EN_ATTENTE_PAIEMENT)
    reference_paiement: Mapped[str | None] = mapped_column(String(80), unique=True)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
    payee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    visiteur: Mapped[Visiteur] = relationship()
    billets: Mapped[list["Billet"]] = relationship(back_populates="commande")
    packs: Mapped[list["LignePack"]] = relationship(back_populates="commande")


class Billet(Base):
    __tablename__ = "billet"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    commande_id: Mapped[str] = mapped_column(ForeignKey("commande.id"))
    numero: Mapped[str] = mapped_column(String(30), unique=True)
    famille: Mapped[Famille] = mapped_column(Enum(Famille))
    visiteur_id: Mapped[str] = mapped_column(ForeignKey("visiteur.id"))
    prix: Mapped[int] = mapped_column(Integer)
    date_visite: Mapped[date] = mapped_column(Date)
    date_visite_initiale: Mapped[date] = mapped_column(Date)
    reports_utilises: Mapped[int] = mapped_column(Integer, default=0)
    statut: Mapped[StatutBillet] = mapped_column(Enum(StatutBillet),
                                                 default=StatutBillet.EN_ATTENTE)
    # --- CHALOUPE ---
    trajet: Mapped[Trajet | None] = mapped_column(Enum(Trajet))
    passagers: Mapped[int | None] = mapped_column(Integer)
    rotation_aller_id: Mapped[str | None] = mapped_column(ForeignKey("rotation.id"))
    rotation_retour_annoncee_id: Mapped[str | None] = mapped_column(ForeignKey("rotation.id"))
    # --- ENTREE_SITE ---
    nationalite: Mapped[Nationalite | None] = mapped_column(Enum(Nationalite))
    adultes_supp: Mapped[int | None] = mapped_column(Integer)
    enfants: Mapped[int | None] = mapped_column(Integer)
    pack: Mapped[int] = mapped_column(Integer, default=NiveauPack.DECOUVERTE.value)
    verification_id: Mapped[str | None] = mapped_column(ForeignKey("verification_identite.id"))
    # Copie dénormalisée de l'empreinte CNI : c'est elle que l'index de quota
    # contraint. NULL pour la chaloupe et pour les internationaux.
    cni_hash_titulaire: Mapped[str | None] = mapped_column(String(64))

    commande: Mapped[Commande] = relationship(back_populates="billets")
    visiteur: Mapped[Visiteur] = relationship()
    verification: Mapped[VerificationIdentite | None] = relationship()
    coupons: Mapped[list["Coupon"]] = relationship(back_populates="billet",
                                                   order_by="Coupon.ordre")

    __table_args__ = (
        # ANTI-DOUBLON NATIONAL : 1 billet d'entrée (en attente ou actif) par
        # CNI et par jour. Index partiel, compris par PostgreSQL et SQLite.
        Index("ux_quota_national", "cni_hash_titulaire", "date_visite", unique=True,
              postgresql_where=text("cni_hash_titulaire IS NOT NULL AND statut <> 'ANNULE'"),
              sqlite_where=text("cni_hash_titulaire IS NOT NULL AND statut <> 'ANNULE'")),
    )


class Coupon(Base):
    __tablename__ = "coupon"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    billet_id: Mapped[str] = mapped_column(ForeignKey("billet.id"), index=True)
    ordre: Mapped[int] = mapped_column(Integer, default=0)
    libelle: Mapped[str] = mapped_column(String(40))
    point_controle: Mapped[PointControle] = mapped_column(Enum(PointControle))
    jeton: Mapped[str] = mapped_column(Text)
    statut: Mapped[StatutCoupon] = mapped_column(Enum(StatutCoupon),
                                                 default=StatutCoupon.EN_ATTENTE)
    scanne_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agent.id"))
    scanne_hors_ligne: Mapped[bool] = mapped_column(Boolean, default=False)

    billet: Mapped[Billet] = relationship(back_populates="coupons")


class JetonRevoque(Base):
    """Anciens QR invalidés (après un report)."""
    __tablename__ = "jeton_revoque"

    jeton: Mapped[str] = mapped_column(Text, primary_key=True)
    coupon_id: Mapped[str] = mapped_column(ForeignKey("coupon.id"))
    revoque_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


class LignePack(Base):
    __tablename__ = "ligne_pack"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    commande_id: Mapped[str] = mapped_column(ForeignKey("commande.id"))
    billet_id: Mapped[str] = mapped_column(ForeignKey("billet.id"))
    niveau: Mapped[int] = mapped_column(Integer)
    prix: Mapped[int] = mapped_column(Integer)

    commande: Mapped[Commande] = relationship(back_populates="packs")


# ---------------------------------------------------------------------------
#  Agents et scans
# ---------------------------------------------------------------------------

class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    nom: Mapped[str] = mapped_column(String(80))
    poste: Mapped[PointControle] = mapped_column(Enum(PointControle))
    cle_api_hash: Mapped[str] = mapped_column(String(64), unique=True)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)


class ConflitScan(Base):
    """Coupon validé hors ligne sur deux appareils : remonté à l'admin."""
    __tablename__ = "conflit_scan"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    coupon_id: Mapped[str] = mapped_column(ForeignKey("coupon.id"))
    agent_initial_id: Mapped[str | None] = mapped_column(String(32))
    agent_conflit_id: Mapped[str] = mapped_column(String(32))
    detecte_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)


# ---------------------------------------------------------------------------
#  Circuits et audio-guide
# ---------------------------------------------------------------------------

class Circuit(Base):
    __tablename__ = "circuit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    nom: Mapped[str] = mapped_column(String(120))
    theme: Mapped[str] = mapped_column(String(40))
    duree_min: Mapped[int] = mapped_column(Integer)
    ton_memoriel: Mapped[bool] = mapped_column(Boolean, default=False)

    etapes: Mapped[list["EtapeCircuit"]] = relationship(
        back_populates="circuit", order_by="EtapeCircuit.ordre")


class EtapeCircuit(Base):
    __tablename__ = "etape_circuit"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    circuit_id: Mapped[str] = mapped_column(ForeignKey("circuit.id"))
    ordre: Mapped[int] = mapped_column(Integer)
    nom: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    rayon_m: Mapped[int] = mapped_column(Integer, default=35)

    circuit: Mapped[Circuit] = relationship(back_populates="etapes")
    pistes: Mapped[list["PisteAudio"]] = relationship()


class PisteAudio(Base):
    __tablename__ = "piste_audio"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    etape_id: Mapped[str] = mapped_column(ForeignKey("etape_circuit.id"))
    langue: Mapped[str] = mapped_column(String(5))
    url: Mapped[str] = mapped_column(String(300))

    __table_args__ = (UniqueConstraint("etape_id", "langue"),)


# ---------------------------------------------------------------------------
#  Marketplace
# ---------------------------------------------------------------------------

class Partenaire(Base):
    __tablename__ = "partenaire"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    type: Mapped[TypePartenaire] = mapped_column(Enum(TypePartenaire))
    nom: Mapped[str] = mapped_column(String(120))
    commission: Mapped[float] = mapped_column(Float, default=0.12)
    langues: Mapped[str | None] = mapped_column(String(60))      # "fr,en,wo"
    licence: Mapped[str | None] = mapped_column(String(40))
    licence_verifiee: Mapped[bool] = mapped_column(Boolean, default=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    # Clé d'API du partenaire (hachée) : sert à confirmer ses prestations.
    cle_api_hash: Mapped[str] = mapped_column(String(64), unique=True)


class Reservation(Base):
    __tablename__ = "reservation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    billet_site_id: Mapped[str] = mapped_column(ForeignKey("billet.id"))
    visiteur_id: Mapped[str] = mapped_column(ForeignKey("visiteur.id"))
    partenaire_id: Mapped[str] = mapped_column(ForeignKey("partenaire.id"))
    description: Mapped[str] = mapped_column(String(200))
    montant: Mapped[int] = mapped_column(Integer)
    statut: Mapped[StatutReservation] = mapped_column(
        Enum(StatutReservation), default=StatutReservation.EN_SEQUESTRE)
    commission: Mapped[int] = mapped_column(Integer, default=0)
    net_partenaire: Mapped[int] = mapped_column(Integer, default=0)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)

    partenaire: Mapped[Partenaire] = relationship()


class Avis(Base):
    __tablename__ = "avis"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=nouvel_id)
    visiteur_id: Mapped[str] = mapped_column(ForeignKey("visiteur.id"))
    partenaire_id: Mapped[str] = mapped_column(ForeignKey("partenaire.id"))
    reservation_id: Mapped[str] = mapped_column(ForeignKey("reservation.id"), unique=True)
    note: Mapped[int] = mapped_column(Integer)
    texte: Mapped[str] = mapped_column(Text)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=maintenant)
