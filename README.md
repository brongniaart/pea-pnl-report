# 📊 Rapport P&L PEA automatisé

Recevez chaque jour de bourse, par email, un **rapport élégant de votre portefeuille PEA + PEE** :
valorisation, gains/pertes (jour, semaine, mois, YTD, depuis achat), répartition, courbe
d'évolution, **PDF joint**, et — le vendredi — une **note de marché rédigée par une IA**.

Le tout **100 % gratuit**, hébergé par GitHub Actions. Aucun serveur, aucune carte bancaire.

---

## ✨ Fonctionnalités

- 💶 **Valorisation & P&L** en euros et en %
- ⏱️ **Toutes les périodes** : jour / semaine / mois / YTD / depuis l'ouverture
- 🥧 **Répartition dynamique** par catégorie (ETF, actions…)
- 📈 **Courbe d'évolution** de la valorisation totale (historique enregistré automatiquement)
- 🤖 **Note de marché IA** le vendredi (Claude + recherche web)
- 📄 **PDF** en pièce jointe + repli **texte** pour tous les clients mail
- 🧮 **Gestion des positions** : achat / vente / nouvelle ligne, avec recalcul automatique du PRU

---

## 🚀 Installation (≈ 15 min, sans code)

👉 **Suivez le guide pas-à-pas : [`guide.html`](guide.html)**

> Ouvrez `guide.html` dans votre navigateur (téléchargez-le, ou activez **GitHub Pages**
> dans *Settings → Pages* pour le consulter en ligne). C'est un guide illustré, conçu
> pour les débutants : fork, configuration, Gmail, secrets, activation, et fiabilisation.

### Résumé express

1. **Forkez** ce dépôt.
2. Copiez `portfolio.example.json` → `portfolio.json` et mettez vos positions.
3. Créez un **mot de passe d'application Gmail**.
4. Ajoutez les **Secrets GitHub** : `GMAIL_USER`, `GMAIL_PASSWORD`, `GMAIL_DEST`
   (+ `ANTHROPIC_API_KEY` optionnel pour la note IA).
5. Activez l'onglet **Actions** et lancez un premier **Run workflow**.

---

## 🧮 Mettre à jour une position

Depuis l'onglet **Actions → « Position — Achat / Vente » → Run workflow**, recopiez
votre avis d'opéré (quantité, cours, frais) : le PRU est recalculé et `portfolio.json`
mis à jour automatiquement. En local :

```bash
python3 position.py achat TTE.PA 10 --cours 57.50 --frais 0   # renforcement
python3 position.py vente TTE.PA 5                            # allègement
python3 position.py achat AI.PA 4 --cours 175.20 --nom "Air Liquide (AI)" --categorie "Actions FR"
```

---

## 🗂️ Structure

| Fichier | Rôle |
|---|---|
| `pea_routine.py` | Script principal : données, rapport HTML/PDF, email, note IA |
| `position.py` | Achat / vente / nouvelle position (recalcul du PRU) |
| `portfolio.example.json` | Modèle de configuration (à copier en `portfolio.json`) |
| `guide.html` | Guide d'installation illustré, A→Z |
| `CLAUDE.md` | Notes techniques (architecture, conventions, tests) |
| `.github/workflows/` | Automatisations (rapport quotidien + formulaire de position) |

---

## 🔒 Confidentialité

`portfolio.json` contient vos montants réels. Si le dépôt est **public**, ils sont visibles.
Pour les garder privés, utilisez un **fork privé** (Actions reste gratuit pour un usage perso).
Vos mots de passe et clés API ne sont **jamais** dans le code : ils vivent dans les **GitHub Secrets**.

---

## ⚠️ Avertissement

Ce projet est fourni à titre informatif et ne constitue **pas un conseil en investissement**.
Les cours proviennent de Yahoo Finance et peuvent comporter des délais ou des erreurs.

---

*Stack : GitHub Actions · Python · yfinance · Gmail SMTP · Claude (Anthropic) · WeasyPrint*
