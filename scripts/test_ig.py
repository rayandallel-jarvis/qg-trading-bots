"""Test de connexion : compte démo IG (API REST).

Se connecte avec les secrets IG_*, lit le solde fictif et cherche les marchés
visés (Nasdaq 100, or, EUR/USD, Bitcoin) pour noter leurs identifiants « epic ».
Écrit le résultat dans etat/test_ig.json (jamais les identifiants) et prévient sur ntfy.
"""
import os, json, urllib.request, urllib.parse, urllib.error, datetime

KEY, USER, PWD = [(os.environ.get(n) or "").strip() or None for n in ("IG_API_KEY", "IG_USERNAME", "IG_PASSWORD")]
ACC_TYPE = (os.environ.get("IG_ACC_TYPE") or "DEMO").strip().upper()
TOPIC = os.environ.get("NTFY_TOPIC")
BASE = "https://demo-api.ig.com/gateway/deal" if ACC_TYPE != "LIVE" else None

res = {"ts": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z", "ok": False}


def call(method, path, headers, body=None, version="1"):
    h = {"X-IG-API-KEY": KEY, "Accept": "application/json; charset=UTF-8",
         "Content-Type": "application/json; charset=UTF-8", "Version": version}
    h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), r.headers


def notify(msg):
    if not TOPIC:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=msg.encode(), method="POST",
                                     headers={"Title": "QG Trading · IG", "Tags": "robot"})
        urllib.request.urlopen(req, timeout=20)
    except Exception:
        pass


def main():
    manquants = [n for n, v in [("IG_API_KEY", KEY), ("IG_USERNAME", USER), ("IG_PASSWORD", PWD)] if not v]
    if manquants:
        res["message"] = "Secrets manquants : " + ", ".join(manquants)
        return
    if BASE is None:
        res["message"] = "IG_ACC_TYPE vaut LIVE : ce test ne se connecte qu'au compte démo."
        return
    try:
        sess, hdr = call("POST", "/session", {}, {"identifier": USER, "password": PWD}, version="2")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        res["message"] = f"Connexion IG refusée (HTTP {e.code}) : {detail}"
        return
    auth = {"CST": hdr.get("CST", ""), "X-SECURITY-TOKEN": hdr.get("X-SECURITY-TOKEN", "")}
    acc_id = str(sess.get("currentAccountId") or "")
    res["compte_actif"] = ("…" + acc_id[-4:]) if acc_id else None
    res["devise"] = sess.get("currencyIsoCode")
    try:
        accs, _ = call("GET", "/accounts", auth)
        res["comptes"] = [{"type": a.get("accountType"), "nom": a.get("accountName"),
                           "solde": (a.get("balance") or {}).get("balance"),
                           "disponible": (a.get("balance") or {}).get("available"),
                           "devise": a.get("currency"), "prefere": a.get("preferred")}
                          for a in accs.get("accounts", [])]
    except Exception as e:
        res["comptes_erreur"] = str(e)[:200]
    marches = {}
    for terme in ["US Tech 100", "Gold", "EUR/USD", "Bitcoin"]:
        try:
            m, _ = call("GET", "/markets?searchTerm=" + urllib.parse.quote(terme), auth)
            marches[terme] = [{"epic": x.get("epic"), "nom": x.get("instrumentName"), "type": x.get("instrumentType"),
                               "expiry": x.get("expiry"), "statut": x.get("marketStatus")}
                              for x in m.get("markets", [])[:6]]
        except Exception as e:
            marches[terme] = [{"erreur": str(e)[:120]}]
    res["marches"] = marches
    res["ok"] = True
    c = next((a for a in res.get("comptes", []) if a.get("prefere")), (res.get("comptes") or [{}])[0])
    res["message"] = f"Connexion IG démo OK. Solde fictif : {c.get('solde')} {c.get('devise') or ''}".strip()


main()
print(json.dumps(res, ensure_ascii=False, indent=1))
os.makedirs("etat", exist_ok=True)
with open("etat/test_ig.json", "w") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
notify(res.get("message", "Test IG terminé"))
