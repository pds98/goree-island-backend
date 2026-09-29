"""
Grilles tarifaires (FCFA) — VALEURS PROVISOIRES à remplacer par les tarifs
officiels. Une grille par entité : la chaloupe (liaison maritime), l'entrée
du site (gestionnaire du site) et les packs (plateforme).
"""

from app.models import Nationalite, NiveauPack, Trajet

# Parcours 1 — CHALOUPE : TARIF UNIQUE, seul le trajet compte.
PRIX_CHALOUPE_PAR_PASSAGER = {
    Trajet.ALLER_SIMPLE: 750,
    Trajet.ALLER_RETOUR: 1_500,
}
PASSAGERS_MAX_PAR_BILLET = 10

# Parcours 2 — ENTRÉE DU SITE : c'est ici (et seulement ici) que la
# nationalité change le prix.
PRIX_ENTREE_SITE = {
    Nationalite.NATIONAL: {"adulte": 500, "enfant": 200},
    Nationalite.INTERNATIONAL: {"adulte": 3_000, "enfant": 1_500},
}
ENFANTS_MAX = 6
ADULTES_SUPP_MAX = 9

PRIX_PACKS = {
    NiveauPack.DECOUVERTE: 0,
    NiveauPack.ESSENTIEL: 2_000,
    NiveauPack.PREMIUM: 5_000,
}


def prix_chaloupe(trajet: Trajet, passagers: int) -> int:
    return PRIX_CHALOUPE_PAR_PASSAGER[trajet] * passagers


def prix_entree(nationalite: Nationalite, adultes: int, enfants: int) -> int:
    grille = PRIX_ENTREE_SITE[nationalite]
    return adultes * grille["adulte"] + enfants * grille["enfant"]
