"""Schémas Pydantic : contrat JSON entre le backend et les applis."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import (Famille, MoyenPaiement, PointControle, Trajet, TypePartenaire)

NomPack = Literal["DECOUVERTE", "ESSENTIEL", "PREMIUM"]


# --- Achats ----------------------------------------------------------------

class Contact(BaseModel):
    nom: str = Field(min_length=2, max_length=120)
    telephone: str = Field(min_length=6, max_length=32)
    email: str | None = None


class AchatChaloupe(Contact):
    date_visite: date
    trajet: Trajet = Trajet.ALLER_RETOUR
    passagers: int = Field(1, ge=1)
    rotation_id: str | None = None
    moyen_paiement: MoyenPaiement


class AchatEntreeSite(Contact):
    date_visite: date
    verification_id: str | None = Field(
        None, description="Obligatoire pour le tarif national (issu de /identite/verifications)")
    adultes_supp: int = Field(0, ge=0)
    enfants: int = Field(0, ge=0)
    pack: NomPack = "DECOUVERTE"
    moyen_paiement: MoyenPaiement


class CouponOut(BaseModel):
    libelle: str
    point_controle: PointControle
    statut: str
    jeton: str


class BilletOut(BaseModel):
    numero: str
    famille: Famille
    date_visite: date
    prix: int
    statut: str
    passagers: int | None = None
    nationalite: str | None = None
    enfants: int | None = None
    adultes_supp: int | None = None
    pack: str | None = None
    coupons: list[CouponOut]


class CommandeOut(BaseModel):
    commande_id: str
    statut: str
    montant: int
    url_paiement: str | None
    billets: list[BilletOut]


# --- Identité -------------------------------------------------------------

class VerificationOut(BaseModel):
    verification_id: str
    nom_carte: str
    cni_4_derniers: str
    expire_le: datetime


# --- Actions sur un billet (preuve de possession : numéro + téléphone) -----

class PreuveBillet(BaseModel):
    numero: str
    telephone: str


class Report(BaseModel):
    telephone: str
    nouvelle_date: date


class AnnonceRetour(BaseModel):
    telephone: str
    rotation_id: str


class PositionAudio(PreuveBillet):
    lat: float
    lon: float
    langue: Literal["fr", "en", "wo", "es", "pt"] = "fr"


class NouvelleReservation(PreuveBillet):
    partenaire_id: str
    description: str = Field(max_length=200)
    montant: int = Field(gt=0)
    moyen_paiement: MoyenPaiement


class NouvelAvis(PreuveBillet):
    reservation_id: str
    note: int = Field(ge=1, le=5)
    texte: str = Field(max_length=1000)


class WebhookPaiement(BaseModel):
    reference: str
    statut: Literal["REUSSI", "ECHOUE"]


# --- Agents -----------------------------------------------------------------

class Scan(BaseModel):
    jeton: str


class ScanOut(BaseModel):
    autorise: bool
    motif: str
    billet: dict | None = None
    coupon: dict | None = None
    restants: list[str] = []


class ScanHorsLigne(BaseModel):
    coupon_id: str
    horodatage: datetime


class Synchronisation(BaseModel):
    scans: list[ScanHorsLigne]


# --- Admin ------------------------------------------------------------------

class NouvelAgent(BaseModel):
    nom: str
    poste: PointControle


class NouvelleRotation(BaseModel):
    nom: str
    depart: Literal["Dakar", "Gorée"]
    heure_prevue: datetime
    capacite: int = Field(gt=0)


class Retard(BaseModel):
    minutes: int = Field(ge=0, le=600)


class NouveauPartenaire(BaseModel):
    type: TypePartenaire
    nom: str
    commission: float = Field(0.12, ge=0, le=0.5)
    langues: str | None = None
    licence: str | None = None
