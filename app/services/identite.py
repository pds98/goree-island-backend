"""
Vérification d'identité (tarif national de l'entrée du site UNIQUEMENT).

Enchaînement : OCR de la CNI → détection du vivant → comparaison des
visages → empreinte HMAC du numéro. Les images reçues ne sont jamais
écrites en clair : seule une photo de contrôle chiffrée est conservée.

Le moteur biométrique est derrière une interface (`FournisseurIdentite`) :
en production on branche un prestataire KYC opérant au Sénégal (à choisir
après appel d'offres ; ex. Smile ID, à valider), en dev/test on utilise
`FournisseurSimule`, qui lit des « images » au format JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import settings
from app.erreurs import ErreurMetier
from app.models import VerificationIdentite, nouvel_id
from app.securite import hacher_cni, stocker_photo

SEUIL_VISAGE = 0.85


@dataclass
class ResultatBiometrique:
    numero: str
    nom: str
    expiration: date
    vivant: bool
    score_visage: float
    photo_controle: bytes          # recadrage du visage, conservé chiffré


class FournisseurIdentite(Protocol):
    def analyser(self, image_cni: bytes, selfie: bytes) -> ResultatBiometrique: ...


class FournisseurSimule:
    """
    Pour le développement et les tests. Les « images » sont du JSON :
      image_cni = {"numero", "nom", "visage", "expiration": "AAAA-MM-JJ"}
      selfie    = {"visage", "vivant": bool}
    Deux visages identiques donnent 0,96, différents 0,41.
    """

    def analyser(self, image_cni: bytes, selfie: bytes) -> ResultatBiometrique:
        try:
            carte, visage = json.loads(image_cni), json.loads(selfie)
            return ResultatBiometrique(
                numero=str(carte["numero"]), nom=carte["nom"],
                expiration=date.fromisoformat(carte["expiration"]),
                vivant=bool(visage.get("vivant", True)),
                score_visage=0.96 if carte["visage"] == visage["visage"] else 0.41,
                photo_controle=f"visage:{carte['visage']}".encode())
        except (ValueError, KeyError) as err:
            raise ErreurMetier("CNI_ILLISIBLE", "CNI illisible, recommence la photo.",
                               422) from err


fournisseur_identite: FournisseurIdentite = FournisseurSimule()


def format_nin_valide(numero: str) -> bool:
    """Hypothèse : NIN CEDEAO de 13 à 17 chiffres — À CONFIRMER."""
    return numero.isdigit() and 13 <= len(numero) <= 17


def verifier(db: Session, image_cni: bytes, selfie: bytes) -> VerificationIdentite:
    r = fournisseur_identite.analyser(image_cni, selfie)

    if not format_nin_valide(r.numero):
        raise ErreurMetier("CNI_ILLISIBLE", "Numéro de CNI illisible ou mal formé.", 422)
    if r.expiration < date.today():
        raise ErreurMetier("CNI_EXPIREE", "Ta CNI est expirée.", 422)
    if not r.vivant:
        raise ErreurMetier("SELFIE_NON_VIVANT",
                           "Selfie refusé : il faut une personne réelle, pas une photo.", 422)
    if r.score_visage < SEUIL_VISAGE:
        raise ErreurMetier("VISAGE_DIFFERENT",
                           "Le visage ne correspond pas à la photo de la CNI. "
                           "Tu peux réessayer ou prendre le tarif international.", 422)

    ident = nouvel_id()
    verif = VerificationIdentite(
        id=ident, nom_carte=r.nom, cni_hash=hacher_cni(r.numero),
        cni_4_derniers=r.numero[-4:], score_visage=r.score_visage,
        photo_controle_ref=stocker_photo(ident, r.photo_controle),
        expire_le=datetime.now(timezone.utc)
        + timedelta(minutes=settings.duree_validite_verification_min))
    db.add(verif)
    db.commit()
    # Les octets `image_cni` et `selfie` ne sont référencés nulle part après
    # ce point : ils disparaissent avec la requête.
    return verif
