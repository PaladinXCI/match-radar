from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

ROME = ZoneInfo("Europe/Rome")
API_BASE = "https://api.football-data.org/v4"

LEAGUES = {
    "Premier League": "PL",
    "LaLiga": "PD",
    "Serie A": "SA",
    "Bundesliga": "BL1",
    "Ligue 1": "FL1",
}

RIVALRIES = {
    frozenset(("FC Barcelona", "Real Madrid CF")): "El Clásico",
    frozenset(("Real Madrid CF", "Club Atlético de Madrid")): "Derby di Madrid",
    frozenset(("Manchester City FC", "Manchester United FC")): "Derby di Manchester",
    frozenset(("Liverpool FC", "Manchester United FC")): "Rivalità storica",
    frozenset(("Arsenal FC", "Tottenham Hotspur FC")): "North London Derby",
    frozenset(("FC Internazionale Milano", "AC Milan")): "Derby di Milano",
    frozenset(("FC Internazionale Milano", "Juventus FC")): "Derby d'Italia",
    frozenset(("AS Roma", "SS Lazio")): "Derby di Roma",
    frozenset(("FC Bayern München", "Borussia Dortmund")): "Der Klassiker",
    frozenset(("Paris Saint-Germain FC", "Olympique de Marseille")): "Le Classique",
}

PRESTIGE = {
    "Real Madrid CF": 100, "FC Barcelona": 98, "Club Atlético de Madrid": 89,
    "Manchester City FC": 98, "Liverpool FC": 97, "Manchester United FC": 94,
    "Arsenal FC": 94, "Chelsea FC": 90, "Tottenham Hotspur FC": 87,
    "FC Bayern München": 98, "Borussia Dortmund": 91, "Bayer 04 Leverkusen": 90,
    "FC Internazionale Milano": 96, "Juventus FC": 94, "AC Milan": 93,
    "SSC Napoli": 90, "AS Roma": 87, "SS Lazio": 84,
    "Paris Saint-Germain FC": 97, "Olympique de Marseille": 88,
    "AS Monaco FC": 85, "Olympique Lyonnais": 84,
}


def get_api_key() -> str:
    try:
        return str(st.secrets["FOOTBALL_DATA_API_KEY"]).strip()
    except Exception:
        return ""


def api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    token = get_api_key()
    if not token:
        raise RuntimeError("Chiave API mancante")
    response = requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"X-Auth-Token": token, "User-Agent": "MatchRadar/2.0"},
        timeout=25,
    )
    if response.status_code == 429:
        raise RuntimeError("Limite richieste raggiunto. Riprova tra circa un minuto.")
    if response.status_code == 403:
        raise RuntimeError("Questa competizione non è inclusa nel tuo piano API.")
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=1800, show_spinner=False)
def get_matches(code: str, date_from: date, date_to: date) -> list[dict[str, Any]]:
    return api_get(
        f"/competitions/{code}/matches",
        {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
    ).get("matches", [])


@st.cache_data(ttl=3600, show_spinner=False)
def get_standings(code: str) -> list[dict[str, Any]]:
    standings = api_get(f"/competitions/{code}/standings").get("standings", [])
    for block in standings:
        if block.get("type") == "TOTAL":
            return block.get("table", [])
    return standings[0].get("table", []) if standings else []


@st.cache_data(ttl=3600, show_spinner=False)
def get_recent_matches(team_id: int, before: str) -> list[dict[str, Any]]:
    payload = api_get(
        f"/teams/{team_id}/matches",
        {"status": "FINISHED", "dateTo": before, "limit": 5},
    )
    return payload.get("matches", [])[-5:]


def standing_map(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["team"]["id"]): row for row in rows if row.get("team", {}).get("id")}


def form_points(matches: list[dict[str, Any]], team_id: int) -> tuple[int, str, float]:
    points = 0
    sequence: list[str] = []
    goals_for = goals_against = 0
    for match in matches:
        home = match.get("homeTeam", {}).get("id") == team_id
        score = match.get("score", {}).get("fullTime", {})
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue
        gf, ga = (hg, ag) if home else (ag, hg)
        goals_for += gf
        goals_against += ga
        if gf > ga:
            points += 3
            sequence.append("V")
        elif gf == ga:
            points += 1
            sequence.append("N")
        else:
            sequence.append("P")
    played = max(len(sequence), 1)
    goal_rate = (goals_for + goals_against) / played
    return points, " ".join(sequence) or "—", goal_rate


def match_score(
    match: dict[str, Any],
    table: dict[int, dict[str, Any]],
    home_form: tuple[int, str, float],
    away_form: tuple[int, str, float],
) -> tuple[int, list[str], str | None]:
    home = match["homeTeam"]
    away = match["awayTeam"]
    hrow, arow = table.get(home["id"], {}), table.get(away["id"], {})

    hp = PRESTIGE.get(home["name"], 66)
    ap = PRESTIGE.get(away["name"], 66)
    prestige = (hp + ap) / 200
    brand_balance = 1 - abs(hp - ap) / 100

    hpos, apos = hrow.get("position", 11), arow.get("position", 11)
    max_pos = max(len(table), 20)
    table_quality = 1 - ((hpos + apos - 2) / (2 * max_pos))
    table_balance = 1 - min(abs(hpos - apos) / max_pos, 1)

    hf, af = home_form[0] / 15, away_form[0] / 15
    form_quality = (hf + af) / 2
    form_balance = 1 - abs(hf - af)
    entertainment = min((home_form[2] + away_form[2]) / 6, 1)

    rivalry = RIVALRIES.get(frozenset((home["name"], away["name"])))
    score = 100 * (
        0.25 * prestige
        + 0.13 * brand_balance
        + 0.20 * table_quality
        + 0.13 * table_balance
        + 0.13 * form_quality
        + 0.08 * form_balance
        + 0.08 * entertainment
    )
    if rivalry:
        score += 9

    reasons: list[str] = []
    if rivalry:
        reasons.append(rivalry)
    if hpos <= 4 and apos <= 4:
        reasons.append("scontro di vertice")
    elif min(hpos, apos) <= 4:
        reasons.append("in campo una squadra da alta classifica")
    if abs(hpos - apos) <= 3:
        reasons.append("posizioni molto vicine")
    if hf >= 0.67 and af >= 0.67:
        reasons.append("entrambe in ottima forma")
    if entertainment >= 0.75:
        reasons.append("ultime gare ricche di gol")
    if prestige >= 0.88:
        reasons.append("grande richiamo internazionale")
    if not reasons:
        reasons.append("sfida potenzialmente equilibrata")

    return max(0, min(100, round(score))), reasons[:3], rivalry


def kickoff_local(match: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00")).astimezone(ROME)


def render_badge(score: int) -> str:
    if score >= 90:
        return "🔥 Imperdibile"
    if score >= 80:
        return "⭐ Big match"
    if score >= 70:
        return "👀 Da vedere"
    return "⚽ Interessante"


st.set_page_config(page_title="Match Radar", page_icon="⚽", layout="wide")
st.title("⚽ Match Radar")
st.caption("Le partite più interessanti delle top 5 leghe europee, ordinate con un indice 0–100.")

if not get_api_key():
    st.error("Manca la chiave di football-data.org.")
    st.markdown(
        """
        **Per attivare l'app:**
        1. crea una chiave gratuita su football-data.org;
        2. in Streamlit apri **Manage app → Settings → Secrets**;
        3. incolla:

        ```toml
        FOOTBALL_DATA_API_KEY = "la_tua_chiave"
        ```
        """
    )
    st.stop()

with st.sidebar:
    st.header("Filtri")
    selected_leagues = st.multiselect("Leghe", list(LEAGUES), default=list(LEAGUES))
    horizon = st.select_slider("Periodo", options=[7, 14, 30, 60, 90], value=30)
    min_score = st.slider("Interesse minimo", 0, 100, 60)
    only_derbies = st.toggle("Solo derby e rivalità", False)
    max_results = st.slider("Numero massimo", 5, 40, 20)
    if st.button("Aggiorna ora", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

today = datetime.now(ROME).date()
date_to = today + timedelta(days=horizon)
items: list[dict[str, Any]] = []
errors: list[str] = []

with st.spinner("Analizzo calendario, classifica e forma…"):
    for league_name in selected_leagues:
        code = LEAGUES[league_name]
        try:
            table_rows = get_standings(code)
            table = standing_map(table_rows)
            matches = get_matches(code, today, date_to)

            for match in matches:
                if match.get("status") not in {"SCHEDULED", "TIMED"}:
                    continue

                home, away = match.get("homeTeam", {}), match.get("awayTeam", {})
                if not home.get("id") or not away.get("id"):
                    continue

                before = kickoff_local(match).date().isoformat()
                home_form = form_points(get_recent_matches(home["id"], before), home["id"])
                away_form = form_points(get_recent_matches(away["id"], before), away["id"])
                score, reasons, rivalry = match_score(match, table, home_form, away_form)

                if score < min_score or (only_derbies and not rivalry):
                    continue

                hrow, arow = table.get(home["id"], {}), table.get(away["id"], {})
                items.append({
                    "score": score,
                    "league": league_name,
                    "kickoff": kickoff_local(match),
                    "home": home,
                    "away": away,
                    "home_pos": hrow.get("position"),
                    "away_pos": arow.get("position"),
                    "home_points": hrow.get("points"),
                    "away_points": arow.get("points"),
                    "home_form": home_form,
                    "away_form": away_form,
                    "reasons": reasons,
                    "rivalry": rivalry,
                })
        except Exception as exc:
            errors.append(f"{league_name}: {exc}")

items.sort(key=lambda x: (-x["score"], x["kickoff"]))
items = items[:max_results]

if errors:
    st.warning("Alcune leghe non sono state caricate:\n\n" + "\n\n".join(errors))

if not items:
    st.info("Nessuna partita trovata. Prova 60–90 giorni e una soglia tra 0 e 50.")
else:
    top1, top2, top3 = st.columns(3)
    top1.metric("Partite selezionate", len(items))
    top2.metric("Miglior indice", f"{items[0]['score']}/100")
    top3.metric("Periodo analizzato", f"{horizon} giorni")

    for item in items:
        with st.container(border=True):
            score_col, match_col = st.columns([1, 5])
            with score_col:
                st.metric("Interesse", f"{item['score']}/100")
                st.caption(render_badge(item["score"]))
            with match_col:
                h, center, a = st.columns([2, 1, 2])
                with h:
                    if item["home"].get("crest"):
                        st.image(item["home"]["crest"], width=55)
                    st.markdown(f"**{item['home']['shortName']}**")
                    if item["home_pos"]:
                        st.caption(f"{item['home_pos']}ª · {item['home_points']} pt")
                with center:
                    st.markdown("### VS")
                    st.caption(item["kickoff"].strftime("%d/%m · %H:%M"))
                with a:
                    if item["away"].get("crest"):
                        st.image(item["away"]["crest"], width=55)
                    st.markdown(f"**{item['away']['shortName']}**")
                    if item["away_pos"]:
                        st.caption(f"{item['away_pos']}ª · {item['away_points']} pt")

                st.caption(f"**{item['league']}**")
                st.write(" · ".join(item["reasons"]))
                st.write(
                    f"Forma: **{item['home']['shortName']} {item['home_form'][1]}** "
                    f"— **{item['away']['shortName']} {item['away_form'][1]}**"
                )

    export = pd.DataFrame([
        {
            "Interesse": x["score"],
            "Lega": x["league"],
            "Data": x["kickoff"].isoformat(),
            "Casa": x["home"]["shortName"],
            "Trasferta": x["away"]["shortName"],
            "Motivi": " | ".join(x["reasons"]),
        }
        for x in items
    ])
    st.download_button(
        "Scarica elenco CSV",
        export.to_csv(index=False).encode("utf-8"),
        "match-radar.csv",
        "text/csv",
    )

st.divider()
st.caption(
    "Fonte dati: football-data.org. L'indice è una valutazione editoriale basata su "
    "classifica, forma, equilibrio, prestigio e rivalità; non è un pronostico."
)
