from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any
import math

import pandas as pd
import requests
import streamlit as st

ROME = ZoneInfo("Europe/Rome")

LEAGUES = {
    "Premier League": "eng.1",
    "LaLiga": "esp.1",
    "Serie A": "ita.1",
    "Bundesliga": "ger.1",
    "Ligue 1": "fra.1",
}

# Valore storico/mediatico indicativo, usato soltanto per ordinare le partite.
TEAM_PRESTIGE = {
    "Real Madrid": 100, "Barcelona": 98, "Atlético Madrid": 88,
    "Manchester City": 98, "Liverpool": 97, "Manchester United": 94,
    "Arsenal": 93, "Chelsea": 89, "Tottenham Hotspur": 86,
    "Bayern Munich": 97, "Borussia Dortmund": 91, "Bayer Leverkusen": 88,
    "Internazionale": 95, "Inter": 95, "Juventus": 93, "AC Milan": 92,
    "Milan": 92, "Napoli": 89, "AS Roma": 86, "Roma": 86, "Lazio": 82,
    "Paris Saint-Germain": 97, "Marseille": 87, "Olympique Marseille": 87,
    "Monaco": 84, "Lyon": 82,
}

RIVALRIES = {
    frozenset(("Real Madrid", "Barcelona")),
    frozenset(("Real Madrid", "Atlético Madrid")),
    frozenset(("Manchester City", "Manchester United")),
    frozenset(("Liverpool", "Manchester United")),
    frozenset(("Arsenal", "Tottenham Hotspur")),
    frozenset(("Internazionale", "AC Milan")),
    frozenset(("Inter", "Milan")),
    frozenset(("Internazionale", "Juventus")),
    frozenset(("Inter", "Juventus")),
    frozenset(("AS Roma", "Lazio")),
    frozenset(("Roma", "Lazio")),
    frozenset(("Bayern Munich", "Borussia Dortmund")),
    frozenset(("Paris Saint-Germain", "Marseille")),
    frozenset(("Paris Saint-Germain", "Olympique Marseille")),
}


def api_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 MatchRadar/1.0",
        "Accept": "application/json",
    }
    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_events(league_code: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard"
    params = {
        "dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
        "limit": 1000,
    }
    payload = api_get(url, params)
    return payload.get("events", [])


def extract_team(competitor: dict[str, Any]) -> dict[str, Any]:
    team = competitor.get("team", {})
    records = competitor.get("records", [])
    record_text = records[0].get("summary", "") if records else ""
    return {
        "name": team.get("displayName") or team.get("shortDisplayName") or "Squadra",
        "logo": team.get("logo", ""),
        "record": record_text,
        "home_away": competitor.get("homeAway", ""),
    }


def record_strength(record: str) -> float:
    # ESPN di solito restituisce una stringa come 12-5-3.
    try:
        parts = [int(x) for x in record.split("-")[:3]]
        if len(parts) != 3:
            return 0.5
        wins, draws, losses = parts
        played = wins + draws + losses
        return (3 * wins + draws) / (3 * played) if played else 0.5
    except Exception:
        return 0.5


def event_interest(home: dict[str, Any], away: dict[str, Any], event: dict[str, Any]) -> tuple[int, list[str]]:
    hp = TEAM_PRESTIGE.get(home["name"], 68)
    ap = TEAM_PRESTIGE.get(away["name"], 68)

    prestige = (hp + ap) / 200
    balance = 1 - abs(hp - ap) / 100

    hs = record_strength(home["record"])
    aps = record_strength(away["record"])
    form_quality = (hs + aps) / 2
    form_balance = 1 - min(abs(hs - aps), 1)

    derby = frozenset((home["name"], away["name"])) in RIVALRIES
    neutral = bool(event.get("competitions", [{}])[0].get("neutralSite", False))

    score = 100 * (
        0.43 * prestige
        + 0.22 * balance
        + 0.18 * form_quality
        + 0.10 * form_balance
        + 0.07 * float(derby)
    )
    if neutral:
        score += 2

    reasons = []
    if prestige >= 0.88:
        reasons.append("grande sfida")
    elif prestige >= 0.76:
        reasons.append("squadre di richiamo")
    if balance >= 0.90:
        reasons.append("molto equilibrata")
    if form_quality >= 0.65:
        reasons.append("buon rendimento stagionale")
    if derby:
        reasons.append("derby o rivalità storica")
    if not reasons:
        reasons.append("partita potenzialmente equilibrata")

    return max(0, min(100, round(score))), reasons[:3]


def parse_event(event: dict[str, Any], league_name: str) -> dict[str, Any] | None:
    competitions = event.get("competitions", [])
    if not competitions:
        return None

    competition = competitions[0]
    competitors = competition.get("competitors", [])
    if len(competitors) != 2:
        return None

    teams = [extract_team(c) for c in competitors]
    home = next((t for t in teams if t["home_away"] == "home"), teams[0])
    away = next((t for t in teams if t["home_away"] == "away"), teams[1])

    try:
        kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(ROME)
    except Exception:
        return None

    score, reasons = event_interest(home, away, event)
    links = event.get("links", [])
    url = links[0].get("href", "") if links else ""

    return {
        "Interesse": score,
        "Partita": f"{home['name']} – {away['name']}",
        "Lega": league_name,
        "Data": kickoff,
        "Perché": " · ".join(reasons),
        "Link": url,
        "Casa": home,
        "Trasferta": away,
    }


st.set_page_config(page_title="Match Radar", page_icon="⚽", layout="wide")
st.title("⚽ Match Radar")
st.caption("Le partite più interessanti delle top 5 leghe europee.")

with st.sidebar:
    st.header("Filtri")
    selected = st.multiselect("Leghe", list(LEAGUES), default=list(LEAGUES))
    horizon = st.slider("Prossimi giorni", 1, 60, 14)
    threshold = st.slider("Interesse minimo", 0, 100, 55)
    max_matches = st.slider("Numero massimo", 5, 50, 20)
    no_spoilers = st.toggle("Nascondi risultati", value=True)
    if st.button("Aggiorna dati"):
        st.cache_data.clear()
        st.rerun()

now = datetime.now(ROME)
end = now + timedelta(days=horizon)
rows: list[dict[str, Any]] = []
errors: list[str] = []

with st.spinner("Aggiorno le partite…"):
    for league_name in selected:
        try:
            events = fetch_events(LEAGUES[league_name], now, end)
            for event in events:
                row = parse_event(event, league_name)
                if not row:
                    continue
                if row["Data"] < now or row["Data"] > end:
                    continue
                if row["Interesse"] < threshold:
                    continue
                rows.append(row)
        except Exception as exc:
            errors.append(f"{league_name}: {exc}")

rows.sort(key=lambda r: (-r["Interesse"], r["Data"]))
rows = rows[:max_matches]

if errors:
    st.warning("Alcune leghe non sono state caricate:\n\n" + "\n\n".join(errors))

if not rows:
    st.info("Nessuna partita trovata con questi filtri. Prova ad ampliare i giorni o abbassare la soglia.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Partite selezionate", len(rows))
    c2.metric("Indice massimo", f"{rows[0]['Interesse']}/100")
    c3.metric("Orizzonte", f"{horizon} giorni")

    for row in rows:
        with st.container(border=True):
            left, middle, right = st.columns([1, 5, 1.3])
            with left:
                st.metric("Interesse", f"{row['Interesse']}/100")
            with middle:
                st.subheader(row["Partita"])
                st.write(f"**{row['Lega']}** · {row['Data']:%A %d %B, %H:%M}")
                st.caption(row["Perché"])
            with right:
                if row["Link"]:
                    st.link_button("Dettagli", row["Link"], use_container_width=True)

    export = pd.DataFrame([
        {
            "Interesse": r["Interesse"],
            "Partita": r["Partita"],
            "Lega": r["Lega"],
            "Data": r["Data"].isoformat(),
            "Perché": r["Perché"],
            "Link": r["Link"],
        }
        for r in rows
    ])
    st.download_button(
        "Scarica CSV",
        export.to_csv(index=False).encode("utf-8"),
        "match-radar.csv",
        "text/csv",
    )

st.caption("Fonte calendario: ESPN. L'indice d'interesse è una stima editoriale, non un pronostico.")
