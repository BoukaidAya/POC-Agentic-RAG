#!/usr/bin/env python3
"""Applique schema.sql puis peuple PostgreSQL depuis extracted/chunks/chunks.jsonl.

Chaque document recoit le(s) groupe(s) de son dossier (acl_config.py) --
c'est ce qui fait de PostgreSQL la source de verite : modifie ici, puis
sync_acl.py repercute le changement dans OpenSearch.

Usage:
    python seed_acl.py
"""
import json
from pathlib import Path

import psycopg2

from acl_config import FOLDER_TO_GROUPE, groupe_pour_dossier

ROOT = Path(__file__).resolve().parent
CHUNKS_PATH = ROOT / "extracted" / "chunks" / "chunks.jsonl"
SCHEMA_PATH = ROOT / "schema.sql"

DSN = "host=localhost port=5432 dbname=agentic_rag user=ragadmin password=ragadmin_dev_only"

# Quelques utilisateurs de test, avec des combinaisons de groupes differentes
# -- utile pour verifier le filtre ACL sur un utilisateur multi-domaine.
UTILISATEURS_TEST = [
    ("alice@art.fr", "Alice (RH)", ["rh"]),
    ("bob@art.fr", "Bob (Finance)", ["finance"]),
    ("carla@art.fr", "Carla (Juridique + Securite)", ["juridique", "securite"]),
    ("admin@art.fr", "Admin (tous domaines)", list(FOLDER_TO_GROUPE.values())),
]


def main():
    if not CHUNKS_PATH.exists():
        print(f"Introuvable : {CHUNKS_PATH} (lancer d'abord chunk_corpus.py)")
        return

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    print("Schema applique.")

    # groupes
    for groupe in set(FOLDER_TO_GROUPE.values()):
        cur.execute(
            "INSERT INTO groupes (nom) VALUES (%s) ON CONFLICT (nom) DO NOTHING", (groupe,))

    # documents (un par doc_path unique dans chunks.jsonl)
    docs = {}
    with CHUNKS_PATH.open(encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            docs[c["doc_path"]] = c

    for doc_path, c in docs.items():
        cur.execute(
            """INSERT INTO documents (doc_path, folder, filename, titre_document)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (doc_path) DO UPDATE SET
                 folder = EXCLUDED.folder, filename = EXCLUDED.filename,
                 titre_document = EXCLUDED.titre_document""",
            (doc_path, c["folder"], c["filename"], c.get("titre_document", "")),
        )
        groupe = groupe_pour_dossier(c["folder"])
        cur.execute("SELECT id FROM groupes WHERE nom = %s", (groupe,))
        groupe_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO document_groupes (doc_path, groupe_id) VALUES (%s, %s)
               ON CONFLICT DO NOTHING""",
            (doc_path, groupe_id),
        )
    conn.commit()
    print(f"{len(docs)} documents et leurs droits inseres.")

    # utilisateurs de test
    for email, nom, groupes in UTILISATEURS_TEST:
        cur.execute(
            """INSERT INTO utilisateurs (email, nom) VALUES (%s, %s)
               ON CONFLICT (email) DO UPDATE SET nom = EXCLUDED.nom
               RETURNING id""",
            (email, nom),
        )
        utilisateur_id = cur.fetchone()[0]
        for groupe in groupes:
            cur.execute("SELECT id FROM groupes WHERE nom = %s", (groupe,))
            groupe_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO utilisateur_groupes (utilisateur_id, groupe_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (utilisateur_id, groupe_id),
            )
    conn.commit()
    print(f"{len(UTILISATEURS_TEST)} utilisateurs de test inseres.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
