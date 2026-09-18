-- Source de verite des droits d'acces et des metadonnees documentaires.
-- OpenSearch ne garde qu'une copie denormalisee (champ groupes_acl) resynchronisee
-- depuis ces tables par sync_acl.py -- on ne modifie jamais les droits directement
-- dans OpenSearch.

CREATE TABLE IF NOT EXISTS groupes (
    id   SERIAL PRIMARY KEY,
    nom  TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS utilisateurs (
    id             SERIAL PRIMARY KEY,
    email          TEXT UNIQUE NOT NULL,
    nom            TEXT NOT NULL,
    password_hash  TEXT
);
-- Pour les bases créées avant l'ajout de l'authentification :
ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS password_hash TEXT;

CREATE TABLE IF NOT EXISTS utilisateur_groupes (
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
    groupe_id       INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    PRIMARY KEY (utilisateur_id, groupe_id)
);

-- Un document par PDF retenu (chunks.jsonl en a plusieurs par document,
-- ici on ne garde que l'identite du document lui-meme).
CREATE TABLE IF NOT EXISTS documents (
    doc_path        TEXT PRIMARY KEY,
    folder          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    titre_document  TEXT
);

CREATE TABLE IF NOT EXISTS document_groupes (
    doc_path   TEXT NOT NULL REFERENCES documents(doc_path) ON DELETE CASCADE,
    groupe_id  INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    PRIMARY KEY (doc_path, groupe_id)
);

CREATE INDEX IF NOT EXISTS idx_document_groupes_doc ON document_groupes(doc_path);
