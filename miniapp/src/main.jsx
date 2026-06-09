import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, RefreshCw, TrendingUp } from "lucide-react";
import "./styles.css";

const PLACE_SIZE = 1000;
const MIN_SCALE = 0.35;
const MAX_SCALE = 48;

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseInitDataFields(initData) {
  const payload = {};
  try {
    const params = new URLSearchParams(initData);
    const chatRaw = params.get("chat");
    const userRaw = params.get("user");
    if (chatRaw) {
      try {
        payload.chat = JSON.parse(chatRaw);
      } catch {
        payload.chat = null;
      }
    }
    if (userRaw) {
      try {
        payload.user = JSON.parse(userRaw);
      } catch {
        payload.user = null;
      }
    }
  } catch {
    // no-op
  }
  return payload;
}

function formatTickTime(value) {
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  } catch {
    return "";
  }
}

function initDataChatId(initData) {
  const parsed = parseInitDataFields(initData);
  const chat = parsed.chat || null;
  return String(chat?.id || chat?.chat_id || "");
}

function initDataChatType(initData) {
  return parseInitDataFields(initData).chat?.type || "";
}

function asChatId(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "0") {
    return "";
  }
  return raw;
}

function StockChart({ history }) {
  const points = useMemo(() => {
    return (history || [])
      .map((point) => ({
        price: toNumber(point.price),
        volume: toNumber(point.volume),
        risk: toNumber(point.manipulation_risk),
        at: point.created_at,
      }))
      .filter((point) => point.price > 0);
  }, [history]);

  if (!points.length) {
    return (
      <div className="finance-chart empty">
        <span>In attesa di tick aggregati dalla chat.</span>
      </div>
    );
  }

  const width = 720;
  const height = 300;
  const pad = { top: 22, right: 22, bottom: 34, left: 54 };
  const minPrice = Math.min(...points.map((point) => point.price));
  const maxPrice = Math.max(...points.map((point) => point.price));
  const spread = Math.max(maxPrice - minPrice, maxPrice * 0.02, 1);
  const low = minPrice - spread * 0.18;
  const high = maxPrice + spread * 0.18;
  const xFor = (index) => pad.left + (points.length === 1 ? 0.5 : index / (points.length - 1)) * (width - pad.left - pad.right);
  const yFor = (price) => pad.top + (1 - (price - low) / (high - low)) * (height - pad.top - pad.bottom);
  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(index).toFixed(2)} ${yFor(point.price).toFixed(2)}`).join(" ");
  const areaPath = `${linePath} L ${xFor(points.length - 1).toFixed(2)} ${height - pad.bottom} L ${xFor(0).toFixed(2)} ${height - pad.bottom} Z`;
  const last = points[points.length - 1];
  const first = points[0];
  const positive = last.price >= first.price;
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    y: pad.top + ratio * (height - pad.top - pad.bottom),
    price: high - (high - low) * ratio,
  }));

  return (
    <div className={`finance-chart ${positive ? "up" : "down"}`}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Grafico prezzo azienda">
        <defs>
          <linearGradient id="chartArea" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.24" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {grid.map((item, index) => (
          <g key={index}>
            <line className="grid-line" x1={pad.left} x2={width - pad.right} y1={item.y} y2={item.y} />
            <text className="axis-label" x={10} y={item.y + 4}>{item.price.toFixed(2)}</text>
          </g>
        ))}
        <path className="area-path" d={areaPath} />
        <path className="line-path" d={linePath} />
        {points.map((point, index) => (
          <g key={`${point.at}-${index}`}>
            <circle
              className={point.risk > 0.45 ? "scatter-point risky" : "scatter-point"}
              cx={xFor(index)}
              cy={yFor(point.price)}
              r={Math.max(3.5, Math.min(8, 3.5 + point.volume / 10))}
            />
            <title>{`${point.price.toFixed(2)} Crowns · ${formatTickTime(point.at)} · volume ${point.volume.toFixed(2)}`}</title>
          </g>
        ))}
        <text className="time-label" x={pad.left} y={height - 10}>{formatTickTime(first.at)}</text>
        <text className="time-label end" x={width - pad.right} y={height - 10}>{formatTickTime(last.at)}</text>
      </svg>
    </div>
  );
}

function SignalSummary({ latest }) {
  const signals = latest?.signals || {};
  if (!latest) return null;
  return (
    <section className="signal-panel">
      <div className="panel-title compact">
        <Activity size={18} />
        <div>
          <h2>Perche si muove</h2>
          <p>Solo dati aggregati, nessun messaggio mostrato</p>
        </div>
      </div>
      <div className="signal-grid">
        <span><strong>{Number(signals.weightedMentions || latest.volume || 0).toFixed(2)}</strong><small>hype pesato</small></span>
        <span><strong>{Number(signals.uniqueUsers || latest.unique_users || 0)}</strong><small>utenti unici</small></span>
        <span><strong>{Number(latest.sentiment || 0).toFixed(2)}</strong><small>sentiment</small></span>
        <span><strong>{Number(latest.manipulation_risk || 0).toFixed(2)}</strong><small>risk</small></span>
      </div>
      <p className="market-rule">Se il gruppo parla davvero del tema il prezzo tende a salire; spam e ripetizioni pesano poco.</p>
    </section>
  );
}

function ArcadeApp() {
  const tg = window.Telegram?.WebApp;
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const queryChatId = useMemo(() => asChatId(params.get("chat_id")), [params]);
  const session = useMemo(() => params.get("session") || "", [params]);
  const initData = tg?.initData || "";
  const initDataType = useMemo(() => initDataChatType(initData), [initData]);
  const derivedChatId = useMemo(() => initDataChatId(initData), [initData]);
  const isPrivateInit = useMemo(() => initDataType === "private", [initDataType]);
  const defaultChatId = useMemo(() => (isPrivateInit ? "" : derivedChatId), [derivedChatId, isPrivateInit]);
  const [selectedChatId, setSelectedChatId] = useState("");
  const [groupChoices, setGroupChoices] = useState([]);
  const [chatHint, setChatHint] = useState("");
  const activeChatId = useMemo(() => asChatId(queryChatId || selectedChatId || defaultChatId || derivedChatId), [defaultChatId, queryChatId, selectedChatId, derivedChatId]);
  const initialView = useMemo(() => params.get("view") || "home", [params]);
  const [view, setView] = useState(initialView);
  const [boot, setBoot] = useState(null);
  const [assets, setAssets] = useState([]);
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState([]);
  const [quantity, setQuantity] = useState("1");
  const [tradeQuote, setTradeQuote] = useState(null);
  const [stockPortfolio, setStockPortfolio] = useState(null);
  const [assetForm, setAssetForm] = useState({ symbol: "", name: "", aliases: "" });
  const [markets, setMarkets] = useState([]);
  const [positions, setPositions] = useState([]);
  const [predictionCredits, setPredictionCredits] = useState("5");
  const [newQuestion, setNewQuestion] = useState("");
  const [status, setStatus] = useState("");
  const selectedAsset = useMemo(() => assets.find((asset) => asset.symbol === selected) || null, [assets, selected]);
  const latestTick = history.length ? history[history.length - 1] : null;
  const singleGroupChoiceId = useMemo(() => {
    if (!isPrivateInit || groupChoices.length !== 1) return "";
    return String(groupChoices[0].chat_id);
  }, [groupChoices, isPrivateInit]);

  const effectiveChatId = useMemo(() => {
    if (queryChatId) return queryChatId;
    if (isPrivateInit && selectedChatId) return selectedChatId;
    if (isPrivateInit && singleGroupChoiceId) return singleGroupChoiceId;
    return activeChatId;
  }, [activeChatId, isPrivateInit, queryChatId, selectedChatId, singleGroupChoiceId]);

  function groupLabel(groupId) {
    const selected = groupChoices.find((group) => String(group.chat_id) === String(groupId));
    if (selected) {
      return selected.title || `Gruppo ${selected.chat_id}`;
    }
    if (String(groupId) === String(derivedChatId)) {
      return "Chat privata";
    }
    return String(groupId || "");
  }

  function requireGroupSelection() {
    if (!isPrivateInit || queryChatId) return false;
    return groupChoices.length > 1 && !selectedChatId;
  }

  function requireTelegram(chatForRequest = "") {
    if (!initData) {
      setStatus("Apri questa mini app da Telegram (non dal browser).");
      return false;
    }
    const chatId = asChatId(chatForRequest || effectiveChatId);
    if (!chatId) {
      setStatus(chatHint || "Scegli un gruppo dall'elenco per aprire la borsa corretta.");
      return false;
    }
    if (isPrivateInit && requireGroupSelection()) {
      setStatus("Scegli un gruppo dall'elenco per aprire la borsa corretta.");
      return false;
    }
    return true;
  }

  async function load(chatForRequest = "") {
    const requestedChatId = asChatId(chatForRequest || effectiveChatId);
    if (!requestedChatId) {
      requireTelegram();
      return;
    }
    if (!requireTelegram(requestedChatId)) return;
    const response = await fetch("/api/tma/bootstrap", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId: requestedChatId }),
    });
    const data = await response.json();
    if (!response.ok) {
      const disabled = data.detail?.code === "feature_disabled";
      setStatus(disabled ? "Arcade temporaneamente disattivato." : data.detail || "Apri questa schermata da Telegram.");
      return;
    }
    setBoot(data);
    setAssets(data.assets || []);
    setStockPortfolio(data.stockPortfolio || null);
    setMarkets(data.predictions || []);
    setSelected((data.assets || [])[0]?.symbol || null);
    await loadPositions();
  }

  async function ensurePrivateGroupChoices() {
    if (!isPrivateInit || queryChatId || !initData) return;
    const mineResponse = await fetch(`/api/groups/mine?initData=${encodeURIComponent(initData)}&session=${encodeURIComponent(session)}`, {
      method: "GET",
      headers: { "content-type": "application/json" },
    });
    if (!mineResponse.ok) return;
    const mineData = await mineResponse.json();
    const groups = mineData.groups || [];
    const visible = groups.filter((group) => String(group.chat_id) !== String(derivedChatId));
    setGroupChoices(visible);
    let nextChatId = selectedChatId || "";
    if (!nextChatId && queryChatId) {
      const found = visible.find((group) => String(group.chat_id) === String(queryChatId));
      if (found) {
        nextChatId = String(queryChatId);
      }
    }
    if (!nextChatId && visible.length === 1) {
      nextChatId = String(visible[0].chat_id);
    }
    if (nextChatId) {
      setSelectedChatId(nextChatId);
      setChatHint("");
    }
    if (!selectedChatId && visible.length > 0) {
      if (visible.length > 1) {
        setChatHint("Seleziona il gruppo da cui vuoi vedere la borsa.");
      } else {
        setChatHint("");
      }
    }
    if (visible.length === 0) {
      setChatHint("Scrivi /borsa in un gruppo dove ti vuoi collegare e riprova.");
    }
    return nextChatId;
  }

  function currentChatId() {
    return asChatId(effectiveChatId);
  }

  function selectGroup(groupId) {
    const nextChatId = asChatId(groupId);
    setSelectedChatId(nextChatId);
    setChatHint("");
    setStatus("");
    load(nextChatId);
  }

  async function trade(side) {
    if (!selected) return;
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    setStatus("Invio ordine...");
    const response = await fetch(`/api/market/${chatId}/trade`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        initData,
        session,
        chatId,
        side,
        symbol: selected,
        quantity,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Ordine rifiutato.");
      return;
    }
    setBoot((old) => ({ ...(old || {}), balance: data.portfolio?.credits || old?.balance || "0" }));
    setStockPortfolio(data.portfolio || null);
    setAssets(data.assets || assets);
    setStatus(`${side === "buy" ? "Comprato" : "Venduto"} ${data.trade.quantity} ${data.trade.symbol}. Fee ${data.trade.fee}`);
    await load();
  }

  async function quote(side) {
    if (!selected) return;
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch("/api/stocks/trade/quote", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId, side, symbol: selected, quantity }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Quote rifiutata.");
      return;
    }
    setTradeQuote(data.quote);
  }

  async function dailyClaim() {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch("/api/credits/daily-claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Claim non disponibile.");
      return;
    }
    setBoot((old) => ({ ...(old || {}), balance: data.balance }));
    setStatus(data.claimed ? "Daily claim riscattato." : "Daily claim gia usato oggi.");
  }

  async function workClaim() {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch("/api/credits/work", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Lavoro non disponibile.");
      return;
    }
    setBoot((old) => ({ ...(old || {}), balance: data.balance }));
    setStatus(data.claimed ? "+25 Crowns. Hai lavorato abbastanza." : `Riprova tra ${Math.ceil((data.retryAfter || 1) / 60)} min.`);
  }

  async function approveAsset(symbol) {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch(`/api/stocks/candidates/${symbol}/approve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Approvazione rifiutata.");
      return;
    }
    setStatus(`${data.asset.symbol} quotata.`);
    await load();
  }

  async function createAsset() {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch("/api/stocks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        initData,
        session,
        chatId,
        symbol: assetForm.symbol,
        name: assetForm.name,
        aliases: assetForm.aliases,
        status: "listed",
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Azienda non creata.");
      return;
    }
    setAssetForm({ symbol: "", name: "", aliases: "" });
    setStatus(`${data.asset.symbol} quotata.`);
    await load();
  }

  function fillAssetExample(type) {
    if (type === "minecraft") {
      setAssetForm({ symbol: "MCFT", name: "Minecraft SpA", aliases: "minecraft, blocchi, creeper, server" });
    } else if (type === "drama") {
      setAssetForm({ symbol: "", name: "Drama Holdings", aliases: "drama, litigio, caos, gossip" });
    } else {
      setAssetForm({ symbol: "", name: "Calcetto FC Group", aliases: "calcetto, partita, gol, torneo" });
    }
  }

  async function loadMarkets() {
    const chatId = currentChatId();
    if (!chatId) return;
    const response = await fetch(`/api/predictions?chat_id=${chatId}`);
    const data = await response.json();
    setMarkets(data.markets || []);
  }

  async function loadPositions() {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch(`/api/predictions/positions?chat_id=${chatId}&initData=${encodeURIComponent(initData)}&session=${encodeURIComponent(session)}`);
    if (response.ok) {
      const data = await response.json();
      setPositions(data.positions || []);
    }
  }

  async function buyPrediction(marketId, outcome) {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch(`/api/predictions/${marketId}/buy`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId, outcome, credits: predictionCredits }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Trade prediction rifiutato.");
      return;
    }
    setBoot((old) => ({ ...(old || {}), balance: data.balance }));
    setMarkets(data.markets || []);
    await loadPositions();
    setStatus(`Comprate shares ${outcome}.`);
  }

  async function cashout(position) {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch(`/api/predictions/${position.market_id}/cashout`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId, outcome: position.outcome, shares: position.shares }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Cash-out rifiutato.");
      return;
    }
    setBoot((old) => ({ ...(old || {}), balance: data.balance }));
    setMarkets(data.markets || []);
    setPositions(data.positions || []);
    setStatus("Cash-out completato.");
  }

  async function createPrediction() {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch("/api/predictions/create", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId, question: newQuestion, scope: "local" }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Mercato non creato.");
      return;
    }
    setNewQuestion("");
    await loadMarkets();
    setStatus(`Mercato #${data.market.id} creato.`);
  }

  async function resolvePrediction(marketId, outcome) {
    if (!requireTelegram()) return;
    const chatId = currentChatId();
    const response = await fetch(`/api/predictions/${marketId}/resolve`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ initData, session, chatId, outcome }),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.detail || "Risoluzione rifiutata.");
      return;
    }
    await loadMarkets();
    await loadPositions();
    setStatus(`Mercato risolto: ${outcome}.`);
  }

  useEffect(() => {
    tg?.ready();
    tg?.expand?.();
    (async () => {
      let selectedFromMine = "";
      if (isPrivateInit) {
        selectedFromMine = (await ensurePrivateGroupChoices()) || "";
      }
      if (selectedFromMine || currentChatId()) {
        await load(selectedFromMine || undefined);
      }
    })();
  }, [activeChatId, initData, queryChatId]);

  useEffect(() => {
    if (!selected) return;
    const chatId = currentChatId();
    if (!chatId || chatId === "0") return;
    fetch(`/api/stocks/${selected}/history?chat_id=${chatId}&range=24h`)
      .then((r) => r.json())
      .then((d) => setHistory(d.history || []));
  }, [selected, activeChatId]);

  useEffect(() => {
    const chatId = currentChatId();
    if (!requireTelegram(chatId) || !chatId) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${location.host}/api/arcade/ws?chat_id=${chatId}&initData=${encodeURIComponent(initData)}&session=${encodeURIComponent(session)}`);
    ws.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "balance") setBoot((old) => ({ ...(old || {}), balance: event.balance }));
      if (event.type === "stock_ticks" || event.type === "stock_trade") load().catch(() => {});
    };
    return () => ws.close();
  }, [activeChatId, initData, session]);

  return (
    <main className="arcade-app">
      <header className="arcade-header">
        <div>
          <p>Chat With Allys</p>
          <h1>Allys Arcade</h1>
        </div>
        <strong>{Number(boot?.balance || 0).toFixed(2)} Crowns</strong>
        <button onClick={load} aria-label="Aggiorna">
          <RefreshCw size={18} />
        </button>
      </header>

      <nav className="arcade-tabs">
        {["home", "market", "predictions", "place", "rank"].map((item) => (
          <button key={item} className={view === item ? "active" : ""} onClick={() => setView(item)}>
            {item === "home" ? "Hub" : item === "market" ? "Borsa" : item === "rank" ? "Rank" : item === "place" ? "Place" : "Predictions"}
          </button>
        ))}
      </nav>
      {isPrivateInit ? (
        <section className="group-selector">
          {chatHint || (effectiveChatId ? `Gruppo attivo: ${groupLabel(effectiveChatId)}` : "Nessun gruppo selezionato")}
          {groupChoices.length > 1 ? (
            <div className="group-picker">
              {groupChoices.map((group) => {
                const groupId = String(group.chat_id);
                const isActive = String(groupId) === String(effectiveChatId);
                return (
                  <button key={groupId} className={isActive ? "active" : ""} onClick={() => selectGroup(groupId)}>
                    {groupLabel(groupId)}
                  </button>
                );
              })}
            </div>
          ) : null}
        </section>
      ) : null}

      {view === "home" ? (
        <section className="arcade-grid">
          <button onClick={dailyClaim}>
            <strong>Daily Claim</strong>
            <span>+100 Crowns ogni 24 ore</span>
          </button>
          <button onClick={workClaim}>
            <strong>Lavora</strong>
            <span>+25 Crowns ogni ora</span>
          </button>
          <button onClick={() => setView("predictions")}>
            <strong>Predictions</strong>
            <span>Mercati YES/NO</span>
          </button>
          <button onClick={() => setView("market")}>
            <strong>Borsa</strong>
            <span>Aziende nate dalla chat</span>
          </button>
          <button onClick={() => (location.href = `/app/place`)}>
            <strong>Pixel Canvas</strong>
            <span>r/place Minecraft</span>
          </button>
        </section>
      ) : null}

      {view === "market" ? (
        <section className="stock-dashboard">
          <aside className="stock-list">
            <div className="stock-list-head">
              <h2>Aziende</h2>
              <button onClick={load}><RefreshCw size={16} /></button>
            </div>
            {assets.map((asset) => (
              <button
                key={asset.symbol}
                className={asset.symbol === selected ? "active" : ""}
                onClick={() => setSelected(asset.symbol)}
              >
                <div>
                  <strong>{asset.symbol}</strong>
                  <small>{asset.name}</small>
                </div>
                <span>{asset.status === "listed" ? Number(asset.price).toFixed(2) : asset.status}</span>
              </button>
            ))}
          </aside>

          <section className="stock-detail">
            <div className="panel-title">
              <TrendingUp size={20} />
              <div>
                <h2>{selected || "Nessun asset"}</h2>
                <p>{selectedAsset?.name || "Seleziona una azienda"}</p>
              </div>
            </div>
            {selectedAsset ? (
              <div className="quote-strip">
                <strong>{Number(selectedAsset.price || 0).toFixed(2)} Crowns</strong>
                <span>Vol {Number(selectedAsset.volume || 0).toFixed(1)}</span>
                <span>Risk {Number(selectedAsset.manipulation_risk || 0).toFixed(2)}</span>
              </div>
            ) : null}
            <StockChart history={history} />
            <SignalSummary latest={latestTick} />

            <section className="trade">
              <input aria-label="Quantita" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
              <button onClick={() => quote("buy")}>Quote compra</button>
              <button onClick={() => quote("sell")}>Quote vendi</button>
            </section>
            {tradeQuote ? (
              <div className="quote-box">
                <span>{tradeQuote.side === "buy" ? "Compra" : "Vendi"} {tradeQuote.quantity} {tradeQuote.symbol}</span>
                <strong>Totale {Number(tradeQuote.total).toFixed(2)} · fee {Number(tradeQuote.fee).toFixed(2)}</strong>
                <button onClick={() => trade(tradeQuote.side)}>Conferma</button>
              </div>
            ) : null}

            <section className="holdings">
              <h2>Portfolio</h2>
              {(stockPortfolio?.holdings || []).length ? stockPortfolio.holdings.map((item) => (
                <p key={item.symbol}>{item.symbol}: {Number(item.quantity).toFixed(2)} · valore {Number(item.market_value).toFixed(2)} · PnL {Number(item.pnl).toFixed(2)}</p>
              )) : <p>Nessuna posizione.</p>}
            </section>

            <section className="admin-assets">
              <h2>Admin aziende</h2>
              <p>Scrivi nome e keyword: il simbolo puo generarlo Allys.</p>
              {assets.filter((asset) => asset.status === "candidate").map((asset) => (
                <button key={asset.symbol} onClick={() => approveAsset(asset.symbol)}>Approva {asset.symbol}</button>
              ))}
              <div className="template-row compact">
                <button onClick={() => fillAssetExample("minecraft")}>Minecraft</button>
                <button onClick={() => fillAssetExample("drama")}>Drama</button>
                <button onClick={() => fillAssetExample("sport")}>Calcetto</button>
              </div>
              <div className="trade">
                <input value={assetForm.symbol} onChange={(event) => setAssetForm({ ...assetForm, symbol: event.target.value })} placeholder="SYMBOL opzionale" />
                <input value={assetForm.name} onChange={(event) => setAssetForm({ ...assetForm, name: event.target.value })} placeholder="Nome azienda" />
                <input value={assetForm.aliases} onChange={(event) => setAssetForm({ ...assetForm, aliases: event.target.value })} placeholder="keyword: minecraft, blocchi" />
                <button onClick={createAsset}>Quota</button>
              </div>
            </section>
          </section>
        </section>
      ) : null}

      {view === "predictions" ? (
        <section className="predictions">
          <div className="trade">
            <input value={newQuestion} onChange={(event) => setNewQuestion(event.target.value)} placeholder="Nuovo mercato admin" />
            <button onClick={createPrediction}>Crea</button>
          </div>
          <div className="trade">
            <input inputMode="decimal" value={predictionCredits} onChange={(event) => setPredictionCredits(event.target.value)} />
            <span>Credits per trade</span>
          </div>
          {markets.map((item) => (
            <article key={item.id} className="market-card">
              <p>#{item.id} {item.scope} | {item.status}</p>
              <h2>{item.question}</h2>
              <div className="market-odds">
                <button disabled={item.status !== "open"} onClick={() => buyPrediction(item.id, "YES")}>YES {Number(item.yes_pool).toFixed(1)}</button>
                <button disabled={item.status !== "open"} onClick={() => buyPrediction(item.id, "NO")}>NO {Number(item.no_pool).toFixed(1)}</button>
              </div>
              {item.status === "open" ? (
                <div className="market-resolve">
                  <button onClick={() => resolvePrediction(item.id, "YES")}>Resolve YES</button>
                  <button onClick={() => resolvePrediction(item.id, "NO")}>Resolve NO</button>
                  <button onClick={() => resolvePrediction(item.id, "CANCEL")}>Cancel</button>
                </div>
              ) : null}
            </article>
          ))}
          {positions.length ? <h2>Posizioni</h2> : null}
          {positions.map((position) => (
            <article key={`${position.market_id}-${position.outcome}`} className="position-row">
              <span>#{position.market_id} {position.outcome}</span>
              <strong>{Number(position.shares).toFixed(2)} shares</strong>
              <button onClick={() => cashout(position)}>Cash-out</button>
            </article>
          ))}
        </section>
      ) : null}

      {view === "place" ? (
        <section className="arcade-grid">
          <button onClick={() => (location.href = `/app/place`)}>
            <strong>Apri Pixel Canvas</strong>
            <span>Mini App fullscreen zoomabile</span>
          </button>
        </section>
      ) : null}

      {view === "rank" ? (
        <section className="leaderboard">
          <h2>Classifica locale</h2>
          {(boot?.leaderboard?.local || []).map((row, index) => (
            <p key={`${row.user_id}-${index}`}><strong>#{index + 1}</strong> {row.username ? `@${row.username}` : row.display_name || row.user_id} · {Number(row.credits_balance).toFixed(2)}</p>
          ))}
          <h2>Classifica globale</h2>
          {(boot?.leaderboard?.global || []).map((row, index) => (
            <p key={`${row.user_id}-g-${index}`}><strong>#{index + 1}</strong> {row.username ? `@${row.username}` : row.display_name || row.user_id} · {Number(row.credits_balance).toFixed(2)}</p>
          ))}
        </section>
      ) : null}

      {status ? <p className="status">{status}</p> : null}
    </main>
  );
}
function PlaceApp() {
  const tg = window.Telegram?.WebApp;
  const session = useMemo(() => new URLSearchParams(location.search).get("session") || "", []);
  const canvasRef = useRef(null);
  const pixelsRef = useRef(new Uint8Array(PLACE_SIZE * PLACE_SIZE));
  const paletteRef = useRef([]);
  const imageRef = useRef(null);
  const bitmapRef = useRef(null);
  const wsRef = useRef(null);
  const lastSeqRef = useRef(0);
  const transformRef = useRef({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef({ active: false, moved: false, x: 0, y: 0 });
  const pointersRef = useRef(new Map());
  const pinchRef = useRef(null);
  const inspectRef = useRef({ key: "", until: 0 });
  const ownerCacheRef = useRef(new Map());
  const [meta, setMeta] = useState(null);
  const [selectedColor, setSelectedColor] = useState(10);
  const [coords, setCoords] = useState(null);
  const [tooltip, setTooltip] = useState(null);
  const [connection, setConnection] = useState("Connessione...");
  const [notice, setNotice] = useState(session ? "Carico canvas..." : "Solo visualizzazione: apri dal comando /place.");
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [cooldownLeft, setCooldownLeft] = useState(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const palette = paletteRef.current;
    if (!canvas || palette.length === 0) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    const dpr = window.devicePixelRatio || 1;
    const width = Math.floor(canvas.clientWidth * dpr);
    const height = Math.floor(canvas.clientHeight * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    if (!imageRef.current) {
      const image = new ImageData(PLACE_SIZE, PLACE_SIZE);
      const pixels = pixelsRef.current;
      for (let i = 0; i < pixels.length; i += 1) {
        const color = palette[pixels[i]] || palette[0];
        const offset = i * 4;
        image.data[offset] = color.r;
        image.data[offset + 1] = color.g;
        image.data[offset + 2] = color.b;
        image.data[offset + 3] = 255;
      }
      imageRef.current = image;
      const bitmap = document.createElement("canvas");
      bitmap.width = PLACE_SIZE;
      bitmap.height = PLACE_SIZE;
      bitmap.getContext("2d", { alpha: false }).putImageData(image, 0, 0);
      bitmapRef.current = bitmap;
    }
    const view = transformRef.current;
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#12091f";
    ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.translate(view.x * dpr, view.y * dpr);
    ctx.scale(view.scale * dpr, view.scale * dpr);
    ctx.drawImage(bitmapRef.current, 0, 0);
    ctx.restore();
  }, []);

  const applyEvent = useCallback(
    (event) => {
      if (!event || event.seq <= lastSeqRef.current) return;
      const index = event.y * PLACE_SIZE + event.x;
      pixelsRef.current[index] = event.colorId;
      const image = imageRef.current;
      const color = paletteRef.current[event.colorId];
      if (image && color) {
        const offset = index * 4;
        image.data[offset] = color.r;
        image.data[offset + 1] = color.g;
        image.data[offset + 2] = color.b;
        image.data[offset + 3] = 255;
        bitmapRef.current?.getContext("2d", { alpha: false }).putImageData(image, 0, 0);
      }
      lastSeqRef.current = event.seq;
      requestAnimationFrame(draw);
    },
    [draw],
  );

  const fetchCatchup = useCallback(async () => {
    const response = await fetch(`/api/place/events?after=${lastSeqRef.current}`);
    const data = await response.json();
    for (const event of data.events || []) applyEvent(event);
  }, [applyEvent]);

  const connectSocket = useCallback(() => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${location.host}/api/place/ws${session ? `?session=${encodeURIComponent(session)}` : ""}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onopen = async () => {
      setConnection("Live");
      setNotice(session ? "Pronto." : "Viewer-only.");
      await fetchCatchup();
    };
    ws.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "update") applyEvent(payload.event);
      if (payload.type === "ack") {
        applyEvent(payload.event);
        setCooldownUntil(Date.now() + (payload.cooldownSeconds || 30) * 1000);
        setNotice("Pixel piazzato.");
      }
      if (payload.type === "error") {
        if (payload.code === "cooldown") setCooldownUntil(Date.now() + (payload.retryAfter || 1) * 1000);
        setNotice(payload.message || payload.code || "Errore.");
      }
    };
    ws.onclose = () => {
      setConnection("Riconnessione...");
      setTimeout(connectSocket, 1600);
    };
    ws.onerror = () => setConnection("Instabile");
  }, [applyEvent, fetchCatchup, session]);

  useEffect(() => {
    tg?.ready();
    tg?.expand?.();
    tg?.disableVerticalSwipes?.();
    tg?.setHeaderColor?.("#12091f");
    tg?.setBackgroundColor?.("#12091f");
    let mounted = true;
    async function load() {
      const metaResponse = await fetch("/api/place/meta");
      const metaData = await metaResponse.json();
      const rgb = metaData.palette.map((color) => hexToRgb(color.hex));
      paletteRef.current = rgb;
      setMeta(metaData);
      lastSeqRef.current = metaData.lastSeq || 0;

      const snapshot = await fetch("/api/place/snapshot");
      const data = new Uint8Array(await snapshot.arrayBuffer());
      if (!mounted) return;
      pixelsRef.current = data;
      imageRef.current = null;
      bitmapRef.current = null;
      fitCanvas();
      requestAnimationFrame(draw);
      connectSocket();
    }
    load().catch(() => {
      setConnection("Errore");
      setNotice("Place temporaneamente disattivato o non disponibile.");
    });
    return () => {
      mounted = false;
      wsRef.current?.close();
    };
  }, [connectSocket, draw, tg]);

  useEffect(() => {
    const timer = setInterval(() => {
      setCooldownLeft(Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000)));
    }, 250);
    return () => clearInterval(timer);
  }, [cooldownUntil]);

  useEffect(() => {
    const onResize = () => requestAnimationFrame(draw);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw]);

  function fitCanvas() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const scale = Math.max(MIN_SCALE, Math.min(canvas.clientWidth, canvas.clientHeight) / PLACE_SIZE);
    transformRef.current = {
      scale,
      x: (canvas.clientWidth - PLACE_SIZE * scale) / 2,
      y: (canvas.clientHeight - PLACE_SIZE * scale) / 2,
    };
  }

  function screenToPixel(clientX, clientY) {
    const rect = canvasRef.current.getBoundingClientRect();
    const view = transformRef.current;
    const x = Math.floor((clientX - rect.left - view.x) / view.scale);
    const y = Math.floor((clientY - rect.top - view.y) / view.scale);
    if (x < 0 || x >= PLACE_SIZE || y < 0 || y >= PLACE_SIZE) return null;
    return { x, y };
  }

  function pointerDown(event) {
    event.preventDefault();
    canvasRef.current.setPointerCapture(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size >= 2) {
      beginPinch();
      dragRef.current = { active: false, moved: true, x: event.clientX, y: event.clientY };
      return;
    }
    dragRef.current = { active: true, moved: false, x: event.clientX, y: event.clientY };
  }

  function pointerMove(event) {
    event.preventDefault();
    if (pointersRef.current.has(event.pointerId)) {
      pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    const pixel = screenToPixel(event.clientX, event.clientY);
    setCoords(pixel);
    if (pixel) {
      const colorId = pixelsRef.current[pixel.y * PLACE_SIZE + pixel.x];
      if (colorId > 0) {
        setTooltip({
          x: Math.min(event.clientX + 12, window.innerWidth - 150),
          y: Math.max(event.clientY - 40, 12),
          text: ownerCacheRef.current.get(`${pixel.x}:${pixel.y}`) || "Pixel colorato",
        });
      } else {
        setTooltip(null);
      }
    } else {
      setTooltip(null);
    }
    if (pointersRef.current.size >= 2) {
      updatePinch();
      return;
    }
    if (!dragRef.current.active) return;
    const dx = event.clientX - dragRef.current.x;
    const dy = event.clientY - dragRef.current.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) dragRef.current.moved = true;
    transformRef.current.x += dx;
    transformRef.current.y += dy;
    dragRef.current.x = event.clientX;
    dragRef.current.y = event.clientY;
    requestAnimationFrame(draw);
  }

  function pointerUp(event) {
    event.preventDefault();
    try {
      canvasRef.current.releasePointerCapture(event.pointerId);
    } catch {
      // Pointer capture can already be gone after Telegram view interruptions.
    }
    const hadMultiplePointers = pointersRef.current.size >= 2;
    const wasDrag = dragRef.current.moved;
    pointersRef.current.delete(event.pointerId);
    if (hadMultiplePointers) {
      pinchRef.current = null;
      const remaining = [...pointersRef.current.values()][0];
      if (remaining) {
        dragRef.current = { active: true, moved: true, x: remaining.x, y: remaining.y };
      }
      return;
    }
    dragRef.current.active = false;
    if (!wasDrag) {
      const pixel = screenToPixel(event.clientX, event.clientY);
      if (pixel) placePixel(pixel.x, pixel.y);
    }
  }

  function pointerCancel(event) {
    pointersRef.current.delete(event.pointerId);
    pinchRef.current = null;
    dragRef.current = { active: false, moved: true, x: 0, y: 0 };
  }

  function wheel(event) {
    event.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const view = transformRef.current;
    const beforeX = (event.clientX - rect.left - view.x) / view.scale;
    const beforeY = (event.clientY - rect.top - view.y) / view.scale;
    const factor = event.deltaY < 0 ? 1.22 : 0.82;
    const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, view.scale * factor));
    view.x = event.clientX - rect.left - beforeX * nextScale;
    view.y = event.clientY - rect.top - beforeY * nextScale;
    view.scale = nextScale;
    requestAnimationFrame(draw);
  }

  function beginPinch() {
    const points = [...pointersRef.current.values()].slice(0, 2);
    if (points.length < 2) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const center = midpoint(points[0], points[1]);
    const view = transformRef.current;
    pinchRef.current = {
      distance: distance(points[0], points[1]),
      scale: view.scale,
      canvasX: (center.x - rect.left - view.x) / view.scale,
      canvasY: (center.y - rect.top - view.y) / view.scale,
    };
  }

  function updatePinch() {
    const start = pinchRef.current;
    const points = [...pointersRef.current.values()].slice(0, 2);
    if (!start || points.length < 2) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const center = midpoint(points[0], points[1]);
    const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, start.scale * (distance(points[0], points[1]) / start.distance)));
    transformRef.current.scale = nextScale;
    transformRef.current.x = center.x - rect.left - start.canvasX * nextScale;
    transformRef.current.y = center.y - rect.top - start.canvasY * nextScale;
    dragRef.current.moved = true;
    requestAnimationFrame(draw);
  }

  async function placePixel(x, y) {
    const colorAtPixel = pixelsRef.current[y * PLACE_SIZE + x];
    const key = `${x}:${y}`;
    if (colorAtPixel > 0 && (inspectRef.current.key !== key || Date.now() > inspectRef.current.until)) {
      await inspectPixel(x, y);
      inspectRef.current = { key, until: Date.now() + 2600 };
      return;
    }
    if (!session) {
      setNotice("Viewer-only: usa /place su Telegram.");
      return;
    }
    if (cooldownLeft > 0) {
      setNotice(`Cooldown: ${cooldownLeft}s.`);
      return;
    }
    const payload = { type: "place", session, x, y, colorId: selectedColor };
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
      return;
    }
    const response = await fetch("/api/place/pixels", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      if (data.code === "cooldown") setCooldownUntil(Date.now() + (data.retryAfter || 1) * 1000);
      setNotice(data.message || "Pixel rifiutato.");
      return;
    }
    applyEvent(data.event);
    setCooldownUntil(Date.now() + (data.cooldownSeconds || 30) * 1000);
    setNotice("Pixel piazzato.");
  }

  async function inspectPixel(x, y) {
    try {
      const response = await fetch(`/api/place/pixels/${x}/${y}`);
      const data = await response.json();
      const placedBy = data.placedBy || "sconosciuto";
      ownerCacheRef.current.set(`${x}:${y}`, placedBy);
      setNotice(`Pixel ${x}, ${y} piazzato da ${placedBy}. Tocca di nuovo per sovrascrivere.`);
      setTooltip({ x: 16, y: 64, text: placedBy });
    } catch {
      setNotice("Non riesco a leggere l'autore del pixel.");
    }
  }

  return (
    <main className="place-app">
      <canvas
        ref={canvasRef}
        aria-label="Minecraft Place canvas"
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={pointerCancel}
        onWheel={wheel}
      />
      <div className="place-topbar">
        <strong>Minecraft Place</strong>
        <span>{connection}</span>
        <span>{coords ? `${coords.x}, ${coords.y}` : "1000 x 1000"}</span>
        <span>{cooldownLeft > 0 ? `${cooldownLeft}s` : "Pronto"}</span>
      </div>
      <div className="place-palette">
        {(meta?.palette || []).map((color) => (
          <button
            key={color.id}
            className={selectedColor === color.id ? "selected" : ""}
            title={color.name}
            aria-label={color.name}
            onClick={() => setSelectedColor(color.id)}
            style={{ backgroundColor: color.hex }}
          />
        ))}
      </div>
      {tooltip ? (
        <div className="place-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          {tooltip.text}
        </div>
      ) : null}
      <div className="place-notice">{notice}</div>
    </main>
  );
}

function hexToRgb(hex) {
  const clean = hex.replace("#", "");
  return {
    r: parseInt(clean.slice(0, 2), 16),
    g: parseInt(clean.slice(2, 4), 16),
    b: parseInt(clean.slice(4, 6), 16),
  };
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function midpoint(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

const RootApp = location.pathname.startsWith("/app/place") ? PlaceApp : ArcadeApp;
createRoot(document.getElementById("root")).render(<RootApp />);
