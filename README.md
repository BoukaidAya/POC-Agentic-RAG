# POC — Assistant documentaire agentique avec contrôle d'accès

Assistant interne qui répond à des questions en français à partir des documents
de l'entreprise, **en respectant les droits d'accès de chaque utilisateur** et en
**citant ses sources**. Ce n'est pas un chatbot généraliste : le système filtre
par droits *avant* de chercher, ne cherche que dans les bons domaines, et reste
traçable.

- **RAG** (Retrieval-Augmented Generation) : recherche des extraits pertinents,
  puis génération d'une réponse fondée uniquement sur ces extraits.
- **Agentique** : un routeur identifie les domaines concernés, puis un agent
  spécialisé répond par domaine (RH, juridique, finance, sécurité/RGPD,
  marketing, management).
- **Contrôle d'accès (ACL)** : PostgreSQL fait autorité sur les droits ;
  OpenSearch en garde une copie (`groupes_acl`) pour filtrer la recherche.

> Documentation détaillée dans [`documentation/`](documentation/) :
> [WIKI.md](documentation/WIKI.md), [ARCHITECTURE.md](documentation/ARCHITECTURE.md),
> [EXEMPLES-RECHERCHE.md](documentation/EXEMPLES-RECHERCHE.md) et les schémas
> `.drawio` (architecture, gestion des droits).

---

## Architecture en bref

```
données/<domaine>/*.pdf                    (corpus source, un dossier par domaine)
        │
        ▼  (pipeline hors-ligne)
 extraction → nettoyage → découpage → embeddings (BGE-M3) → indexation
        │                                                      │
        ▼                                                      ▼
 PostgreSQL (droits : qui a accès à quoi)          OpenSearch (recherche hybride
        │  source de vérité                          BM25 + vectoriel + groupes_acl)
        └───────────── sync_acl ───────────────────────────────┘

Requête : Utilisateur → API → routeur → agent(s) de domaine autorisé(s)
          → recherche hybride filtrée par droits → réponse sourcée (Claude)
```

| Brique | Rôle |
|---|---|
| **PyMuPDF** | extraction du texte des PDF |
| **BGE-M3** | embeddings (vecteurs 1024 dim.) |
| **OpenSearch 2.19** | moteur de recherche hybride (BM25 + kNN/faiss) + `groupes_acl` |
| **PostgreSQL 16** | source de vérité des droits (utilisateurs, groupes, documents) |
| **Claude (API Anthropic)** | génération de la réponse sourcée |
| **Flask** | API HTTP (`/chat`, `/sante`, `/document`, `/groupes`, `/utilisateurs`) |
| **Docker** | exécute OpenSearch, PostgreSQL et le conteneur applicatif Python |

> **Pourquoi tout passe par Docker ?** Sous Windows, Smart App Control bloque les
> DLL non signées de PyTorch. La partie Python/torch tourne donc dans un
> conteneur Linux (service `app`), où cette restriction ne s'applique pas — sans
> avoir à désactiver Smart App Control sur la machine.

---

## Prérequis

- **Docker Desktop** installé et démarré.
- **git**.
- Une **clé API Anthropic** (https://console.anthropic.com/settings/keys).

Rien d'autre à installer : Python, torch, OpenSearch et PostgreSQL tournent tous
dans des conteneurs.

---

## Installation

Toutes les commandes se lancent depuis la racine du projet.

```bash
# 1. Récupérer le projet
git clone <url-du-depot>
cd data

# 2. Clé API : copier le modèle et y coller la vraie clé
cp .env.example .env
#   puis éditer .env :  ANTHROPIC_API_KEY=sk-ant-...

# 3. Démarrer l'infrastructure (OpenSearch + PostgreSQL ; dashboards optionnel)
docker compose up -d opensearch postgres

# 4. Construire l'image applicative (torch CPU + dépendances, quelques minutes)
docker compose build app
```

> **Ports.** OpenSearch est publié sur **`localhost:9201`** côté hôte (le 9200
> peut être occupé par une autre instance) ; PostgreSQL sur `5432`, les
> dashboards sur `5601`. En interne, les conteneurs se joignent par leur nom de
> service (`opensearch:9200`, `postgres:5432`).

### Construire l'index et les droits

Le corpus découpé (`extracted/chunks/chunks.jsonl`) est versionné, mais **les
embeddings ne le sont pas** (trop volumineux) : il faut les (re)générer.

```bash
# 5. Embeddings BGE-M3 (~45 min sur CPU ; reprise automatique si interrompu)
docker compose run --rm --no-deps app python embed_chunks.py

# 6. Index OpenSearch + pipeline de recherche hybride
docker compose run --rm --no-deps app python create_index.py

# 7. Indexer les 4783 chunks (doit afficher "Indexes : 4783/4783")
docker compose run --rm --no-deps app python bulk_index.py

# 8. Droits d'accès : schéma + documents + utilisateurs de test (PostgreSQL)
docker compose run --rm --no-deps app python seed_acl.py

# 9. Synchroniser les droits PostgreSQL → OpenSearch
docker compose run --rm --no-deps app python sync_acl.py
```

### (Optionnel) Régénérer le corpus depuis les PDF

Uniquement si vous modifiez les documents sous `données/` :

```bash
docker compose run --rm --no-deps app python extract_pdfs.py
docker compose run --rm --no-deps app python clean_corpus.py
docker compose run --rm --no-deps app python chunk_corpus.py
# puis reprendre à l'étape 5 (embeddings)
```

---

## Utilisation

### En ligne de commande

```bash
# Réponse simple (recherche filtrée + génération), pour un utilisateur donné
docker compose run --rm app python rag_query.py --email alice@art.fr "Comment fonctionne le bonus-malus ?"

# Mode agentique (routeur + un agent par domaine autorisé)
docker compose run --rm app python agents.py "Quels sont mes droits en cas de licenciement et quelles regles RGPD s'appliquent ?" --email carla@art.fr

# Recherche seule, sans génération (utile pour tester l'ACL)
docker compose run --rm --no-deps app python search_test.py "RGPD TPE PME" --groupes securite
```

### Via l'API HTTP

```bash
docker compose run --rm -p 127.0.0.1:5000:5000 app python api.py
```

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/sante` | GET | état de l'API |
| `/chat` | POST | `{ "question": "...", "email": "..." }` → réponse par domaine + sources |
| `/document?path=<doc_path>` | GET | sert le PDF source (`&dl=1` pour télécharger) |
| `/groupes` | GET | liste des domaines disponibles |
| `/utilisateurs` | POST | `{ "email", "nom", "groupes": [...] }` → crée/met à jour un utilisateur |

### Interface web (frontend)

Le frontend est un projet séparé (voir le dépôt `POC-Agentic-RAG-Frontend`). Une
fois l'API démarrée :

```bash
# dans le dossier du frontend
python -m http.server 8080      # puis ouvrir http://localhost:8080
```

---

## Utilisateurs et droits

Utilisateurs de test créés par `seed_acl.py` :

| Email | Domaines autorisés |
|---|---|
| `alice@art.fr` | rh |
| `bob@art.fr` | finance |
| `carla@art.fr` | juridique, securite |
| `admin@art.fr` | tous |

Ajouter un utilisateur (les droits prennent effet immédiatement, sans
réindexation) :

```bash
# via SQL
docker compose exec postgres psql -U ragadmin -d agentic_rag -c "INSERT INTO utilisateurs (email, nom) VALUES ('david@art.fr','David') ON CONFLICT (email) DO UPDATE SET nom=EXCLUDED.nom; INSERT INTO utilisateur_groupes (utilisateur_id, groupe_id) SELECT u.id, g.id FROM utilisateurs u JOIN groupes g ON g.nom IN ('rh','finance') WHERE u.email='david@art.fr' ON CONFLICT DO NOTHING;"
```

Ou depuis le frontend : bouton **« + Ajouter un utilisateur »**.

---

## Structure du projet

```
data/
├── données/                  corpus PDF, un sous-dossier par domaine
│   ├── Finance/  RH/  juridique droit du travail/  ...
├── extracted/                sorties du pipeline (texte, chunks ; embeddings non versionnés)
├── documentation/            wiki, architecture, exemples, schémas .drawio
├── extract_pdfs.py           1. extraction du texte des PDF
├── clean_corpus.py           2. nettoyage (dédup, boilerplate, fusion des dossiers)
├── chunk_corpus.py           3. découpage adaptatif en chunks
├── embed_chunks.py           4. embeddings BGE-M3
├── create_index.py           5. index OpenSearch + pipeline hybride
├── bulk_index.py             6. indexation des chunks
├── seed_acl.py               7. droits + utilisateurs (PostgreSQL)
├── sync_acl.py               8. synchronisation des droits → OpenSearch
├── rag_query.py              RAG simple (CLI)
├── agents.py                 couche agentique (routeur + agents)
├── api.py                    API Flask
├── search_test.py            test de recherche seule
├── config.py                 config réseau (OS_HOST / OS_PORT / PG_DSN)
├── acl_config.py             mapping dossier → domaine + descriptions
├── mapping.json              schéma de l'index OpenSearch
├── schema.sql                schéma PostgreSQL des droits
├── docker-compose.yml        opensearch, postgres, dashboards, app
├── Dockerfile                image applicative Linux (torch CPU + deps)
└── .env.example              modèle pour la clé API
```

---

## Dépannage

- **`docker compose up` échoue sur le port 9200** : une autre instance OpenSearch
  l'occupe. Le compose publie déjà OpenSearch sur `9201` côté hôte ; si le conflit
  persiste, arrêtez l'autre instance ou changez le port publié.
- **`bulk_index.py` n'affiche pas `4783/4783`** : vérifiez que les embeddings sont
  complets — relancez `embed_chunks.py` (il reprend là où il s'était arrêté ; le
  fichier `extracted/embeddings/chunks_embeddings.jsonl` doit avoir 4783 lignes).
- **`/document` renvoie 404** : le PDF doit exister sous `données/<doc_path>` ;
  vérifiez que le corpus est bien présent.
- **Erreur d'authentification Claude (502)** : `ANTHROPIC_API_KEY` absente ou
  invalide dans `.env`.
- **L'API ne répond pas depuis le navigateur** : lancez-la avec le mapping de port
  (`-p 127.0.0.1:5000:5000`) — elle écoute sur `0.0.0.0` dans le conteneur.

---

## Notes

- POC : à durcir avant toute mise en production (authentification réelle, CORS
  restreint, `Flask debug=False`, sécurité OpenSearch activée).
- Les mots de passe et DSN par défaut (`ragadmin_dev_only`) sont **pour le
  développement local uniquement**.
