# CLAUDE.md

Guide pour Claude Code (et tout contributeur) travaillant sur ce dépôt.

## Objectif du projet

Routine automatisée qui envoie par email un **rapport P&L quotidien** d'un
portefeuille **PEA + PEE** (actions / ETF français), avec :

- valorisation, P&L jour / semaine / mois / YTD / depuis achat ;
- répartition du portefeuille (graphe en barres) ;
- courbe d'évolution de la valorisation totale (historique) ;
- le **vendredi**, une **note de marché** rédigée par Claude (Sonnet + recherche web) ;
- un **PDF** du rapport en pièce jointe.

Le tout tourne **gratuitement** via GitHub Actions (cron), sans serveur.

## Architecture (1 script principal)

`pea_routine.py` — tout le pipeline, exécuté par `main()` → `run()` :

1. `load_config()` — lit `portfolio.json` (fallback `DEFAULT_PORTFOLIO`/`DEFAULT_PEE`).
2. `fetch_pea(PORTFOLIO)` — cours via **yfinance** ; `_close_n_days_ago(h, n)`
   fait les lookups semaine (n=7) / mois (n=30) en **jours calendaires**
   (robuste aux week-ends et jours fériés, contrairement à `iloc[-6]`).
3. `fetch_marche()` — indices CAC/S&P/Nasdaq/EURUSD.
4. `append_history(snapshot)` — upsert par date dans `history.csv`.
5. `generate_commentary(...)` — **vendredi uniquement** + clé API présente :
   appelle `claude-sonnet-4-5` avec l'outil `web_search`. Le prompt est dans
   cette fonction. La sortie est nettoyée (voir « Pièges » plus bas).
6. `build_html(...)` — génère l'email HTML (CSS inline dans la constante `CSS`).
7. `build_text(...)` — version texte brut (fallback MIME `alternative`).
8. `generate_pdf(html)` — **WeasyPrint** (fonctionne sur Linux / GitHub Actions).
9. `send_email(...)` — SMTP Gmail, multipart `mixed` > `alternative` (texte puis HTML) + PDF.

`position.py` — outil de mise à jour de `portfolio.json` (achat/vente/nouvelle
position) avec recalcul du PRU. Utilisé en local **et** par le workflow
`position.yml`.

## Fichiers

| Fichier | Rôle |
|---|---|
| `pea_routine.py` | Script principal (rapport + email + PDF + commentaire IA). |
| `position.py` | Achat/vente/nouvelle position → réécrit `portfolio.json`. |
| `portfolio.json` | **Données utilisateur** (positions + PEE). Lu à chaque run. |
| `portfolio.example.json` | Modèle sans données perso (à copier en `portfolio.json`). |
| `history.csv` | Historique des snapshots (créé au 1ᵉʳ run, committé par le workflow). |
| `.github/workflows/pea_routine.yml` | Cron quotidien + envoi + commit `history.csv`. |
| `.github/workflows/position.yml` | Formulaire `workflow_dispatch` achat/vente. |
| `guide.html` | Guide d'installation grand public (A→Z, débutant). |

## Variables d'environnement (secrets)

- `GMAIL_USER` — adresse Gmail expéditrice.
- `GMAIL_PASSWORD` — **mot de passe d'application** Gmail (pas le mot de passe du compte).
- `GMAIL_DEST` — destinataire du rapport.
- `ANTHROPIC_API_KEY` — *(optionnel)* active la note de marché du vendredi.

`EMAIL = {...}` lit ces variables **au chargement du module** → toute commande
qui importe `pea_routine` doit les avoir définies (même factices).

## Tester en local

```bash
# Aperçu HTML + PDF sans envoyer d'email (variables factices)
GMAIL_USER=x GMAIL_PASSWORD=x GMAIL_DEST=x python3 -c "
import pea_routine as m
from datetime import datetime
pf = m.fetch_pea(m.PORTFOLIO); mkt = m.fetch_marche()
html = m.build_html(pf, m.PEE, mkt, datetime.now(m.PARIS), history=m.load_history())
open('preview_note.html','w').write(html)
"
# PDF via Chrome (macOS, alternative locale à WeasyPrint) :
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --no-pdf-header-footer \
  --print-to-pdf=preview.pdf "file://$PWD/preview_note.html"

# Mise à jour de position
python3 position.py achat TTE.PA 10 --cours 57.50 --frais 0
python3 position.py vente TTE.PA 5
```

`test_commentary.py` force un vendredi pour tester le récap hebdo + commentaire.

## Conventions & pièges

- **PDF** : WeasyPrint marche sur Linux mais **échoue sur macOS** (libgobject
  introuvable, SIP). En local sur Mac, utiliser **Chrome headless** (cf. ci-dessus).
- **Dark mode** : les couleurs P&L sont en `style=` inline. Le bloc
  `@media (prefers-color-scheme:dark)` ne doit **pas** mettre `!important` sur
  `tbody td`/`tfoot td`, sinon il écrase le rouge/vert.
- **Apostrophes** : utiliser uniquement des apostrophes ASCII `'` dans les
  littéraux Python (les apostrophes typographiques `’` cassent le parsing).
- **Nettoyage du commentaire** : le modèle insère des `\n` intra-phrase ; le code
  protège les `\n\n` (sentinelle `\x00`), aplatit le reste, refusionne les
  paragraphes commençant en minuscule, et filtre le préambule méta.
- **Catégories** : `position_cat(p)` lit le champ `cat` de chaque position ; le
  graphe de répartition et le badge ETF/Action en dépendent (dynamiques).
- **Coût IA** : ≈ 0,10-0,15 € par note (≈ 0,03 € de sortie pour 2000 tokens +
  contexte + 3 `web_search` à ≈ 0,03 € max), soit ~5 à 8 € / an (1 run le vendredi). `web_search`
  `max_uses=3`, `max_tokens=2000`.

## Données personnelles

`portfolio.json` contient des montants réels. Si le dépôt est **public**, ces
positions sont visibles. Recommander un **fork privé** (Actions reste gratuit).
Ne jamais committer de clés API, tokens, ou mots de passe : tout passe par les
**GitHub Secrets**.
