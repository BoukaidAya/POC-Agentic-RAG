# POC Agentic RAG avec droits d'accès — guide de démarrage

## Objectif du projet

Assistant interne agentique multi-domaine (RH, juridique du travail, marketing,
sécurité/RGPD, finance) qui répond avec citation de source, **en respectant les
droits d'accès de l'utilisateur**. Pas un chatbot générique : un système qui
filtre par droits *avant* de chercher, cherche dans le bon domaine, et reste
traçable.

## Prérequis

- Python ≥ 3.10
- [`uv`](https://docs.astral.sh/uv/) (gestionnaire de dépendances/paquets)
- Docker Desktop installé et démarré

## Installation complète, dans l'ordre

```bash
# 1. Dependances Python (lit pyproject.toml, cree un .venv)
uv sync

# 2. Infrastructure locale : OpenSearch + Dashboards + PostgreSQL
docker compose up -d

# 3. Verifier qu'OpenSearch repond (peut prendre 30-60s au premier demarrage)
curl http://localhost:9200
```

## Pipeline de données, étape par étape (avec l'intérêt de chaque étape)

```bash
uv run python extract_pdfs.py
```
Extrait le texte de tous les PDF (`PyMuPDF`), page par page, avec les liens
externes réels (annotations, pas le texte affiché) et une détection du vrai
titre du document quand le nom de fichier est trompeur (cas trouvé sur les
27 PDF Finance — nom de fichier ≠ contenu réel, corrigé en lisant le titre
dans le corps du texte). → `extracted/corpus.jsonl`

```bash
uv run python clean_corpus.py
```
Écarte les PDF quasi vides ou bloqués (pages anti-bot), déduplique les PDF
identiques présents dans plusieurs dossiers, supprime les en-têtes/pieds de
page répétés (uniquement en bord de page, pour ne jamais supprimer du
contenu réel). → `extracted/clean/corpus_clean.jsonl`

```bash
uv run python chunk_corpus.py
```
Découpe chaque document en passages ("chunks"), avec une stratégie qui
dépend de la nature du document : un article de loi = un chunk (avec son
fil d'Ariane Titre>Chapitre>Section), un document court = un seul chunk, un
guide générique = regroupement de pages jusqu'à une taille cible.
→ `extracted/chunks/chunks.jsonl` (4783 chunks sur le corpus actuel)

```bash
uv run python embed_chunks.py
```
Calcule un vecteur (embedding) par chunk avec le modèle **BGE-M3**
(multilingue, bon support du français), via `sentence-transformers`. Sert à
la recherche par similarité sémantique. ~0.5s/chunk sur CPU — prévoir du
temps sur le corpus complet (~45 min). → `extracted/embeddings/chunks_embeddings.jsonl`
(fichier volontairement non versionné dans git — régénérable, 117 Mo, au-dessus
de la limite GitHub de 100 Mo).

```bash
uv run python create_index.py --recreate
```
Crée l'index OpenSearch (schéma défini dans `mapping.json`) et un pipeline
de recherche hybride qui combine score BM25 (mots-clés) et score kNN
(similarité vectorielle) sur une échelle commune.

```bash
uv run python bulk_index.py
```
Envoie tous les chunks vectorisés dans OpenSearch, avec un droit d'accès
initial dérivé du dossier source (`acl_config.py`).

```bash
uv run python seed_acl.py
```
Crée les tables PostgreSQL (`schema.sql`) et les peuple : un groupe par
dossier thématique, un document par PDF retenu, les droits associés, et
4 utilisateurs de test avec des combinaisons de groupes différentes.

```bash
uv run python sync_acl.py
```
Relit les droits réels depuis PostgreSQL et les réécrit dans OpenSearch
(champ `groupes_acl` de chaque chunk). **À relancer à chaque fois qu'un
droit change dans PostgreSQL.**

## Pourquoi PostgreSQL pour les droits d'accès (et pas juste OpenSearch)

**PostgreSQL est une base relationnelle** : les données vivent dans des
tables qui se référencent entre elles (clés étrangères). OpenSearch est un
moteur de **recherche**, optimisé pour trouver du texte/des vecteurs vite —
pas pour gérer des relations entre entités.

Or les droits d'accès sont fondamentalement une relation : utilisateurs ↔
groupes ↔ documents (many-to-many dans les deux sens). C'est exactement ce
pour quoi les bases relationnelles existent.

Ce que ça apporte concrètement :
- **Intégrité garantie** : `schema.sql` définit `groupe_id INTEGER REFERENCES
  groupes(id)` — Postgres refuse d'insérer un droit vers un groupe qui
  n'existe pas. Impossible avec un dictionnaire Python ou un champ libre.
- **Modifiable sans toucher au code** : changer un droit = une ligne SQL,
  pas une modification de code à redéployer.
- **Auditabilité** : "quels utilisateurs ont accès à Finance ?" = une
  requête SQL de 3 lignes. Impossible à poser proprement dans un moteur de
  recherche.
- **Une seule vérité** : PostgreSQL décide qui a accès à quoi. OpenSearch ne
  garde qu'une **copie dénormalisée** (`groupes_acl`) pour filtrer vite
  pendant la recherche — on ne modifie jamais ce champ directement dans
  OpenSearch, il serait écrasé au prochain `sync_acl.py`.

Le filtre ACL s'applique **avant** le calcul du score de pertinence (BM25 et
kNN), jamais après — sinon on risquerait de filtrer un résultat pertinent
qui n'était déjà plus dans le top-k retourné.

## Tester le système

```bash
# Question posée avec les droits d'un groupe donne
uv run python search_test.py "taux de cotisation VTC auto-entrepreneur" --groupes rh
uv run python search_test.py "financement par obligations" --groupes finance

# Le test le plus important : un groupe SANS le droit ne doit RIEN voir
# de pertinent, meme sur une question qui correspond directement au domaine
uv run python search_test.py "financement par obligations" --groupes rh
```

### Modifier un droit d'accès (exemple : partager un document entre domaines)

```bash
docker exec -it postgres-acl psql -U ragadmin -d agentic_rag
```
```sql
INSERT INTO document_groupes (doc_path, groupe_id)
SELECT 'RH/PPN-FP-06-Prelevement-FR.pdf.pdf', id FROM groupes WHERE nom = 'finance';
\q
```
```bash
uv run python sync_acl.py   # repercute le changement dans OpenSearch
uv run python search_test.py "taux de prelevement plateforme" --groupes finance
```

## État d'avancement

| Phase | Statut |
|---|---|
| Extraction, nettoyage, chunking | ✅ fait — 128 PDF sources, 123 retenus, 4783 chunks |
| Titres réels des documents | ✅ fait — corrige le décalage nom de fichier/contenu sur les PDF Finance |
| Embeddings BGE-M3 | ✅ fait — 4783/4783 chunks vectorisés |
| Index OpenSearch + recherche hybride | ✅ fait, validé |
| ACL PostgreSQL | ✅ fait, validé (droit ajouté/retiré, répercuté correctement) |
| Reranking | ⬜ non commencé |
| Agent(s) génératif(s) (LangChain/LlamaIndex) | ⬜ non commencé — nécessite de choisir un LLM |
| Interface + journalisation | ⬜ non commencé |

## Organisation Git

- `data-extraction` : branche "tronc", contient les phases validées.
- `embeddings-indexing`, `acl-postgres` : branches de phase, mergées dans le
  tronc une fois validées.
- Une branche par couche fonctionnelle — évite de mélanger des concerns qui
  évoluent à des rythmes différents (le nettoyage de données est stable, la
  couche de recherche/agents va beaucoup itérer).

Documentation complémentaire dans `documentation/CHANGELOG.md` (historique
détaillé, bugs trouvés et corrigés) et `documentation/ARCHITECTURE.md`
(choix technologiques et pourquoi).
