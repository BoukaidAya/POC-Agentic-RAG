# Journal des modifications

Historique de ce qui a été construit sur ce POC, phase par phase. Sert de
référence pour comprendre pourquoi le code est écrit ainsi, pas seulement ce
qu'il fait.

## Branche `data-extraction`

### `extract_pdfs.py` — extraction brute
- Parcourt tous les PDF du projet (`ROOT.rglob("*.pdf")`) avec PyMuPDF.
- Sort un `.txt` lisible par PDF, un `corpus.jsonl` (texte par page) et un
  `index.csv` (inventaire : pages, caractères, `likely_scanned`).
- **Ajouté ensuite** : extraction des liens réels (annotations `page.get_links()`,
  pas le texte affiché — un lien long est souvent coupé par un retour à la
  ligne dans le texte). Chaque lien porte son `domaine` (via `urlparse`).
  Propagé dans `corpus.jsonl` (champ `liens` par page, `domaines`/`num_liens`
  au niveau document) et dans `index.csv`.
- **Dossier `Finance/` intégré** (27 PDF) sans changement de code — le
  `rglob` recursif le capte automatiquement. Total : 128 PDF sources.

### `clean_corpus.py` — nettoyage
- Écarte les documents quasi vides ou bloqués (page anti-bot "Please enable
  JavaScript...", seuil `< 300` caractères) → 3 PDF rejetés, à re-télécharger.
- Déduplique par hash de contenu (deux PDF identiques dans des dossiers
  différents) → 2 doublons écartés (`vosdroits_F22570.pdf`,
  `bpi-cnil-rgpd_guide-tpe-pme.pdf`).
- Supprime le boilerplate (en-têtes/pieds de page répétés) en ne regardant
  que les lignes en **bord de page** (les `BOILERPLATE_ZONE` premières/dernières
  lignes), jamais au milieu — sinon on supprime du vrai contenu répétitif
  (ex: balises XML d'un schéma).
  - **Bug trouvé et corrigé** : le pied de page imprimé par le navigateur sur
    les PDF Légifrance fait 4 lignes, pas 3 — la 4e ligne ("Livre II : ... -
    Légif...") passait à travers et polluait le fil d'Ariane de chaque
    article de ces 2 documents (1188 chunks concernés). `BOILERPLATE_ZONE`
    passé de 3 à 4.
- Sortie : `extracted/clean/corpus_clean.jsonl` + mirror `.txt` + `rapport.json`
  (audit : documents rejetés, doublons, réduction de caractères par doc).
- Résultat sur 128 PDF : 123 retenus, 4.7% de bruit en moins.

### `chunk_corpus.py` — découpe adaptée à la nature du document
Trois stratégies selon le type de document, pas une taille fixe universelle :

| Type | Détection | Découpe |
|---|---|---|
| `code_juridique` | ≥5 occurrences de `Article Lxxxx-x` | 1 chunk = 1 article, avec fil d'Ariane Titre>Chapitre>Section |
| `court` | < 3000 car. après nettoyage | 1 chunk = tout le document |
| `generique` | tout le reste | regroupement de pages jusqu'à 500-1500 car., jamais de coupure au milieu d'une page |

- **Bug trouvé et corrigé** : des titres de page isolés en bord de découpe
  ("DECLARER EN DSN", "FOCUS", 4-15 caractères) devenaient des chunks à eux
  seuls, inutiles pour l'embedding → fusion automatique de tout chunk
  `< CHUNK_MIN_VIABLE` (200 car.) avec son voisin.
- **Bug trouvé et corrigé** : le fil d'Ariane tronquait le titre quand une
  section s'étalait sur 2 lignes physiques dans le PDF (`(Articles L1222-1 à`
  sans fermeture) → regex `RE_PARENTHESE_ARTICLES` corrigée pour couper dès
  `(Articles` sans exiger la parenthèse fermante sur la même ligne.
- Sortie : `extracted/chunks/chunks.jsonl`. Sur le corpus final (123 docs) :
  4783 chunks (121 docs génériques → 3595 chunks, 2 codes juridiques → 1188 chunks).

**Décision volontairement non traitée** : les documents génériques (guides,
PPN, Finance...) n'ont pas de titre de section détecté (pas d'info de police
disponible dans le texte brut extrait) — seul `page_debut/page_fin` sert de
repère. Choix assumé : itérer seulement si les tests de recherche montrent
un vrai manque de contexte, plutôt que de risquer les faux positifs déjà
rencontrés avec la détection de boilerplate.

## Branche `embeddings-indexing`

### `mapping.json` — schéma de l'index OpenSearch
- `texte` : `text` + analyseur français → champ utilisé par BM25.
- `groupes_acl` : `keyword` → champ filtré **avant** toute recherche pour
  appliquer les droits d'accès.
- `embedding` : `knn_vector`, dimension 1024, `hnsw` / `cosinesimil` (cohérent
  avec des vecteurs BGE-M3 normalisés).
- `doc_path`, `folder`, `article`, etc. : `keyword` (valeur exacte, pas de
  tokenisation) — servent au filtrage/tri, pas à la recherche plein texte.
- `texte_avec_contexte` volontairement absent de l'index : il ne sert qu'à
  calculer l'embedding, il serait redondant avec `texte` + `fil_ariane`.

### `embed_chunks.py` — vectorisation BGE-M3
- Encode `texte_avec_contexte` (pas `texte` seul) : le contexte hiérarchique
  (fil d'Ariane) doit faire partie du vecteur, sinon un article isolé comme
  "Le contrat de travail est exécuté de bonne foi." perd le fait qu'il
  s'agit de droit du travail.
- Lots de 16, écriture au fur et à mesure (pas tout en mémoire).
- `normalize_embeddings=True` — nécessaire pour la similarité cosinus.
- Testé sur 50 chunks avant le lancement complet : ~0.54s/chunk sur CPU,
  soit ~43 min pour les 4783 chunks du corpus.

### `docker-compose.yml` — infra locale
Corrigé à partir d'un brouillon initial (voir `ARCHITECTURE.md` pour le détail
des choix) : PostgreSQL ajouté (absent du brouillon), version OpenSearch
figée (`2.19.1` au lieu de `:latest`), heap remonté à 1 Go, volumes nommés
Docker au lieu de bind-mounts (évite les erreurs de permissions Windows),
cluster simplifié à un seul nœud (le brouillon en avait 2, complexité inutile
pour un POC solo).

### `pyproject.toml` — dépendances (gérées avec `uv`)
Corrigé : `pypdf` retiré (non utilisé par le code — c'est `pymupdf` qui sert
à l'extraction), `pymupdf` ajouté (utilisé mais absent du fichier).

### `create_index.py`, `bulk_index.py`, `search_test.py`
- `create_index.py` : crée l'index depuis `mapping.json` + un pipeline de
  recherche hybride (`normalization-processor`, combinaison `arithmetic_mean`
  50/50 entre score BM25 et score kNN).
- `bulk_index.py` : indexe `chunks_embeddings.jsonl`, dérive `groupes_acl`
  depuis le dossier source (`FOLDER_TO_GROUPE`).
- `search_test.py` : requête hybride BM25+kNN avec filtre ACL appliqué dans
  la clause `filter` de la requête `hybrid` (filtre les deux sous-requêtes
  avant scoring, jamais après).
- **Statut à la rédaction de ce document** : écrits mais non exécutés de
  bout en bout (Docker Desktop non démarré au moment de l'écriture) — à
  valider avant de les considérer fiables.

## Git — organisation des branches

- `data-extraction` : scope figé sur extraction/nettoyage/chunking. Reste la
  branche "tronc" dans laquelle les phases suivantes sont mergées une fois
  validées.
- `embeddings-indexing` : créée à partir de `data-extraction`, contient tout
  ce qui touche à l'infra de recherche (Docker, embeddings, OpenSearch).
- **Incident évité** : du travail non committé (Finance + extraction des
  liens) avait été emporté par erreur lors de la création de
  `embeddings-indexing`. Corrigé en committant d'abord sur `data-extraction`
  (sa vraie place), puis en recréant `embeddings-indexing` proprement.
- Prochaines branches prévues : `acl-postgres`, `reranking`, `agents`,
  `interface-journalisation` — une branche par couche fonctionnelle.
