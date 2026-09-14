#!/usr/bin/env python3
"""Resynchronise les droits d'acces PostgreSQL -> OpenSearch.

PostgreSQL est la source de verite : on y modifie les droits (table
document_groupes), puis ce script re-ecrit le champ "groupes_acl" de tous
les chunks concernes dans OpenSearch. Ne jamais modifier groupes_acl
directement dans OpenSearch -- il serait ecrase au prochain sync, et
surtout desynchronise de la vraie source de verite.

Usage:
    python sync_acl.py
"""
from pathlib import Path

import psycopg2
from opensearchpy import OpenSearch

from config import DSN, OS_HOST, OS_PORT

ROOT = Path(__file__).resolve().parent
INDEX_NAME = "chunks_rag"


def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT d.doc_path, array_agg(g.nom ORDER BY g.nom)
        FROM documents d
        JOIN document_groupes dg ON dg.doc_path = d.doc_path
        JOIN groupes g ON g.id = dg.groupe_id
        GROUP BY d.doc_path
    """)
    droits = cur.fetchall()
    cur.close()
    conn.close()

    if not droits:
        print("Aucun document dans PostgreSQL (lancer d'abord seed_acl.py).")
        return

    client = OpenSearch(hosts=[{"host": OS_HOST, "port": OS_PORT}], use_ssl=False, verify_certs=False)
    if not client.ping():
        print(f"Impossible de joindre OpenSearch sur {OS_HOST}:{OS_PORT}")
        return

    for doc_path, groupes in droits:
        client.update_by_query(
            index=INDEX_NAME,
            body={
                "query": {"term": {"doc_path": doc_path}},
                "script": {
                    "source": "ctx._source.groupes_acl = params.groupes",
                    "params": {"groupes": groupes},
                },
            },
            conflicts="proceed",
        )

    client.indices.refresh(index=INDEX_NAME)
    print(f"{len(droits)} documents resynchronises depuis PostgreSQL vers OpenSearch.")


if __name__ == "__main__":
    main()
