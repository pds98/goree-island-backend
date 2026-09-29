"""Erreurs métier traduites en réponses HTTP lisibles par l'app mobile."""

from __future__ import annotations


class ErreurMetier(Exception):
    """
    `code` est stable (l'app mobile s'en sert pour afficher le bon écran),
    `message` est destiné à l'humain.
    """

    def __init__(self, code: str, message: str, statut_http: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.statut_http = statut_http


def introuvable(quoi: str) -> ErreurMetier:
    return ErreurMetier("INTROUVABLE", f"{quoi} introuvable.", 404)


def interdit(message: str, code: str = "INTERDIT") -> ErreurMetier:
    return ErreurMetier(code, message, 403)
