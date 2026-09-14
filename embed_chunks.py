#!/usr/bin/env python3
"""Calcule les embeddings BGE-M3 des chunks -> fichier pret pour bulk_index.py.

On encode "texte_avec_contexte" (le texte du chunk, prefixe du fil d'Ariane
pour les articles de loi) et pas "texte" seul : le contexte hierarchique doit
faire partie du vecteur, sinon un article isole comme "Le contrat de travail
est execute de bonne foi." perd le fait qu'il s'agit de droit du travail.

Usage:
    python embed_chunks.py            # tout le corpus
    python embed_chunks.py --test 50  # 50 chunks, pour mesurer le temps avant
                                       # de lancer les ~4800 en entier
Entree:
    extracted/chunks/chunks.jsonl
Sortie:
    extracted/embeddings/chunks_embeddings.jsonl
        (chaque chunk d'origine + son vecteur "embedding")
"""
import argparse
import json
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent
CHUNKS_IN = ROOT / "extracted" / "chunks" / "chunks.jsonl"
OUT_DIR = ROOT / "extracted" / "embeddings"
MODELE = "BAAI/bge-m3"
TAILLE_BATCH = 16  # sur CPU ; augmenter si tu as un GPU (--device cuda)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, default=0,
                     help="n'encoder que les N premiers chunks, pour estimer le temps")
    ap.add_argument("--fresh", action="store_true",
                     help="ignorer les embeddings deja calcules et tout recalculer")
    args = ap.parse_args()

    if not CHUNKS_IN.exists():
        print(f"Introuvable : {CHUNKS_IN} (lancer d'abord chunk_corpus.py)")
        return

    chunks = [json.loads(l) for l in CHUNKS_IN.open(encoding="utf-8")]
    if args.test:
        chunks = chunks[:args.test]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffixe = f".test{args.test}" if args.test else ""
    out_path = OUT_DIR / f"chunks_embeddings{suffixe}.jsonl"

    # Reprise sur interruption : l'encodage complet (~45 min CPU) ecrit au fil de
    # l'eau ; s'il est coupe, le fichier de sortie est un prefixe valide mais
    # incomplet. On relit les chunk_id deja encodes pour ne pas les recalculer, et
    # on ecrit en mode "append". --fresh force un recalcul complet.
    mode = "w"
    deja_faits: set[str] = set()
    if out_path.exists() and not args.fresh:
        deja_faits = {json.loads(l)["chunk_id"] for l in out_path.open(encoding="utf-8")}
        mode = "a"
    a_faire = [c for c in chunks if c["chunk_id"] not in deja_faits]

    if not a_faire:
        print(f"Rien a faire : les {len(chunks)} chunks sont deja encodes ({out_path}).")
        return
    if deja_faits:
        print(f"Reprise : {len(deja_faits)} chunks deja encodes ignores, "
              f"{len(a_faire)} restants a encoder.")

    print(f"Chargement du modele {MODELE} (premier lancement : telechargement ~2.2 Go)...")
    modele = SentenceTransformer(MODELE)

    chunks = a_faire
    debut = time.time()
    with out_path.open(mode, encoding="utf-8") as sortie:
        for i in range(0, len(chunks), TAILLE_BATCH):
            lot = chunks[i:i + TAILLE_BATCH]
            textes = [c["texte_avec_contexte"] for c in lot]
            vecteurs = modele.encode(textes, normalize_embeddings=True)

            for c, v in zip(lot, vecteurs):
                c["embedding"] = v.tolist()
                sortie.write(json.dumps(c, ensure_ascii=False) + "\n")

            traites = min(i + TAILLE_BATCH, len(chunks))
            ecoule = time.time() - debut
            rythme = traites / ecoule if ecoule > 0 else 0
            reste = (len(chunks) - traites) / rythme if rythme > 0 else 0
            print(f"[{traites}/{len(chunks)}] {ecoule:.0f}s ecoulees, "
                  f"~{reste:.0f}s restantes", end="\r")

    print(f"\n\n{len(chunks)} chunks encodes en {time.time() - debut:.0f}s -> {out_path}")


if __name__ == "__main__":
    main()
