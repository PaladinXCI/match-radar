from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
ROME = ZoneInfo("Europe/Rome")

LEAGUES = {
    "Premier League": {"tournament_id": 17},
    "LaLiga": {"tournament_id": 8},
    "Serie A": {"tournament_id": 23},
    "Bundesliga": {"tournament_id": 35},
    "Ligue 1": {"tournament_id": 34},
}

RIVALRIES = [
    ("Arsenal", "Tottenham Hotspur"), ("Liverpool", "Manchester United"),
    ("Manchester City", "Manchester United"), ("Chelsea", "Arsenal"),
    ("Real Madrid", "Barcelona"), ("Real Madrid", "Atletico Madrid"),
    ("Barcelona", "Espanyol"), ("Sevilla", "Real Betis"),
    ("Inter", "Milan"), ("Juventus", "Inter"), ("Roma", "Lazio"),
    ("Juventus", "Torino"), ("Napoli", "Roma"),
    ("Bayern Munich", "Borussia Dortmund"),
    ("Schalke 04", "Borussia Dortmund"), ("Hamburger SV", "Werder Bremen"),
    ("Paris Saint-Germain", "Olympique de Marseille"),
    ("Olympique Lyonnais", "Saint-Etienne"), ("Lille", "Lens"),
]


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


RIVALRY_KEYS = {frozenset((_norm(a), _norm(b))) for a, b in RIVALRIES}


class SofaScoreError(RuntimeError):
    pass


@dataclass
class SofaScoreClient:
    base_url: str = "https://www.sofascore.com/api/v1"
    timeout: int = 15
    min_interval: float = 0.35

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MatchRadar/0.1 (personal dashboard; respectful polling)",
            "Accept": "application/json",
        })
        self._last_request = 0.0

    def _get(self, path: str) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        try:
            response = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
            self._last_request = time.monotonic()
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SofaScoreError(f"Errore SofaScore: {exc}") from exc

    def current_season(self, tournament_id: int) -> dict[str, Any]:
        seasons = self._get(f"/unique-tournament/{tournament_id}/seasons").get("seasons", [])
        if not seasons:
            raise SofaScoreError("Nessuna stagione trovata")
        return max(seasons, key=lambda item: int(item.get("id", 0)))

    def standings(self, tournament_id: int, season_id: int) -> list[dict[str, Any]]:
        blocks = self._get(
            f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
        ).get("standings", [])
        return blocks[0].get("rows", []) if blocks else []

    def all_events(self, tournament_id: int, season_id: int, max_pages: int = 30) -> list[dict[str, Any]]:
        by_id: dict[int, dict[str, Any]] = {}
        for direction in ("last", "next"):
            for page in range(max_pages):
                payload = self._get(
                    f"/unique-tournament/{tournament_id}/season/{season_id}/events/{direction}/{page}"
                )
                for event in payload.get("events", []):
                    if event.get("id") is not None:
                        by_id[int(event["id"])] = event
                if not payload.get("hasNextPage", False):
                    break
        return sorted(by_id.values(), key=lambda event: int(event.get("startTimestamp", 0)))


def build_team_context(events: list[dict[str, Any]], standings: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    ctx: dict[int, dict[str, float]] = defaultdict(lambda: {
        "position": 20.0, "form_points": 0.0, "goals_for_recent": 0.0,
    })
    for row in standings:
        team_id = row.get("team", {}).get("id")
        if team_id is not None:
            ctx[int(team_id)]["position"] = float(row.get("position", 20))

    recent: dict[int, deque[tuple[int, int]]] = defaultdict(lambda: deque(maxlen=5))
    completed = sorted(
        [event for event in events if event.get("status", {}).get("type") == "finished"],
        key=lambda event: int(event.get("startTimestamp", 0)),
    )
    for event in completed:
        home_id = event.get("homeTeam", {}).get("id")
        away_id = event.get("awayTeam", {}).get("id")
        home_score = event.get("homeScore", {}).get("current")
        away_score = event.get("awayScore", {}).get("current")
        if None in (home_id, away_id, home_score, away_score):
            continue
        home_points = 3 if home_score > away_score else 1 if home_score == away_score else 0
        away_points = 3 if away_score > home_score else 1 if home_score == away_score else 0
        recent[int(home_id)].append((home_points, int(home_score)))
        recent[int(away_id)].append((away_points, int(away_score)))

    for team_id, games in recent.items():
        ctx[team_id]["form_points"] = sum(game[0] for game in games) / (3 * len(games))
        ctx[team_id]["goals_for_recent"] = sum(game[1] for game in games) / len(games)
    return ctx


def score_event(event: dict[str, Any], ctx: dict[int, dict[str, float]], league_size: int) -> tuple[float, list[str]]:
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    home_ctx = ctx.get(int(home.get("id", -1)), {})
    away_ctx = ctx.get(int(away.get("id", -1)), {})
    hp = home_ctx.get("position", league_size)
    ap = away_ctx.get("position", league_size)

    quality = 1 - ((hp + ap - 2) / max(2 * (league_size - 1), 1))
    balance = 1 - min(abs(hp - ap) / max(league_size - 1, 1), 1)
    form = (home_ctx.get("form_points", 0) + away_ctx.get("form_points", 0)) / 2
    goals = min((home_ctx.get("goals_for_recent", 0) + away_ctx.get("goals_for_recent", 0)) / 3.2, 1)
    top_clash = hp <= 6 and ap <= 6
    relegation_clash = hp >= league_size - 4 and ap >= league_size - 4
    stakes = 1.0 if top_clash or relegation_clash else 0.0
    derby = frozenset((_norm(home.get("name", "")), _norm(away.get("name", "")))) in RIVALRY_KEYS

    value = 100 * (
        0.29 * quality + 0.23 * balance + 0.17 * form
        + 0.13 * goals + 0.10 * stakes + 0.08 * float(derby)
    )
    reasons: list[str] = []
    if quality >= 0.72: reasons.append("squadre di alta classifica")
    if balance >= 0.78: reasons.append("sfida molto equilibrata")
    if form >= 0.65: reasons.append("buona forma recente")
    if goals >= 0.72: reasons.append("alto potenziale offensivo")
    if top_clash: reasons.append("scontro diretto europeo/titolo")
    if relegation_clash: reasons.append("scontro salvezza")
    if derby: reasons.append("rivalità/derby")
    return round(max(0, min(100, value)), 1), (reasons or ["profilo complessivo interessante"])[:3]


def ranked_upcoming(events: list[dict[str, Any]], standings: list[dict[str, Any]], now_ts: int) -> list[dict[str, Any]]:
    ctx = build_team_context(events, standings)
    league_size = max(len(standings), 18)
    output = []
    for event in events:
        if int(event.get("startTimestamp", 0)) < now_ts:
            continue
        if event.get("status", {}).get("type") not in {"notstarted", "postponed"}:
            continue
        score, reasons = score_event(event, ctx, league_size)
        output.append({"event": event, "interest": score, "reasons": reasons})
    return sorted(output, key=lambda item: (-item["interest"], item["event"].get("startTimestamp", 0)))


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Configura TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    response.raise_for_status()


st.set_page_config(page_title="Match Radar", page_icon="⚽", layout="wide")
st.title("⚽ Match Radar — Top 5 leghe")
st.caption("Le partite più interessanti da vedere, ordinate con un indice da 0 a 100.")


@st.cache_data(ttl=1800, show_spinner=False)
def load_league(name: str):
    tournament_id = LEAGUES[name]["tournament_id"]
    client = SofaScoreClient()
    season = client.current_season(tournament_id)
    season_id = int(season["id"])
    return season, client.all_events(tournament_id, season_id), client.standings(tournament_id, season_id)


with st.sidebar:
    st.header("Filtri")
    selected = st.multiselect("Leghe", list(LEAGUES), default=list(LEAGUES))
    horizon = st.slider("Orizzonte (giorni)", 1, 60, 14)
    threshold = st.slider("Interesse minimo", 0, 100, 60)
    max_matches = st.slider("Numero massimo", 5, 50, 20)
    if st.button("Aggiorna dati"):
        st.cache_data.clear()
        st.rerun()

now = datetime.now(tz=ROME)
cutoff = now + timedelta(days=horizon)
all_rows = []
errors = []

with st.spinner("Aggiorno calendario, classifica e forma recente…"):
    for league in selected:
        try:
            _, events, standings = load_league(league)
            for item in ranked_upcoming(events, standings, int(now.timestamp())):
                event = item["event"]
                kickoff = datetime.fromtimestamp(event["startTimestamp"], tz=timezone.utc).astimezone(ROME)
                if kickoff > cutoff or item["interest"] < threshold:
                    continue
                home = event.get("homeTeam", {}).get("name", "Casa")
                away = event.get("awayTeam", {}).get("name", "Trasferta")
                all_rows.append({
                    "Interesse": item["interest"], "Partita": f"{home} – {away}",
                    "Lega": league, "Data": kickoff,
                    "Perché": " · ".join(item["reasons"]),
                    "SofaScore": f"https://www.sofascore.com/event/{event.get('id')}",
                })
        except SofaScoreError as exc:
            errors.append(f"{league}: {exc}")

all_rows = sorted(all_rows, key=lambda row: (-row["Interesse"], row["Data"]))[:max_matches]

if errors:
    st.warning("Alcune leghe non sono state caricate:\n\n" + "\n\n".join(errors))

if not all_rows:
    st.info("Nessuna partita soddisfa i filtri. Riduci la soglia o amplia l’orizzonte.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Partite selezionate", len(all_rows))
    c2.metric("Indice massimo", f"{all_rows[0]['Interesse']:.0f}/100")
    c3.metric("Aggiornato", now.strftime("%d/%m %H:%M"))

    for index, row in enumerate(all_rows, 1):
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.subheader(f"{index}. {row['Partita']}")
                st.write(f"**{row['Lega']}** · {row['Data'].strftime('%a %d %b, %H:%M')} · {row['Perché']}")
                st.link_button("Apri su SofaScore", row["SofaScore"])
            with right:
                st.metric("Interesse", f"{row['Interesse']:.0f}/100")

    csv_rows = [{**row, "Data": row["Data"].isoformat()} for row in all_rows]
    st.download_button(
        "Scarica CSV", pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8"),
        file_name="match_radar.csv", mime="text/csv",
    )

    if telegram_configured() and st.button("Invia la selezione su Telegram"):
        lines = ["⚽ Match Radar"]
        for row in all_rows[:10]:
            lines.append(
                f"{row['Interesse']:.0f}/100 — {row['Partita']}\n"
                f"{row['Data'].strftime('%d/%m %H:%M')} · {row['Lega']}"
            )
        send_telegram("\n\n".join(lines))
        st.success("Messaggio inviato.")

with st.expander("Come viene calcolato l’indice?"):
    st.markdown("""
L’indice combina qualità delle squadre, equilibrio, forma nelle ultime cinque gare,
potenziale offensivo, importanza di classifica e rivalità. È un criterio editoriale,
non una previsione del risultato.
""")

st.caption("Uso personale. Gli endpoint SofaScore utilizzati non sono documentati e possono cambiare.")
