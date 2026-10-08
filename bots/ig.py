"""Client REST minimal pour le compte démo IG (CFD).

- Connexion par les secrets IG_API_KEY, IG_USERNAME, IG_PASSWORD (IG_ACC_TYPE doit valoir DEMO).
- Les positions et ordres des bots portent une référence qui commence par « QGB » : c'est ce qui
  les sépare des positions que Rayan prend à la main sur le même compte (jamais touchées).
- Jamais de compte réel : le client refuse de se connecter si IG_ACC_TYPE vaut LIVE.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE_DEMO = "https://demo-api.ig.com/gateway/deal"
PREFIXE_REF = "QGB"


class ErreurIG(Exception):
    pass


class IG:
    def __init__(self):
        self.cle = (os.environ.get("IG_API_KEY") or "").strip()
        self.user = (os.environ.get("IG_USERNAME") or "").strip()
        self.mdp = (os.environ.get("IG_PASSWORD") or "").strip()
        if (os.environ.get("IG_ACC_TYPE") or "DEMO").strip().upper() != "DEMO":
            raise ErreurIG("IG_ACC_TYPE doit valoir DEMO : les bots ne tradent jamais sur le compte réel.")
        if not (self.cle and self.user and self.mdp):
            raise ErreurIG("Secrets IG manquants")
        self.base = BASE_DEMO
        self.jetons = {}
        self.compte = ""
        self.devise = "EUR"
        self.dernier_appel = 0.0

    # ------------------------------------------------------------------ bas niveau
    def _requete(self, methode, chemin, corps=None, version="1", entetes=None, reessai=True):
        # IG limite les requêtes (environ 60 par minute hors ordres) : on espace un peu les appels
        attente = 0.25 - (time.time() - self.dernier_appel)
        if attente > 0:
            time.sleep(attente)
        h = {"X-IG-API-KEY": self.cle, "Accept": "application/json; charset=UTF-8",
             "Content-Type": "application/json; charset=UTF-8", "Version": version}
        h.update(self.jetons)
        h.update(entetes or {})
        data = json.dumps(corps).encode() if corps is not None else None
        req = urllib.request.Request(self.base + chemin, data=data, headers=h, method=methode)
        self.dernier_appel = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode() or "{}"
                return json.loads(txt), r.headers
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 401 and reessai and chemin != "/session":
                self.connecter()
                return self._requete(methode, chemin, corps, version, entetes, reessai=False)
            raise ErreurIG(f"HTTP {e.code} sur {methode} {chemin.split('?')[0]} : {detail}") from None

    def get(self, chemin, version="1"):
        return self._requete("GET", chemin, version=version)[0]

    def post(self, chemin, corps, version="1"):
        return self._requete("POST", chemin, corps, version=version)[0]

    def supprimer(self, chemin, corps=None, version="1"):
        # IG attend un POST avec l'en-tête _method: DELETE quand il y a un corps
        if corps is None:
            return self._requete("DELETE", chemin, version=version)[0]
        return self._requete("POST", chemin, corps, version=version, entetes={"_method": "DELETE"})[0]

    # ------------------------------------------------------------------ session et compte
    def connecter(self):
        self.jetons = {}
        corps, h = self._requete("POST", "/session", {"identifier": self.user, "password": self.mdp},
                                 version="2", reessai=False)
        self.jetons = {"CST": h.get("CST", ""), "X-SECURITY-TOKEN": h.get("X-SECURITY-TOKEN", "")}
        self.compte = str(corps.get("currentAccountId") or "")
        self.devise = corps.get("currencyIsoCode") or "EUR"
        return corps

    def solde(self):
        """Valeur du compte CFD (solde + résultat latent), en devise du compte."""
        for a in self.get("/accounts").get("accounts", []):
            if str(a.get("accountId")) == self.compte:
                b = a.get("balance") or {}
                return float(b.get("balance", 0)) + float(b.get("profitLoss", 0))
        raise ErreurIG("Compte actif introuvable")

    # ------------------------------------------------------------------ marchés
    def marche(self, epic):
        d = self.get(f"/markets/{epic}", version="3")
        s, ins, r = d.get("snapshot", {}), d.get("instrument", {}), d.get("dealingRules", {})
        return {"bid": s.get("bid"), "offer": s.get("offer"), "statut": s.get("marketStatus"),
                "valeur_point": float(ins.get("valueOfOnePip") or 1), "devise": (ins.get("currencies") or [{}])[0].get("code"),
                "taille_min": float((r.get("minDealSize") or {}).get("value") or 0),
                "stop_min": float((r.get("minNormalStopOrLimitDistance") or {}).get("value") or 0),
                "maj": s.get("updateTime")}

    def marches(self, epics):
        """Instantané de plusieurs marchés en une requête."""
        d = self.get("/markets?epics=" + ",".join(epics), version="2")
        out = {}
        for x in d.get("marketDetails", []):
            s = x.get("snapshot", {})
            out[x["instrument"]["epic"]] = {"bid": s.get("bid"), "offer": s.get("offer"),
                                            "statut": s.get("marketStatus"), "maj": s.get("updateTime")}
        return out

    def prix_historiques(self, epic, debut, fin, resolution="MINUTE"):
        """Bougies IG (consomme le quota hebdomadaire : à utiliser seulement pour combler un trou court)."""
        q = f"/prices/{epic}?resolution={resolution}&from={debut:%Y-%m-%dT%H:%M:%S}&to={fin:%Y-%m-%dT%H:%M:%S}&pageSize=0"
        return self.get(q, version="3").get("prices", [])

    # ------------------------------------------------------------------ positions et ordres
    def positions_bots(self):
        pos = self.get("/positions", version="2").get("positions", [])
        return [p for p in pos if (p.get("position", {}).get("dealReference") or "").startswith(PREFIXE_REF)]

    def ordres_bots(self):
        o = self.get("/workingorders", version="2").get("workingOrders", [])
        return [x for x in o if (x.get("workingOrderData", {}).get("dealReference") or "").startswith(PREFIXE_REF)]

    def confirmation(self, ref, essais=8):
        for _ in range(essais):
            try:
                return self.get(f"/confirms/{ref}")
            except ErreurIG as e:
                if "404" not in str(e):
                    raise
            time.sleep(1)
        return None

    def ouvrir_marche(self, epic, sens, taille, stop, limite, ref, devise):
        corps = {"epic": epic, "expiry": "-", "direction": "BUY" if sens == 1 else "SELL", "size": taille,
                 "orderType": "MARKET", "timeInForce": "FILL_OR_KILL", "guaranteedStop": False,
                 "forceOpen": True, "stopLevel": stop, "limitLevel": limite, "currencyCode": devise,
                 "dealReference": ref}
        self.post("/positions/otc", corps, version="2")
        return self.confirmation(ref)

    def ordre_limite(self, epic, sens, taille, prix, stop, limite, ref, devise, expire_le):
        corps = {"epic": epic, "expiry": "-", "direction": "BUY" if sens == 1 else "SELL", "size": taille,
                 "level": prix, "type": "LIMIT", "timeInForce": "GOOD_TILL_DATE",
                 "goodTillDate": expire_le.strftime("%Y/%m/%d %H:%M:%S"), "guaranteedStop": False,
                 "forceOpen": True, "stopLevel": stop, "limitLevel": limite, "currencyCode": devise,
                 "dealReference": ref}
        self.post("/workingorders/otc", corps, version="2")
        return self.confirmation(ref)

    def annuler_ordre(self, deal_id):
        return self.supprimer(f"/workingorders/otc/{deal_id}", version="2")

    def modifier_stop(self, deal_id, stop, limite):
        return self._requete("PUT", f"/positions/otc/{deal_id}",
                             {"stopLevel": stop, "limitLevel": limite, "guaranteedStop": False}, version="2")[0]

    def fermer(self, deal_id, sens, taille):
        corps = {"dealId": deal_id, "direction": "SELL" if sens == 1 else "BUY", "size": taille,
                 "orderType": "MARKET", "timeInForce": "FILL_OR_KILL", "expiry": None}
        r = self.supprimer("/positions/otc", corps, version="1")
        return self.confirmation(r.get("dealReference")) if r.get("dealReference") else None

    def activite(self, depuis_iso):
        """Opérations du compte depuis une date (sert à retrouver le prix de sortie d'un stop ou d'un objectif)."""
        return self.get(f"/history/activity?from={depuis_iso}&detailed=true&pageSize=200", version="3").get(
            "activities", [])
