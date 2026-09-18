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
from werkzeug.security import check_password_hash, generate_password_hash

from agents import traiter_question
from config import DSN
from rag_query import LLMError

app = Flask(__name__)
CORS(app)  # POC : autorise toutes les origines -- a restreindre avant toute mise en prod

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "données"  # les PDF sources sont regroupes ici (doc_path = "<domaine>/<fichier>")


@app.route("/sante", methods=["GET"])
def sante():
    return jsonify({"statut": "ok"})


@app.route("/document", methods=["GET"])
def document():
    """Sert le PDF source d'un chunk, pour que le frontend rende les sources
    cliquables (lecture en ligne, ou telechargement si ?dl=1).

    'path' est le doc_path relatif renvoye dans les sources (ex:
    'Finance/Les actions (1).pdf'). Anti-traversal : le chemin resolu doit
    rester sous DATA_DIR et pointer un PDF existant."""
    rel = (request.args.get("path") or "").strip()
    if not rel:
        return jsonify({"erreur": "parametre 'path' requis"}), 400

    cible = (DATA_DIR / rel).resolve()
    try:
        cible.relative_to(DATA_DIR.resolve())  # empeche de sortir de DATA_DIR via '..' / chemin absolu
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


@app.route("/register", methods=["POST"])
def register():
    """Inscription : cree un compte (email + mot de passe hache) avec les
    domaines choisis, puis renvoie l'identite pour ouvrir la session.

    POC : self-service (l'utilisateur choisit lui-meme ses domaines) et sans
    HTTPS ni jeton signe -- a encadrer avant toute mise en service."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    nom = (data.get("nom") or "").strip() or email
    password = data.get("password") or ""
    demandes = data.get("groupes") or []

    if not email or not password:
        return jsonify({"erreur": "email et mot de passe requis"}), 400
    if len(password) < 6:
        return jsonify({"erreur": "mot de passe trop court (6 caracteres minimum)"}), 400
    if not isinstance(demandes, list) or not demandes:
        return jsonify({"erreur": "choisis au moins un domaine"}), 400

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT nom FROM groupes")
    valides = {r[0] for r in cur.fetchall()}
    inconnus = [g for g in demandes if g not in valides]
    if inconnus:
        cur.close(); conn.close()
        return jsonify({"erreur": f"domaines inconnus : {inconnus}"}), 400

    cur.execute("SELECT 1 FROM utilisateurs WHERE email = %s", (email,))
    if cur.fetchone():
        cur.close(); conn.close()
        return jsonify({"erreur": "un compte existe deja pour cet email"}), 409

    cur.execute(
        "INSERT INTO utilisateurs (email, nom, password_hash) VALUES (%s, %s, %s) RETURNING id",
        (email, nom, generate_password_hash(password)),
    )
    uid = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO utilisateur_groupes (utilisateur_id, groupe_id)
           SELECT %s, id FROM groupes WHERE nom = ANY(%s)""",
        (uid, demandes),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"email": email, "nom": nom, "groupes": sorted(demandes)}), 201


@app.route("/login", methods=["POST"])
def login():
    """Connexion : verifie l'email + le mot de passe, renvoie l'identite et les
    domaines autorises (ce qui ouvre la session cote frontend)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"erreur": "email et mot de passe requis"}), 400

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT id, nom, password_hash FROM utilisateurs WHERE email = %s", (email,))
    row = cur.fetchone()
    if not row or not row[2] or not check_password_hash(row[2], password):
        cur.close(); conn.close()
        return jsonify({"erreur": "email ou mot de passe incorrect"}), 401

    uid, nom = row[0], row[1]
    cur.execute(
        """SELECT g.nom FROM utilisateur_groupes ug
           JOIN groupes g ON g.id = ug.groupe_id
           WHERE ug.utilisateur_id = %s ORDER BY g.nom""",
        (uid,),
    )
    groupes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({"email": email, "nom": nom, "groupes": groupes})


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
