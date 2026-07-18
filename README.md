# Match Radar 1.0

## File da caricare su GitHub

- `app.py`
- `requirements.txt`
- `README.md`

## Chiave API

Crea un account su API-Football e copia la chiave.

In Streamlit apri:

`Manage app → Settings → Secrets`

Incolla:

```toml
API_FOOTBALL_KEY = "LA_TUA_CHIAVE"
```

## Primo test

- Stagione: anno iniziale del campionato, per esempio `2025` per la stagione 2025/26.
- Vista: `Dall'inizio della stagione`.
- Interesse minimo: `0`.
- Solo derby: disattivato.

L'app effettua al massimo una richiesta per lega e conserva i risultati in cache per 12 ore.
