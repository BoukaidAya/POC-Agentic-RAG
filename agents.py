#!/usr/bin/env python3
"""Premiere version simple des agents par domaine : un routeur (Mistral)
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
import sys

from sentence_transformers import SentenceTransformer

from acl_config import FOLDER_TO_GROUPE
from rag_query import appeler_mistral, construire_contexte, groupes_utilisateur, rechercher, reference

sys.stdout.reconfigure(encoding="utf-8")

DOMAINES = sorted(set(FOLDER_TO_GROUPE.values()))

ROUTEUR_SYSTEM = (
    "Tu identifies quel(s) domaine(s) parmi cette liste exacte sont pertinents "
    f"pour repondre a la question : {DOMAINES}. "
    "Reponds UNIQUEMENT avec un tableau JSON de chaines prises dans cette liste "
    "exacte, sans aucun texte autour. Exemple : [\"rh\", \"finance\"]. "
    "Si un seul domaine est clairement pertinent, ne renvoie que celui-la."
)


def classer_domaines(question: str) -> list[str]:
    brut = appeler_mistral(ROUTEUR_SYSTEM, question, temperature=0.0)
    try:
        domaines = json.loads(brut)
    except json.JSONDecodeError:
        print(f"[routeur] reponse non-JSON, ignoree : {brut!r}")
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
    return appeler_mistral(system_prompt, message), chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--email", required=True)
    args = ap.parse_args()

    groupes = groupes_utilisateur(args.email)
    if not groupes:
        print(f"Aucun groupe trouve pour {args.email}")
        return
    print(f"Utilisateur : {args.email}  (groupes : {groupes})")

    domaines_pertinents = classer_domaines(args.question)
    print(f"Domaines identifies par le routeur : {domaines_pertinents or '(aucun)'}")

    autorises = [d for d in domaines_pertinents if d in groupes]
    refuses = [d for d in domaines_pertinents if d not in groupes]
    if refuses:
        print(f"Domaines pertinents mais NON autorises pour cet utilisateur (ignores) : {refuses}")
    if not autorises:
        print("Aucun domaine pertinent et autorise -- pas de reponse possible.")
        return

    modele = SentenceTransformer("BAAI/bge-m3")
    for domaine in autorises:
        reponse, chunks = agent_domaine(domaine, args.question, modele)
        print(f"\n=== Agent '{domaine}' ===")
        print(reponse)
        if chunks:
            print("Sources :")
            for c in chunks:
                print(f"  - {reference(c)}")


if __name__ == "__main__":
    main()
