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

# Description d'une ligne de CE QUE CONTIENT chaque domaine, pour le routeur
# d'agents.py. Sans ca, le routeur ne voit que le nom nu du domaine et devine
# mal (ex: il rangeait "RGPD / donnees personnelles" sous 'rh' au lieu de
# 'securite'). Les mots-cles ici tranchent les ambiguites frequentes.
DOMAIN_DESCRIPTIONS = {
    "rh": ("ressources humaines, paie, declarations sociales (DSN, DPAE), "
           "cotisations URSSAF, assurance chomage et bonus-malus, CSE, "
           "revenus de remplacement, guides declaratifs"),
    "juridique": ("droit du travail : contrat de travail, licenciement, "
                  "preavis, inaptitude, articles du Code du travail (Legifrance)"),
    "management": ("direction et management, dispositifs PACTE Entreprises, "
                   "appels a manifestation d'interet, aides, categorisation "
                   "des depenses, egalite professionnelle"),
    "marketing": ("marketing et communication, performance numerique, "
                  "cookies et traceurs, relation client"),
    "securite": ("securite informatique et protection des donnees : RGPD, "
                 "donnees personnelles, CNIL, sous-traitance des donnees, "
                 "cybersecurite, intelligence artificielle"),
    "finance": ("financement des entreprises : prets, obligations, actions, "
                "fonds propres, Bpifrance, partenariats public-prive (PPP), "
                "titres de creances"),
}


def groupe_pour_dossier(folder: str) -> str:
    return FOLDER_TO_GROUPE.get(folder, folder.lower())
