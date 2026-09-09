#!/usr/bin/env python3
"""Extraction de texte de tous les PDF du projet -> .txt individuels + corpus.jsonl

Usage:
    python extract_pdfs.py
Sortie:
    extracted/txt/<meme arbo>.txt   (texte lisible, page par page)
    extracted/corpus.jsonl          (1 ligne JSON par PDF, texte par page)
    extracted/index.csv             (inventaire: fichier, dossier, pages, chars, ocr?)
"""
import csv
import json
import sys
from pathlib import Path

import pymupdf  # PyMuPDF

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "extracted"
TXT_DIR = OUT / "txt"
MIN_CHARS_PER_PAGE = 20  # en dessous -> page probablement scannée (image)


def extract_one(pdf_path: Path):
    rel = pdf_path.relative_to(ROOT)
    doc = pymupdf.open(pdf_path)
    meta = doc.metadata or {}
    pages = []
    empty_pages = 0
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) < MIN_CHARS_PER_PAGE:
            empty_pages += 1
        pages.append({"page": i + 1, "text": text})
    doc.close()

    total_chars = sum(len(p["text"]) for p in pages)
    likely_scanned = len(pages) > 0 and empty_pages / len(pages) > 0.5

    record = {
        "path": str(rel).replace("\\", "/"),
        "folder": str(rel.parent).replace("\\", "/"),
        "filename": pdf_path.name,
        "title": meta.get("title") or "",
        "num_pages": len(pages),
        "total_chars": total_chars,
        "empty_pages": empty_pages,
        "likely_scanned": likely_scanned,
        "pages": pages,
    }
    return record


def main():
    pdfs = sorted(ROOT.rglob("*.pdf"))
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
                    "path": str(pdf.relative_to(ROOT)).replace("\\", "/"),
                    "folder": str(pdf.relative_to(ROOT).parent).replace("\\", "/"),
                    "filename": pdf.name, "num_pages": 0, "total_chars": 0,
                    "likely_scanned": "ERROR", "error": str(e),
                })
                continue

            # .txt individuel
            txt_out = TXT_DIR / rec["path"]
            txt_out = txt_out.with_suffix(".txt")
            txt_out.parent.mkdir(parents=True, exist_ok=True)
            with txt_out.open("w", encoding="utf-8") as f:
                for p in rec["pages"]:
                    f.write(f"\n----- page {p['page']} -----\n{p['text']}\n")

            # ligne corpus.jsonl
            corpus.write(json.dumps(rec, ensure_ascii=False) + "\n")

            index_rows.append({
                "path": rec["path"], "folder": rec["folder"],
                "filename": rec["filename"], "num_pages": rec["num_pages"],
                "total_chars": rec["total_chars"],
                "likely_scanned": rec["likely_scanned"], "error": "",
            })
            flag = " [SCAN?]" if rec["likely_scanned"] else ""
            print(f"[{n}/{len(pdfs)}] {rec['num_pages']:>3}p  "
                  f"{rec['total_chars']:>7} chars  {rec['path']}{flag}")

    # index.csv
    with index_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "path", "folder", "filename", "num_pages",
            "total_chars", "likely_scanned", "error"])
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
