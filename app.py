from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

ROME = ZoneInfo("Europe/Rome")
API_URL = "https://v3.football.api-sports.io"

LEAGUES = {
    "Premier League": 39,
    "LaLiga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
    "Ligue 1": 61,
}

RIVALRIES = {
    frozenset(("Real Madrid", "Barcelona")): "El Clásico",
    frozenset(("Real Madrid", "Atletico Madrid")): "Derby di Madrid",
    frozenset(("Manchester City", "Manchester United")): "Derby di Manchester",
    frozenset(("Liverpool", "Manchester United")): "Rivalità storica",
    frozenset(("Arsenal", "Tottenham")): "North London Derby",
    frozenset(("Inter", "AC Milan")): "Derby di Milano",
    frozenset(("Inter", "Juventus")): "Derby d'Italia",
    frozenset(("AS Roma", "Lazio")): "Derby di Roma",
    frozenset(("Bayern Munich", "Borussia Dortmund")): "Der Klassiker",
    frozenset(("Paris Saint Germain", "Marseille")): "Le Classique",
}

PRESTIGE = {
    "Real Madrid": 100, "Barcelona": 98, "Atletico Madrid": 89,
    "Manchester City": 98, "Liverpool": 97, "Manchester United": 94,
    "Arsenal": 94, "Chelsea": 90, "Tottenham": 87,
    "Bayern Munich": 98, "Borussia Dortmund": 91, "Bayer Leverkusen": 90,
    "Inter": 96, "Juventus": 94, "AC Milan": 93,
    "Napoli": 90, "AS Roma": 87, "Lazio": 84,
    "Paris Saint Germain": 97, "Marseille": 88,
    "Monaco": 85, "Lyon": 84,
}


def get_key() -> str:
    try:
        return str(st.secrets["API_FOOTBALL_KEY"]).strip()
    except Exception:
        return ""


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_league_fixtures(league_id: int, season: int, api_key: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{API_URL}/fixtures",
        params={"league": league_id, "season": season},
        headers={"x-apisports-key": api_key},
        timeout=40,
    )
    if response.status_code == 429:
        raise RuntimeError("limite API raggiunto; attendi prima di aggiornare")
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors")
    if errors:
        raise RuntimeError(str(errors))
    return payload.get("response", [])


def fixture_datetime(item: dict[str, Any]) -> datetime:
    raw = item["fixture"]["date"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ROME)


def final_score(item: dict[str, Any]) -> tuple[int, int] | None:
    goals = item.get("goals", {})
    home, away = goals.get("home"), goals.get("away")
    status = item.get("fixture", {}).get("status", {}).get("short")
    if status not in {"FT", "AET", "PEN"} or home is None or away is None:
        return None
    return int(home), int(away)


def compute_stats(fixtures: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "team": {}, "played": 0, "wins": 0, "draws": 0, "losses": 0,
        "points": 0, "gf": 0, "ga": 0, "form": [],
    })

    finished = [x for x in fixtures if final_score(x) is not None]
    finished.sort(key=fixture_datetime)

    for item in finished:
        home = item["teams"]["home"]
        away = item["teams"]["away"]
        score = final_score(item)
        if score is None:
            continue
        hg, ag = score
        hid, aid = int(home["id"]), int(away["id"])
        stats[hid]["team"] = home
        stats[aid]["team"] = away

        for team_id, gf, ga in ((hid, hg, ag), (aid, ag, hg)):
            row = stats[team_id]
            row["played"] += 1
            row["gf"] += gf
            row["ga"] += ga
            if gf > ga:
                row["wins"] += 1
                row["points"] += 3
                row["form"].append("V")
            elif gf == ga:
                row["draws"] += 1
                row["points"] += 1
                row["form"].append("N")
            else:
                row["losses"] += 1
                row["form"].append("P")
            row["form"] = row["form"][-5:]

    ordered = sorted(
        stats.items(),
        key=lambda pair: (
            -pair[1]["points"],
            -(pair[1]["gf"] - pair[1]["ga"]),
            -pair[1]["gf"],
            pair[1]["team"].get("name", ""),
        ),
    )
    for pos, (_, row) in enumerate(ordered, 1):
        played = max(row["played"], 1)
        row["position"] = pos
        row["gd"] = row["gf"] - row["ga"]
        row["ppg"] = row["points"] / played
        row["goal_rate"] = (row["gf"] + row["ga"]) / played
    return dict(stats)


def interest_score(
    item: dict[str, Any], stats: dict[int, dict[str, Any]]
) -> tuple[int, list[str], str | None]:
    home, away = item["teams"]["home"], item["teams"]["away"]
    hs = stats.get(int(home["id"]), {})
    aws = stats.get(int(away["id"]), {})

    hp = PRESTIGE.get(home["name"], 65)
    ap = PRESTIGE.get(away["name"], 65)
    prestige = (hp + ap) / 200
    prestige_balance = 1 - min(abs(hp - ap) / 100, 1)

    nteams = max(len(stats), 18)
    hpos = hs.get("position", nteams / 2)
    apos = aws.get("position", nteams / 2)
    table_quality = 1 - ((hpos + apos - 2) / (2 * nteams))
    table_balance = 1 - min(abs(hpos - apos) / nteams, 1)

    hppg = min(hs.get("ppg", 1.25) / 3, 1)
    appg = min(aws.get("ppg", 1.25) / 3, 1)
    form_quality = (hppg + appg) / 2
    form_balance = 1 - abs(hppg - appg)
    entertainment = min(
        (hs.get("goal_rate", 2.5) + aws.get("goal_rate", 2.5)) / 6, 1
    )

    rivalry = RIVALRIES.get(frozenset((home["name"], away["name"])))
    score = 100 * (
        0.27 * prestige
        + 0.12 * prestige_balance
        + 0.20 * table_quality
        + 0.13 * table_balance
        + 0.12 * form_quality
        + 0.07 * form_balance
        + 0.09 * entertainment
    )
    if rivalry:
        score += 9

    reasons: list[str] = []
    if rivalry:
        reasons.append(rivalry)
    if hpos <= 4 and apos <= 4:
        reasons.append("scontro di vertice")
    elif min(hpos, apos) <= 4:
        reasons.append("una squadra in zona Champions")
    if abs(hpos - apos) <= 3:
        reasons.append("classifica molto equilibrata")
    if hppg >= 0.65 and appg >= 0.65:
        reasons.append("entrambe in buona forma")
    if entertainment >= 0.78:
        reasons.append("alto potenziale di gol")
    if prestige >= 0.88:
        reasons.append("grande richiamo internazionale")
    if not reasons:
        reasons.append("sfida potenzialmente equilibrata")

    return max(0, min(100, round(score))), reasons[:3], rivalry


def badge(score: int) -> str:
    if score >= 90:
        return "🔥 Imperdibile"
    if score >= 80:
        return "⭐ Big match"
    if score >= 70:
        return "👀 Da vedere"
    return "⚽ Interessante"


def form(row: dict[str, Any]) -> str:
    return " ".join(row.get("form", [])) or "—"


now = datetime.now(ROME)
default_season = now.year if now.month >= 7 else now.year - 1

st.set_page_config(page_title="Match Radar", page_icon="⚽", layout="wide")
st.title("⚽ Match Radar")
st.caption("Le partite che vale davvero la pena vedere, ordinate con un indice 0–100.")

key = get_key()
if not key:
    st.error("Manca la chiave API-Football.")
    st.markdown(
        """
        Inseriscila in **Manage app → Settings → Secrets**:

        ```toml
        API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
        ```
        """
    )
    st.stop()

with st.sidebar:
    st.header("Filtri")
    selected = st.multiselect("Leghe", list(LEAGUES), default=list(LEAGUES))
    season = st.number_input(
        "Stagione (anno iniziale)",
        min_value=2020,
        max_value=now.year,
        value=default_season,
        step=1,
    )
    view = st.radio("Vista", ["Prossime partite", "Dall'inizio della stagione"])
    horizon = st.select_slider("Prossimi giorni", [7, 14, 30, 60, 90], value=30)
    threshold = st.slider("Interesse minimo", 0, 100, 55)
    only_derbies = st.toggle("Solo derby e rivalità", False)
    no_spoilers = st.toggle("Nascondi risultati", True)
    maximum = st.slider("Numero massimo", 5, 50, 20)
    st.caption("Massimo 5 richieste per aggiornamento. Cache: 12 ore.")
    if st.button("Aggiorna ora", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

items: list[dict[str, Any]] = []
tables: dict[str, list[dict[str, Any]]] = {}
errors: list[str] = []

with st.spinner("Carico e analizzo le stagioni…"):
    for league_name in selected:
        try:
            fixtures = fetch_league_fixtures(LEAGUES[league_name], int(season), key)
            stats = compute_stats(fixtures)
            tables[league_name] = sorted(
                stats.values(), key=lambda row: row.get("position", 999)
            )

            for item in fixtures:
                kickoff = fixture_datetime(item)
                status = item["fixture"]["status"]["short"]

                if view == "Prossime partite":
                    if status not in {"TBD", "NS"}:
                        continue
                    if not (now <= kickoff <= now + timedelta(days=horizon)):
                        continue
                else:
                    if final_score(item) is None:
                        continue

                score, reasons, rivalry = interest_score(item, stats)
                if score < threshold or (only_derbies and not rivalry):
                    continue

                home, away = item["teams"]["home"], item["teams"]["away"]
                items.append({
                    "score": score,
                    "league": league_name,
                    "kickoff": kickoff,
                    "home": home,
                    "away": away,
                    "hs": stats.get(int(home["id"]), {}),
                    "aws": stats.get(int(away["id"]), {}),
                    "reasons": reasons,
                    "result": final_score(item),
                })
        except Exception as exc:
            errors.append(f"{league_name}: {exc}")

items.sort(
    key=lambda x: (-x["score"], x["kickoff"])
    if view == "Prossime partite"
    else (-x["score"], -x["kickoff"].timestamp())
)
items = items[:maximum]

if errors:
    st.warning("Alcune leghe non sono state caricate:\n\n" + "\n\n".join(errors))

matches_tab, tables_tab, info_tab = st.tabs(
    ["🔥 Partite", "📊 Classifiche", "ℹ️ Metodo"]
)

with matches_tab:
    if not items:
        if view == "Prossime partite":
            st.info(
                "Nessuna partita disponibile con questi filtri. Prova 90 giorni, "
                "soglia 0 oppure seleziona la stagione precedente per verificare i dati."
            )
        else:
            st.info("Nessuna partita supera i filtri. Imposta interesse minimo a 0.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Partite", len(items))
        c2.metric("Indice massimo", f"{items[0]['score']}/100")
        c3.metric("Leghe", len(tables))

        for item in items:
            hs, aws = item["hs"], item["aws"]
            with st.container(border=True):
                rating, content = st.columns([1, 5])
                with rating:
                    st.metric("Interesse", f"{item['score']}/100")
                    st.caption(badge(item["score"]))
                with content:
                    left, centre, right = st.columns([2, 1, 2])
                    with left:
                        if item["home"].get("logo"):
                            st.image(item["home"]["logo"], width=52)
                        st.markdown(f"**{item['home']['name']}**")
                        if hs:
                            st.caption(
                                f"{hs.get('position', '—')}ª · {hs.get('points', 0)} pt · {form(hs)}"
                            )
                    with centre:
                        st.markdown("### VS")
                        st.caption(item["kickoff"].strftime("%d/%m · %H:%M"))
                        if item["result"] and not no_spoilers:
                            st.markdown(f"### {item['result'][0]}–{item['result'][1]}")
                    with right:
                        if item["away"].get("logo"):
                            st.image(item["away"]["logo"], width=52)
                        st.markdown(f"**{item['away']['name']}**")
                        if aws:
                            st.caption(
                                f"{aws.get('position', '—')}ª · {aws.get('points', 0)} pt · {form(aws)}"
                            )
                    st.caption(f"**{item['league']}**")
                    st.write(" · ".join(item["reasons"]))

        export = pd.DataFrame([
            {
                "Interesse": x["score"],
                "Lega": x["league"],
                "Data": x["kickoff"].isoformat(),
                "Casa": x["home"]["name"],
                "Trasferta": x["away"]["name"],
                "Motivi": " | ".join(x["reasons"]),
            }
            for x in items
        ])
        st.download_button(
            "Scarica CSV",
            export.to_csv(index=False).encode("utf-8"),
            "match-radar.csv",
            "text/csv",
        )

with tables_tab:
    for league_name, rows in tables.items():
        with st.expander(league_name, expanded=len(tables) == 1):
            if not rows:
                st.caption("Non ci sono ancora risultati per questa stagione.")
                continue
            df = pd.DataFrame([
                {
                    "#": row.get("position"),
                    "Squadra": row.get("team", {}).get("name"),
                    "G": row.get("played"),
                    "Pt": row.get("points"),
                    "GF": row.get("gf"),
                    "GS": row.get("ga"),
                    "DR": row.get("gd"),
                    "Forma": form(row),
                }
                for row in rows
            ])
            st.dataframe(df, hide_index=True, use_container_width=True)

with info_tab:
    st.markdown(
        """
        Il punteggio combina prestigio, posizione, equilibrio, punti per partita,
        forma recente, frequenza dei gol e rivalità storiche.

        Tutti i calcoli vengono eseguiti localmente dopo aver scaricato una sola
        volta il calendario della stagione di ciascuna lega. L'indice non è un
        pronostico sul risultato.
        """
    )

st.divider()
st.caption("Fonte: API-Football · Cache 12 ore · Orario Europe/Rome")
