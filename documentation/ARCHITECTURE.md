# Architecture et choix technologiques

## Objectif du projet

Assistant interne agentique multi-domaine (RH, juridique du travail, marketing,
sécurité/RGPD, finance) qui répond avec citation de source, en respectant les
droits d'accès de l'utilisateur — pas un chatbot générique, un système qui
filtre par droits *avant* de chercher, cherche dans le bon domaine, et reste
traçable.

## Pourquoi chaque brique, et les alternatives écartées

### PyMuPDF pour l'extraction
Extraction rapide, donne accès au texte page par page, aux métadonnées et
aux annotations de liens (`page.get_links()`) dans la même bibliothèque —
évite d'avoir un outil pour le texte et un autre pour les liens.

### Découpe (chunking) adaptée à la nature du document, pas taille fixe
Un découpage uniforme (ex: 1000 caractères partout) casserait un article de
loi au milieu, ou fragmenterait inutilement une fiche déjà courte. Voir
`CHANGELOG.md` pour le détail des 3 stratégies (article / document entier /
regroupement de pages).

### BGE-M3 pour les embeddings
- Multilingue avec un bon support du français (le corpus est 100% en
  français à ce jour).
- Tourne via `sentence-transformers`, pas besoin d'un serveur d'inférence
  séparé pour un POC.
- Faisable sur CPU (~0.5s/chunk mesuré) — pas besoin de GPU pour ce volume
  (~5000 chunks). À revoir si le corpus grossit d'un ordre de grandeur.
- Famille cohérente avec le reranker prévu (`bge-reranker-v2-m3`).

### OpenSearch pour la recherche hybride (BM25 + kNN)
- **Pourquoi hybride et pas seulement vectoriel** : les embeddings seuls
  échouent souvent sur du texte exact (numéros d'article "L1221-1", codes,
  montants) — le BM25 lexical rattrape ce que le sens sémantique seul rate.
  Inversement, le kNN rattrape les reformulations que le BM25 seul rate. Les
  deux sont complémentaires, pas redondants.
- **Pourquoi un seul moteur (OpenSearch) plutôt que deux systèmes séparés**
  (ex: Elasticsearch pour le texte + Chroma/FAISS pour les vecteurs) :
  un seul système à maintenir, un seul endroit où appliquer le filtre ACL
  (sinon il faudrait synchroniser un filtre de droits dans deux moteurs
  différents, avec un risque de désynchronisation).
- **Pourquoi OpenSearch et pas Elasticsearch** : open source sans limitation
  de licence sur les fonctionnalités utilisées ici (k-NN, hybrid query),
  auto-hébergeable — cohérent avec l'exigence de souveraineté déjà
  identifiée dans le projet cible ("hors cloud public").
- Filtre ACL appliqué dans la clause `filter` de la requête `hybrid` :
  s'applique aux deux sous-requêtes (BM25 et kNN) **avant** le calcul du
  score — jamais un filtre a posteriori sur des résultats déjà tronqués au
  top-k.

### PostgreSQL pour l'ACL et les métadonnées
- Base relationnelle = intégrité du modèle de permissions (un utilisateur
  appartient à des groupes, un document est autorisé pour des groupes) —
  plus adapté qu'un stockage document pour ce type de relation.
- **Postgres = source de vérité, OpenSearch = copie dénormalisée** : on
  modifie les droits dans Postgres, puis on resynchronise le champ
  `groupes_acl` dans OpenSearch. Ne jamais faire l'inverse (modifier les
  droits directement dans l'index de recherche).
- Granularité retenue pour ce POC : **par dossier/domaine**, pas par document
  individuel — c'est le seul niveau que les données actuelles (PDF organisés
  par thème, pas de vraie source de permissions type SharePoint) permettent
  de simuler honnêtement.

### Docker Compose pour l'infra locale
- OpenSearch **nœud unique** plutôt que cluster à plusieurs nœuds : un POC
  solo n'a pas besoin de haute disponibilité, et un cluster multi-nœuds
  double la RAM utilisée et ajoute de la complexité de découverte réseau
  pour aucun bénéfice ici.
- Versions figées (`opensearch:2.19.1`, pas `:latest`) : reproductibilité —
  éviter qu'un `docker compose up` dans 3 mois tire une version majeure
  incompatible avec le mapping existant.
- Volumes Docker nommés plutôt que bind-mounts (`./data/...`) : sur
  Windows/Docker Desktop, le conteneur OpenSearch tourne avec un utilisateur
  non-root qui n'a souvent pas les droits d'écriture sur un dossier Windows
  monté directement — erreur classique "failed to obtain node locks".

### uv pour la gestion des dépendances Python
Gestionnaire moderne avec lockfile (reproductibilité), plus rapide que pip —
déjà le choix initial du projet, conservé tel quel.

### LangChain + LlamaIndex (prévu, pas encore implémenté)
- **LlamaIndex** pour la couche retrieval : un query engine par domaine, qui
  encapsule filtre ACL + recherche hybride + reranking pour ce domaine
  seulement — bonnes abstractions pour ce genre de pipeline.
- **LangChain/LangGraph** pour l'orchestration au-dessus : un routeur qui
  décide quel(s) agent(s) de domaine interroger selon la question. Une
  question mono-domaine ("taux VTC Urssaf") ne doit interroger qu'un agent ;
  une question transverse (RH + juridique) doit en déclencher plusieurs.
- Les deux frameworks sont complémentaires ici, pas redondants : chacun sur
  la couche où il est le plus adapté.

## Installation

### Prérequis
- Python ≥ 3.10
- [`uv`](https://docs.astral.sh/uv/) installé
- Docker Desktop installé et **démarré**

### Étapes

```bash
# 1. Dépendances Python (lit pyproject.toml, cree un .venv)
uv sync

# 2. Infrastructure locale : OpenSearch + Dashboards + PostgreSQL
docker compose up -d

# 3. Verifier qu'OpenSearch repond (peut prendre 30-60s au premier demarrage)
curl http://localhost:9200
```

### Pipeline de données, dans l'ordre

```bash
uv run python extract_pdfs.py     # PDF -> extracted/txt + corpus.jsonl
uv run python clean_corpus.py     # -> extracted/clean/corpus_clean.jsonl
uv run python chunk_corpus.py     # -> extracted/chunks/chunks.jsonl
uv run python embed_chunks.py     # -> extracted/embeddings/chunks_embeddings.jsonl
                                   #    (~0.5s/chunk sur CPU, prevoir du temps)
uv run python create_index.py     # cree l'index OpenSearch + le pipeline hybride
uv run python bulk_index.py       # indexe les chunks vectorises
```

### Tester la recherche

```bash
uv run python search_test.py "taux de cotisation VTC auto-entrepreneur" --groupes rh
```

## État d'avancement

| Phase | Statut |
|---|---|
| Extraction, nettoyage, chunking | fait, validé sur 128 PDF / 123 retenus / 4783 chunks |
| Embeddings BGE-M3 | script écrit et testé (échantillon), exécution complète en cours |
| Index OpenSearch + recherche hybride | code écrit, **non encore validé de bout en bout** (dépend du démarrage de Docker) |
| ACL PostgreSQL | non commencé (branche `acl-postgres` prévue) |
| Reranking | non commencé |
| Agents (LangChain/LlamaIndex) | non commencé |
| Interface + journalisation | non commencé |
