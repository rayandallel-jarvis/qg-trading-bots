# QG Trading Bots

Bots de trading **en argent fictif** (compte démo Alpaca), pilotés depuis le QG Jarvis.

## Stratégies (unités 1, 5 et 15 min)
- **ICT** : biais 15 min, balayage d'une liquidité (plus haut/bas de la veille, session asiatique, swings 15 min), changement de structure avec bougie de déplacement ≥ 1,5 ATR, entrée limite au milieu du FVG, objectif sur la liquidité opposée (≥ 2R). Fenêtres : Londres 8 h–11 h (crypto) et New York 15 h 30–17 h 30 (heure de Paris).
- **Support / résistance** : zones issues des swings 15 min des 3 derniers jours (≥ 2 touches), deux setups notés séparément : rebond et cassure-retest, sur clôture 5 min avec mèche de rejet ≥ 50 % (≥ 1,5R).
- **Volume Profile** : POC, VAH, VAL de la séance précédente ; règle des 80 % et rejet de bord de la zone de valeur (≥ 1,5R).

## Règles communes
Risque 0,5 % par trade ; trade ignoré si frais + glissement > 25 % du risque ; arrêt de la stratégie pour la journée après 3 pertes d'affilée ; positions actions clôturées à 21 h 55 ; une position par actif.

## Fonctionnement
- `config.yaml` : tous les paramètres.
- `bots/backtest.py` : test sur 4 semaines d'historique (filtre), résultat dans `etat/backtest.json`. Se relance à chaque modification des stratégies.
- `bots/direct.py` : bot en direct sur le compte démo, relancé 4 fois par jour par GitHub Actions. État dans `etat/etat.json` (trades, cycle, statistiques).
- Notifications sur ntfy.

Secrets GitHub requis : `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `NTFY_TOPIC`.
