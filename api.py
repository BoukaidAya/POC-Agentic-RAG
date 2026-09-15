#!/usr/bin/env python3
"""API Flask minimale exposant les agents par domaine -- backend pour un
frontend separe (ex: le futur projet GitLab d'interface).

Usage:
    uv run python api.py
Puis :
    POST http://localhost:5000/chat   {"question": "...", "email": "alice@art.fr"}
    GET  http://localhost:5000/sante
"""
from pathlib import Path

import psycopg2
from flask import Flask, abort, jsonify, request, send_file
from flask_cors import CORS

from agents import traiter_question
from config import DSN
from rag_query import LLMError

app = Flask(__name__)
CORS(app)  # POC : autorise toutes les origines -- a restreindre avant toute mise en prod

ROOT = Path(__file__).resolve().parent  # racine ou vivent les PDF (doc_path relatif)


@app.route("/sante", methods=["GET"])
def sante():
    return jsonify({"statut": "ok"})


@app.route("/document", methods=["GET"])
def document():
    """Sert le PDF source d'un chunk, pour que le frontend rende les sources
    cliquables (lecture en ligne, ou telechargement si ?dl=1).

    'path' est le doc_path relatif renvoye dans les sources (ex:
    'Finance/Les actions (1).pdf'). Anti-traversal : le chemin resolu doit
    rester sous ROOT et pointer un PDF existant."""
    rel = (request.args.get("path") or "").strip()
    if not rel:
        return jsonify({"erreur": "parametre 'path' requis"}), 400

    cible = (ROOT / rel).resolve()
    try:
        cible.relative_to(ROOT)  # empeche de sortir de ROOT via '..' / chemin absolu
    except ValueError:
        abort(403)
    if cible.suffix.lower() != ".pdf" or not cible.is_file():
        abort(404)

    telecharger = request.args.get("dl") == "1"
    return send_file(cible, mimetype="application/pdf",
                     as_attachment=telecharger, download_name=cible.name)


@app.route("/groupes", methods=["GET"])
def groupes():
    """Liste des domaines/groupes disponibles -- alimente le formulaire de
    creation d'utilisateur cote frontend."""
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT nom FROM groupes ORDER BY nom")
    noms = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({"groupes": noms})


@app.route("/utilisateurs", methods=["POST"])
def creer_utilisateur():
    """Cree (ou met a jour) un utilisateur et FIXE ses droits d'acces a la liste
    de groupes fournie. Sans authentification -- POC uniquement : a proteger
    avant toute mise en service (n'importe qui pourrait s'accorder des droits)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    nom = (data.get("nom") or email).strip()
    demandes = data.get("groupes") or []

    if not email:
        return jsonify({"erreur": "'email' est requis"}), 400
    if not isinstance(demandes, list) or not demandes:
        return jsonify({"erreur": "'groupes' (liste non vide) est requis"}), 400

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    cur.execute("SELECT nom FROM groupes")
    valides = {r[0] for r in cur.fetchall()}
    inconnus = [g for g in demandes if g not in valides]
    if inconnus:
        cur.close()
        conn.close()
        return jsonify({"erreur": f"domaines inconnus : {inconnus}. "
                        f"Valides : {sorted(valides)}"}), 400

    cur.execute(
        """INSERT INTO utilisateurs (email, nom) VALUES (%s, %s)
           ON CONFLICT (email) DO UPDATE SET nom = EXCLUDED.nom
           RETURNING id""",
        (email, nom),
    )
    utilisateur_id = cur.fetchone()[0]

    # on FIXE l'ensemble des droits = liste fournie (remplace l'existant)
    cur.execute("DELETE FROM utilisateur_groupes WHERE utilisateur_id = %s", (utilisateur_id,))
    cur.execute(
        """INSERT INTO utilisateur_groupes (utilisateur_id, groupe_id)
           SELECT %s, id FROM groupes WHERE nom = ANY(%s)""",
        (utilisateur_id, demandes),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"email": email, "nom": nom, "groupes": demandes}), 201


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    email = (data.get("email") or "").strip()

    if not question or not email:
        return jsonify({"erreur": "'question' et 'email' sont requis"}), 400

    try:
        resultat = traiter_question(question, email)
    except LLMError as e:
        # Erreur du LLM traduite en statut HTTP (429 avec Retry-After sur limite
        # de debit, 502/503 sinon) -- surtout pas de SystemExit qui casserait le
        # worker Flask.
        reponse = jsonify({"erreur": e.message})
        if e.retry_after:
            reponse.headers["Retry-After"] = e.retry_after
        return reponse, e.http_status

    if "erreur" in resultat:
        return jsonify(resultat), 404

    return jsonify(resultat)


if __name__ == "__main__":
    # host 0.0.0.0 : l'API tourne en conteneur (elle importe torch, bloque par
    # Smart App Control hors Docker) -- il faut ecouter sur toutes les interfaces
    # pour que le port publie soit joignable depuis l'hote (le navigateur).
    # debug=True : POC local uniquement.
    app.run(debug=True, host="0.0.0.0", port=5000)
