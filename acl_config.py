"""Correspondance dossier -> groupe ACL, partagee entre bulk_index.py et
seed_acl.py pour eviter que les deux scripts divergent sur qui a acces a quoi.

Granularite retenue pour ce POC : par dossier/domaine, pas par document
individuel -- le seul niveau que les PDF organises par theme permettent de
simuler honnetement (pas de vraie source de permissions type SharePoint).
"""

FOLDER_TO_GROUPE = {
    "RH": "rh",
    "juridique droit du travail": "juridique",
    "Management direction": "management",
    "marketing Communication": "marketing",
    "securité rgpd informatique": "securite",
    "Finance": "finance",
}


def groupe_pour_dossier(folder: str) -> str:
    return FOLDER_TO_GROUPE.get(folder, folder.lower())
