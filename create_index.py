#!/usr/bin/env python3
"""Cree l'index OpenSearch (mapping.json) + le pipeline de recherche hybride.

Le pipeline de recherche (normalization-processor) est ce qui permet de
combiner un score BM25 et un score kNN dans une seule requete "hybrid" :
sans lui, les deux scores ne sont pas sur la meme echelle et un des deux
ecrase systematiquement l'autre.

Usage:
    python create_index.py            # cree si absent
    python create_index.py --recreate # supprime et recree (perd les donnees)
"""
import argparse
import json
from pathlib import Path

from opensearchpy import OpenSearch

from config import OS_HOST, OS_PORT

ROOT = Path(__file__).resolve().parent
MAPPING_PATH = ROOT / "mapping.json"
INDEX_NAME = "chunks_rag"
PIPELINE_NAME = "hybrid-search-pipeline"

PIPELINE_DEF = {
    "description": "normalise puis combine les scores BM25 et kNN",
    "phase_results_processors": [
        {
            "normalization-processor": {
                "normalization": {"technique": "min_max"},
                "combination": {
                    "technique": "arithmetic_mean",
                    "parameters": {"weights": [0.5, 0.5]},
                },
            }
        }
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true",
                     help="supprime l'index existant avant de le recreer")
    args = ap.parse_args()

    client = OpenSearch(hosts=[{"host": OS_HOST, "port": OS_PORT}], use_ssl=False, verify_certs=False)

    if not client.ping():
        print(f"Impossible de joindre OpenSearch sur {OS_HOST}:{OS_PORT} "
              f"(Docker Desktop + docker compose up ?)")
        return

    if client.indices.exists(index=INDEX_NAME):
        if args.recreate:
            client.indices.delete(index=INDEX_NAME)
            print(f"Index '{INDEX_NAME}' supprime.")
        else:
            print(f"Index '{INDEX_NAME}' existe deja (--recreate pour repartir a zero).")
            return

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    client.indices.create(index=INDEX_NAME, body=mapping)
    print(f"Index '{INDEX_NAME}' cree.")

    client.transport.perform_request(
        "PUT", f"/_search/pipeline/{PIPELINE_NAME}", body=PIPELINE_DEF)
    print(f"Pipeline de recherche hybride '{PIPELINE_NAME}' cree.")


if __name__ == "__main__":
    main()
