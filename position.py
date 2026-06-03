#!/usr/bin/env python3
"""Met à jour portfolio.json à partir d'un avis d'opéré.

Recalcule automatiquement la quantité et le PRU (prix de revient unitaire).

Exemples
--------
  # Renforcer (ou ouvrir) une position : 16 titres à 5,726 €, frais 0 €
  python3 position.py achat DCAM.PA 16 --cours 5.726 --frais 0

  # Ouvrir une NOUVELLE position (nom + catégorie requis)
  python3 position.py achat AI.PA 4 --cours 175.20 \
      --nom "Air Liquide (AI)" --categorie "Actions FR"

  # Alléger une position : vendre 20 titres (le PRU ne change pas)
  python3 position.py vente TTE.PA 20

Les nombres acceptent la virgule décimale française (ex: 5,726).
"""
import argparse, json, os, sys

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "portfolio.json")


def num(s):
    """Parse un nombre en tolérant la virgule décimale et les espaces."""
    if s is None:
        return 0.0
    return float(str(s).replace(" ", "").replace(",", "."))


def fmt_qte(q):
    """Quantité en int si entière, sinon float."""
    return int(q) if float(q).is_integer() else round(float(q), 6)


def load():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find(portfolio, ticker):
    for p in portfolio:
        if p["ticker"].upper() == ticker.upper():
            return p
    return None


def achat(cfg, ticker, qte, cours, frais, nom, categorie):
    portfolio = cfg["portfolio"]
    qte   = num(qte)
    cours = num(cours)
    frais = num(frais)
    if qte <= 0:
        sys.exit("Erreur : la quantité achetée doit être > 0.")
    cout = qte * cours + frais  # = montant net de l'avis d'opéré

    p = find(portfolio, ticker)
    if p:  # ── Renforcement : moyenne pondérée ──
        ancienne_qte = num(p["qte"])
        ancien_pru   = num(p["pru"])
        nouvelle_qte = ancienne_qte + qte
        nouveau_pru  = (ancienne_qte * ancien_pru + cout) / nouvelle_qte
        print(f"Renforcement {p['nom']} ({ticker})")
        print(f"  avant : {fmt_qte(ancienne_qte)} titres @ PRU {ancien_pru:.4f} €")
        print(f"  achat : {fmt_qte(qte)} titres @ {cours:.4f} € + {frais:.2f} € frais "
              f"= {cout:.2f} €")
        p["qte"] = fmt_qte(nouvelle_qte)
        p["pru"] = round(nouveau_pru, 4)
        print(f"  après : {fmt_qte(nouvelle_qte)} titres @ PRU {nouveau_pru:.4f} €")
    else:  # ── Nouvelle position ──
        if not nom or not categorie:
            sys.exit(f"Erreur : {ticker} est une nouvelle position — "
                     f"--nom ET --categorie sont obligatoires.")
        pru = cout / qte
        nouvelle = {"nom": nom, "ticker": ticker.upper(),
                    "qte": fmt_qte(qte), "pru": round(pru, 4), "cat": categorie}
        portfolio.append(nouvelle)
        print(f"Nouvelle position {nom} ({ticker.upper()})")
        print(f"  {fmt_qte(qte)} titres @ {cours:.4f} € + {frais:.2f} € frais")
        print(f"  → PRU {pru:.4f} € · catégorie « {categorie} »")


def vente(cfg, ticker, qte, frais):
    portfolio = cfg["portfolio"]
    qte = num(qte)
    if qte <= 0:
        sys.exit("Erreur : la quantité vendue doit être > 0.")
    p = find(portfolio, ticker)
    if not p:
        sys.exit(f"Erreur : aucune position {ticker} dans le portefeuille.")
    ancienne_qte = num(p["qte"])
    if qte > ancienne_qte + 1e-9:
        sys.exit(f"Erreur : vente de {fmt_qte(qte)} > {fmt_qte(ancienne_qte)} détenus.")
    restante = ancienne_qte - qte
    print(f"Vente {p['nom']} ({ticker})")
    print(f"  avant : {fmt_qte(ancienne_qte)} titres @ PRU {num(p['pru']):.4f} €")
    print(f"  vente : {fmt_qte(qte)} titres (le PRU ne change pas)")
    if restante <= 1e-9:
        portfolio.remove(p)
        print(f"  après : position soldée et retirée du portefeuille.")
    else:
        p["qte"] = fmt_qte(restante)
        print(f"  après : {fmt_qte(restante)} titres @ PRU {num(p['pru']):.4f} €")


def main():
    ap = argparse.ArgumentParser(description="Met à jour portfolio.json (achat/vente).")
    ap.add_argument("operation", choices=["achat", "vente"])
    ap.add_argument("ticker", help="Ticker Yahoo Finance, ex: TTE.PA")
    ap.add_argument("quantite", help="Quantité de l'opération")
    ap.add_argument("--cours", default="0", help="Cours unitaire en € (achat)")
    ap.add_argument("--frais", default="0", help="Frais de courtage en €")
    ap.add_argument("--nom", default="", help="Nom complet (nouvelle position)")
    ap.add_argument("--categorie", default="", help="Catégorie (nouvelle position)")
    args = ap.parse_args()

    cfg = load()
    if args.operation == "achat":
        achat(cfg, args.ticker, args.quantite, args.cours, args.frais,
              args.nom.strip(), args.categorie.strip())
    else:
        vente(cfg, args.ticker, args.quantite, args.frais)

    save(cfg)
    print(f"\n✅ {CONFIG_PATH} mis à jour.")


if __name__ == "__main__":
    main()
