#!/usr/bin/env python3
"""Premiere version simple des agents par domaine : un routeur (Claude)
identifie quel(s) domaine(s) sont pertinents pour la question, puis chaque
domaine pertinent ET autorise pour l'utilisateur est interroge separement
(un "agent" = recherche + generation restreinte a ce domaine).

Volontairement simple : pas de nouvelle couche d'habilitation (on reutilise
telle quelle la table PostgreSQL existante), pas de synthese finale entre
agents -- chaque reponse de domaine est affichee separement, etiquetee.
Objectif ici : valider le principe agentique (routage + specialisation par
domaine), pas construire un systeme complet.

Usage:
    python agents.py "question en francais" --email carla@art.fr
"""
import argparse
import json
import re
import sys

from sentence_transformers import SentenceTransformer

from acl_config import DOMAIN_DESCRIPTIONS, FOLDER_TO_GROUPE
from rag_query import (
    MODELE_EMBEDDING,
    LLMError,
    appeler_llm,
    construire_contexte,
    groupes_utilisateur,
    rechercher,
    reference,
)

sys.stdout.reconfigure(encoding="utf-8")

DOMAINES = sorted(set(FOLDER_TO_GROUPE.values()))

_modele_embedding = None  # charge une seule fois, reutilise entre appels
                           # (important pour l'API Flask : recharger le
                           # modele a chaque requete serait trop lent)


def get_modele_embedding() -> SentenceTransformer:
    global _modele_embedding
    if _modele_embedding is None:
        _modele_embedding = SentenceTransformer(MODELE_EMBEDDING)
    return _modele_embedding

# Chaque domaine est presente avec sa description (acl_config.DOMAIN_DESCRIPTIONS)
# pour que le routeur sache CE QUE CONTIENT chaque domaine et ne classe pas a
# l'aveugle a partir du seul nom (ex: "RGPD" -> 'securite', pas 'rh').
_CATALOGUE = "\n".join(f'- "{d}" : {DOMAIN_DESCRIPTIONS.get(d, d)}' for d in DOMAINES)
ROUTEUR_SYSTEM = (
    "Tu identifies quel(s) domaine(s), parmi cette liste, sont pertinents pour "
    "repondre a la question. Voici les domaines et ce que chacun couvre :\n"
    f"{_CATALOGUE}\n\n"
    "Reponds UNIQUEMENT avec un tableau JSON des cles de domaine pertinentes "
    "(exactement telles qu'ecrites entre guillemets ci-dessus), sans aucun texte "
    "ni balise de code autour. Exemple : [\"rh\", \"finance\"]. Si un seul "
    "domaine est clairement pertinent, ne renvoie que celui-la."
)

# Le routeur doit repondre en JSON pur, mais un modele glisse parfois un bloc
# ```json ... ``` autour -- on l'enleve avant de parser plutot que de rejeter
# une reponse par ailleurs correcte.
RE_BLOC_CODE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def classer_domaines(question: str) -> list[str]:
    # Pas de parametre "temperature" ici : supprime sur claude-opus-5 (400 si
    # fourni) -- le format de sortie strict est impose par le prompt.
    brut = appeler_llm(ROUTEUR_SYSTEM, question).strip()
    nettoye = RE_BLOC_CODE.sub("", brut).strip()
    try:
        domaines = json.loads(nettoye)
    except json.JSONDecodeError:
        print(f"[routeur] reponse non-JSON, ignoree : {brut!r}")
        return []
    if not isinstance(domaines, list):
        print(f"[routeur] reponse JSON inattendue (pas une liste), ignoree : {brut!r}")
        return []
    return [d for d in domaines if d in DOMAINES]


def agent_domaine(domaine: str, question: str, modele: SentenceTransformer) -> tuple[str, list[dict]]:
    chunks = rechercher(question, [domaine], modele)
    if not chunks:
        return "Aucun document trouve dans ce domaine pour cette question.", []
    system_prompt = (
        f"Tu es l'agent specialise du domaine '{domaine}'. Tu reponds "
        "UNIQUEMENT a partir des extraits fournis entre balises <extraits>, "
        "qui appartiennent tous a ce domaine. Cite tes sources sous la forme "
        "[Titre, page X]. Si les extraits ne suffisent pas, dis-le. Reponds "
        "en francais, de maniere concise."
    )
    message = f"<extraits>\n{construire_contexte(chunks)}\n</extraits>\n\nQuestion : {question}"
    return appeler_llm(system_prompt, message), chunks


def traiter_question(question: str, email: str) -> dict:
    """Logique complete, sans aucun print -- reutilisable par le CLI (main)
    et par l'API Flask (api.py). Retourne un dict serialisable en JSON."""
    groupes = groupes_utilisateur(email)
    if not groupes:
        return {"erreur": f"Aucun groupe trouve pour {email} (utilisateur inconnu de PostgreSQL ?)"}

    domaines_pertinents = classer_domaines(question)
    autorises = [d for d in domaines_pertinents if d in groupes]
    refuses = [d for d in domaines_pertinents if d not in groupes]

    resultats = []
    if autorises:
        modele = get_modele_embedding()
        for domaine in autorises:
            reponse, chunks = agent_domaine(domaine, question, modele)
            resultats.append({
                "domaine": domaine,
                "reponse": reponse,
                "sources": [reference(c) for c in chunks],
            })

    return {
        "utilisateur": email,
        "groupes": groupes,
        "domaines_identifies": domaines_pertinents,
        "domaines_refuses": refuses,
        "resultats": resultats,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--email", required=True)
    args = ap.parse_args()

    try:
        r = traiter_question(args.question, args.email)
    except LLMError as e:
        print(e.message)
        return
    if "erreur" in r:
        print(r["erreur"])
        return

    print(f"Utilisateur : {r['utilisateur']}  (groupes : {r['groupes']})")
    print(f"Domaines identifies par le routeur : {r['domaines_identifies'] or '(aucun)'}")
    if r["domaines_refuses"]:
        print(f"Domaines pertinents mais NON autorises pour cet utilisateur (ignores) : {r['domaines_refuses']}")
    if not r["resultats"]:
        print("Aucun domaine pertinent et autorise -- pas de reponse possible.")
        return

    for res in r["resultats"]:
        print(f"\n=== Agent '{res['domaine']}' ===")
        print(res["reponse"])
        if res["sources"]:
            print("Sources :")
            for s in res["sources"]:
                print(f"  - {s}")


if __name__ == "__main__":
    main()
