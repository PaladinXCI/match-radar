from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
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


def api_key() -> str:
    try:
        return str(st.secrets["FOOTBALL_DATA_API_KEY"]).strip()
    except Exception:
        return ""


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_season(code: str, token: str) -> dict[str, Any]:
    response = requests.get(
        f"{API_BASE}/competitions/{code}/matches",
        headers={"X-Auth-Token": token, "User-Agent": "MatchRadar/3.0"},
        timeout=30,
    )
    if response.status_code == 429:
        raise RuntimeError("limite temporaneo dell'API; attendi un minuto senza premere Aggiorna")
    if response.status_code == 403:
        raise RuntimeError("competizione non inclusa nel piano API")
    response.raise_for_status()
    return response.json()


def local_kickoff(match: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00")).astimezone(ROME)


def completed_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score", {}).get("fullTime", {})
    home, away = score.get("home"), score.get("away")
    if home is None or away is None:
        return None
    return int(home), int(away)


def derive_team_stats(matches: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "played": 0, "won": 0, "draw": 0, "lost": 0,
        "points": 0, "gf": 0, "ga": 0, "form": [],
        "team": {},
    })

    finished = [
        m for m in matches
        if m.get("status") in {"FINISHED", "AWARDED"} and completed_score(m) is not None
    ]
    finished.sort(key=local_kickoff)

    for match in finished:
        home = match.get("homeTeam", {})
        away = match.get("awayTeam", {})
        if not home.get("id") or not away.get("id"):
            continue
        score = completed_score(match)
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
                row["won"] += 1
                row["points"] += 3
                row["form"].append("V")
            elif gf == ga:
                row["draw"] += 1
                row["points"] += 1
                row["form"].append("N")
            else:
                row["lost"] += 1
                row["form"].append("P")
            row["form"] = row["form"][-5:]

    ordered = sorted(
        stats.items(),
        key=lambda item: (
            -item[1]["points"],
            -(item[1]["gf"] - item[1]["ga"]),
            -item[1]["gf"],
            item[1]["team"].get("name", ""),
        ),
    )
    for position, (team_id, row) in enumerate(ordered, start=1):
        row["position"] = position
        row["gd"] = row["gf"] - row["ga"]
        played = max(row["played"], 1)
        row["ppg"] = row["points"] / played
        row["goal_rate"] = (row["gf"] + row["ga"]) / played

    return dict(stats)


def match_interest(
    match: dict[str, Any],
    stats: dict[int, dict[str, Any]],
) -> tuple[int, list[str], str | None]:
    home, away = match["homeTeam"], match["awayTeam"]
    hs = stats.get(int(home["id"]), {})
    aws = stats.get(int(away["id"]), {})

    hp = PRESTIGE.get(home.get("name", ""), 65)
    ap = PRESTIGE.get(away.get("name", ""), 65)
    prestige = (hp + ap) / 200
    brand_balance = 1 - min(abs(hp - ap) / 100, 1)

    team_count = max(len(stats), 18)
    hpos = hs.get("position", team_count / 2)
    apos = aws.get("position", team_count / 2)
    table_quality = 1 - ((hpos + apos - 2) / (2 * team_count))
    table_balance = 1 - min(abs(hpos - apos) / team_count, 1)

    hppg = min(hs.get("ppg", 1.2) / 3, 1)
    appg = min(aws.get("ppg", 1.2) / 3, 1)
    form_quality = (hppg + appg) / 2
    form_balance = 1 - abs(hppg - appg)

    entertainment = min((hs.get("goal_rate", 2.4) + aws.get("goal_rate", 2.4)) / 6, 1)
    rivalry = RIVALRIES.get(frozenset((home.get("name", ""), away.get("name", ""))))

    score = 100 * (
        0.27 * prestige
        + 0.12 * brand_balance
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


def form_text(row: dict[str, Any]) -> str:
    return " ".join(row.get("form", [])) or "—"


st.set_page_config(page_title="Match Radar", page_icon="⚽", layout="wide")
st.title("⚽ Match Radar")
st.caption("Scopri le partite più interessanti delle top 5 leghe con un indice editoriale 0–100.")

token = api_key()
if not token:
    st.error("Manca FOOTBALL_DATA_API_KEY nei Secrets di Streamlit.")
    st.stop()

with st.sidebar:
    st.header("Filtri")
    selected = st.multiselect("Leghe", list(LEAGUES), default=list(LEAGUES))
    view = st.radio("Vista", ["Prossime partite", "Stagione dall'inizio"])
    horizon = st.select_slider("Orizzonte", options=[7, 14, 30, 60, 90], value=30)
    threshold = st.slider("Interesse minimo", 0, 100, 55)
    only_rivalries = st.toggle("Solo derby e rivalità", False)
    hide_scores = st.toggle("Nascondi risultati", True)
    max_results = st.slider("Numero massimo", 5, 50, 20)
    st.caption("I dati restano in cache per 6 ore.")
    if st.button("Aggiorna dati", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

now = datetime.now(ROME)
items: list[dict[str, Any]] = []
league_tables: dict[str, list[dict[str, Any]]] = {}
errors: list[str] = []

with st.spinner("Analizzo le stagioni…"):
    for league_name in selected:
        try:
            payload = fetch_season(LEAGUES[league_name], token)
            matches = payload.get("matches", [])
            stats = derive_team_stats(matches)
            league_tables[league_name] = sorted(stats.values(), key=lambda r: r.get("position", 999))

            for match in matches:
                status = match.get("status")
                kickoff = local_kickoff(match)

                if view == "Prossime partite":
                    if status not in {"SCHEDULED", "TIMED"}:
                        continue
                    if not (now <= kickoff <= now + timedelta(days=horizon)):
                        continue
                else:
                    if status not in {"FINISHED", "AWARDED"}:
                        continue

                home, away = match.get("homeTeam", {}), match.get("awayTeam", {})
                if not home.get("id") or not away.get("id"):
                    continue

                score, reasons, rivalry = match_interest(match, stats)
                if score < threshold or (only_rivalries and not rivalry):
                    continue

                items.append({
                    "score": score,
                    "league": league_name,
                    "kickoff": kickoff,
                    "status": status,
                    "home": home,
                    "away": away,
                    "home_stats": stats.get(int(home["id"]), {}),
                    "away_stats": stats.get(int(away["id"]), {}),
                    "reasons": reasons,
                    "result": completed_score(match),
                })
        except Exception as exc:
            errors.append(f"{league_name}: {exc}")

items.sort(
    key=lambda x: (-x["score"], x["kickoff"])
    if view == "Prossime partite"
    else (-x["score"], -x["kickoff"].timestamp())
)
items = items[:max_results]

if errors:
    st.warning("Alcune leghe non sono state caricate:\n\n" + "\n\n".join(errors))

tab_matches, tab_tables, tab_method = st.tabs(["🔥 Partite", "📊 Classifiche", "ℹ️ Come funziona"])

with tab_matches:
    if not items:
        if view == "Prossime partite":
            st.info(
                "Non risultano partite programmate nel periodo scelto. "
                "Prova 90 giorni oppure usa “Stagione dall'inizio”. "
                "Durante la pausa estiva i calendari della nuova stagione possono non essere ancora disponibili."
            )
        else:
            st.info("Nessuna partita supera i filtri scelti. Abbassa la soglia.")
    else:
        a, b, c = st.columns(3)
        a.metric("Partite", len(items))
        b.metric("Indice massimo", f"{items[0]['score']}/100")
        c.metric("Leghe caricate", len(league_tables))

        for item in items:
            hs, aws = item["home_stats"], item["away_stats"]
            with st.container(border=True):
                score_col, body_col = st.columns([1, 5])
                with score_col:
                    st.metric("Interesse", f"{item['score']}/100")
                    st.caption(badge(item["score"]))
                with body_col:
                    left, middle, right = st.columns([2, 1, 2])
                    with left:
                        if item["home"].get("crest"):
                            st.image(item["home"]["crest"], width=48)
                        st.markdown(f"**{item['home'].get('shortName', item['home'].get('name'))}**")
                        if hs:
                            st.caption(f"{hs.get('position', '—')}ª · {hs.get('points', 0)} pt · Forma {form_text(hs)}")
                    with middle:
                        st.markdown("### VS")
                        st.caption(item["kickoff"].strftime("%d/%m · %H:%M"))
                        if item["result"] and not hide_scores:
                            st.markdown(f"### {item['result'][0]}–{item['result'][1]}")
                    with right:
                        if item["away"].get("crest"):
                            st.image(item["away"]["crest"], width=48)
                        st.markdown(f"**{item['away'].get('shortName', item['away'].get('name'))}**")
                        if aws:
                            st.caption(f"{aws.get('position', '—')}ª · {aws.get('points', 0)} pt · Forma {form_text(aws)}")
                    st.caption(f"**{item['league']}**")
                    st.write(" · ".join(item["reasons"]))

        export = pd.DataFrame([
            {
                "Interesse": x["score"],
                "Lega": x["league"],
                "Data": x["kickoff"].isoformat(),
                "Casa": x["home"].get("shortName", x["home"].get("name")),
                "Trasferta": x["away"].get("shortName", x["away"].get("name")),
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

with tab_tables:
    for league_name, rows in league_tables.items():
        with st.expander(league_name, expanded=len(league_tables) == 1):
            if not rows:
                st.caption("La stagione non è ancora iniziata.")
                continue
            table_df = pd.DataFrame([
                {
                    "#": r.get("position"),
                    "Squadra": r.get("team", {}).get("shortName", r.get("team", {}).get("name")),
                    "G": r.get("played"),
                    "Pt": r.get("points"),
                    "GF": r.get("gf"),
                    "GS": r.get("ga"),
                    "DR": r.get("gd"),
                    "Forma": form_text(r),
                }
                for r in rows
            ])
            st.dataframe(table_df, hide_index=True, use_container_width=True)

with tab_method:
    st.markdown(
        """
        L'indice considera:

        - prestigio e richiamo delle due squadre;
        - posizione e vicinanza in classifica;
        - punti per partita e forma recente;
        - frequenza dei gol nelle gare stagionali;
        - derby e rivalità storiche.

        Il calcolo usa l'intero calendario della stagione con **una sola richiesta per lega**.
        Non è un pronostico e non indica quale squadra vincerà.
        """
    )

st.divider()
st.caption("Fonte dati: football-data.org · Cache: 6 ore · Orario: Europe/Rome")
