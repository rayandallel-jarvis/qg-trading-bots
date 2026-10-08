"""Test de connexion : compte paper Alpaca + notification ntfy."""
import os, json, urllib.request

def get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def notify(topic, msg):
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode(), method="POST",
                                 headers={"Title": "QG Trading", "Tags": "robot"})
    urllib.request.urlopen(req, timeout=20)

key, secret, topic = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY"), os.environ.get("NTFY_TOPIC")
manquants = [n for n, v in [("ALPACA_API_KEY", key), ("ALPACA_SECRET_KEY", secret), ("NTFY_TOPIC", topic)] if not v]
if manquants:
    raise SystemExit(f"Secrets manquants : {', '.join(manquants)}")

h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
try:
    acc = get("https://paper-api.alpaca.markets/v2/account", h)
    msg = f"Connexion Alpaca paper OK. Capital fictif : {float(acc['equity']):,.0f} $".replace(",", " ")
    ok = True
except Exception as e:
    msg = f"Échec connexion Alpaca : {e}"
    ok = False
print(msg)
notify(topic, msg)
if not ok:
    raise SystemExit(1)
