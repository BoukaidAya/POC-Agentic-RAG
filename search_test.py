#!/usr/bin/env python3
"""Requete hybride (BM25 + kNN) filtree par droits d'acces -- pour voir et
tester ce que l'indexation a produit.

Le filtre ACL commun au niveau racine de "hybrid" n'existe qu'a partir
d'OpenSearch 3.0 (on est en 2.19.1, fige pour la stabilite) -- on duplique
donc le filtre "groupes_acl" DANS chaque sous-requete : en "filter" d'un
bool pour le BM25, et dans le parametre "filter" natif du kNN (necessite le
moteur "faiss" ou "lucene" -- "nmslib" ne supporte pas le filtrage pendant
la recherche kNN). Applique AVANT le scoring dans les deux cas, jamais
apres -- sinon on risque de filtrer un resultat pertinent qui n'etait deja
plus dans le top-k retourne.

Usage:
    python search_test.py "question en francais" --groupes rh juridique
"""
import argparse
import sys

from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer

sys.stdout.reconfigure(encoding="utf-8")  # PowerShell est souvent en cp1252,
                                           # qui plante sur certains caracteres
                                           # du texte extrait des PDF

HOTE, PORT = "localhost", 9200
INDEX_NAME = "chunks_rag"
PIPELINE_NAME = "hybrid-search-pipeline"
MODELE = "BAAI/bge-m3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--groupes", nargs="+", required=True,
                     help="groupes ACL de l'utilisateur simule, ex: rh juridique")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    client = OpenSearch(hosts=[{"host": HOTE, "port": PORT}], use_ssl=False, verify_certs=False)
    if not client.ping():
        print(f"Impossible de joindre OpenSearch sur {HOTE}:{PORT}")
        return

    modele = SentenceTransformer(MODELE)
    vecteur = modele.encode(args.question, normalize_embeddings=True).tolist()

    filtre_acl = {"terms": {"groupes_acl": args.groupes}}
    requete = {
        "size": args.k,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "bool": {
                            "must": [{"match": {"texte": {"query": args.question}}}],
                            "filter": [filtre_acl],
                        }
                    },
                    {
                        "knn": {
                            "embedding": {
                                "vector": vecteur,
                                "k": args.k * 4,
                                "filter": filtre_acl,
                            }
                        }
                    },
                ],
            }
        },
    }

    reponse = client.search(
        index=INDEX_NAME, body=requete, params={"search_pipeline": PIPELINE_NAME})

    print(f"Question : {args.question}")
    print(f"Groupes autorises : {args.groupes}\n")
    for hit in reponse["hits"]["hits"]:
        s = hit["_source"]
        ref = f"[{s['article']}]" if s.get("article") else f"p.{s['page_debut']}-{s['page_fin']}"
        titre = s.get("titre_document") or s["filename"]
        print(f"score={hit['_score']:.3f}  {titre} {ref}  ({s['doc_path']})")
        print(f"  {s['texte'][:200]}...\n")


if __name__ == "__main__":
    main()
