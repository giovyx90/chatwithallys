# Chat With Allys

Allys e un bot Telegram multi-tenant per gruppi: memoria storica per `chat_id`,
roast configurabile, borsa sentiment-driven con Telegram Mini App e podcast
periodico configurabile dagli admin.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Variabili minime:

- `TELEGRAM_BOT_TOKEN`: token nuovo da BotFather.
- `TELEGRAM_WEBHOOK_SECRET`: stringa casuale.
- `PUBLIC_BASE_URL=https://allys.giovyx-server.it`

Da BotFather disattiva privacy mode, altrimenti Allys non potra leggere i
messaggi comuni dei gruppi.

## Comandi Gruppo

- `/allys` stato e aiuto.
- `/borsa` apre la Mini App.
- `/portfolio` mostra portafoglio.
- `/buy MEME 2` compra asset con i punti del gruppo.
- `/sell MEME 2` vende asset posseduti.
- `/podcast` mostra configurazione podcast.
- `/podcast_config weekly friday 21:00`
- `/podcast_config daily 21:00`
- `/podcast_config off`
- `/podcast_now` genera un episodio manuale.
- `/roast_level soft|medium|chaos`
- `/memoria testo` salva una memoria manuale.
- `/meme_stats` mostra quante GIF/video della chat Allys puo riusare.
- `/meme_test testo` prova la ricerca meme senza chiamare l'AI.
- `/predictions` apre il bot Predictions separato con sessione del gruppo.

Allys non interviene spontaneamente: risponde solo quando un messaggio contiene
`Allys` o quando qualcuno fa reply a un suo messaggio.
Le risposte AI sono tenute brevi e gli `@username` vengono sostituiti con `@/`.
Ogni tanto, se ha senso, puo accompagnare la risposta con GIF/video gia mandati
nel gruppo o con un link meme controllato.
Nei canali gestisce i `channel_post`: se il post contiene `Allys` puo commentare,
e i comandi podcast possono essere usati se il bot e admin del canale.

Comandi admin per silenziarla nel gruppo:

- `/allys_off` spegne le risposte AI.
- `/allys_on` riaccende e cancella eventuali pause.
- `/allys_pause 30m` mette in pausa per minuti, ore o giorni (`30m`, `2h`, `1d`).
- `/allys_status` mostra lo stato.
- `/meme_mode off|low|medium|high` regola la frequenza meme.
- `/meme_clear` svuota l'archivio GIF/video indicizzato per il gruppo.

Durante `/podcast_now` e durante i podcast programmati Allys manda un messaggio
di avanzamento e lo aggiorna mentre raccoglie messaggi, scrive lo script e crea
l'audio.
Se arrivano piu `/podcast_now` mentre un episodio e in corso, viene tenuta solo
l'ultima richiesta. Ogni chat/canale puo ricevere massimo un podcast al giorno.
Il podcast usa una voce femminile italiana e uno stile parlato tipo mini-TG,
senza markdown, asterischi o indicazioni di scena.

## Predictions separato

`/predictions` non usa piu l'Arcade vecchio di Allys: apre
`PREDICTIONS_BASE_URL` e, se `PREDICTIONS_SESSION_SECRET` e configurato,
passa una sessione firmata compatibile con il bot Predictions.

## Deploy Nginx

Il container espone `127.0.0.1:8000`. Nginx deve proxyare:

- `/telegram/webhook` verso `http://127.0.0.1:8000`
- `/api/*` verso `http://127.0.0.1:8000`
- `/health` verso `http://127.0.0.1:8000`
- `/app/*` verso `http://127.0.0.1:8000`
