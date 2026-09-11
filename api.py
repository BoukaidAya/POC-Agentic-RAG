#!/usr/bin/env python3
"""API Flask minimale exposant les agents par domaine -- backend pour un
frontend separe (ex: le futur projet GitLab d'interface).

Usage:
    uv run python api.py
Puis :
    POST http://localhost:5000/chat   {"question": "...", "email": "alice@art.fr"}
    GET  http://localhost:5000/sante
"""
from flask import Flask, jsonify, request
from flask_cors import CORS

from agents import traiter_question

app = Flask(__name__)
CORS(app)  # POC : autorise toutes les origines -- a restreindre avant toute mise en prod


@app.route("/sante", methods=["GET"])
def sante():
    return jsonify({"statut": "ok"})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    email = (data.get("email") or "").strip()

    if not question or not email:
        return jsonify({"erreur": "'question' et 'email' sont requis"}), 400

    resultat = traiter_question(question, email)
    if "erreur" in resultat:
        return jsonify(resultat), 404

    return jsonify(resultat)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
