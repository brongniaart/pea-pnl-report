"""Vérifie le correctif NaN / marché fermé sans réseau (yfinance simulé)."""
import os, sys
os.environ.setdefault("GMAIL_USER", "x"); os.environ.setdefault("GMAIL_PASSWORD", "x")
os.environ.setdefault("GMAIL_DEST", "x")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from datetime import datetime
import pea_routine as m

def frame(closes, jours):
    idx = pd.to_datetime(jours).tz_localize("Europe/Paris")
    return pd.DataFrame({"Close": closes}, index=idx)

JOURS = ["2026-08-24","2026-08-25","2026-08-26","2026-08-27","2026-08-28"]

class FakeTicker:
    def __init__(self, t): self.t = t
    def history(self, period="13mo"):
        # dernière barre = celle du jour, non cotée -> Close NaN (cas réel)
        return frame([100.0, 101.0, 102.0, 103.0, np.nan], JOURS)
    @property
    def info(self): return {}
    @property
    def dividends(self): return pd.Series(dtype=float)

m.yf.Ticker = FakeTicker

pf = m.fetch_pea([dict(p) for p in m.DEFAULT_PORTFOLIO])
print("prix     :", [p["prix"] for p in pf])
print("cotation :", [str(p["cotation"]) for p in pf])
assert all(p["prix"] == 103.0 for p in pf), "le NaN doit être écarté"
assert all(str(p["cotation"]) == "2026-08-27" for p in pf)

c = m.calc(pf[0])
assert all(v is None or v == v for v in c.values()), c   # aucun NaN
print("calc     :", {k: round(v, 2) for k, v in c.items() if v is not None})

now = datetime(2026, 8, 28, 8, 6, tzinfo=m.PARIS)          # avant l'ouverture
print("etat     :", m.etat_marche(pf, now))
now_ferie = datetime(2026, 8, 28, 17, 40, tzinfo=m.PARIS)  # séance manquante
print("férié    :", m.etat_marche(pf, now_ferie))
assert "férié" in m.etat_marche(pf, now_ferie)[2]

html = m.build_html(pf, m.DEFAULT_PEE, m.fetch_marche(), now, history=m.load_history())
assert "nan" not in html.lower().replace("finance", ""), "NaN encore présent !"
assert "Marché fermé" in html
print("bandeau  : OK, aucun 'nan' dans le HTML")

txt = m.build_text(pf, m.DEFAULT_PEE, {}, now)
print("texte    :", [l for l in txt.splitlines() if "MARCHE" in l or "cloture" in l])

# formatage défensif
print("eur(nan) :", m.eur(float("nan")), "| pct(nan) :", m.pct(float("nan")),
      "| col(nan) :", m.col(float("nan")))
assert m.eur(float("nan")) == "N/A"

# history refuse un snapshot pollué
bad = {"date": "2026-08-28", "pea_valo": float("nan"), "pea_pl": 0, "pee_valo": 0,
       "pee_pl": 0, "total_valo": float("nan"), "total_pl": 0, "total_pl_pct": 0}
before = open(m.HISTORY_PATH).read()
m.append_history(bad)
assert open(m.HISTORY_PATH).read() == before, "history pollué !"
print("history  : snapshot NaN rejeté, fichier intact")

# tickers tous en échec -> aucun cours -> pas de point à 0
class Dead(FakeTicker):
    def history(self, period="13mo"): return pd.DataFrame({"Close": []})
m.yf.Ticker = Dead
pf2 = m.fetch_pea([dict(p) for p in m.DEFAULT_PORTFOLIO])
assert all(p["prix"] is None for p in pf2)
print("panne    :", "prix=None, valo exclue des totaux")
print("\nTOUS LES TESTS PASSENT")
