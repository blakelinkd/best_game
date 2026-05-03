const CONFIG = {
  maxMessages: 18,
  soundEnabled: true,
  soundVolume: 0.55,
  soundCooldownMs: 450,
  reconnectBaseMs: 1200,
  reconnectMaxMs: 15000,
  showConnectionStatus: true,
  statsPollMs: 20000,
  highlightMentionsOf: []
};

const chatEl = document.getElementById("chat");
const viewerCountEl = document.getElementById("viewerCount");
const chatterCountEl = document.getElementById("chatterCount");
const colorPool = [
  "#8be9fd",
  "#50fa7b",
  "#ff79c6",
  "#bd93f9",
  "#f1fa8c",
  "#ffb86c",
  "#ff5555"
];

let appConfig = null;
let session = null;
let broadcasterId = "";
let ws = null;
let reconnectAttempt = 0;
let lastSoundAt = 0;
let audioCtx = null;
const seenIds = new Set();

init();

async function init() {
  appConfig = await getJson("/api/config");
  CONFIG.highlightMentionsOf = [appConfig.channel];
  session = await getJson("/api/session");
  connectChat();
  refreshStats();
  setInterval(refreshStats, CONFIG.statsPollMs);
}

function connectChat() {
  ws = new WebSocket("wss://irc-ws.chat.twitch.tv:443");

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    const nick = "justinfan" + Math.floor(10000 + Math.random() * 89999);
    send("CAP REQ :twitch.tv/tags twitch.tv/commands");
    send("PASS SCHMOOPIIE");
    send("NICK " + nick);
    send("JOIN #" + appConfig.channel.toLowerCase());
    status("Connected to #" + appConfig.channel);
  });

  ws.addEventListener("message", (event) => {
    String(event.data).split("\r\n").forEach(handleLine);
  });

  ws.addEventListener("close", scheduleReconnect);
  ws.addEventListener("error", () => {
    status("Chat connection error");
    try {
      ws.close();
    } catch (error) {
      scheduleReconnect();
    }
  });
}

function scheduleReconnect() {
  const delay = Math.min(
    CONFIG.reconnectMaxMs,
    CONFIG.reconnectBaseMs * Math.pow(1.7, reconnectAttempt++)
  );
  status("Reconnecting in " + Math.round(delay / 1000) + "s");
  setTimeout(connectChat, delay);
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(message + "\r\n");
  }
}

function handleLine(line) {
  if (!line) {
    return;
  }

  if (line.startsWith("PING")) {
    send("PONG :tmi.twitch.tv");
    return;
  }

  const parsed = parseIrc(line);
  if (!parsed || parsed.command !== "PRIVMSG") {
    return;
  }

  const stableId = parsed.tags["source-id"] || parsed.tags.id || line;
  if (seenIds.has(stableId)) {
    return;
  }
  seenIds.add(stableId);
  if (seenIds.size > 250) {
    seenIds.delete(seenIds.values().next().value);
  }

  addMessage({
    user: parsed.tags["display-name"] || parsed.nick || "chat",
    color: parsed.tags.color || fallbackColor(parsed.nick || "chat"),
    text: parsed.trailing || "",
    emotes: parsed.tags.emotes || ""
  });
  playSound();
}

function parseIrc(line) {
  let rest = line;
  const tags = {};
  let prefix = "";
  let command = "";
  let trailing = "";

  if (rest[0] === "@") {
    const tagEnd = rest.indexOf(" ");
    const rawTags = rest.slice(1, tagEnd);
    rest = rest.slice(tagEnd + 1);
    rawTags.split(";").forEach((tag) => {
      const splitAt = tag.indexOf("=");
      const key = splitAt >= 0 ? tag.slice(0, splitAt) : tag;
      const value = splitAt >= 0 ? tag.slice(splitAt + 1) : "";
      tags[key] = decodeTag(value);
    });
  }

  if (rest[0] === ":") {
    const prefixEnd = rest.indexOf(" ");
    prefix = rest.slice(1, prefixEnd);
    rest = rest.slice(prefixEnd + 1);
  }

  const trailingAt = rest.indexOf(" :");
  if (trailingAt >= 0) {
    trailing = rest.slice(trailingAt + 2);
    rest = rest.slice(0, trailingAt);
  }

  const parts = rest.split(" ");
  command = parts[0];

  return {
    tags,
    prefix,
    command,
    params: parts.slice(1),
    trailing,
    nick: prefix.split("!")[0]
  };
}

function decodeTag(value) {
  return value
    .replace(/\\s/g, " ")
    .replace(/\\:/g, ";")
    .replace(/\\\\/g, "\\")
    .replace(/\\r/g, "\r")
    .replace(/\\n/g, "\n");
}

function addMessage(message) {
  const row = document.createElement("div");
  row.className = "message";
  row.append(nameNode(message.user, message.color));
  row.append(Object.assign(document.createElement("span"), {
    className: "colon",
    textContent: ": "
  }));

  const body = document.createElement("span");
  body.className = "body";
  renderBody(body, message.text, message.emotes);
  row.append(body);

  if (isAlert(message.text)) {
    row.classList.add("alert");
  }

  chatEl.append(row);
  trimMessages();
}

function nameNode(user, color) {
  const span = document.createElement("span");
  span.className = "name";
  span.textContent = user;
  span.style.color = color || fallbackColor(user);
  return span;
}

function renderBody(container, text, emotes) {
  const fragments = emoteFragments(text, emotes);
  if (!fragments.length) {
    appendTextWithMentions(container, text);
    return;
  }

  fragments.forEach((fragment) => {
    if (fragment.type === "text") {
      appendTextWithMentions(container, fragment.value);
      return;
    }

    const img = document.createElement("img");
    img.className = "emote";
    img.alt = fragment.value;
    img.src = "https://static-cdn.jtvnw.net/emoticons/v2/" +
      encodeURIComponent(fragment.id) + "/default/dark/2.0";
    container.append(img);
  });
}

function emoteFragments(text, emotes) {
  if (!emotes) {
    return [];
  }

  const ranges = [];
  emotes.split("/").forEach((entry) => {
    const [id, positions] = entry.split(":");
    if (!id || !positions) {
      return;
    }
    positions.split(",").forEach((position) => {
      const [start, end] = position.split("-").map(Number);
      if (Number.isFinite(start) && Number.isFinite(end)) {
        ranges.push({ id, start, end });
      }
    });
  });

  ranges.sort((a, b) => a.start - b.start);
  const fragments = [];
  let cursor = 0;
  ranges.forEach((range) => {
    if (range.start > cursor) {
      fragments.push({ type: "text", value: text.slice(cursor, range.start) });
    }
    fragments.push({
      type: "emote",
      id: range.id,
      value: text.slice(range.start, range.end + 1)
    });
    cursor = range.end + 1;
  });
  if (cursor < text.length) {
    fragments.push({ type: "text", value: text.slice(cursor) });
  }
  return fragments;
}

function appendTextWithMentions(container, text) {
  const words = text.split(/(\s+)/);
  words.forEach((word) => {
    const clean = word.replace(/^@/, "").toLowerCase();
    if (CONFIG.highlightMentionsOf.includes(clean)) {
      const span = document.createElement("span");
      span.className = "mention";
      span.textContent = word;
      container.append(span);
      return;
    }
    container.append(document.createTextNode(word));
  });
}

async function refreshStats() {
  try {
    session = await getJson("/api/session");
    if (!session.authenticated) {
      viewerCountEl.textContent = "--";
      chatterCountEl.textContent = "--";
      return;
    }

    if (!broadcasterId) {
      broadcasterId = await getBroadcasterId();
    }

    const [viewers, chatters] = await Promise.all([
      getViewerCount(),
      getChatterCount()
    ]);

    viewerCountEl.textContent = viewers;
    chatterCountEl.textContent = chatters;
  } catch (error) {
    viewerCountEl.textContent = "--";
    chatterCountEl.textContent = "--";
  }
}

async function getBroadcasterId() {
  const response = await twitchFetch(
    "/users?login=" + encodeURIComponent(appConfig.channel)
  );
  const user = response.data && response.data[0];
  if (!user) {
    throw new Error("Channel not found.");
  }
  return user.id;
}

async function getViewerCount() {
  const response = await twitchFetch(
    "/streams?user_login=" + encodeURIComponent(appConfig.channel)
  );
  const stream = response.data && response.data[0];
  return stream ? formatCount(stream.viewer_count) : "Offline";
}

async function getChatterCount() {
  const params = new URLSearchParams({
    broadcaster_id: broadcasterId,
    moderator_id: session.user_id,
    first: "1"
  });
  const response = await twitchFetch("/chat/chatters?" + params);
  return formatCount(response.total || 0);
}

async function twitchFetch(path) {
  const response = await fetch("/api/helix?path=" + encodeURIComponent(path));

  if (response.status === 401) {
    status("Twitch token expired. Reconnect on the setup page.");
  } else if (response.status === 403 && path.startsWith("/chat/chatters")) {
    status("Chat count needs broadcaster or moderator auth.");
  }

  if (!response.ok) {
    throw new Error("Twitch API error " + response.status);
  }
  return response.json();
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("GET " + url + " failed.");
  }
  return response.json();
}

function playSound() {
  if (!CONFIG.soundEnabled) {
    return;
  }

  const now = Date.now();
  if (now - lastSoundAt < CONFIG.soundCooldownMs) {
    return;
  }
  lastSoundAt = now;

  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }

  const start = audioCtx.currentTime;
  const gain = audioCtx.createGain();
  const oscA = audioCtx.createOscillator();
  const oscB = audioCtx.createOscillator();

  oscA.type = "sine";
  oscB.type = "triangle";
  oscA.frequency.setValueAtTime(880, start);
  oscA.frequency.exponentialRampToValueAtTime(1320, start + 0.055);
  oscB.frequency.setValueAtTime(1760, start);

  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(CONFIG.soundVolume, start + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);

  oscA.connect(gain);
  oscB.connect(gain);
  gain.connect(audioCtx.destination);
  oscA.start(start);
  oscB.start(start + 0.035);
  oscA.stop(start + 0.17);
  oscB.stop(start + 0.14);
}

function status(text) {
  if (!CONFIG.showConnectionStatus) {
    return;
  }
  const row = document.createElement("div");
  row.className = "chat-status";
  row.textContent = text;
  chatEl.append(row);
  trimMessages();
}

function trimMessages() {
  while (chatEl.children.length > CONFIG.maxMessages) {
    chatEl.firstElementChild.remove();
  }
}

function fallbackColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  }
  return colorPool[Math.abs(hash) % colorPool.length];
}

function isAlert(text) {
  return CONFIG.highlightMentionsOf.some((name) => {
    return text.toLowerCase().includes("@" + name.toLowerCase());
  });
}

function formatCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "--";
  }
  return new Intl.NumberFormat().format(number);
}
