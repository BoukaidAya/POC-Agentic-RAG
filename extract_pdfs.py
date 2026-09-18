#!/usr/bin/env python3
"""Extraction de texte de tous les PDF du projet -> .txt individuels + corpus.jsonl

Usage:
    python extract_pdfs.py
Sortie:
    extracted/txt/<meme arbo>.txt   (texte lisible, page par page)
    extracted/corpus.jsonl          (1 ligne JSON par PDF, texte par page + liens)
    extracted/index.csv             (inventaire: fichier, dossier, pages, chars, ocr?, liens)
"""
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pymupdf  # PyMuPDF

ROOT = Path(__file__).resolve().parent
# Les PDF sources sont regroupes sous "donnees/" (un sous-dossier par domaine).
# On calcule les chemins RELATIVEMENT a ce dossier : "folder"/"path" restent
# "Finance", "RH/..."  (sans le prefixe "donnees/") -> le mapping des droits
# (acl_config) et les doc_path deja indexes ne changent pas.
DATA_DIR = ROOT / "données"
OUT = ROOT / "extracted"
TXT_DIR = OUT / "txt"
MIN_CHARS_PER_PAGE = 20  # en dessous -> page probablement scannée (image)


RE_FICHE_TITRE = re.compile(r"Fiche\s+\d+\s*:\s*(.*)$", re.M)


def detecter_titre_reel(pages: list[dict]) -> str | None:
    """Certaines series de PDF (ex: 'Referentiel des financements des
    entreprises' de la Banque de France) ont un nom de fichier qui ne
    correspond pas a leur contenu reel (fichiers renommes/decales a la
    source). Le vrai titre est cite dans le corps du document lui-meme
    ("... | Fiche 325 : <titre>") -- on le prefere au nom de fichier.

    Un titre coupe par un retour a la ligne automatique de mise en page
    laisse un espace residuel juste avant le saut de ligne (contrairement
    a une fin de ligne "naturelle") -- c'est le signal utilise ici pour
    recoller la suite du titre sur la ligne physique suivante."""
    for page in pages:
        lignes = page["text"].split("\n")
        for i, ligne in enumerate(lignes):
            m = RE_FICHE_TITRE.search(ligne)
            if not m:
                continue
            titre = m.group(1)
            if titre.endswith(" ") and i + 1 < len(lignes):
                titre = titre.rstrip(" ") + " " + lignes[i + 1].strip()
            titre = titre.strip()
            if titre:
                return titre
    return None


def extraire_liens(page) -> list[dict]:
    """Liens externes (annotations 'uri'), pas les liens internes de navigation
    entre pages. Le texte d'une URL longue est souvent coupe par un retour a
    la ligne dans page.get_text() -> l'annotation est la source fiable."""
    liens = []
    for l in page.get_links():
        uri = l.get("uri")
        if not uri:
            continue
        liens.append({"uri": uri, "domaine": urlparse(uri).netloc})
    return liens


def extract_one(pdf_path: Path):
    rel = pdf_path.relative_to(DATA_DIR)
    doc = pymupdf.open(pdf_path)
    meta = doc.metadata or {}
    pages = []
    empty_pages = 0
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) < MIN_CHARS_PER_PAGE:
            empty_pages += 1
        pages.append({"page": i + 1, "text": text, "liens": extraire_liens(page)})
    doc.close()

    total_chars = sum(len(p["text"]) for p in pages)
    likely_scanned = len(pages) > 0 and empty_pages / len(pages) > 0.5
    tous_liens = [l for p in pages for l in p["liens"]]
    domaines = sorted({l["domaine"] for l in tous_liens})

    record = {
        "path": str(rel).replace("\\", "/"),
        "folder": str(rel.parent).replace("\\", "/"),
        "filename": pdf_path.name,
        "title": detecter_titre_reel(pages) or meta.get("title") or "",
        "num_pages": len(pages),
        "total_chars": total_chars,
        "empty_pages": empty_pages,
        "likely_scanned": likely_scanned,
        "num_liens": len(tous_liens),
        "domaines": domaines,
        "pages": pages,
    }
    return record


def main():
    pdfs = sorted(DATA_DIR.rglob("*.pdf"))
    if not pdfs:
        print("Aucun PDF trouve.")
        return

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    corpus_path = OUT / "corpus.jsonl"
    index_path = OUT / "index.csv"

    index_rows = []
    with corpus_path.open("w", encoding="utf-8") as corpus:
        for n, pdf in enumerate(pdfs, 1):
            try:
                rec = extract_one(pdf)
            except Exception as e:  # PDF corrompu / protege
                print(f"[{n}/{len(pdfs)}] ERREUR {pdf.name}: {e}", file=sys.stderr)
                index_rows.append({
                    "path": str(pdf.relative_to(DATA_DIR)).replace("\\", "/"),
                    "folder": str(pdf.relative_to(DATA_DIR).parent).replace("\\", "/"),
                    "filename": pdf.name, "num_pages": 0, "total_chars": 0,
                    "likely_scanned": "ERROR", "error": str(e),
                    "num_liens": 0, "domaines": "",
                })
                continue

            # .txt individuel
            txt_out = TXT_DIR / rec["path"]
            txt_out = txt_out.with_suffix(".txt")
            txt_out.parent.mkdir(parents=True, exist_ok=True)
            with txt_out.open("w", encoding="utf-8") as f:
                for p in rec["pages"]:
                    f.write(f"\n----- page {p['page']} -----\n{p['text']}\n")
                    for l in p["liens"]:
                        f.write(f"[lien] {l['uri']}\n")

            # ligne corpus.jsonl
            corpus.write(json.dumps(rec, ensure_ascii=False) + "\n")

            index_rows.append({
                "path": rec["path"], "folder": rec["folder"],
                "filename": rec["filename"], "num_pages": rec["num_pages"],
                "total_chars": rec["total_chars"],
                "likely_scanned": rec["likely_scanned"], "error": "",
                "num_liens": rec["num_liens"], "domaines": ";".join(rec["domaines"]),
            })
            flag = " [SCAN?]" if rec["likely_scanned"] else ""
            print(f"[{n}/{len(pdfs)}] {rec['num_pages']:>3}p  "
                  f"{rec['total_chars']:>7} chars  {rec['path']}{flag}")

    # index.csv
    with index_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "path", "folder", "filename", "num_pages",
            "total_chars", "likely_scanned", "error", "num_liens", "domaines"])
        w.writeheader()
        w.writerows(index_rows)

    scanned = [r for r in index_rows if r["likely_scanned"] == True]
    errors = [r for r in index_rows if r["likely_scanned"] == "ERROR"]
    print("\n=== RESUME ===")
    print(f"PDF traites      : {len(index_rows)}")
    print(f"Total pages      : {sum(r['num_pages'] for r in index_rows)}")
    print(f"Total caracteres : {sum(r['total_chars'] for r in index_rows):,}")
    print(f"PDF scannes (OCR requis) : {len(scanned)}")
    print(f"PDF en erreur    : {len(errors)}")
    print(f"\nSorties : {OUT}")


if __name__ == "__main__":
    main()
