#!/usr/bin/env python3
"""Nettoyage de extracted/corpus.jsonl -> corpus pret pour chunking/embedding RAG.

Usage:
    python clean_corpus.py

Entree:
    extracted/corpus.jsonl   (sortie de extract_pdfs.py)
Sorties:
    extracted/clean/corpus_clean.jsonl   1 ligne JSON par doc retenu, texte par page nettoye
    extracted/clean/txt/<meme arbo>.txt  version texte lisible du corpus nettoye
    extracted/clean/rapport.json         ce qui a ete supprime/ecarte et pourquoi (a relire)
"""
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS_IN = ROOT / "extracted" / "corpus.jsonl"
OUT = ROOT / "extracted" / "clean"
TXT_DIR = OUT / "txt"

# un doc juge "junk" (page de blocage, erreur, pdf vide) en dessous de ce seuil
JUNK_MAX_CHARS = 300
# une ligne est jugee "boilerplate" (en-tete/pied de page repete) si elle apparait
# sur au moins cette fraction des pages du document
BOILERPLATE_RATIO = 0.6
# on n'essaie de detecter du boilerplate que sur les docs d'au moins N pages
# (sous ce seuil, une ligne repetee peut juste etre du contenu normal)
BOILERPLATE_MIN_PAGES = 3
# on ne regarde que les N premieres / N dernieres lignes de chaque page : un
# en-tete/pied de page est toujours en bord de page. Une ligne repetee au milieu
# du corps (ex: balises XML d'un schema, items de liste recurrents) n'est pas
# du boilerplate, meme si elle revient souvent -> on ne la touche pas.
# 4, car les PDF issus d'un "imprimer la page" (ex: Legifrance) ont un bloc
# pied de page sur 4 lignes : titre tronque, URL, "X of N", horodatage.
BOILERPLATE_ZONE = 4

RE_JS_BLOCK = re.compile(r"enable javascript|support id\s*:", re.I)
RE_PAGE_OF = re.compile(r"^\d+\s+of\s+\d+$", re.I)
RE_PRINT_TS = re.compile(r"^\d{1,2}/\d{1,2}/\d{4},?\s*\d{1,2}:\d{2}\s*(AM|PM)$", re.I)
RE_LONE_NUMBER = re.compile(r"^\d{1,4}$")
RE_MULTI_BLANK = re.compile(r"\n{3,}")
RE_MULTI_WS = re.compile(r"[ \t  ]+")


def nettoyer_ligne(ligne: str) -> str:
    t = unicodedata.normalize("NFKC", ligne)
    t = RE_MULTI_WS.sub(" ", t).strip()
    return t


def est_bruit_ligne(ligne: str) -> bool:
    """Artefacts d'impression/navigateur qui ne portent aucune info metier."""
    if not ligne:
        return True
    if RE_PAGE_OF.match(ligne):
        return True
    if RE_PRINT_TS.match(ligne):
        return True
    if RE_LONE_NUMBER.match(ligne):
        return True
    return False


def detecter_boilerplate(pages: list[str]) -> set[str]:
    """Lignes en bord de page (en-tete/pied) qui se repetent sur la majorite des
    pages. On ne regarde que les bords : une ligne repetee au milieu du corps
    (balises XML, items recurrents) est du contenu, pas du boilerplate."""
    if len(pages) < BOILERPLATE_MIN_PAGES:
        return set()
    freq = Counter()
    for page_txt in pages:
        lignes = [nettoyer_ligne(l) for l in page_txt.splitlines() if l.strip()]
        zone = lignes[:BOILERPLATE_ZONE] + lignes[-BOILERPLATE_ZONE:]
        for l in set(zone):
            if len(l) >= 8:  # les lignes courtes sont trop souvent des faux positifs
                freq[l] += 1
    seuil = max(2, int(len(pages) * BOILERPLATE_RATIO))
    return {l for l, n in freq.items() if n >= seuil}


def nettoyer_page(texte: str, boilerplate: set[str]) -> str:
    lignes_gardees = []
    for ligne in texte.splitlines():
        l = nettoyer_ligne(ligne)
        if not l or l in boilerplate or est_bruit_ligne(l):
            continue
        lignes_gardees.append(l)
    return "\n".join(lignes_gardees)


def nettoyer_doc(rec: dict) -> dict:
    pages_brutes = [p["text"] for p in rec["pages"]]
    boilerplate = detecter_boilerplate(pages_brutes)

    pages_propres = []
    for p in rec["pages"]:
        texte = nettoyer_page(p["text"], boilerplate)
        if texte:
            pages_propres.append({"page": p["page"], "text": texte})

    texte_total = "\n\n".join(p["text"] for p in pages_propres)
    return {
        "path": rec["path"],
        "folder": rec["folder"],
        "filename": rec["filename"],
        "title": rec.get("title", ""),
        "num_pages": len(pages_propres),
        "total_chars": len(texte_total),
        "boilerplate_supprime": sorted(boilerplate),
        "pages": pages_propres,
    }


def hash_contenu(rec: dict) -> str:
    brut = "\n".join(p["text"] for p in rec["pages"])
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()


def main():
    if not CORPUS_IN.exists():
        print(f"Introuvable : {CORPUS_IN} (lancer d'abord extract_pdfs.py)")
        return

    TXT_DIR.mkdir(parents=True, exist_ok=True)

    docs = []
    with CORPUS_IN.open(encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))

    rapport = {"total_docs": len(docs), "rejetes_junk": [], "doublons": [], "retenus": []}

    vus_par_hash: dict[str, str] = {}  # hash -> path du 1er exemplaire garde
    clean_path = OUT / "corpus_clean.jsonl"
    with clean_path.open("w", encoding="utf-8") as sortie:
        for rec in docs:
            # 1) doc probablement bloque/vide (page anti-bot, erreur de telechargement)
            texte_brut = " ".join(p["text"] for p in rec["pages"])
            if rec["total_chars"] < JUNK_MAX_CHARS or RE_JS_BLOCK.search(texte_brut):
                rapport["rejetes_junk"].append({
                    "path": rec["path"], "total_chars": rec["total_chars"],
                    "raison": "contenu quasi vide ou page de blocage (a re-televerser/re-extraire)",
                })
                continue

            # 2) doublon exact deja vu (meme contenu, chemin different)
            h = hash_contenu(rec)
            if h in vus_par_hash:
                rapport["doublons"].append({
                    "path": rec["path"], "doublon_de": vus_par_hash[h],
                })
                continue
            vus_par_hash[h] = rec["path"]

            # 3) nettoyage du contenu retenu
            propre = nettoyer_doc(rec)
            if propre["total_chars"] < JUNK_MAX_CHARS:
                rapport["rejetes_junk"].append({
                    "path": rec["path"], "total_chars": propre["total_chars"],
                    "raison": "vide apres nettoyage (ne contenait que du boilerplate)",
                })
                continue

            sortie.write(json.dumps(propre, ensure_ascii=False) + "\n")

            txt_out = (TXT_DIR / propre["path"]).with_suffix(".txt")
            txt_out.parent.mkdir(parents=True, exist_ok=True)
            with txt_out.open("w", encoding="utf-8") as f:
                for p in propre["pages"]:
                    f.write(f"\n----- page {p['page']} -----\n{p['text']}\n")

            rapport["retenus"].append({
                "path": propre["path"],
                "chars_avant": rec["total_chars"],
                "chars_apres": propre["total_chars"],
                "lignes_boilerplate_supprimees": len(propre["boilerplate_supprime"]),
            })

    rapport_path = OUT / "rapport.json"
    rapport_path.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")

    total_avant = sum(r["chars_avant"] for r in rapport["retenus"])
    total_apres = sum(r["chars_apres"] for r in rapport["retenus"])
    reduction = 100 * (1 - total_apres / total_avant) if total_avant else 0

    print("=== RESUME NETTOYAGE ===")
    print(f"Documents source        : {rapport['total_docs']}")
    print(f"Retenus                 : {len(rapport['retenus'])}")
    print(f"Rejetes (vides/bloques) : {len(rapport['rejetes_junk'])}")
    print(f"Doublons ecartes        : {len(rapport['doublons'])}")
    print(f"Caracteres avant/apres  : {total_avant:,} -> {total_apres:,}  ({reduction:.1f}% de bruit en moins)")
    print(f"\nSorties : {OUT}")
    print(f"A relire en priorite    : {rapport_path} (section 'rejetes_junk')")


if __name__ == "__main__":
    main()
