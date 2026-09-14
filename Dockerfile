# Image Linux pour la partie Python/torch : contourne Smart App Control de
# Windows (qui bloque les DLL non signees de PyTorch). Le code n'est PAS copie
# dans l'image -- il est monte en volume (voir docker-compose.yml service "app"),
# pour que les sorties (extracted/embeddings/...) atterrissent sur l'hote.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app

# pip a jour d'abord : le pip livre avec l'image bute sinon sur les noms de
# paquets a casse incoherente de l'index PyTorch (Jinja2/typing_extensions) et
# retombe sur une compilation source qui echoue.
RUN pip install --upgrade pip setuptools wheel

# torch en version CPU (l'index par defaut de PyPI tire la build CUDA, ~2 Go de
# plus, inutile sans GPU). Versions ML epinglees a celles du lockfile du projet
# (uv.lock) : c'est la combinaison deja resolue et testee. torch/transformers/
# sentence-transformers doivent bouger ensemble (un torch trop ancien fait voir
# is_torch_available()=False a transformers 5.x -> erreurs a l'import).
RUN pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu

RUN pip install \
    "transformers==5.17.0" \
    "sentence-transformers==6.0.1" \
    "pymupdf>=1.24.0" \
    "opensearch-py>=2.7.0" \
    "flask>=3.0.0" \
    "flask-cors>=4.0.0" \
    "psycopg2-binary>=2.9.0" \
    "python-dotenv>=1.0.0" \
    "anthropic>=0.40.0"

# Commande par defaut neutre : on lance les scripts via `docker compose run app python <script>.py`
CMD ["python", "--version"]
