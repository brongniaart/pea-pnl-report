#!/usr/bin/env python3
"""Routine quotidienne P&L PEA + PEE - Design minimaliste."""
import smtplib, warnings, os, json, csv
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import yfinance as yf

warnings.filterwarnings("ignore")

PARIS = ZoneInfo("Europe/Paris")

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(BASE_DIR, "portfolio.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history.csv")
HISTORY_FIELDS = ["date", "pea_valo", "pea_pl", "pee_valo", "pee_pl",
                  "total_valo", "total_pl", "total_pl_pct"]

JOURS_FR = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
MOIS_FR  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]

def date_fr(now):
    return f"{JOURS_FR[now.weekday()]} {now.day:02d} {MOIS_FR[now.month-1]} {now.year}"

# ── Configuration par défaut (fallback si portfolio.json absent/illisible) ──
# Données d'EXEMPLE - remplacez-les via portfolio.json (voir portfolio.example.json).
DEFAULT_PORTFOLIO = [
    {"nom": "Amundi MSCI World (CW8)", "ticker": "CW8.PA", "qte": 10, "pru": 500.00, "cat": "ETF MSCI World"},
    {"nom": "Amundi Nasdaq-100 (PUST)", "ticker": "PUST.PA", "qte": 50, "pru": 70.00, "cat": "ETF Nasdaq-100"},
    {"nom": "TotalEnergies (TTE)", "ticker": "TTE.PA", "qte": 20, "pru": 55.00, "cat": "Actions FR"},
    {"nom": "LVMH (MC)", "ticker": "MC.PA", "qte": 2, "pru": 650.00, "cat": "Actions FR"},
]
DEFAULT_PEE = {"nom": "FCPE Actions Monde",
               "parts": 25.0, "pru": 150.00, "vl_last": 165.00, "vl_j2": 164.50,
               "vl_date": "01/01/2026", "disponibilite": "01/01/2030"}

def position_cat(p):
    """Catégorie d'une position pour la répartition. Utilise le champ 'cat' du
    JSON ; à défaut, heuristique de repli rétrocompatible."""
    if p.get("cat"):
        return p["cat"]
    if "Amundi" in p.get("nom", ""):
        return "ETF"
    return "Actions FR"

def position_type(p):
    """Badge ETF vs Action, dérivé de la catégorie."""
    return "ETF" if "ETF" in position_cat(p).upper() else "Action"

def pee_active(pee):
    """True si un PEE est réellement configuré (section présente ET parts > 0).
    Permet de n'utiliser le rapport que pour un PEA : il suffit de laisser la
    section 'pee' vide ({}) ou de mettre parts à 0 dans portfolio.json, et tout
    le bloc PEE (KPIs, table, disponibilité) disparaît du rapport."""
    try:
        return bool(pee) and float(pee.get("parts") or 0) > 0
    except (TypeError, ValueError):
        return False

def load_config():
    """Charge portfolio.json. Retourne (portfolio, pee). Fallback sur les
    valeurs par défaut si le fichier est absent ou invalide."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        portfolio = cfg.get("portfolio") or DEFAULT_PORTFOLIO
        # On NE force PAS de fallback sur le PEE : si la clé est absente, on
        # prend le défaut ; si elle est présente mais vide, on respecte le
        # choix de l'utilisateur (PEA seul) → voir pee_active().
        pee       = cfg.get("pee", DEFAULT_PEE)
        pee       = pee if pee is not None else {}
        # On travaille sur des copies pour ne pas muter la config en mémoire
        portfolio = [dict(p) for p in portfolio]
        pee       = dict(pee)
        print(f"[Config] {CONFIG_PATH} chargé - {len(portfolio)} position(s)", flush=True)
        return portfolio, pee
    except Exception as e:
        print(f"[Config] Échec lecture {CONFIG_PATH} ({e}) - fallback défaut", flush=True)
        return [dict(p) for p in DEFAULT_PORTFOLIO], dict(DEFAULT_PEE)

PORTFOLIO, PEE = load_config()

EMAIL = {
    "smtp_serveur": "smtp.gmail.com",
    "smtp_port": 587,
    "expediteur": os.environ["GMAIL_USER"],
    "mot_de_passe": os.environ["GMAIL_PASSWORD"],
    "destinataire": os.environ["GMAIL_DEST"],
}

def _close_n_days_ago(h, n):
    """Renvoie le cours de clôture le plus proche de (dernière date − n jours
    calendaires), en cherchant le dernier jour coté ≤ cette cible. Robuste aux
    week-ends et jours fériés, contrairement à un simple iloc[-6]/iloc[-22]."""
    if h is None or len(h) == 0:
        return None
    closes = h["Close"]
    last_date = closes.index[-1]
    target = last_date - timedelta(days=n)
    # Toutes les séances à la date cible ou avant
    prior = closes[closes.index <= target]
    if len(prior) >= 1:
        return float(prior.iloc[-1])
    # Pas assez d'historique : on prend la plus ancienne dispo
    return float(closes.iloc[0])

def fetch_pea(portfolio):
    for p in portfolio:
        try:
            h = yf.Ticker(p["ticker"]).history(period="13mo")
            p["prix"]   = float(h["Close"].iloc[-1]) if len(h) >= 1 else None
            p["veille"] = float(h["Close"].iloc[-2]) if len(h) >= 2 else p["prix"]
            p["5d"]     = _close_n_days_ago(h, 7)  if len(h) >= 2 else None
            p["1mo"]    = _close_n_days_ago(h, 30) if len(h) >= 2 else None
            jan1 = h[h.index.year == datetime.now().year]
            p["ytd"]    = float(jan1["Close"].iloc[0]) if len(jan1) >= 1 else None
        except Exception:
            p["prix"] = p["veille"] = p["5d"] = p["1mo"] = p["ytd"] = None
    return portfolio

def fetch_marche():
    indices = {"CAC 40": "^FCHI", "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "EUR/USD": "EURUSD=X"}
    result = {}
    for nom, ticker in indices.items():
        try:
            h = yf.Ticker(ticker).history(period="5d")
            if len(h) >= 2:
                cur, prev = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
                result[nom] = {"val": cur, "pct": (cur - prev) / prev * 100}
        except Exception:
            result[nom] = None
    return result

def generate_commentary(portfolio, marche, now):
    """Génère une note de marché via Claude Sonnet + web_search. Vendredi seulement."""
    try:
        import anthropic

        # Résumé marchés (contexte pour Claude)
        mkt_lines = []
        for nom, d in marche.items():
            if d:
                s = "+" if d["pct"] > 0 else ""
                mkt_lines.append(f"{nom} : {fmt_index(d['val'])} ({s}{d['pct']:.2f}%)")

        # Résumé portefeuille avec variation semaine
        port_lines = []
        for p in portfolio:
            if p.get("prix") and p.get("veille"):
                inv    = p["qte"] * p["pru"]
                valo   = p["qte"] * p["prix"]
                pl_p   = (valo - inv) / inv * 100
                jour_p = (p["prix"] - p["veille"]) / p["veille"] * 100
                sem_p  = (p["prix"] - p["5d"]) / p["5d"] * 100 if p.get("5d") else None
                sj = "+" if jour_p > 0 else ""
                sp = "+" if pl_p   > 0 else ""
                ss = (("+" if sem_p > 0 else "") + f"{sem_p:.2f}% sem") if sem_p else "sem N/A"
                port_lines.append(
                    f"{p['nom'].split('(')[0].strip()} ({p['ticker']}) : "
                    f"cours {p['prix']:.2f}€ · jour {sj}{jour_p:.2f}% · {ss} · "
                    f"latent {sp}{pl_p:.1f}%"
                )

        tickers_str = ", ".join(
            p["nom"].split("(")[0].strip() for p in portfolio if p.get("prix")
        )

        prompt = f"""Tu es un analyste financier senior rédigeant la note hebdomadaire pour un investisseur particulier français.

Date : {date_fr(now)}, {now.strftime('%H:%M')}

PORTEFEUILLE (contexte uniquement - NE PAS recopier ces chiffres dans ta note) :
{chr(10).join(port_lines)}

INDICES (contexte uniquement) :
{chr(10).join(mkt_lines)}

ÉTAPE 1 - Fais 3 à 5 recherches web sur :
- L'actualité de chaque titre en portefeuille cette semaine : {tickers_str}
- Le contexte macro : pétrole, Fed, BCE, Chine, résultats trimestriels

ÉTAPE 2 - Rédige une note en EXACTEMENT 3 paragraphes distincts :

§1 - MICRO : L'actualité la plus marquante sur UN titre du portefeuille cette semaine. Explique le fait précis (annonce, résultat, deal, déclaration), son contexte sectoriel, et ce que ça signifie concrètement pour la position.

§2 - MACRO : Le mouvement dominant des marchés cette semaine et sa cause réelle (décision Fed/BCE, données éco, géopolitique, flux de capitaux). Pas de généralités - cite l'événement précis.

§3 - PERSPECTIVE : Un risque ou une opportunité spécifique à surveiller la semaine prochaine pour CE portefeuille (pas une liste générique).

RÈGLES :
- Minimum 4 phrases par paragraphe
- Chiffres que TU cites (pas ceux du rapport) : <strong class="up"> pour positifs, <strong class="dn"> pour négatifs
- Zéro formule creuse : interdit d'écrire "contexte incertain", "environnement volatile", "prises de bénéfices", "wait and see"
- Français, ton direct et professionnel, pas de titre ni de bullet points, ne mentionne pas que tu es une IA
- COMMENCE DIRECTEMENT par le contenu - interdit d'écrire "D'après mes recherches", "Je vais maintenant", "Voici la note" ou toute phrase expliquant ce que tu vas faire"""

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3
            }],
            messages=[{"role": "user", "content": prompt}]
        )

        import re

        # Concaténer tous les blocs texte dans l'ordre d'apparition.
        # web_search intercale des blocs courts (réflexion) entre les recherches :
        # on les filtre en ignorant tout bloc de moins de 80 caractères.
        text_blocks = [block.text.strip() for block in msg.content
                       if hasattr(block, "text") and len(block.text.strip()) > 80]
        if not text_blocks:
            print("[Commentary] Aucun bloc texte suffisant", flush=True)
            return None

        text = "\n\n".join(text_blocks)

        # ── Nettoyage des artefacts de formatage ────────────────────────────
        # Le modèle insère des \n simples au milieu des phrases (surtout après
        # les balises </strong>). On efface TOUS ces sauts simples en les
        # remplaçant par un espace, en préservant les vrais \n\n de paragraphe.

        # 1. Protéger les vrais séparateurs de paragraphes
        text = re.sub(r'\n{2,}', '\x00', text)
        # 2. Coller toutes les coupures intra-phrase
        text = text.replace('\n', ' ')
        # 3. Restaurer les séparateurs
        text = text.replace('\x00', '\n\n')
        # 4. Nettoyer les espaces multiples
        text = re.sub(r' {2,}', ' ', text)

        # Découper en paragraphes bruts
        raw_paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        # Filtrer les artéfacts (préambule méta + séparateurs)
        PREAMBLE = ('d\'apres mes recherches', 'je vais maintenant',
                    'je vais rediger', 'voici la note', 'voici mon analyse')
        filtered = []
        for p in raw_paragraphs:
            pl = p.lower().replace('\xe9', 'e').replace('\xe8', 'e').replace('\xea', 'e')
            if any(pl.startswith(m) for m in PREAMBLE):
                continue
            if p.strip('- =*') == '':
                continue
            filtered.append(p)

        # Fusionner les continuations :
        # En français, un vrai paragraphe commence toujours par une majuscule.
        # Minuscule en debut = coupure intra-phrase due au modele.
        merged = []
        for p in filtered:
            is_continuation = (
                p[:2] in ('. ', ', ', '; ', '-- ', '- ') or
                p[0:1].islower()
            )
            if merged and is_continuation:
                merged[-1] = merged[-1].rstrip() + ' ' + p
            else:
                merged.append(p)
        paragraphs = merged

        print(f"[Commentary] Générée via Sonnet+search - {len(paragraphs)} paragraphe(s), "
              f"{len(text)} caractères", flush=True)
        return "".join(f"<p>{p}</p>" for p in paragraphs)

    except Exception as e:
        print(f"[Commentary] Génération échouée : {e}", flush=True)
        import traceback; traceback.print_exc()
        return None


# ── Historique (#1) ─────────────────────────────────────────────────────────
def load_history():
    """Charge history.csv en liste de dicts triée par date croissante."""
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        # Convertir les colonnes numériques
        for r in rows:
            for k in HISTORY_FIELDS:
                if k != "date" and r.get(k) not in (None, ""):
                    try:
                        r[k] = float(r[k])
                    except ValueError:
                        r[k] = None
        rows.sort(key=lambda r: r.get("date", ""))
        return rows
    except Exception as e:
        print(f"[History] Lecture échouée : {e}", flush=True)
        return []

def append_history(snapshot):
    """Ajoute (ou met à jour, upsert par date) un snapshot dans history.csv."""
    try:
        rows = load_history()
        by_date = {r["date"]: r for r in rows}
        by_date[snapshot["date"]] = snapshot  # upsert
        merged = sorted(by_date.values(), key=lambda r: r["date"])
        with open(HISTORY_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
            w.writeheader()
            for r in merged:
                w.writerow({k: r.get(k, "") for k in HISTORY_FIELDS})
        print(f"[History] {snapshot['date']} enregistré - {len(merged)} point(s)", flush=True)
        return merged
    except Exception as e:
        print(f"[History] Écriture échouée : {e}", flush=True)
        return load_history()

def build_history_chart_html(history):
    """Mini graphique (barres CSS) de la valorisation totale sur les 5 derniers
    relevés. Renvoie '' si moins de 2 points disponibles. Les barres vivent dans
    une piste (.ch-track) à hauteur fixe pour ne jamais déborder du cadre."""
    pts = [r for r in history if r.get("total_valo") not in (None, "")][-5:]
    if len(pts) < 2:
        return ""

    valos = [r["total_valo"] for r in pts]
    vmin, vmax = min(valos), max(valos)
    span = (vmax - vmin) or 1

    first_v = pts[0]["total_valo"]
    last_v  = pts[-1]["total_valo"]
    delta   = last_v - first_v
    delta_p = delta / first_v * 100 if first_v else 0
    dcol    = "up" if delta >= 0 else "dn"

    def _kfmt(v):
        # Etiquette compacte au-dessus de la barre : 37,1k si >= 1000
        return f"{v/1000:.1f}k".replace(".", ",") if v >= 1000 else f"{v:.0f}"

    bars = ""
    for r in pts:
        v = r["total_valo"]
        # Hauteur normalisée 18-100% pour que les petites barres restent visibles
        h = 30 + (v - vmin) / span * 70
        bcol = "#16a34a" if (r.get("total_pl") or 0) >= 0 else "#dc2626"
        # Étiquette : JJ/MM extrait de la date ISO YYYY-MM-DD
        d = r.get("date", "")
        lbl = f"{d[8:10]}/{d[5:7]}" if len(d) >= 10 else d
        bars += (f'<div class="ch-bar" title="{lbl} · {v:,.0f} €">'
                 f'<div class="ch-bv">{_kfmt(v)}</div>'
                 f'<div class="ch-track"><div class="ch-bar-fill" '
                 f'style="height:{h:.0f}%;background:{bcol}"></div></div>'
                 f'<div class="ch-bar-lbl">{lbl}</div></div>').replace(",", " ")

    return (
        f'<div class="sec" style="padding-top:18px">Évolution de la valorisation totale</div>'
        f'<div class="chart">'
        f'  <div class="ch-head">'
        f'    <div><span class="ch-val">{last_v:,.0f} €</span>'
        f'      <span class="ch-delta {dcol}">{"+" if delta>=0 else ""}{delta:,.0f} € '
        f'({"+" if delta_p>=0 else ""}{delta_p:.2f}%)</span></div>'
        f'    <div class="ch-lbl">{len(pts)} derniers relevés</div>'
        f'  </div>'
        f'  <div class="ch-bars">{bars}</div>'
        f'</div>'
    ).replace(",", " ")


def compute_ath(history):
    """Analyse du plus-haut historique (ATH) sur la valorisation totale.
    Renvoie un dict : nouveau record, ou distance (drawdown) au plus-haut
    precedent. None si moins de 2 releves."""
    pts = [r for r in history if r.get("total_valo") not in (None, "")]
    if len(pts) < 2:
        return None
    valos    = [r["total_valo"] for r in pts]
    current  = valos[-1]
    prev     = valos[:-1]
    prev_ath = max(prev)
    prev_dt  = pts[prev.index(prev_ath)].get("date", "")
    if current >= prev_ath:
        return {"new": True, "ath": current, "prev_ath": prev_ath,
                "prev_date": prev_dt, "gain": current - prev_ath}
    return {"new": False, "ath": prev_ath, "prev_date": prev_dt,
            "dist": current - prev_ath,
            "dist_pct": (current - prev_ath) / prev_ath * 100 if prev_ath else 0}


def _date_court(iso):
    """JJ/MM/AAAA depuis une date ISO YYYY-MM-DD."""
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if len(iso) >= 10 else iso


def build_ath_html(history):
    """Bandeau plus-haut historique : message de record, ou distance au dernier
    sommet. Renvoie '' si l'historique est trop court."""
    ath = compute_ath(history) if history else None
    if not ath:
        return ""
    if ath["new"]:
        extra = ""
        if ath["gain"] > 0:
            extra = (f" - soit {eur(ath['gain'], True)} au-dessus du precedent "
                     f"record du {_date_court(ath['prev_date'])}")
        return (f'<div class="ath ath-up"><span class="ath-i">&#127942;</span>'
                f'<div><b>Nouveau plus-haut historique</b> : {eur(ath["ath"])}{extra}.</div></div>')
    return (f'<div class="ath ath-dn"><span class="ath-i">&#128202;</span>'
            f'<div><b>{eur(ath["dist"], True)}</b> ({pct(ath["dist_pct"])}) sous le plus-haut '
            f'historique de {eur(ath["ath"])}, atteint le {_date_court(ath["prev_date"])}.</div></div>')


def eur(v, sign=False):
    if v is None: return "N/A"
    s = "+" if sign and v > 0 else ""
    return f"{s}{v:,.0f}&nbsp;€".replace(",", "&nbsp;")

def pct(v, sign=True):
    if v is None: return "N/A"
    s = "+" if sign and v > 0 else ""
    return f"{s}{v:.2f}%"

def col(v):
    if v is None or v == 0: return "#9ca3af"
    return "#16a34a" if v > 0 else "#dc2626"

def fmt_index(v):
    if v is None: return "-"
    if v > 1000: return f"{v:,.0f}".replace(",", "&nbsp;")
    return f"{v:.4f}"

def calc(p):
    px, pru, qte = p["prix"], p["pru"], p["qte"]
    invest = qte * pru
    valo   = qte * px if px else None
    pl     = valo - invest if valo is not None else None
    pl_pct = pl / invest * 100 if pl is not None else None
    jour   = qte * (px - p["veille"]) if px and p["veille"] else None
    jour_p = (px - p["veille"]) / p["veille"] * 100 if px and p["veille"] else None
    semaine = qte * (px - p["5d"])  if px and p["5d"]  else None
    mois    = qte * (px - p["1mo"]) if px and p["1mo"] else None
    ytd_pl  = qte * (px - p["ytd"]) if px and p["ytd"] else None
    return dict(invest=invest, valo=valo, pl=pl, pl_pct=pl_pct,
                jour=jour, jour_p=jour_p, semaine=semaine, mois=mois, ytd_pl=ytd_pl)

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f3f4f6;padding:24px 12px;font-family:'Inter',-apple-system,sans-serif}
.w{max-width:720px;margin:0 auto;background:#fff;border-radius:10px;border:1px solid #e5e7eb;overflow:hidden}
.hdr{padding:32px 36px 24px;border-bottom:1px solid #f3f4f6}
.hdr-date{font-size:12px;color:#9ca3af;margin-bottom:5px}
.hdr-title{font-size:21px;font-weight:600;color:#111;letter-spacing:-.3px}
.kpi-row{display:flex;border-bottom:1px solid #f3f4f6}
.kpi{flex:1;padding:18px 22px;border-right:1px solid #f3f4f6}
.kpi:last-child{border-right:none}
.kpi-l{font-size:9.5px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px}
.kpi-v{font-size:18px;font-weight:600;color:#111;letter-spacing:-.3px}
.kpi-s{font-size:11px;margin-top:2px;font-weight:500}
.up{color:#16a34a}.dn{color:#dc2626}.mu{color:#9ca3af}
.mkt{display:flex;padding:10px 22px;background:#fafafa;border-bottom:1px solid #f3f4f6;gap:0}
.mkt-i{flex:1;display:flex;align-items:center;gap:7px;padding:0 8px;border-right:1px solid #f0f0f0}
.mkt-i:first-child{padding-left:0}.mkt-i:last-child{border-right:none}
.mkt-n{font-size:9.5px;font-weight:600;color:#6b7280;letter-spacing:.4px}
.mkt-v{font-size:11px;font-weight:600;color:#111}
.mkt-c{font-size:10px;font-weight:600}
.alert-ok{display:flex;align-items:center;gap:8px;padding:9px 22px;background:#f0fdf4;border-bottom:1px solid #dcfce7;font-size:11.5px;color:#16a34a;font-weight:500}
.alert-w{display:flex;align-items:center;gap:8px;padding:9px 22px;background:#fef9c3;border-bottom:1px solid #fef08a;font-size:11.5px;color:#854d0e;font-weight:500}
.bw{display:flex;gap:10px;padding:12px 22px;border-bottom:1px solid #f3f4f6}
.bw-c{flex:1;padding:12px 14px;border-radius:7px;border:1px solid #f3f4f6;background:#fafafa}
.bw-t{font-size:9px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px}
.bw-n{font-size:12px;font-weight:600;color:#111}
.bw-p{font-size:16px;font-weight:700;margin-top:2px}
.bw-d{font-size:10px;color:#9ca3af;margin-top:2px}
.sec{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#d1d5db;padding:14px 22px 5px}
.per{display:flex;padding:0 22px 12px;gap:8px}
.per-c{flex:1;background:#fafafa;border:1px solid #f3f4f6;border-radius:7px;padding:10px 10px;text-align:center}
.per-l{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:1px;color:#9ca3af;margin-bottom:4px}
.per-v{font-size:13px;font-weight:700}
.per-s{font-size:10px;margin-top:1px;color:#9ca3af}
.alloc{padding:0 22px 14px}
.al-r{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.al-l{font-size:11px;color:#374151;font-weight:500;width:130px;flex-shrink:0}
.al-b{flex:1;background:#f3f4f6;border-radius:3px;height:5px;overflow:hidden}
.al-f{height:100%;border-radius:3px}
.al-p{font-size:11px;font-weight:600;width:38px;text-align:right;flex-shrink:0}
.al-e{font-size:10px;color:#9ca3af;width:58px;text-align:right;flex-shrink:0}
.tw{padding:0 22px 4px}
table{width:100%;border-collapse:collapse;font-size:11.5px;table-layout:fixed}
thead th{padding:6px 4px;font-size:8.5px;font-weight:600;letter-spacing:.8px;text-transform:uppercase;color:#d1d5db;border-bottom:1px solid #f0f0f0;text-align:right;white-space:nowrap}
thead th.L{text-align:left}
tbody td{padding:9px 4px;border-bottom:1px solid #f9fafb;text-align:right;color:#374151;vertical-align:middle}
tbody td.L{text-align:left}
tbody tr:last-child td{border-bottom:none}
.tn{font-weight:500;color:#111;font-size:12px;display:block}
.tt{font-size:9.5px;color:#d1d5db;display:block;margin-top:1px}
tfoot td{padding:9px 4px;font-size:11.5px;font-weight:600;color:#111;border-top:2px solid #f3f4f6;text-align:right}
tfoot td.L{text-align:left}
.ps{display:flex;align-items:center;gap:10px;padding:16px 22px}
.pl{flex:1;height:1px;background:#f3f4f6}
.pt{font-size:8.5px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#0d9488;background:#f0fdfa;border:1px solid #ccfbf1;padding:4px 10px;border-radius:20px;white-space:nowrap}
.cd{margin:0 22px 14px;padding:13px 16px;background:#f0fdfa;border:1px solid #ccfbf1;border-radius:8px;display:flex;justify-content:space-between;align-items:center}
.cd-l{font-size:11.5px;color:#0f766e;font-weight:500}
.cd-l span{display:block;font-size:9.5px;color:#5eead4;margin-top:2px;font-weight:400}
.cd-r{text-align:right}
.cd-d{font-size:20px;font-weight:700;color:#0f766e}
.cd-s{font-size:9.5px;color:#5eead4;margin-top:1px}
.tg{margin:14px 22px 18px;padding:16px 20px;background:#111;border-radius:8px;display:flex;justify-content:space-between;align-items:center}
.tg-l{font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;font-weight:500}
.tg-v{font-size:22px;font-weight:700;color:#fff;letter-spacing:-.4px}
.tg-r{text-align:right}
.tg-p{font-size:18px;font-weight:700;color:#4ade80}
.tg-s{font-size:10px;color:#4ade80;margin-top:2px}
.ftr{padding:12px;text-align:center;font-size:9.5px;color:#e5e7eb;border-top:1px solid #f9fafb}
.note{margin:0 22px 20px;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb}
.note-hdr{background:#111;padding:14px 20px;display:flex;align-items:center;gap:10px}
.note-hdr-icon{font-size:14px}
.note-hdr-left{flex:1}
.note-hdr-title{font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#fff}
.note-hdr-sub{font-size:9.5px;color:#6b7280;margin-top:2px}
.note-hdr-badge{font-size:8.5px;font-weight:600;letter-spacing:1px;text-transform:uppercase;background:#27272a;color:#a3e635;padding:3px 8px;border-radius:12px;white-space:nowrap}
.note-body{padding:18px 20px;background:#fafafa;font-size:12.5px;line-height:1.75;color:#374151}
.note-body p{margin-bottom:10px}
.note-body p:last-child{margin-bottom:0}
.note-sig{padding:10px 20px;background:#f3f4f6;border-top:1px solid #e5e7eb;font-size:9px;color:#9ca3af;text-align:right}
.chart{margin:0 22px 18px;padding:14px 18px;background:#fafafa;border:1px solid #f3f4f6;border-radius:8px}
.ch-head{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:12px}
.ch-val{font-size:18px;font-weight:700;color:#111}
.ch-delta{font-size:11px;font-weight:600;margin-left:8px}
.ch-lbl{font-size:9.5px;color:#9ca3af;font-weight:500;text-transform:uppercase;letter-spacing:.5px}
.ch-bars{display:flex;align-items:flex-end;gap:10px}
.ch-bar{flex:1;display:flex;flex-direction:column;align-items:center}
.ch-bv{font-size:9px;color:#6b7280;font-weight:600;margin-bottom:3px;white-space:nowrap}
.ch-track{height:90px;width:100%;display:flex;align-items:flex-end;justify-content:center}
.ch-bar-fill{width:100%;max-width:46px;border-radius:4px 4px 0 0;min-height:4px}
.ch-bar-lbl{font-size:9px;color:#9ca3af;margin-top:6px;white-space:nowrap}
.ath{display:flex;align-items:center;gap:10px;margin:0 22px 14px;padding:12px 16px;border-radius:8px;font-size:12px;line-height:1.45}
.ath-i{font-size:18px;flex-shrink:0;line-height:1}
.ath-up{background:#f0fdf4;border:1px solid #bbf7d0;color:#15803d}
.ath-dn{background:#fffbeb;border:1px solid #fde68a;color:#b45309}
@media (prefers-color-scheme:dark){
  body{background:#f3f4f6 !important}
  .w{background:#fff !important;color:#111 !important}
  .hdr{background:#fff !important}
  .hdr-date{color:#9ca3af !important}
  .hdr-title{color:#111 !important}
  .kpi{background:#fff !important}
  .kpi-v{color:#111 !important}
  .kpi-s{color:#6b7280 !important}
  .mkt{background:#fafafa !important}
  .mkt-v{color:#111 !important}
  .bw-c{background:#fafafa !important}
  .bw-n{color:#111 !important}
  .per-c{background:#fafafa !important}
  .sec{color:#d1d5db !important}
  tbody td{color:#374151}
  tfoot td{color:#111}
  .tn{color:#111 !important}
  .note-body{background:#fafafa !important;color:#374151 !important}
  .note-sig{background:#f3f4f6 !important;color:#9ca3af !important}
  .chart{background:#fafafa !important}
  .ch-val{color:#111 !important}
}
@media only screen and (max-width:600px){
  body{padding:10px 0 !important;background:#fff !important}
  .w{max-width:100% !important;border:none !important;border-radius:0 !important}
  .hdr{padding:20px 16px 14px !important}
  .hdr-title{font-size:18px !important}
  .kpi{padding:12px 9px !important}
  .kpi-l{font-size:8.5px !important;letter-spacing:.4px !important}
  .kpi-v{font-size:15px !important}
  .kpi-s{font-size:10px !important}
  .mkt{padding:8px 10px !important}
  .mkt-i{padding:0 5px !important;gap:4px !important}
  .bw{padding:10px 12px !important;gap:8px !important}
  .bw-c{padding:10px 11px !important}
  .per{padding:0 12px 12px !important;gap:6px !important}
  .per-c{padding:8px 6px !important}
  .alloc{padding:0 12px 12px !important}
  .al-l{width:92px !important;font-size:10px !important}
  .al-e{width:48px !important;font-size:9px !important}
  .sec{padding:12px 14px 5px !important;letter-spacing:1.2px !important}
  .tw{padding:0 8px 4px !important}
  table{font-size:10px !important}
  thead th{padding:5px 2px !important;font-size:7px !important;letter-spacing:.2px !important}
  tbody td{padding:7px 3px !important}
  tfoot td{padding:7px 3px !important;font-size:10px !important}
  .tn{font-size:10.5px !important}
  .tt{font-size:8px !important}
  .ps,.cd,.tg,.chart,.ath,.note{margin-left:12px !important;margin-right:12px !important}
  .cd{padding:12px 14px !important}
  .cd-d{font-size:17px !important}
  .tg{padding:14px 16px !important}
  .tg-v{font-size:19px !important}
  .tg-p{font-size:16px !important}
  .chart{padding:12px 12px !important}
  .ch-head{flex-direction:column;align-items:flex-start !important;gap:2px !important}
  .ch-val{font-size:16px !important}
  .ch-bars{gap:6px !important}
  .ch-track{height:74px !important}
  .ch-bv{font-size:8px !important}
  .ch-bar-lbl{font-size:8px !important}
}
"""

def build_html(pf, pee_cfg, marche, now, commentary_html=None, history=None):
    is_friday = now.weekday() == 4
    chart_html = build_history_chart_html(history) if history else ""
    ath_html   = build_ath_html(history) if history else ""
    items = [(p, calc(p)) for p in pf]
    tot_inv  = sum(c["invest"] for _, c in items)
    tot_valo = sum(c["valo"]   for _, c in items if c["valo"])
    tot_pl   = sum(c["pl"]     for _, c in items if c["pl"])
    tot_jour = sum(c["jour"]   for _, c in items if c["jour"])
    tot_sem  = sum(c["semaine"] for _, c in items if c["semaine"])
    tot_mois = sum(c["mois"]    for _, c in items if c["mois"])
    tot_ytd  = sum(c["ytd_pl"]  for _, c in items if c["ytd_pl"])

    pl_pct   = tot_pl   / tot_inv * 100 if tot_inv else 0
    jour_pct = tot_jour / (tot_valo - tot_jour) * 100 if tot_valo and tot_jour else 0
    sem_pct  = tot_sem  / (tot_valo - tot_sem)  * 100 if tot_valo and tot_sem  else 0
    mois_pct = tot_mois / (tot_valo - tot_mois) * 100 if tot_valo and tot_mois else 0
    ytd_pct  = tot_ytd  / (tot_valo - tot_ytd)  * 100 if tot_valo and tot_ytd  else 0

    with_jour = [(p, c) for p, c in items if c["jour"] is not None]
    best  = max(with_jour, key=lambda x: x[1]["jour_p"]) if with_jour else None
    worst = min(with_jour, key=lambda x: x[1]["jour_p"]) if with_jour else None

    # ── Répartition dynamique par catégorie ───────────────────────────────────
    alloc = {}
    for p, c in items:
        if c["valo"]:
            cat = position_cat(p)
            alloc[cat] = alloc.get(cat, 0) + c["valo"]
    alloc_tot = sum(alloc.values()) or 1
    ALLOC_PALETTE = ["#111827", "#6b7280", "#0d9488", "#9333ea", "#ea580c", "#0284c7", "#d1d5db"]
    alloc_sorted = sorted(alloc.items(), key=lambda kv: kv[1], reverse=True)
    alloc_rows = ""
    for i, (cat, val) in enumerate(alloc_sorted):
        color = ALLOC_PALETTE[i % len(ALLOC_PALETTE)]
        pctg  = val / alloc_tot * 100
        alloc_rows += (
            f'<div class="al-r"><div class="al-l">{cat}</div>'
            f'<div class="al-b"><div class="al-f" style="width:{pctg:.1f}%;background:{color}"></div></div>'
            f'<div class="al-p" style="color:{color}">{pctg:.1f}%</div>'
            f'<div class="al-e">{eur(val)}</div></div>'
        )

    mkt_html = ""
    for nom, d in marche.items():
        if d:
            mkt_html += (f'<div class="mkt-i"><div><div class="mkt-n">{nom}</div>'
                         f'<div class="mkt-v">{fmt_index(d["val"])}</div></div>'
                         f'<div class="mkt-c {"up" if d["pct"]>0 else "dn"}">{pct(d["pct"])}</div></div>')

    rows = ""
    for p, c in items:
        rows += (f'<tr>'
                 f'<td class="L"><span class="tn">{p["nom"].split("(")[0].strip()}</span>'
                 f'<span class="tt">{p["ticker"].replace(".PA","")} · {position_type(p)}</span></td>'
                 f'<td>{p["qte"]}</td>'
                 f'<td>{eur(p["pru"])}</td>'
                 f'<td style="font-weight:600">{eur(p["prix"])}</td>'
                 f'<td style="color:{col(c["jour"])}">{pct(c["jour_p"])} / {eur(c["jour"],True)}</td>'
                 f'<td style="color:{col(c["pl"])}">{eur(c["pl"],True)} / {pct(c["pl_pct"])}</td>'
                 f'<td>{eur(c["valo"])}</td>'
                 f'</tr>')

    recap_rows = ""
    for p, c in items:
        nom_court = p["nom"].split("(")[0].strip()
        poids = c["valo"] / tot_valo * 100 if tot_valo and c["valo"] else 0
        recap_rows += (f'<tr>'
                       f'<td class="L">{nom_court}</td>'
                       f'<td>{eur(c["invest"])}</td><td>{eur(c["valo"])}</td>'
                       f'<td style="color:{col(c["pl"])}">{eur(c["pl"],True)}</td>'
                       f'<td style="color:{col(c["pl_pct"])}">{pct(c["pl_pct"])}</td>'
                       f'<td style="color:#6b7280;font-weight:600">{poids:.1f}%</td>'
                       f'</tr>')

    # ── Récap hebdomadaire (vendredi uniquement) ──────────────────────────────
    hebdo_rows = ""
    if is_friday:
        for p, c in items:
            nom_court = p["nom"].split("(")[0].strip()
            poids     = c["valo"] / tot_valo * 100 if tot_valo and c["valo"] else 0
            prix_prec = f'{p["5d"]:.2f} €' if p.get("5d") else "N/A"
            prix_now  = f'{p["prix"]:.2f} €' if p.get("prix") else "N/A"
            sem_p     = (p["prix"] - p["5d"]) / p["5d"] * 100 if p.get("prix") and p.get("5d") else None
            hebdo_rows += (
                f'<tr>'
                f'<td class="L">{nom_court}</td>'
                f'<td style="color:#9ca3af">{prix_prec}</td>'
                f'<td style="font-weight:600">{prix_now}</td>'
                f'<td style="color:{col(sem_p)};font-weight:600">{pct(sem_p) if sem_p is not None else "N/A"}</td>'
                f'<td style="color:{col(c["semaine"])}">{eur(c["semaine"],True) if c["semaine"] is not None else "N/A"}</td>'
                f'<td style="color:#6b7280;font-weight:600">{poids:.1f}%</td>'
                f'</tr>'
            )

    pee      = pee_cfg or {}
    has_pee  = pee_active(pee)
    if has_pee:
        pee_inv  = pee["parts"] * pee["pru"]
        pee_valo = pee["parts"] * pee["vl_last"]
        pee_pl   = pee_valo - pee_inv
        pee_pl_p = pee_pl / pee_inv * 100 if pee_inv else 0
        pee_j1   = pee["parts"] * (pee["vl_last"] - pee["vl_j2"]) if pee.get("vl_j2") else None
        # Date de disponibilité lue depuis la config (plus de date en dur)
        try:
            dispo = datetime.strptime(pee.get("disponibilite", ""), "%d/%m/%Y").date()
            jours_restants = (dispo - date.today()).days
        except (ValueError, TypeError):
            jours_restants = None
    else:
        pee_inv = pee_valo = pee_pl = pee_pl_p = 0
        pee_j1 = None
        jours_restants = None

    total_glob     = tot_valo + pee_valo
    total_pl_glob  = tot_pl + pee_pl
    total_inv_glob = tot_inv + pee_inv
    total_pl_pct   = total_pl_glob / total_inv_glob * 100 if total_inv_glob else 0

    date_str  = date_fr(now)
    heure_str = now.strftime("%H:%M")
    label     = "Clôture" if now.hour >= 17 else "Ouverture"

    best_nom  = best[0]["nom"].split("(")[0].strip() if best else "-"
    best_pct  = pct(best[1]["jour_p"]) if best else "-"
    best_eur  = eur(best[1]["jour"], True) if best else ""
    best_prix = eur(best[0]["prix"]) if best else ""
    worst_nom = worst[0]["nom"].split("(")[0].strip() if worst else "-"
    worst_pct = pct(worst[1]["jour_p"]) if worst else "-"
    worst_eur = eur(worst[1]["jour"], True) if worst else ""
    worst_prix= eur(worst[0]["prix"]) if worst else ""

    # ── Titre & total : adaptés selon présence du PEE ─────────────────────────
    titre    = "Rapport P&amp;L · PEA" + (" + PEE" if has_pee else "")
    tg_label = "Total PEA + PEE" if has_pee else "Patrimoine PEA"

    # ── Bloc PEE complet (rendu uniquement si un PEE est configuré) ────────────
    if has_pee:
        if jours_restants is not None:
            dispo_card = f'''<div class="cd">
  <div class="cd-l">Disponibilité du PEE
    <span>Fonds bloqués jusqu'au {pee.get("disponibilite","")}</span></div>
  <div class="cd-r">
    <div class="cd-d" style="color:#0f766e">{jours_restants} jours</div>
    <div class="cd-s">environ {jours_restants//365} ans restants</div>
  </div>
</div>'''
        else:
            dispo_card = ""
        pee_section = f'''<div class="ps"><div class="pl"></div><div class="pt">Plan d'Épargne Entreprise</div><div class="pl"></div></div>
<div class="kpi-row" style="background:#fafffe">
  <div class="kpi"><div class="kpi-l">Valorisation PEE</div>
    <div class="kpi-v" style="color:#0f766e">{eur(pee_valo)}</div>
    <div class="kpi-s mu">investi {eur(pee_inv)}</div></div>
  <div class="kpi"><div class="kpi-l">Var. J-1 (dernière VL)</div>
    <div class="kpi-v" style="color:{col(pee_j1)}">{eur(pee_j1,True) if pee_j1 else "N/A"}</div>
    <div class="kpi-s mu">VL {pee["vl_last"]:.2f} € au {pee["vl_date"]}</div></div>
  <div class="kpi"><div class="kpi-l">P&amp;L total PEE</div>
    <div class="kpi-v" style="color:{col(pee_pl)}">{eur(pee_pl,True)}</div>
    <div class="kpi-s" style="color:{col(pee_pl_p)}">{pct(pee_pl_p)}</div></div>
</div>
<div class="sec">Positions PEE</div>
<div class="tw">
  <table>
    <colgroup><col style="width:38%"><col style="width:12%"><col style="width:13%">
      <col style="width:13%"><col style="width:12%"><col style="width:12%"></colgroup>
    <thead><tr><th class="L">Fonds</th><th>Parts</th><th>PRU</th><th>VL</th>
      <th>Latent €</th><th>Latent %</th></tr></thead>
    <tbody><tr>
      <td class="L"><span class="tn">{pee.get("nom","Fonds PEE")}</span>
        <span class="tt">FCPE · Dispo {pee.get("disponibilite","")}</span></td>
      <td>{pee["parts"]}</td><td>{eur(pee["pru"])}</td>
      <td>{pee["vl_last"]:.2f} €<br><span style="font-size:9px;color:#d1d5db">au {pee["vl_date"]}</span></td>
      <td style="color:{col(pee_pl)}">{eur(pee_pl,True)}</td>
      <td style="color:{col(pee_pl_p)}">{pct(pee_pl_p)}</td>
    </tr></tbody>
    <tfoot><tr><td class="L" colspan="4" style="color:#0f766e">Total PEE</td>
      <td style="color:{col(pee_pl)}">{eur(pee_pl,True)}</td>
      <td style="color:{col(pee_pl_p)}">{pct(pee_pl_p)}</td></tr></tfoot>
  </table>
</div>
{dispo_card}'''
    else:
        pee_section = ""

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>:root{{color-scheme:light only}}{CSS}</style></head>
<body><div class="w">
<div class="hdr">
  <div class="hdr-date">{date_str} · {label} {heure_str}</div>
  <div class="hdr-title">{titre}</div>
</div>
<div class="kpi-row">
  <div class="kpi"><div class="kpi-l">Valorisation PEA</div>
    <div class="kpi-v">{eur(tot_valo)}</div>
    <div class="kpi-s mu">investi {eur(tot_inv)}</div></div>
  <div class="kpi"><div class="kpi-l">P&amp;L du jour</div>
    <div class="kpi-v" style="color:{col(tot_jour)}">{eur(tot_jour,True)}</div>
    <div class="kpi-s" style="color:{col(tot_jour)}">{pct(jour_pct)}</div></div>
  <div class="kpi"><div class="kpi-l">P&amp;L total PEA</div>
    <div class="kpi-v" style="color:{col(tot_pl)}">{eur(tot_pl,True)}</div>
    <div class="kpi-s" style="color:{col(pl_pct)}">{pct(pl_pct)}</div></div>
</div>
<div class="mkt">{mkt_html}</div>
<div class="bw">
  <div class="bw-c">
    <div class="bw-t up">Best du jour</div>
    <div class="bw-n">{best_nom}</div>
    <div class="bw-p up">{best_pct}</div>
    <div class="bw-d">{best_eur} · cours {best_prix}</div>
  </div>
  <div class="bw-c">
    <div class="bw-t dn">Worst du jour</div>
    <div class="bw-n">{worst_nom}</div>
    <div class="bw-p dn">{worst_pct}</div>
    <div class="bw-d">{worst_eur} · cours {worst_prix}</div>
  </div>
</div>
<div class="sec">Performance sur les périodes</div>
<div class="per">
  <div class="per-c"><div class="per-l">Semaine</div>
    <div class="per-v" style="color:{col(sem_pct)}">{pct(sem_pct)}</div>
    <div class="per-s" style="color:{col(tot_sem)}">{eur(tot_sem,True)}</div></div>
  <div class="per-c"><div class="per-l">Mois</div>
    <div class="per-v" style="color:{col(mois_pct)}">{pct(mois_pct)}</div>
    <div class="per-s" style="color:{col(tot_mois)}">{eur(tot_mois,True)}</div></div>
  <div class="per-c"><div class="per-l">YTD {now.year}</div>
    <div class="per-v" style="color:{col(ytd_pct)}">{pct(ytd_pct)}</div>
    <div class="per-s" style="color:{col(tot_ytd)}">{eur(tot_ytd,True)}</div></div>
  <div class="per-c"><div class="per-l">Depuis achat</div>
    <div class="per-v" style="color:{col(pl_pct)}">{pct(pl_pct)}</div>
    <div class="per-s" style="color:{col(tot_pl)}">{eur(tot_pl,True)}</div></div>
</div>
<div class="sec">Répartition du portefeuille PEA</div>
<div class="alloc">{alloc_rows}</div>
<div class="sec">Positions PEA</div>
<div class="tw">
  <table>
    <colgroup><col style="width:28%"><col style="width:6%"><col style="width:10%">
      <col style="width:10%"><col style="width:18%"><col style="width:17%"><col style="width:11%"></colgroup>
    <thead><tr><th class="L">Valeur</th><th>Qté</th><th>PRU</th><th>Cours</th>
      <th>Var. jour</th><th>+/− latent</th><th>Valo</th></tr></thead>
    <tbody>{rows}</tbody>
    <tfoot><tr><td class="L" colspan="4">Total PEA</td>
      <td style="color:{col(tot_jour)}">{eur(tot_jour,True)}</td>
      <td style="color:{col(tot_pl)}">{eur(tot_pl,True)} / {pct(pl_pct)}</td>
      <td>{eur(tot_valo)}</td></tr></tfoot>
  </table>
</div>
<div class="sec" style="padding-top:14px">Performance depuis ouverture de position</div>
<div class="tw">
  <table>
    <colgroup><col style="width:28%"><col style="width:14%"><col style="width:14%">
      <col style="width:14%"><col style="width:16%"><col style="width:14%"></colgroup>
    <thead><tr><th class="L">Valeur</th><th>Investi</th><th>Valo</th>
      <th>Gain €</th><th>Gain %</th><th>Poids</th></tr></thead>
    <tbody>{recap_rows}</tbody>
  </table>
</div>
{pee_section}
<div class="tg">
  <div><div class="tg-l">{tg_label}</div><div class="tg-v">{eur(total_glob)}</div></div>
  <div class="tg-r"><div class="tg-l">P&amp;L global depuis ouverture</div>
    <div class="tg-p">{eur(total_pl_glob,True)}</div>
    <div class="tg-s">{pct(total_pl_pct)}</div></div>
</div>
{ath_html}
{chart_html}
{f'''<div class="sec" style="padding-top:18px">Récap de la semaine</div>
<div class="tw">
  <table>
    <colgroup><col style="width:26%"><col style="width:13%"><col style="width:13%">
      <col style="width:13%"><col style="width:18%"><col style="width:17%"></colgroup>
    <thead><tr>
      <th class="L">Valeur</th>
      <th>Ven. passé</th>
      <th>Aujourd'hui</th>
      <th>Sem. %</th>
      <th>P&L semaine</th>
      <th>Poids</th>
    </tr></thead>
    <tbody>{hebdo_rows}</tbody>
    <tfoot><tr>
      <td class="L" colspan="3">Total PEA - semaine</td>
      <td style="color:{col(sem_pct)};font-weight:700">{pct(sem_pct)}</td>
      <td style="color:{col(tot_sem)}">{eur(tot_sem,True)}</td>
      <td></td>
    </tr></tfoot>
  </table>
</div>''' if is_friday else ''}
{f'''<div class="note">
  <div class="note-hdr">
    <div class="note-hdr-icon">📋</div>
    <div class="note-hdr-left">
      <div class="note-hdr-title">Note de marché</div>
      <div class="note-hdr-sub">{date_str} · {heure_str}</div>
    </div>
    <div class="note-hdr-badge">Analyse IA</div>
  </div>
  <div class="note-body">{commentary_html}</div>
  <div class="note-sig">Analyse générée par Claude · Yahoo Finance · {heure_str}</div>
</div>''' if commentary_html else ''}
<div class="ftr">Yahoo Finance · GitHub Actions · Ne pas répondre</div>
</div></body></html>"""


def generate_pdf(html_body):
    try:
        from weasyprint import HTML, CSS
        import logging
        logging.getLogger("weasyprint").setLevel(logging.ERROR)

        html_pdf = html_body.replace(
            '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">',
            ""
        ).replace(
            "font-family:'Inter',-apple-system,sans-serif",
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif"
        )

        pdf_css = CSS(string="""
            @page {
                size: A4;
                margin: 1.2cm 1.8cm;
            }
            body {
                padding: 0 !important;
                background: #fff !important;
            }
            .w {
                max-width: 100% !important;
                border-radius: 0 !important;
                border: none !important;
            }

            /* ── Anti-coupure entre pages ── */
            tr           { break-inside: avoid; page-break-inside: avoid; }
            thead        { display: table-header-group; }
            tfoot        { display: table-footer-group; }
            .bw, .bw-c  { break-inside: avoid; page-break-inside: avoid; }
            .kpi-row     { break-inside: avoid; page-break-inside: avoid; }
            .per, .per-c { break-inside: avoid; page-break-inside: avoid; }
            .alloc, .al-r{ break-inside: avoid; page-break-inside: avoid; }
            .cd, .tg     { break-inside: avoid; page-break-inside: avoid; }
            .ps          { break-inside: avoid; page-break-inside: avoid; break-after: avoid; page-break-after: avoid; }

            .hdr { padding: 28px 36px 22px !important; }
            .hdr-date { font-size: 13px !important; margin-bottom: 7px !important; }
            .hdr-title { font-size: 26px !important; }
            .kpi { padding: 22px 26px !important; }
            .kpi-l { font-size: 11px !important; margin-bottom: 6px !important; }
            .kpi-v { font-size: 24px !important; }
            .kpi-s { font-size: 13px !important; margin-top: 4px !important; }
            .mkt { padding: 13px 26px !important; }
            .mkt-n { font-size: 11px !important; }
            .mkt-v { font-size: 13px !important; }
            .mkt-c { font-size: 12px !important; }
            .alert-ok, .alert-w { padding: 12px 26px !important; font-size: 13px !important; }
            .bw { padding: 18px 26px !important; gap: 14px !important; }
            .bw-c { padding: 16px 20px !important; }
            .bw-t { font-size: 10px !important; margin-bottom: 8px !important; }
            .bw-n { font-size: 15px !important; }
            .bw-p { font-size: 22px !important; margin-top: 4px !important; }
            .bw-d { font-size: 12px !important; margin-top: 4px !important; }
            .sec { padding: 20px 26px 8px !important; font-size: 10px !important; letter-spacing: 2.5px !important; }
            .per { padding: 0 26px 18px !important; gap: 12px !important; }
            .per-c { padding: 16px 14px !important; }
            .per-l { font-size: 10px !important; margin-bottom: 6px !important; }
            .per-v { font-size: 17px !important; }
            .per-s { font-size: 12px !important; margin-top: 3px !important; }
            .alloc { padding: 0 26px 20px !important; }
            .al-r { margin-bottom: 12px !important; }
            .al-l { font-size: 13px !important; width: 150px !important; }
            .al-b { height: 7px !important; }
            .al-p { font-size: 13px !important; }
            .al-e { font-size: 12px !important; }
            .tw { padding: 0 26px 8px !important; }
            table { font-size: 13px !important; }
            thead th { padding: 9px 6px !important; font-size: 10px !important; }
            tbody td { padding: 12px 6px !important; }
            tfoot td { padding: 12px 6px !important; font-size: 13px !important; }
            .tn { font-size: 14px !important; }
            .tt { font-size: 11px !important; margin-top: 2px !important; }
            .ps { padding: 20px 26px !important; }
            .cd { margin: 0 26px 20px !important; padding: 18px 22px !important; }
            .cd-l { font-size: 13px !important; }
            .cd-d { font-size: 26px !important; }
            .cd-s { font-size: 11px !important; }
            .tg { margin: 18px 26px 22px !important; padding: 22px 26px !important; }
            .tg-l { font-size: 10px !important; margin-bottom: 6px !important; }
            .tg-v { font-size: 28px !important; }
            .tg-p { font-size: 24px !important; }
            .tg-s { font-size: 12px !important; }
            .ftr { padding: 16px !important; font-size: 11px !important; }
        """)

        pdf = HTML(string=html_pdf).write_pdf(stylesheets=[pdf_css])
        print(f"[PDF] Généré - {len(pdf):,} octets", flush=True)
        return pdf
    except Exception as e:
        print(f"[PDF] Génération échouée : {e}", flush=True)
        import traceback; traceback.print_exc()
        return None


def build_text(pf, pee_cfg, marche, now, commentary_html=None):
    """Version texte brut du rapport (fallback pour clients sans HTML, #7)."""
    items = [(p, calc(p)) for p in pf]
    tot_inv  = sum(c["invest"] for _, c in items)
    tot_valo = sum(c["valo"]   for _, c in items if c["valo"])
    tot_pl   = sum(c["pl"]     for _, c in items if c["pl"])
    tot_jour = sum(c["jour"]   for _, c in items if c["jour"])
    pl_pct   = tot_pl / tot_inv * 100 if tot_inv else 0

    pee      = pee_cfg or {}
    has_pee  = pee_active(pee)
    if has_pee:
        pee_inv  = pee["parts"] * pee["pru"]
        pee_valo = pee["parts"] * pee["vl_last"]
        pee_pl   = pee_valo - pee_inv
        pee_pl_p = pee_pl / pee_inv * 100 if pee_inv else 0
    else:
        pee_inv = pee_valo = pee_pl = pee_pl_p = 0

    total_glob    = tot_valo + pee_valo
    total_pl_glob = tot_pl + pee_pl
    total_inv     = tot_inv + pee_inv
    total_pl_pct  = total_pl_glob / total_inv * 100 if total_inv else 0

    def e(v, sign=False):
        if v is None: return "N/A"
        s = "+" if sign and v > 0 else ""
        return f"{s}{v:,.0f} EUR".replace(",", " ")
    def p_(v):
        if v is None: return "N/A"
        return f"{'+' if v > 0 else ''}{v:.2f}%"

    L = []
    L.append("RAPPORT P&L - PEA + PEE" if has_pee else "RAPPORT P&L - PEA")
    L.append(f"{date_fr(now)} - {now.strftime('%H:%M')}")
    L.append("=" * 48)
    L.append("")
    L.append("PEA")
    L.append(f"  Valorisation : {e(tot_valo)}  (investi {e(tot_inv)})")
    L.append(f"  P&L du jour  : {e(tot_jour, True)}")
    L.append(f"  P&L total    : {e(tot_pl, True)}  ({p_(pl_pct)})")
    L.append("")
    L.append("  Positions :")
    for p, c in items:
        nom = p["nom"].split("(")[0].strip()
        L.append(f"   - {nom:<28} {e(c['valo']):>12}  "
                 f"jour {p_(c['jour_p'])}  latent {e(c['pl'], True)} ({p_(c['pl_pct'])})")
    L.append("")
    if has_pee:
        L.append("PEE")
        L.append(f"  {pee.get('nom', 'Fonds PEE')}")
        L.append(f"  Valorisation : {e(pee_valo)}  (investi {e(pee_inv)})")
        L.append(f"  P&L total    : {e(pee_pl, True)}  ({p_(pee_pl_p)})")
        L.append(f"  VL {pee['vl_last']:.2f} EUR au {pee['vl_date']}")
        L.append("")
    L.append("-" * 48)
    L.append((f"TOTAL PEA + PEE : {e(total_glob)}") if has_pee else (f"TOTAL PEA : {e(total_glob)}"))
    L.append(f"P&L global      : {e(total_pl_glob, True)}  ({p_(total_pl_pct)})")
    L.append("")
    L.append("Marchés :")
    for nom, d in marche.items():
        if d:
            L.append(f"   {nom:<10} {fmt_index(d['val']).replace('&nbsp;', ' '):>10}  {p_(d['pct'])}")
    if commentary_html:
        import re
        texte = re.sub(r'<[^>]+>', '', commentary_html)
        texte = texte.replace("&nbsp;", " ").replace("&amp;", "&")
        L.append("")
        L.append("NOTE DE MARCHÉ")
        L.append("-" * 48)
        L.append(texte)
    L.append("")
    L.append("Yahoo Finance - GitHub Actions - Ne pas répondre")
    return "\n".join(L)


def send_email(cfg, subject, html_body, pdf_bytes=None, text_body=None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = cfg["expediteur"]
    msg["To"]      = cfg["destinataire"]

    alt = MIMEMultipart("alternative")
    # Dans un conteneur "alternative", le client choisit la DERNIÈRE partie
    # qu'il sait afficher : on met donc le texte d'abord, le HTML ensuite.
    if text_body:
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    if pdf_bytes:
        pdf_part = MIMEBase("application", "pdf")
        pdf_part.set_payload(pdf_bytes)
        encoders.encode_base64(pdf_part)
        safe_name = subject.replace(" ", "_").replace("-", "-").replace("/", "-")
        pdf_part.add_header("Content-Disposition", "attachment",
                            filename=f"{safe_name}.pdf")
        msg.attach(pdf_part)

    with smtplib.SMTP(cfg["smtp_serveur"], cfg["smtp_port"]) as s:
        s.ehlo(); s.starttls()
        s.login(cfg["expediteur"], cfg["mot_de_passe"])
        s.sendmail(cfg["expediteur"], cfg["destinataire"], msg.as_string())


def send_failure_email(cfg, error, tb):
    """Notification en cas de plantage de la routine (#3)."""
    try:
        now = datetime.now(PARIS)
        subject = f"⚠️ Échec routine PEA - {now.strftime('%d/%m/%Y %H:%M')}"
        body = (
            f"La routine PEA a échoué.\n\n"
            f"Date  : {date_fr(now)} {now.strftime('%H:%M')}\n"
            f"Erreur : {type(error).__name__}: {error}\n\n"
            f"Traceback\n{'-'*48}\n{tb}\n"
        )
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"]    = cfg["expediteur"]
        msg["To"]      = cfg["destinataire"]
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(alt)
        with smtplib.SMTP(cfg["smtp_serveur"], cfg["smtp_port"]) as s:
            s.ehlo(); s.starttls()
            s.login(cfg["expediteur"], cfg["mot_de_passe"])
            s.sendmail(cfg["expediteur"], cfg["destinataire"], msg.as_string())
        print(f"[Failure] Notification d'échec envoyée à {cfg['destinataire']}", flush=True)
    except Exception as e:
        print(f"[Failure] Impossible d'envoyer la notification d'échec : {e}", flush=True)


def run():
    pf   = fetch_pea(PORTFOLIO)
    mkt  = fetch_marche()
    now  = datetime.now(PARIS)

    # ── Enregistrement de l'historique (#1) ─────────────────────────────────
    items    = [(p, calc(p)) for p in pf]
    tot_inv  = sum(c["invest"] for _, c in items)
    tot_valo = sum(c["valo"]   for _, c in items if c["valo"])
    tot_pl   = sum(c["pl"]     for _, c in items if c["pl"])
    if pee_active(PEE):
        pee_inv  = PEE["parts"] * PEE["pru"]
        pee_valo = PEE["parts"] * PEE["vl_last"]
        pee_pl   = pee_valo - pee_inv
    else:
        pee_inv = pee_valo = pee_pl = 0
    total_valo = tot_valo + pee_valo
    total_pl   = tot_pl + pee_pl
    total_inv  = tot_inv + pee_inv
    total_pl_pct = total_pl / total_inv * 100 if total_inv else 0
    snapshot = {
        "date":         now.strftime("%Y-%m-%d"),
        "pea_valo":     round(tot_valo, 2),
        "pea_pl":       round(tot_pl, 2),
        "pee_valo":     round(pee_valo, 2),
        "pee_pl":       round(pee_pl, 2),
        "total_valo":   round(total_valo, 2),
        "total_pl":     round(total_pl, 2),
        "total_pl_pct": round(total_pl_pct, 2),
    }
    history = append_history(snapshot)

    # Note de marché : uniquement le vendredi
    commentary = None
    if now.weekday() == 4 and "ANTHROPIC_API_KEY" in os.environ:
        print("[Commentary] Vendredi détecté - génération de la note de marché…", flush=True)
        commentary = generate_commentary(pf, mkt, now)

    html  = build_html(pf, PEE, mkt, now, commentary_html=commentary, history=history)
    text  = build_text(pf, PEE, mkt, now, commentary_html=commentary)
    heure = "Ouverture" if now.hour < 12 else "Clôture"
    subj  = f"PEA P&L - {now.strftime('%d/%m/%Y')} {heure}"
    if now.weekday() == 4 and now.hour >= 17:
        subj = f"PEA P&L - Hebdo {now.strftime('%d/%m/%Y')}"
    pdf   = generate_pdf(html)
    send_email(EMAIL, subj, html, pdf_bytes=pdf, text_body=text)
    statut_pdf  = "✓ PDF joint"       if pdf        else "✗ PDF non généré"
    statut_note = "✓ Note de marché"  if commentary else "- (pas de note)"
    print(f"Mail envoyé à {EMAIL['destinataire']} | {statut_pdf} | {statut_note}")


def main():
    try:
        run()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[Fatal] La routine a échoué : {e}\n{tb}", flush=True)
        send_failure_email(EMAIL, e, tb)
        raise


if __name__ == "__main__":
    main()
