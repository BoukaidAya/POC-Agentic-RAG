#!/usr/bin/env python3
"""Chunking de extracted/clean/corpus_clean.jsonl -> chunks prets a embedder.

La strategie de decoupe depend de la NATURE du document, pas d'une taille fixe
universelle :

  - code juridique (Legifrance, structure "Article Lxxxx-x")
        -> 1 chunk = 1 article, avec le fil d'Ariane (Titre/Chapitre/Section)
           comme contexte. C'est l'unite de recherche naturelle en droit :
           un decoupe par taille fixe casserait des articles au milieu.

  - document court (fiche, newsletter... sous un seuil de caracteres)
        -> 1 chunk = tout le document. Le decouper ne ferait que fragmenter
           un contenu deja court, sans gain pour la recherche.

  - document generique (guides, PPN, CNIL...)
        -> chunk par regroupement de pages jusqu'a une taille cible, en ne
           coupant jamais une page en deux sauf si elle depasse la taille max
           a elle seule. Pas de heading detectable de facon fiable dans du
           texte brut sans info de police -> la page reste l'unite de repere
           la plus sure pour la citation.

Usage:
    python chunk_corpus.py
Entree:
    extracted/clean/corpus_clean.jsonl
Sortie:
    extracted/chunks/chunks.jsonl
    extracted/chunks/rapport.json
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS_IN = ROOT / "extracted" / "clean" / "corpus_clean.jsonl"
OUT = ROOT / "extracted" / "chunks"

SEUIL_DOC_COURT = 3000          # sous ce total de caracteres -> 1 seul chunk
MIN_ARTICLES_POUR_CODE = 5      # nb d'"Article Lxxxx" pour classer en code juridique

CHUNK_MIN = 500                 # taille cible (generique) : on ferme un chunk
CHUNK_MAX = 1500                # des qu'on a au moins CHUNK_MIN et qu'ajouter
                                 # une page de plus depasserait CHUNK_MAX
PHRASE_MAX = 1200               # taille max d'un morceau quand on doit decouper
PHRASE_OVERLAP = 150             # une page trop grosse par phrases
CHUNK_MIN_VIABLE = 200          # un chunk plus petit que ca (souvent un titre
                                 # de page isole en bord de decoupe) est fusionne
                                 # au chunk voisin plutot que garde tel quel

RE_ARTICLE = re.compile(r"^Article\s+((?:LO|L|R|D)\s?\d[\w.\-]*)", re.I)
RE_NIVEAU = re.compile(
    r"^(Livre|Titre|Chapitre|Sous-chapitre|Section|Sous-section)\b\s*:?\s*(.*)", re.I)
NIVEAUX = {"livre": 0, "titre": 1, "chapitre": 2, "sous-chapitre": 3,
           "section": 4, "sous-section": 5}
RE_PARENTHESE_ARTICLES = re.compile(r"\s*\(Articles?\s+.*$", re.I)
RE_FIN_PHRASE = re.compile(r"(?<=[.!?])\s+")


def classer_doc(rec: dict) -> str:
    texte = " ".join(p["text"] for p in rec["pages"])
    if len(re.findall(r"^Article\s+(?:LO|L|R|D)\s?\d", texte, re.M)) >= MIN_ARTICLES_POUR_CODE:
        return "code_juridique"
    if rec["total_chars"] < SEUIL_DOC_COURT:
        return "court"
    return "generique"


def chunks_code_juridique(rec: dict) -> list[dict]:
    """1 chunk par article, tague avec son fil d'Ariane (Titre > Chapitre > Section)."""
    chunks = []
    fil: dict[int, str] = {}
    article_id, article_lignes = None, []
    page_debut = page_fin = None

    def cloturer():
        if article_id and article_lignes:
            chemin = [fil[k] for k in sorted(fil)]
            corps = "\n".join(article_lignes).strip()
            contexte = " > ".join(chemin)
            chunks.append({
                "type": "article", "article": article_id, "fil_ariane": chemin,
                "page_debut": page_debut, "page_fin": page_fin,
                "n_chars": len(corps),
                "texte": corps,
                "texte_avec_contexte": f"[{contexte}]\n{corps}" if contexte else corps,
            })

    for page in rec["pages"]:
        for ligne in page["text"].splitlines():
            if not ligne.strip():
                continue
            m_niv = RE_NIVEAU.match(ligne)
            if m_niv:
                niveau, libelle = m_niv.group(1).lower(), m_niv.group(2)
                libelle = RE_PARENTHESE_ARTICLES.sub("", libelle).strip(" .:")
                lvl = NIVEAUX[niveau]
                fil = {k: v for k, v in fil.items() if k < lvl}
                fil[lvl] = f"{m_niv.group(1)} : {libelle}" if libelle else m_niv.group(1)
                continue
            m_art = RE_ARTICLE.match(ligne)
            if m_art:
                cloturer()
                article_id = m_art.group(1).replace(" ", "")
                article_lignes = []
                page_debut = page_fin = page["page"]
                continue
            if ligne.upper().startswith("VERSION EN VIGUEUR"):
                continue  # metadonnee de version, pas du contenu juridique
            if article_id:
                article_lignes.append(ligne)
                page_fin = page["page"]
    cloturer()
    return chunks


def _decouper_par_phrases(texte: str) -> list[str]:
    phrases = RE_FIN_PHRASE.split(texte)
    morceaux, courant = [], ""
    for ph in phrases:
        if courant and len(courant) + len(ph) + 1 > PHRASE_MAX:
            morceaux.append(courant.strip())
            queue = courant[-PHRASE_OVERLAP:]
            courant = queue + " " + ph
        else:
            courant = (courant + " " + ph).strip()
    if courant.strip():
        morceaux.append(courant.strip())
    return morceaux


def chunks_generique(rec: dict) -> list[dict]:
    """Regroupe les pages jusqu'a une taille cible ; ne coupe jamais une page
    sauf si, seule, elle depasse deja la taille max."""
    chunks = []
    tampon_pages, tampon_texte = [], []

    def cloturer():
        if not tampon_texte:
            return
        texte = "\n\n".join(tampon_texte)
        chunks.append({
            "type": "page_groupee",
            "page_debut": tampon_pages[0], "page_fin": tampon_pages[-1],
            "n_chars": len(texte), "texte": texte, "texte_avec_contexte": texte,
        })
        tampon_pages.clear()
        tampon_texte.clear()

    for page in rec["pages"]:
        texte_page = page["text"].strip()
        if not texte_page:
            continue
        taille_courante = sum(len(t) for t in tampon_texte)

        if len(texte_page) > CHUNK_MAX:
            cloturer()
            for morceau in _decouper_par_phrases(texte_page):
                chunks.append({
                    "type": "page_decoupee", "page_debut": page["page"],
                    "page_fin": page["page"], "n_chars": len(morceau),
                    "texte": morceau, "texte_avec_contexte": morceau,
                })
            continue

        if taille_courante + len(texte_page) > CHUNK_MAX and taille_courante >= CHUNK_MIN:
            cloturer()

        tampon_pages.append(page["page"])
        tampon_texte.append(texte_page)
        if sum(len(t) for t in tampon_texte) >= CHUNK_MIN:
            cloturer()
    cloturer()
    return _fusionner_chunks_orphelins(chunks)


def _fusionner_chunks_orphelins(chunks: list[dict]) -> list[dict]:
    """Un chunk trop petit (souvent un titre de page isole par la coupure en
    pages) n'est pas exploitable seul -> on le recolle au chunk voisin."""
    i = 0
    while i < len(chunks) and len(chunks) > 1:
        if chunks[i]["n_chars"] >= CHUNK_MIN_VIABLE:
            i += 1
            continue
        voisin = i - 1 if i > 0 else i + 1
        cible, autre = (voisin, i) if voisin < i else (i, voisin)
        a, b = chunks[cible], chunks[autre]
        a["texte"] = a["texte"] + "\n\n" + b["texte"]
        a["texte_avec_contexte"] = a["texte"]
        a["n_chars"] = len(a["texte"])
        a["page_debut"] = min(a["page_debut"], b["page_debut"])
        a["page_fin"] = max(a["page_fin"], b["page_fin"])
        del chunks[autre]
        # ne pas incrementer i : on re-verifie l'element qui occupe desormais
        # cet index (soit le chunk fusionne, soit le suivant apres decalage)
    return chunks


def chunks_court(rec: dict) -> list[dict]:
    texte = "\n\n".join(p["text"] for p in rec["pages"])
    return [{
        "type": "document_entier",
        "page_debut": rec["pages"][0]["page"], "page_fin": rec["pages"][-1]["page"],
        "n_chars": len(texte), "texte": texte, "texte_avec_contexte": texte,
    }]


def main():
    if not CORPUS_IN.exists():
        print(f"Introuvable : {CORPUS_IN} (lancer d'abord clean_corpus.py)")
        return
    OUT.mkdir(parents=True, exist_ok=True)

    docs = [json.loads(l) for l in CORPUS_IN.open(encoding="utf-8")]
    rapport = {"total_docs": len(docs), "par_type": {}, "total_chunks": 0}

    chunks_path = OUT / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as sortie:
        for rec in docs:
            type_doc = classer_doc(rec)
            if type_doc == "code_juridique":
                chunks = chunks_code_juridique(rec)
            elif type_doc == "court":
                chunks = chunks_court(rec)
            else:
                chunks = chunks_generique(rec)

            stat = rapport["par_type"].setdefault(
                type_doc, {"docs": 0, "chunks": 0, "chars_moy": []})
            stat["docs"] += 1
            stat["chunks"] += len(chunks)

            liens_par_page: dict[int, list[dict]] = {}
            for p in rec["pages"]:
                if p.get("liens"):
                    liens_par_page[p["page"]] = p["liens"]

            for i, c in enumerate(chunks):
                c["chunk_id"] = f"{rec['path']}#{i}"
                liens_chunk, vus = [], set()
                for pg in range(c["page_debut"], c["page_fin"] + 1):
                    for l in liens_par_page.get(pg, []):
                        if l["uri"] not in vus:
                            vus.add(l["uri"])
                            liens_chunk.append(l)
                c["liens"] = liens_chunk
                c["doc_path"] = rec["path"]
                c["folder"] = rec["folder"]
                c["filename"] = rec["filename"]
                c["titre_document"] = rec.get("title", "")
                c["doc_type"] = type_doc
                stat["chars_moy"].append(c["n_chars"])
                sortie.write(json.dumps(c, ensure_ascii=False) + "\n")
            rapport["total_chunks"] += len(chunks)

    for stat in rapport["par_type"].values():
        tailles = stat.pop("chars_moy")
        stat["chars_moyen"] = round(sum(tailles) / len(tailles)) if tailles else 0
        stat["chars_min"] = min(tailles) if tailles else 0
        stat["chars_max"] = max(tailles) if tailles else 0

    (OUT / "rapport.json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== RESUME CHUNKING ===")
    print(f"Documents            : {rapport['total_docs']}")
    print(f"Chunks produits       : {rapport['total_chunks']}")
    for t, s in rapport["par_type"].items():
        print(f"  {t:<15} {s['docs']:>3} docs -> {s['chunks']:>5} chunks "
              f"(moy {s['chars_moyen']} car, min {s['chars_min']}, max {s['chars_max']})")
    print(f"\nSorties : {OUT}")


if __name__ == "__main__":
    main()
