# Wiki technique — POC Agentic RAG avec droits d'accès

Guide étape par étape : objectif, méthodes, et explication du code — pensé
pour comprendre *comment* et *pourquoi* chaque brique existe, pas juste ce
qu'elle fait.

---

## 1. Objectif du projet

Un assistant interne qui répond à des questions en s'appuyant sur des
documents d'entreprise (RH, juridique du travail, marketing, sécurité/RGPD,
finance, management), **en respectant les droits d'accès de la personne qui
pose la question**. La contrainte d'accès est au cœur du projet, pas un
ajout après-coup : le système filtre par droits *avant* de chercher, ne
cherche que dans le bon domaine, et cite ses sources.

## 2. Vue d'ensemble du pipeline

```
PDF (128 fichiers, 6 dossiers thematiques)
  │
  ▼
extract_pdfs.py     -- lit le texte, les liens, detecte le vrai titre
  │
  ▼
clean_corpus.py     -- dedoublonne, ecarte les PDF casses, enleve le bruit
  │
  ▼
chunk_corpus.py     -- decoupe en passages cherchables ("chunks")
  │
  ▼
embed_chunks.py     -- calcule un vecteur (BGE-M3) par chunk
  │
  ▼
create_index.py + bulk_index.py   -- indexe dans OpenSearch
  │                                          │
  │                              seed_acl.py + sync_acl.py
  │                              -- droits reels dans PostgreSQL,
  │                                 copies dans OpenSearch
  ▼                                          │
rag_query.py / agents.py <───────────────────┘
  -- question -> droits reels -> recherche filtree -> Claude -> reponse
  │
  ▼
api.py  -- porte HTTP pour un frontend separe (agentic-rag-interface)
```

Deux bases de donnees, deux roles distincts :
- **OpenSearch** = moteur de recherche (texte + vecteurs), optimise pour
  chercher vite.
- **PostgreSQL** = source de verite des droits et des metadonnees,
  optimise pour les relations (qui a acces a quoi).

---

## 3. Extraction — `extract_pdfs.py`

Utilise **PyMuPDF** (`import pymupdf`) pour ouvrir chaque PDF et,
page par page :

```python
text = page.get_text("text").strip()          # le texte brut de la page
liens = extraire_liens(page)                   # les vrais liens (annotations)
```

Point important : les liens sont lus depuis les **annotations** du PDF
(`page.get_links()`), pas depuis le texte affiché — un lien long est
souvent coupe par un retour a la ligne dans le texte, l'annotation est la
seule source fiable.

**Detection du vrai titre** (`detecter_titre_reel`) : certains PDF (la
serie Finance "Referentiel des financements des entreprises") ont un nom
de fichier qui ne correspond pas a leur contenu reel. Le vrai titre est
cite dans le texte lui-meme (`Fiche 325 : Les obligations convertibles en
actions`) -- une regex le retrouve, avec un recollage si le titre est
coupe sur 2 lignes (signal : un espace residuel juste avant le saut de
ligne, laisse par une mise en page qui a coupe un mot en fin de ligne).

Sortie : `extracted/corpus.jsonl`, une ligne JSON par PDF avec son texte
page par page, ses liens, son titre reel.

## 4. Nettoyage — `clean_corpus.py`

Trois passes sur `corpus.jsonl` :

1. **Rejet des PDF casses** : hash de contenu pour reperer les doublons
   exacts (meme PDF present dans 2 dossiers), et detection des pages de
   blocage anti-bot (`< 300 caracteres` ou motif "enable javascript").
2. **Suppression du boilerplate** (en-tetes/pieds de page repetes) :
   ne regarde que les **4 premieres et 4 dernieres lignes de chaque page**
   (`BOILERPLATE_ZONE = 4`) -- jamais le milieu, sinon on risque de
   supprimer du vrai contenu qui se repete legitimement (ex: des balises
   XML dans un schema technique).
3. Nettoyage typographique fin (espaces, ponctuation).

Sortie : `extracted/clean/corpus_clean.jsonl` + `rapport.json` (audit :
combien de caracteres retires, quels PDF rejetes et pourquoi).

## 5. Chunking — `chunk_corpus.py` — pourquoi des chunks, et pourquoi 3 strategies

**Pourquoi decouper du tout ?** Un embedding sur un document de 300 pages
ne veut rien dire (le vecteur "moyennerait" un sens beaucoup trop large).
Il faut des unites plus petites, assez precises pour qu'un vecteur
capture un sens coherent, assez grandes pour garder du contexte.

**Pourquoi pas une taille fixe partout ?** Parce que la nature du document
change ce qui fait une bonne unite :

| Type de document | Detection | Decoupe |
|---|---|---|
| Code juridique (Legifrance) | ≥ 5 occurrences de `Article Lxxxx-x` | 1 chunk = 1 article, avec son fil d'Ariane (Titre > Chapitre > Section) |
| Document court | < 3000 caracteres apres nettoyage | 1 chunk = tout le document |
| Guide generique | tout le reste | regroupement de pages jusqu'a 500-1500 caracteres |

Un decoupage a taille fixe casserait un article de loi au milieu (mauvais
pour la recherche : "l'article dit que le salarie..." coupe avant la fin
n'a plus de sens juridique exploitable). A l'inverse, un guide sans
structure claire n'a pas d'unite naturelle plus fine que "un bloc de
pages" (pas d'info de police disponible pour detecter des titres de
maniere fiable sur des PDF venant de sources tres differentes).

Chaque chunk final porte : `chunk_id`, `doc_path`, `folder`,
`titre_document`, `page_debut/fin`, `texte`, `texte_avec_contexte` (avec
le fil d'Ariane en prefixe pour les articles), `liens`. C'est
`texte_avec_contexte` qui est vectorise, pas `texte` seul -- le contexte
hierarchique doit faire partie du vecteur.

Sortie : `extracted/chunks/chunks.jsonl` (4783 chunks sur 123 documents).

## 6. Embeddings — `embed_chunks.py`

Modele **BGE-M3** (multilingue, bon francais), via `sentence_transformers` :

```python
vecteurs = modele.encode(textes, normalize_embeddings=True)
```

`normalize_embeddings=True` : les vecteurs sont ramenes a une longueur de
1 -- necessaire pour que la similarite cosinus soit calculee correctement
par OpenSearch (voir mapping plus bas). ~0.5s/chunk sur CPU.

Sortie : `extracted/embeddings/chunks_embeddings.jsonl` (chaque chunk +
son vecteur `embedding`, 1024 nombres).

---

## 7. Le mapping OpenSearch — `mapping.json` expliqué champ par champ

Un "mapping" dit a OpenSearch quel type chaque champ a. Sans lui,
OpenSearch devine (et se trompe souvent, ex: indexer un chemin de fichier
comme texte analyse au lieu de valeur exacte).

```json
"texte": { "type": "text", "analyzer": "francais" }
```
`text` = analyse (decoupe en mots, gere accents/pluriels/mots vides). C'est
le champ utilise par la recherche par mots-cles (BM25).

```json
"doc_path": { "type": "keyword" }
"groupes_acl": { "type": "keyword" }
```
`keyword` = valeur exacte, jamais decoupee. Utilise pour **filtrer**
(`folder = "RH"`) ou trier -- jamais pour chercher du texte libre.
**`groupes_acl` est le champ le plus important du projet** : c'est lui qui
porte, sur chaque chunk, la liste des groupes autorises a le voir.

```json
"embedding": {
  "type": "knn_vector",
  "dimension": 1024,
  "method": { "name": "hnsw", "space_type": "cosinesimil", "engine": "faiss", ... }
}
```
`knn_vector` = le champ vectoriel. `space_type: cosinesimil` = similarite
cosinus (coherent avec des vecteurs normalises). `engine: faiss` -- choix
important : c'est l'un des deux seuls moteurs (avec `lucene`) qui supporte
le **filtrage pendant la recherche kNN** (`nmslib`, essaye au debut, ne le
supporte pas -- corrige apres avoir trouve l'erreur en testant).

`"settings.index.knn": true` active le module de recherche vectorielle
pour cet index -- sans ca, le champ `knn_vector` est refuse a la creation.

---

## 8. Indexation — `create_index.py` et `bulk_index.py`

`create_index.py` cree l'index depuis `mapping.json`, **et** un pipeline
de recherche :

```python
PIPELINE_DEF = {
    "phase_results_processors": [{
        "normalization-processor": {
            "normalization": {"technique": "min_max"},
            "combination": {"technique": "arithmetic_mean", "parameters": {"weights": [0.5, 0.5]}},
        }
    }]
}
```
Le score BM25 (souvent 0-20) et le score kNN (0-1) ne sont pas sur la
meme echelle -- sans ce pipeline, un des deux ecraserait systematiquement
l'autre dans le score final. `min_max` ramene les deux sur 0-1 avant de
les moyenner.

`bulk_index.py` envoie tous les chunks vectorises dans OpenSearch, avec un
`groupes_acl` initial derive du dossier source (`acl_config.py`) --
valeur de depart, ecrasee ensuite par `sync_acl.py` depuis PostgreSQL.

---

## 9. Les droits d'accès — le code expliqué en détail

### Pourquoi PostgreSQL et pas juste un champ dans OpenSearch

Les droits sont une **relation** : utilisateurs ↔ groupes ↔ documents,
dans les deux sens (many-to-many). C'est exactement ce pour quoi une base
relationnelle existe -- OpenSearch est un moteur de recherche, pas un
gestionnaire de relations.

### `schema.sql` — les 5 tables

```sql
CREATE TABLE groupes (
    id   SERIAL PRIMARY KEY,
    nom  TEXT UNIQUE NOT NULL
);
```
Un groupe = un domaine (`rh`, `finance`, `juridique`...).

```sql
CREATE TABLE utilisateurs (
    id     SERIAL PRIMARY KEY,
    email  TEXT UNIQUE NOT NULL,
    nom    TEXT NOT NULL
);

CREATE TABLE utilisateur_groupes (
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
    groupe_id       INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    PRIMARY KEY (utilisateur_id, groupe_id)
);
```
`utilisateur_groupes` est une **table de liaison** : un utilisateur peut
appartenir a plusieurs groupes, un groupe a plusieurs utilisateurs. La
`PRIMARY KEY` composite empeche d'ajouter deux fois le meme droit.
`REFERENCES ... ON DELETE CASCADE` : si un utilisateur ou un groupe est
supprime, ses liens le sont automatiquement (pas de ligne orpheline).

```sql
CREATE TABLE documents (
    doc_path        TEXT PRIMARY KEY,
    folder          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    titre_document  TEXT
);

CREATE TABLE document_groupes (
    doc_path   TEXT NOT NULL REFERENCES documents(doc_path) ON DELETE CASCADE,
    groupe_id  INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    PRIMARY KEY (doc_path, groupe_id)
);
```
Meme principe pour les documents : `document_groupes` dit quel document
est visible par quel(s) groupe(s) -- un document **peut appartenir a
plusieurs groupes** (partage entre domaines), ce n'est pas 1 document = 1
seul groupe.

`REFERENCES groupes(id)` = **cle etrangere** : Postgres refuse d'inserer un
droit vers un groupe qui n'existe pas. Impossible d'avoir une faute de
frappe silencieuse ("rh " avec un espace) qui casserait le filtre sans
que personne ne s'en apercoive -- c'est le genre d'erreur qu'un
dictionnaire Python ou un champ libre ne peut pas empecher.

### `acl_config.py` — la correspondance dossier -> groupe

```python
FOLDER_TO_GROUPE = {
    "RH": "rh",
    "juridique droit du travail": "juridique",
    ...
}
```
Un seul endroit, importe a la fois par `bulk_index.py` (droit initial) et
`seed_acl.py` (peuplement Postgres) -- pour que les deux ne divergent
jamais sur "quel dossier correspond a quel groupe".

### `seed_acl.py` — peupler la base

Lit `chunks.jsonl`, en extrait les documents uniques, et pour chacun :

```python
cur.execute(
    "INSERT INTO documents (doc_path, folder, filename, titre_document) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (doc_path) DO UPDATE SET ...",
    (doc_path, c["folder"], c["filename"], c.get("titre_document", "")),
)
groupe = groupe_pour_dossier(c["folder"])
cur.execute("SELECT id FROM groupes WHERE nom = %s", (groupe,))
groupe_id = cur.fetchone()[0]
cur.execute(
    "INSERT INTO document_groupes (doc_path, groupe_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
    (doc_path, groupe_id),
)
```
`ON CONFLICT ... DO UPDATE` / `DO NOTHING` : relancer le script ne
duplique rien -- il met a jour ou ignore si la ligne existe deja
(idempotent).

Cree aussi 4 utilisateurs de test avec des combinaisons de groupes
differentes (mono-domaine, multi-domaine, tous domaines) -- pour tester
des profils realistes, pas juste "un utilisateur par dossier".

### `sync_acl.py` — repercuter les droits dans OpenSearch

```sql
SELECT d.doc_path, array_agg(g.nom ORDER BY g.nom)
FROM documents d
JOIN document_groupes dg ON dg.doc_path = d.doc_path
JOIN groupes g ON g.id = dg.groupe_id
GROUP BY d.doc_path
```
Cette requete **joint** 3 tables pour reconstruire, pour chaque document,
la liste de ses groupes autorises (`array_agg` = agrege les noms de
groupes en tableau). Puis, pour chaque document :

```python
client.update_by_query(
    index=INDEX_NAME,
    body={
        "query": {"term": {"doc_path": doc_path}},
        "script": {"source": "ctx._source.groupes_acl = params.groupes", "params": {"groupes": groupes}},
    },
)
```
Trouve tous les chunks de ce document dans OpenSearch (`term` sur
`doc_path`) et **ecrase** leur `groupes_acl` avec la valeur fraiche
calculee depuis Postgres. **PostgreSQL est la seule chose qu'on modifie
-- jamais OpenSearch directement**, sinon le changement serait perdu au
prochain `sync_acl.py`.

Valide en conditions reelles : ajout d'un droit dans Postgres invisible
avant `sync_acl.py`, visible et limite au bon document apres.

---

## 10. La recherche hybride expliquée

Fonction `rechercher()` (dans `rag_query.py`, identique dans
`search_test.py`) :

```python
filtre_acl = {"terms": {"groupes_acl": groupes}}
requete = {
    "query": {
        "hybrid": {
            "queries": [
                {"bool": {"must": [{"match": {"texte": {"query": question}}}], "filter": [filtre_acl]}},
                {"knn": {"embedding": {"vector": vecteur, "k": K * 4, "filter": filtre_acl}}},
            ]
        }
    }
}
```

**Pourquoi hybride (BM25 + kNN) et pas juste vectoriel ?** Les embeddings
seuls echouent souvent sur du texte exact (numeros d'article "L1221-1",
codes CTP, montants) -- le BM25 lexical rattrape ce que le sens
semantique seul rate. A l'inverse, le kNN rattrape les reformulations
("qui doit voir le medecin du travail" -> trouve un passage qui dit
"visite medicale" sans le mot "voir") que le BM25 seul rate.

**Le filtre ACL est duplique dans les 2 sous-requetes** (`bool.filter`
pour le BM25, parametre `filter` natif du `knn`) plutot que mis une seule
fois au niveau de la requete `hybrid` -- parce que ce filtre commun n'est
supporte qu'a partir d'OpenSearch 3.0 (le projet est fige en 2.19.1 pour
la stabilite). Dans les deux cas, le filtre s'applique **avant** le calcul
du score -- jamais apres, sinon on risquerait de filtrer un resultat
pertinent qui n'etait deja plus dans le top-k retourne.

`k: K * 4` sur le kNN : on demande plus de candidats vectoriels que le
nombre final voulu, pour laisser le pipeline de normalisation/fusion
(section 8) un choix suffisant avant de couper au top `K`.

---

## 11. Génération et agents

**`rag_query.py`** : boucle simple a un domaine.
`groupes_utilisateur(email)` -- requete SQL sur PostgreSQL pour les vrais
droits -> `rechercher()` -> `construire_contexte()` (numerote les
sources) -> `appeler_llm()` (Claude, `claude-opus-5`, SDK `anthropic`) ->
reponse + sources affichees.

Le prompt separe **strictement** instructions systeme et contenu recupere
(deux champs differents de l'appel API, jamais concatenes dans un seul
message) -- le modele ne doit jamais confondre "ce qu'on lui demande de
faire" et "le contenu qu'on lui donne a lire", meme si ce contenu vient
de documents externes potentiellement manipules.

**`agents.py`** : version multi-domaine.
1. **Routeur** : Claude classe la question parmi les domaines connus
   (`rh`, `juridique`, `finance`, `securite`, `marketing`, `management`).
2. **Filtre ACL applique au routage lui-meme** : un domaine que le routeur
   juge pertinent mais que l'utilisateur n'a pas le droit de voir est
   **ignore**, jamais interroge -- valide en conditions reelles (Carla,
   question touchant `rh` en plus de ses domaines, `rh` bloque et signale).
3. **Un agent par domaine restant** : recherche restreinte a ce seul
   domaine + generation avec un system prompt qui rappelle que tous les
   extraits appartiennent a ce domaine.

Pas de synthese finale entre agents (chaque reponse de domaine est
affichee separement) -- version volontairement simple pour valider le
principe, pas un systeme complet.

---

## 12. API et interface

**`api.py`** : Flask, `POST /chat {question, email}` -> meme logique que
`agents.py` (fonction `traiter_question`, extraite du CLI pour etre
reutilisable), retourne du JSON. Le modele d'embedding est charge une
seule fois (singleton) -- le recharger a chaque requete HTTP serait trop
lent.

**`agentic-rag-interface`** (repo separe) : page HTML + JavaScript natif,
sans dependance. Appelle `POST /chat` via `fetch()`, affiche la reponse
par domaine autorise + sources, et signale les domaines refuses par le
filtre ACL (transparence).

---

## 13. État d'avancement

| Phase | Statut |
|---|---|
| Extraction, nettoyage, chunking | fait, valide (128 PDF, 123 retenus, 4783 chunks) |
| Titres reels des documents | fait (corrige un decalage nom de fichier/contenu sur 27 PDF Finance) |
| Embeddings + index OpenSearch hybride | fait, valide |
| ACL PostgreSQL | fait, valide (ajout/retrait de droit teste en conditions reelles) |
| Generation (Claude) + agents par domaine | fait, valide (routeur + blocage ACL confirmes) |
| API + interface web | fait, testee (contrat JSON verifie de bout en bout) |
| Reranking | non commence |
| Journalisation | non commence |
