#!/usr/bin/env python3
"""Boucle RAG minimale, sans framework : question -> droits reels de
l'utilisateur (PostgreSQL) -> recherche hybride filtree (OpenSearch) ->
Claude (API Anthropic) -> reponse sourcee.

Volontairement sans LangChain/LlamaIndex pour l'instant : valider que
la chaine complete marche, un maillon a la fois, avant d'ajouter la
complexite d'un framework d'orchestration par-dessus.

Necessite la variable d'environnement ANTHROPIC_API_KEY (dans .env, jamais
dans le code ni en argument de ligne de commande) -- voir .env.example.

Usage:
    python rag_query.py --email alice@art.fr "question en francais"
"""
import argparse
import sys

import anthropic
import psycopg2
from dotenv import load_dotenv
from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer

from config import DSN, OS_HOST, OS_PORT

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()  # lit .env s'il existe -- doit s'executer avant la creation
                # du client Anthropic ci-dessous

INDEX_NAME = "chunks_rag"
PIPELINE_NAME = "hybrid-search-pipeline"
MODELE_EMBEDDING = "BAAI/bge-m3"
MODELE_CLAUDE = "claude-opus-5"
K = 6

client_anthropic = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY automatiquement

# Instructions et contenu recupere strictement separes (system prompt vs
# message utilisateur) : le modele ne doit jamais confondre "ce qu'on lui
# demande de faire" et "le contenu qu'on lui donne a lire", meme si ce
# contenu vient de documents externes.
SYSTEM_PROMPT = (
    "Tu es un assistant interne qui repond UNIQUEMENT a partir des extraits "
    "de documents fournis entre balises <extraits>. N'utilise aucune autre "
    "connaissance. Si les extraits ne permettent pas de repondre, dis-le "
    "clairement plutot que d'inventer. Cite systematiquement tes sources "
    "sous la forme [Titre, page X]. Reponds en francais, de maniere concise."
)


def groupes_utilisateur(email: str) -> list[str]:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """SELECT g.nom FROM utilisateurs u
           JOIN utilisateur_groupes ug ON ug.utilisateur_id = u.id
           JOIN groupes g ON g.id = ug.groupe_id
           WHERE u.email = %s""",
        (email,),
    )
    groupes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return groupes


def rechercher(question: str, groupes: list[str], modele: SentenceTransformer) -> list[dict]:
    client = OpenSearch(hosts=[{"host": OS_HOST, "port": OS_PORT}], use_ssl=False, verify_certs=False)
    vecteur = modele.encode(question, normalize_embeddings=True).tolist()
    filtre_acl = {"terms": {"groupes_acl": groupes}}
    requete = {
        "size": K,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "hybrid": {
                "queries": [
                    {
                        "bool": {
                            "must": [{"match": {"texte": {"query": question}}}],
                            "filter": [filtre_acl],
                        }
                    },
                    {
                        "knn": {
                            "embedding": {"vector": vecteur, "k": K * 4, "filter": filtre_acl}
                        }
                    },
                ]
            }
        },
    }
    reponse = client.search(
        index=INDEX_NAME, body=requete, params={"search_pipeline": PIPELINE_NAME})
    return [h["_source"] for h in reponse["hits"]["hits"]]


def reference(chunk: dict) -> str:
    titre = chunk.get("titre_document") or chunk["filename"]
    pos = f"[{chunk['article']}]" if chunk.get("article") else f"p.{chunk['page_debut']}-{chunk['page_fin']}"
    return f"{titre} {pos}"


def source_detaillee(chunk: dict) -> dict:
    """Source structuree pour le frontend : libelle affichable + de quoi
    construire un lien vers le PDF (chemin relatif du document + page de debut).
    Le chemin sert le PDF via la route /document de l'API."""
    return {
        "label": reference(chunk),
        "doc_path": chunk.get("doc_path") or chunk.get("filename"),
        "page": chunk.get("page_debut"),
    }


def construire_contexte(chunks: list[dict]) -> str:
    blocs = [f"Source {i} -- {reference(c)} :\n{c['texte']}" for i, c in enumerate(chunks, 1)]
    return "\n\n".join(blocs)


class LLMError(Exception):
    """Erreur lors de l'appel au LLM.

    Ne herite PAS de SystemExit : sur le chemin Flask (api.py), un SystemExit
    (BaseException) n'est pas rattrape par la gestion d'erreurs normale et
    casse la requete/le worker. Porte le statut HTTP a renvoyer cote API ; le
    CLI, lui, se contente d'afficher le message."""

    def __init__(self, message: str, http_status: int = 502, retry_after: str | None = None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after


def appeler_llm(system_prompt: str, message: str) -> str:
    """Appel generique -- reutilise par la generation finale (rag_query.py)
    et par le routeur de domaines (agents.py), chacun avec son propre
    system_prompt.

    Pas de parametre "temperature" : sur claude-opus-5, temperature/top_p/
    top_k sont supprimes et renvoient une erreur 400 (contrairement aux
    anciens modeles) -- le controle de determinisme passe desormais par le
    prompt, pas par un parametre d'echantillonnage."""
    try:
        reponse = client_anthropic.messages.create(
            model=MODELE_CLAUDE,
            max_tokens=16000,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
    except anthropic.RateLimitError as e:
        retry_after = e.response.headers.get("retry-after", "60")
        raise LLMError(
            f"[Claude] limite de debit atteinte -- reessaie dans {retry_after}s.",
            http_status=429, retry_after=retry_after)
    except anthropic.APIStatusError as e:
        raise LLMError(f"[Claude] erreur API ({e.status_code}) : {e.message}",
                       http_status=502)
    except anthropic.APIConnectionError:
        raise LLMError(
            "[Claude] impossible de joindre l'API -- verifie ta connexion et "
            "ANTHROPIC_API_KEY dans .env.", http_status=503)

    return next((b.text for b in reponse.content if b.type == "text"), "(pas de reponse textuelle)")


def repondre(contexte: str, question: str) -> str:
    message = f"<extraits>\n{contexte}\n</extraits>\n\nQuestion : {question}"
    return appeler_llm(SYSTEM_PROMPT, message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--email", required=True, help="utilisateur simule, ex: alice@art.fr")
    args = ap.parse_args()

    groupes = groupes_utilisateur(args.email)
    if not groupes:
        print(f"Aucun groupe trouve pour {args.email} (utilisateur inconnu de PostgreSQL ?)")
        return
    print(f"Utilisateur : {args.email}  (groupes : {groupes})")

    modele = SentenceTransformer(MODELE_EMBEDDING)
    chunks = rechercher(args.question, groupes, modele)
    if not chunks:
        print("Aucun document accessible ne correspond a la question.")
        return

    try:
        reponse = repondre(construire_contexte(chunks), args.question)
    except LLMError as e:
        print(e.message)
        return

    print("\n--- Reponse ---")
    print(reponse)
    print("\n--- Sources utilisees (recherche, avant filtrage par le modele) ---")
    for c in chunks:
        print(f"  - {reference(c)}")


if __name__ == "__main__":
    main()
