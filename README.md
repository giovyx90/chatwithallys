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
- `/recap` riassunto AI di cosa si e detto in chat ("cosa mi sono persa").
- `/mood` umore del gruppo letto dal sentiment dei messaggi recenti.
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

## Farla tacere: lo puo' fare chiunque

Non serve essere admin. Questi comandi valgono per tutti i membri:

- `/zitta` (o `/zitto`, `/silenzio`, `/muta`) la mette in silenzio 30 minuti.
- `/zitta 2h` sceglie la durata (`30m`, `2h`, `1d`, massimo 7 giorni).
- `/parla` la riaccende subito.
- Funziona anche a parole: "Allys stai zitta" o un reply con "smettila" hanno lo
  stesso effetto del comando.

Il silenzio chiesto dai membri e' distinto da `/allys_off` e `/allys_pause` degli
admin: `/parla` toglie il primo, non spegne il secondo. E ogni "zitta" vale anche
come voto negativo sull'ultima cosa che ha detto (vedi sotto).

## Come impara dal gruppo

Il modello locale non viene riaddestrato: quello che cambia e' cosa gli mettiamo
davanti a ogni risposta. Allys raccoglie i voti che il gruppo le da' senza
accorgersene e li usa per costruirsi lo stile.

- **Reaction**: un 😂 o un 🔥 su una sua risposta valgono positivo, un 💩 o un
  🥱 negativo. Telegram manda le reaction ai bot **solo se il bot e' admin del
  gruppo** e solo con `message_reaction` fra gli `allowed_updates` (viene
  registrato da solo all'avvio, vedi `TELEGRAM_AUTO_WEBHOOK`). Se Allys non e'
  admin non si rompe niente: resta il segnale delle risposte, che funziona
  sempre.
- **Risposte**: se qualcuno replica a un suo messaggio e' gia' un segnale
  positivo, pesato con il sentiment della replica. "Stai zitta" lo ribalta.
- **Esempi di stile**: le risposte con punteggio piu' alto tornano nel prompt
  come esempi few-shot. Allys imita il taglio di quello che in *quel* gruppo ha
  gia' funzionato, non un tono generico.
- **Lessico**: i tormentoni ricorrenti (parole e coppie di parole usate da almeno
  due persone diverse) finiscono nel prompt. Chi spamma la stessa parola da solo
  non conta.
- **Taratura**: se il gradimento medio scende, Allys accorcia le risposte e vira
  sull'utile; se sale, si allunga un po'. `/autonomia` senza argomenti mostra il
  gradimento corrente.

## Parlare da sola

Ogni 4 minuti Allys guarda le chat vive e decide se intromettersi. Perche' parli
servono *tutte* queste condizioni: la conversazione e' attiva (ultimo messaggio
umano entro 8 minuti), lei tace da abbastanza, ci sono abbastanza messaggi nuovi
dall'ultima volta, la quota giornaliera non e' finita e non e' stata zittita.

`/autonomia off|bassa|media|alta` (solo admin) regola la soglia:

| livello | pausa minima | max al giorno | messaggi nuovi richiesti |
|---------|--------------|---------------|--------------------------|
| off     | -            | 0             | -                        |
| bassa   | 3 ore        | 2             | 40                       |
| media   | 75 minuti    | 5             | 18                       |
| alta    | 35 minuti    | 10            | 10                       |

Il default e' `media`. Se in quel momento la borsa si e' mossa parecchio, il suo
intervento diventa un commento di borsa con l'immagine del listino allegata.

## Borsa: immagini, non mini app

I prezzi si vedono scorrendo la chat, senza aprire niente:

- `/borsa` nel gruppo manda il listino come PNG (una riga per azienda con
  sparkline, prezzo e variazione 24h). La mini app resta come pulsante per chi la
  vuole.
- `/prezzo SYMBOL` manda la scheda del titolo: prezzo grande, grafico del
  periodo, menzioni, persone coinvolte, volume e rischio spam.
- `/portfolio` manda la card delle posizioni con liquidita', valore e PnL.
- `/compra` e `/vendi` rispondono con la scheda aggiornata del titolo mosso.
- Ogni sera a `MARKET_REPORT_TIME` (default 21:00) arriva la chiusura di
  giornata: listino illustrato piu' una riga di commento scritta da Allys.

Le immagini sono generate con Pillow dentro al container (nessuna GPU, nessun
browser headless) e disegnate a 2x per avere i bordi lisci. Servono i font
DejaVu, gia' inclusi nel `Dockerfile` (`fonts-dejavu-core`).

Allys risponde quando un messaggio contiene `Allys`, quando qualcuno fa reply a
un suo messaggio e, ogni tanto, di sua iniziativa (vedi "Parlare da sola").
Quando risponde tiene conto del filo della conversazione (gli ultimi messaggi,
con i nomi di chi parla) e dell'umore del gruppo, e adatta il tono: piu utile e
calorosa se l'aria e tesa, piu pungente se il gruppo e su di giri. La scelta
roast/utile non e piu casuale ma dipende dall'intento del messaggio (domande e
richieste di aiuto ottengono sempre una risposta concreta) e dal `roast_level`
del gruppo. Le risposte AI sono tenute brevi.

### Nomi veri, non "utente A"

Il modello vede i nomi Telegram di chi parla, e si rivolge alle persone per nome.
E' il default perche' in una chat tra amici l'anonimato costa ogni battuta che
abbia bisogno di sapere *chi* ha detto una cosa. I nomi arrivano da `group_users`,
quindi funzionano anche sui messaggi gia' archiviati; chi non ha un nome Telegram
compare con lo `@username`, e chi non ha nemmeno quello resta "utente A".

Restano fermi i guardrail che contano: niente fatti inventati sulle persone,
niente dati privati (numeri, indirizzi, lavoro, relazioni) anche se sono passati
in chat, niente attacchi personali gravi.

Con `ANONYMIZE_SPEAKERS=true` si torna agli alias "utente A/B/C" e gli
`@username` vengono sostituiti con `@/`: serve se metti Allys in un gruppo grosso
dove non si conoscono tutti.
Se in un thread appena avviato con Allys arriva un follow-up (una domanda), puo
intervenire anche senza essere chiamata per nome, con finestra e cooldown
anti-spam. Ricorda anche le proprie risposte, quindi la conversazione le "torna"
come un vero botta-e-risposta.
Ogni tanto, invece di rispondere, reagisce a un messaggio con un'emoji coerente
con l'umore (usa lo stesso sentiment della borsa).
Costruisce nel tempo un profilo breve di ogni membro (interessi, tono,
tormentoni; mai nomi o dati sensibili) e lo usa come contesto interno.

### Media/meme che funzionano

Ogni tanto, se ha senso, Allys accompagna la risposta con GIF/video gia mandati
nel gruppo (riuso affidabile via `file_id`). Come fallback cerca un meme reale
online e **valida l'URL prima di inviarlo** (status, tipo e dimensione), cosi non
compare piu "media non disponibile": se nulla e valido ripiega sul testo.
Sorgenti: Giphy o Tenor se imposti `GIPHY_API_KEY` / `TENOR_API_KEY` (meme
pertinenti alla conversazione), altrimenti fallback keyless su Reddit
(`MEME_REDDIT_FALLBACK=true`). Con `/meme_test testo` gli admin vedono sia i
media del gruppo sia l'URL online validato.
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

## Due cervelli: VPS sempre acceso, GPU quando c'e'

La VPS non ha GPU: sei vCPU e circa 6 GB di RAM libera, quindi `qwen3:8b` gira
ma risponde in decine di secondi. Il PC di Giovyx ha una RTX 5050 da 8 GB, dove
lo stesso modello va una decina di volte piu' veloce.

Allys usa la GPU quando la trova e la VPS quando non c'e', senza che nessuno nel
gruppo debba fare niente. Il default resta la VPS: se il PC e' spento non cambia
nulla, solo la velocita'.

| Variabile | Cosa fa |
| --- | --- |
| `OLLAMA_GPU_BASE_URL` | Dove sta la GPU. Vuoto = solo VPS. |
| `OLLAMA_GPU_CHAT_MODEL` | Modello da usare sulla GPU (di solito piu' grosso). |
| `OLLAMA_GPU_PROBE_SECONDS` | Ogni quanto ricontrolla se il PC e' acceso (30s). |
| `OLLAMA_GPU_PREDICT_SCALE` | Quanto puo' allungarsi la risposta sulla GPU (1.6x). |

Gli embedding restano **sempre** sulla VPS: cambiare backend a meta' strada
significherebbe due spazi vettoriali diversi dentro la stessa collezione Qdrant.

`/allys_status` dice quale cervello sta rispondendo in quel momento.

### Collegare il PC

Sulla VPS, una riga in `/etc/ssh/sshd_config` (poi `systemctl reload ssh`):

```
GatewayPorts clientspecified
```

Nel `.env`:

```
OLLAMA_GPU_BASE_URL=http://172.17.0.1:11435
```

Sul PC:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
cp scripts/gpu-tunnel.sh ~/.local/share/allys/gpu-tunnel.sh
cp scripts/allys-gpu-tunnel.service ~/.config/systemd/user/
systemctl --user enable --now allys-gpu-tunnel
loginctl enable-linger $USER
```

Il tunnel si appoggia alla `docker0` della VPS (172.17.0.1), quindi la porta e'
raggiungibile dai container ma non da internet. Quando il PC si spegne la porta
sparisce, la sonda fallisce in pochi millisecondi e Allys torna sulla VPS.

### Quando il cervello e' giu'

Se Ollama non risponde, Allys manda **una** scusa per chat e poi tace, e la
rimanda solo dopo che e' tornata a rispondere davvero. Scusarsi a ogni messaggio
in un canale attivo e' spam peggiore del silenzio, ed e' esattamente quello che
succedeva quando la VPS andava in timeout.

`OLLAMA_TIMEOUT_SECONDS` (90 di default) taglia le richieste che non arriveranno
mai: una risposta dopo tre minuti non serve a nessuno e nel frattempo le
richieste si accumulano.

### Il modello della VPS va tenuto piccolo

Non e' un consiglio estetico. La VPS ha circa 6 GB di RAM libera e sopra ci
girano server di gioco, database e una quindicina di siti: caricare `qwen3:8b`
(5.2 GB) porta la macchina a grattare swap finche' *nessun* processo userspace
risponde piu', sshd compreso. La macchina resta pingabile e accetta le
connessioni TCP, ma non risponde a niente.

Quindi con la GPU collegata: `OLLAMA_CHAT_MODEL=qwen3:4b`, che occupa 2.6 GB e
risponde in circa meta' tempo. La qualita' cala poco per quattro battute in un
gruppo di amici, e quando il PC e' acceso il modello grosso ce l'hai comunque.

Il servizio `ollama` ha anche un `mem_limit: 5g` nel compose: se qualcuno prova a
caricare un modello troppo grosso muore il container, non la VPS.

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
