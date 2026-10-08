"""Relevé des caractéristiques des marchés IG visés (compte démo) : prix, écart achat/vente,
taille minimale, distance minimale du stop, heures d'ouverture, quota de données historiques.

Écrit etat/infos_ig.json (jamais les identifiants). Sert à régler les coûts du backtest.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bots.ig import IG  # noqa: E402

EPICS = ["IX.D.NASDAQ.IFD.IP", "IX.D.NASDAQ.IEN.IP", "IX.D.NASDAQ.IFM.IP", "CS.D.CFEGOLD.CEF.IP",
         "CS.D.BITCOIN.CEF.IP", "CS.D.BITCOIN.CFE.IP", "CS.D.EURUSD.CFD.IP", "CS.D.EURUSD.MINI.IP",
         "CS.D.EURUSD.CSD.IP"]


def main():
    res = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ok": False}
    ig = IG()
    ig.connecter()
    res["compte"] = "…" + ig.compte[-4:]
    rech = {}
    for terme in ["EURUSD", "EUR USD", "Euro"]:
        try:
            m = ig.get(f"/markets?searchTerm={terme.replace(' ', '%20')}")
            rech[terme] = [{"epic": x.get("epic"), "nom": x.get("instrumentName"), "type": x.get("instrumentType"),
                            "statut": x.get("marketStatus"), "bid": x.get("bid"), "offer": x.get("offer")}
                           for x in m.get("markets", [])[:8]]
        except Exception as e:
            rech[terme] = [{"erreur": str(e)[:150]}]
    res["recherche_eurusd"] = rech
    infos = {}
    for epic in EPICS:
        try:
            d = ig.get(f"/markets/{epic}", version="3")
            ins, snap, regles = d.get("instrument", {}), d.get("snapshot", {}), d.get("dealingRules", {})
            bid, offer = snap.get("bid"), snap.get("offer")
            infos[epic] = {
                "nom": ins.get("name"), "type": ins.get("type"), "statut": snap.get("marketStatus"),
                "bid": bid, "offer": offer, "ecart": round(offer - bid, 5) if bid and offer else None,
                "facteur_echelle": snap.get("scalingFactor"), "valeur_point": ins.get("valueOfOnePip"),
                "unite_point": ins.get("onePipMeans"), "taille_contrat": ins.get("contractSize"),
                "devises": [c.get("code") for c in ins.get("currencies", [])],
                "taille_min": (regles.get("minDealSize") or {}),
                "stop_min": (regles.get("minNormalStopOrLimitDistance") or {}),
                "stop_garanti_min": (regles.get("minControlledRiskStopDistance") or {}),
                "pas_taille": (regles.get("minStepDistance") or {}),
                "marge": ins.get("marginFactor"), "marge_unite": ins.get("marginFactorUnit"),
                "heures": ins.get("openingHours"),
                "decimales": snap.get("decimalPlacesFactor"),
            }
        except Exception as e:
            infos[epic] = {"erreur": str(e)[:200]}
    res["marches"] = infos
    try:
        p = ig.get("/prices/CS.D.CFEGOLD.CEF.IP?resolution=MINUTE&max=3&pageSize=3", version="3")
        res["quota_historique"] = (p.get("metadata") or {}).get("allowance")
        res["exemple_prix"] = p.get("prices", [])[:1]
    except Exception as e:
        res["quota_historique"] = {"erreur": str(e)[:200]}
    try:
        res["positions_ouvertes"] = len(ig.get("/positions", version="2").get("positions", []))
        res["ordres_en_attente"] = len(ig.get("/workingorders", version="2").get("workingOrders", []))
    except Exception as e:
        res["positions_erreur"] = str(e)[:200]
    res["ok"] = True
    return res


if __name__ == "__main__":
    try:
        r = main()
    except Exception as e:
        r = {"ok": False, "erreur": str(e)[:300]}
    print(json.dumps(r, ensure_ascii=False, indent=1))
    os.makedirs("etat", exist_ok=True)
    with open("etat/infos_ig.json", "w") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
