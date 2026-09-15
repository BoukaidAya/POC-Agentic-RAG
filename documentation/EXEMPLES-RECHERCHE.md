# Exemples de recherche — POC Agentic RAG

Jeu de questions prêtes à l'emploi pour tester et démontrer le système. Les
questions sont calées sur le contenu réel du corpus et sur les 4 comptes de
test. Copiables telles quelles dans le frontend (choix de l'utilisateur +
question) ou en CLI.

## Comptes de test

| Email | Domaines autorisés |
|---|---|
| `alice@art.fr` | RH |
| `bob@art.fr` | Finance |
| `carla@art.fr` | Juridique + Sécurité |
| `admin@art.fr` | Tous les domaines |

Rappel CLI :

```bash
# réponse simple (1 appel, filtré par droits)
docker compose run --rm app python rag_query.py --email alice@art.fr "..."

# routage agentique (routeur + 1 agent par domaine autorisé)
docker compose run --rm app python agents.py "..." --email carla@art.fr
```

---

## 1. Par domaine — questions qui aboutissent (1 domaine, utilisateur autorisé)

### RH — `alice@art.fr` (ou `admin@art.fr`)
- Comment fonctionne le bonus-malus de l'assurance chômage ?
- Qu'est-ce que la DSN et qui doit la transmettre ?
- Comment fonctionne la DPAE (déclaration préalable à l'embauche) ?
- Quelles sont les obligations liées à l'emploi des travailleurs handicapés (OETH) ?
- À quoi sert le CSE et à partir de quel effectif est-il obligatoire ?

### Finance — `bob@art.fr` (ou `admin@art.fr`)
- Quelle est la différence entre les actions ordinaires et les actions de préférence ?
- Comment fonctionne le crédit-bail pour financer un investissement ?
- Qu'est-ce qu'un prêt participatif ?
- Comment Bpifrance finance-t-elle les entreprises ?
- Qu'est-ce qu'un partenariat public-privé (PPP) ?

### Juridique (droit du travail) — `carla@art.fr` (ou `admin@art.fr`)
- Quels sont mes droits en cas de licenciement économique ?
- Quel est le délai pour contester un licenciement ?
- Que se passe-t-il en cas d'inaptitude médicale constatée par le médecin du travail ?
- Comment se calcule le préavis de licenciement ?

### Sécurité / RGPD — `carla@art.fr` (ou `admin@art.fr`)
- Quels sont les droits d'une personne sur ses données personnelles selon le RGPD ?
- Quelles sont les obligations d'un sous-traitant au sens du RGPD ?
- Quelles précautions de cybersécurité pour une TPE/PME ?
- Que dit la CNIL sur l'usage de l'intelligence artificielle ?

### Marketing / Communication — `admin@art.fr`
- Quelles sont les règles sur les cookies et traceurs ?
- Comment améliorer la relation client grâce au numérique ?

### Management / Direction — `admin@art.fr`
- En quoi consiste le dispositif PACTE Entreprises ?
- Comment catégoriser les dépenses dans un dossier de financement ?

---

## 2. Démonstration du contrôle d'accès (le cœur du POC)

La **même question**, deux utilisateurs, deux résultats :

| Question | `alice@art.fr` (RH) | `bob@art.fr` (Finance) |
|---|---|---|
| « Comment fonctionne le bonus-malus de l'assurance chômage ? » | ✅ répond | ❌ ne trouve rien (documents RH inaccessibles) |
| « Comment fonctionne le crédit-bail ? » | ❌ ne trouve rien | ✅ répond |

Le filtre par droits s'applique **avant** la recherche : un utilisateur ne voit
jamais un document hors de ses domaines, même si c'est le plus pertinent.

---

## 3. Routage multi-domaines (endpoint `agents.py` / frontend)

**Deux domaines autorisés** — `carla@art.fr` (juridique + sécurité) :
- **Quels sont mes droits en cas de licenciement et quelles règles RGPD s'appliquent à mes données ?**
  → le routeur active `juridique` **et** `securite` ; deux agents répondent, chacun avec ses sources.

**Un domaine pertinent mais refusé** — `alice@art.fr` (RH seulement) :
- **Quels sont mes droits en cas de licenciement et comment est calculé mon bonus-malus ?**
  → `rh` répond ; `juridique` est identifié comme pertinent mais **refusé** (bandeau 🔒 « domaines non autorisés »).

---

## 4. Vérifier le correctif ACL (documents auparavant perdus)

Ces deux documents existaient en double dans deux dossiers ; la déduplication
faisait perdre un des groupes d'accès. Après correctif, ils sont accessibles aux
deux groupes :

- `carla@art.fr` → **Guide RGPD pratique pour les TPE/PME (BPI + CNIL)**
  → retrouvé (rangé sous `marketing`, désormais aussi accessible à `securite`).
- `alice@art.fr` → une question couverte par la fiche `vosdroits_F22570`
  → RH y a de nouveau accès (avant : réservé à `juridique`).

---

## 5. Cas limites (comportement attendu)

- `bob@art.fr` → **Comment poser mes congés payés ?**
  → aucun domaine à la fois pertinent **et** autorisé → « pas de réponse possible ».
- N'importe quel utilisateur → **Quelle est la capitale de l'Australie ?**
  → hors corpus : le modèle doit répondre qu'il ne peut pas conclure à partir des
  extraits (garde-fou anti-hallucination), au lieu d'inventer.
