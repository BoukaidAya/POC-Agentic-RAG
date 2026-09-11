#!/usr/bin/env python3
"""Indexe extracted/embeddings/chunks_embeddings.jsonl dans OpenSearch.

Ajoute le champ "groupes_acl" a chaque chunk a partir de son dossier source
(acl_config.py) : c'est ce champ que le filtre de droits utilisera avant
toute recherche. Valeur initiale seulement -- sync_acl.py la re-ecrit
ensuite depuis PostgreSQL (source de verite des droits reels).

Usage:
    python bulk_index.py
"""
import json
from pathlib import Path

from opensearchpy import OpenSearch, helpers

from acl_config import groupe_pour_dossier

ROOT = Path(__file__).resolve().parent
EMBEDDINGS_PATH = ROOT / "extracted" / "embeddings" / "chunks_embeddings.jsonl"
HOTE, PORT = "localhost", 9200
INDEX_NAME = "chunks_rag"


def vers_document(chunk: dict) -> dict:
    groupe = groupe_pour_dossier(chunk["folder"])
    return {
        "_index": INDEX_NAME,
        "_id": chunk["chunk_id"],
        "_source": {
            "chunk_id": chunk["chunk_id"],
            "doc_path": chunk["doc_path"],
            "folder": chunk["folder"],
            "filename": chunk["filename"],
            "titre_document": chunk.get("titre_document", ""),
            "doc_type": chunk["doc_type"],
            "type": chunk["type"],
            "article": chunk.get("article"),
            "fil_ariane": " > ".join(chunk.get("fil_ariane", [])),
            "page_debut": chunk["page_debut"],
            "page_fin": chunk["page_fin"],
            "n_chars": chunk["n_chars"],
            "texte": chunk["texte"],
            "groupes_acl": [groupe],
            "liens": chunk.get("liens", []),
            "embedding": chunk["embedding"],
        },
    }


def main():
    if not EMBEDDINGS_PATH.exists():
        print(f"Introuvable : {EMBEDDINGS_PATH} (lancer d'abord embed_chunks.py)")
        return

    client = OpenSearch(hosts=[{"host": HOTE, "port": PORT}], use_ssl=False, verify_certs=False)
    if not client.ping():
        print(f"Impossible de joindre OpenSearch sur {HOTE}:{PORT}")
        return

    chunks = [json.loads(l) for l in EMBEDDINGS_PATH.open(encoding="utf-8")]
    actions = (vers_document(c) for c in chunks)

    ok, erreurs = helpers.bulk(client, actions, raise_on_error=False)
    print(f"Indexes : {ok}/{len(chunks)}")
    if erreurs:
        print(f"Erreurs ({len(erreurs)}), 3 premieres :")
        for e in erreurs[:3]:
            print(" ", e)

    client.indices.refresh(index=INDEX_NAME)
    stats = client.count(index=INDEX_NAME)
    print(f"Total de documents dans l'index : {stats['count']}")


if __name__ == "__main__":
    main()
