"""Config reseau centralisee (OpenSearch + PostgreSQL).

Surchargeable par variables d'environnement : indispensable en conteneur, ou
les services se joignent par leur nom (opensearch, postgres) et non par
localhost. Par defaut (sans variable), on garde localhost -> le comportement
d'origine hors Docker est inchange.

Regroupe aussi le DSN PostgreSQL qui etait duplique dans rag_query.py,
seed_acl.py et sync_acl.py (source de divergence).
"""
import os

OS_HOST = os.getenv("OS_HOST", "localhost")
OS_PORT = int(os.getenv("OS_PORT", "9200"))
DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=agentic_rag user=ragadmin password=ragadmin_dev_only",
)
