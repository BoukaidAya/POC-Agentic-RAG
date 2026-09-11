#!/usr/bin/env python3
"""Boucle RAG minimale, sans framework : question -> droits reels de
l'utilisateur (PostgreSQL) -> recherche hybride filtree (OpenSearch) ->
Mistral API -> reponse sourcee.

Volontairement sans LangChain/LlamaIndex pour l'instant : valider que
la chaine complete marche, un maillon a la fois, avant d'ajouter la
complexite d'un framework d'orchestration par-dessus.

Necessite la variable d'environnement MISTRAL_API_KEY (jamais dans le code
ni en argument de ligne de commande).

Usage:
    python rag_query.py --email alice@art.fr "question en francais"
"""
import argparse
import os
import sys

import psycopg2
import requests
from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer

sys.stdout.reconfigure(encoding="utf-8")

DSN = "host=localhost port=5432 dbname=agentic_rag user=ragadmin password=ragadmin_dev_only"
HOTE, PORT = "localhost", 9200
INDEX_NAME = "chunks_rag"
PIPELINE_NAME = "hybrid-search-pipeline"
MODELE_EMBEDDING = "BAAI/bge-m3"
MODELE_MISTRAL = "mistral-small-latest"
K = 6

# Instructions et contenu recupere strictement separes (deux messages
# differents) : le modele ne doit jamais confondre "ce qu'on lui demande de
# faire" et "le contenu qu'on lui donne a lire", meme si ce contenu vient de
# documents externes.
SYSTEM_PROMPT = (
    "Tu es un assistant interne qui repond UNIQUEMENT a partir des extraits "
    "de documents fournis entre balises <extraits>. N'utilise aucune autre "
    "connaissance. Si les extraits ne permettent pas de repondre, dis-le "
    "clairement plutot que d'inventer. Cite systematiquement tes sources "
    "sous la forme [Titre, page X]. Reponds en francais, de maniere concise."
)


def groupes_utilisateur(email: str) -> list[str]:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """SELECT g.nom FROM utilisateurs u
           JOIN utilisateur_groupes ug ON ug.utilisateur_id = u.id
           JOIN groupes g ON g.id = ug.groupe_id
           WHERE u.email = %s""",
        (email,),
    )
    groupes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return groupes


def rechercher(question: str, groupes: list[str], modele: SentenceTransformer) -> list[dict]:
    client = OpenSearch(hosts=[{"host": HOTE, "port": PORT}], use_ssl=False, verify_certs=False)
    vecteur = modele.encode(question, normalize_embeddings=True).tolist()
    filtre_acl = {"terms": {"groupes_acl": groupes}}
    requete = {
        "size": K,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "bool": {
                            "must": [{"match": {"texte": {"query": question}}}],
                            "filter": [filtre_acl],
                        }
                    },
                    {
                        "knn": {
                            "embedding": {"vector": vecteur, "k": K * 4, "filter": filtre_acl}
                        }
                    },
                ]
            }
        },
    }
    reponse = client.search(
        index=INDEX_NAME, body=requete, params={"search_pipeline": PIPELINE_NAME})
    return [h["_source"] for h in reponse["hits"]["hits"]]


def reference(chunk: dict) -> str:
    titre = chunk.get("titre_document") or chunk["filename"]
    pos = f"[{chunk['article']}]" if chunk.get("article") else f"p.{chunk['page_debut']}-{chunk['page_fin']}"
    return f"{titre} {pos}"


def construire_contexte(chunks: list[dict]) -> str:
    blocs = [f"Source {i} -- {reference(c)} :\n{c['texte']}" for i, c in enumerate(chunks, 1)]
    return "\n\n".join(blocs)


def appeler_mistral(contexte: str, question: str) -> str:
    cle = os.environ.get("MISTRAL_API_KEY")
    if not cle:
        raise SystemExit(
            "Variable d'environnement MISTRAL_API_KEY manquante.\n"
            'PowerShell : $env:MISTRAL_API_KEY = "ta_cle"')

    reponse = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        json={
            "model": MODELE_MISTRAL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"<extraits>\n{contexte}\n</extraits>\n\nQuestion : {question}"},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    reponse.raise_for_status()
    return reponse.json()["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--email", required=True, help="utilisateur simule, ex: alice@art.fr")
    args = ap.parse_args()

    groupes = groupes_utilisateur(args.email)
    if not groupes:
        print(f"Aucun groupe trouve pour {args.email} (utilisateur inconnu de PostgreSQL ?)")
        return
    print(f"Utilisateur : {args.email}  (groupes : {groupes})")

    modele = SentenceTransformer(MODELE_EMBEDDING)
    chunks = rechercher(args.question, groupes, modele)
    if not chunks:
        print("Aucun document accessible ne correspond a la question.")
        return

    reponse = appeler_mistral(construire_contexte(chunks), args.question)

    print("\n--- Reponse ---")
    print(reponse)
    print("\n--- Sources utilisees (recherche, avant filtrage par le modele) ---")
    for c in chunks:
        print(f"  - {reference(c)}")


if __name__ == "__main__":
    main()
