const REQUIRED_SCOPES = ["moderator:read:chatters"];

const channelInput = document.getElementById("channel");
const clientIdInput = document.getElementById("clientId");
const saveButton = document.getElementById("save");
const connectButton = document.getElementById("connect");
const disconnectButton = document.getElementById("disconnect");
const statusEl = document.getElementById("status");
const redirectUriEl = document.getElementById("redirectUri");

let config = null;

init();

async function init() {
  config = await getJson("/api/config");
  channelInput.value = config.channel;
  clientIdInput.value = config.client_id;
  redirectUriEl.textContent = config.redirect_uri;

  const hash = new URLSearchParams(window.location.hash.slice(1));
  if (hash.has("access_token")) {
    await receiveToken(hash);
    history.replaceState(null, document.title, window.location.pathname);
  }

  const session = await getJson("/api/session");
  renderSession(session);

  const params = new URLSearchParams(window.location.search);
  if (params.get("connect") === "1" && !session.authenticated) {
    connectTwitch();
  }
}

saveButton.addEventListener("click", async () => {
  config = await postJson("/api/config", {
    channel: channelInput.value,
    client_id: clientIdInput.value
  });
  statusEl.textContent = "Settings saved.";
});

connectButton.addEventListener("click", async () => {
  connectTwitch();
});

disconnectButton.addEventListener("click", async () => {
  const session = await postJson("/api/logout", {});
  renderSession(session);
});

async function connectTwitch() {
  config = await postJson("/api/config", {
    channel: channelInput.value,
    client_id: clientIdInput.value
  });

  if (!config.client_id) {
    statusEl.textContent = "Enter your Twitch Client ID first.";
    return;
  }

  const state = crypto.randomUUID();
  sessionStorage.setItem("twitch_oauth_state", state);
  const params = new URLSearchParams({
    response_type: "token",
    client_id: config.client_id,
    redirect_uri: config.redirect_uri,
    scope: REQUIRED_SCOPES.join(" "),
    state
  });
  window.location.href = "https://id.twitch.tv/oauth2/authorize?" + params;
}

async function receiveToken(hash) {
  const expected = sessionStorage.getItem("twitch_oauth_state");
  if (expected && hash.get("state") !== expected) {
    statusEl.textContent = "OAuth state did not match. Try Connect Twitch again.";
    return;
  }

  const accessToken = hash.get("access_token");
  const expiresIn = Number(hash.get("expires_in") || 0);
  if (!accessToken) {
    statusEl.textContent = "Twitch did not return an access token.";
    return;
  }

  const user = await validateToken(accessToken);
  const session = await postJson("/api/session", {
    access_token: accessToken,
    expires_at: Date.now() + expiresIn * 1000,
    login: user.login,
    user_id: user.user_id
  });
  renderSession(session);
}

async function validateToken(accessToken) {
  const response = await fetch("https://id.twitch.tv/oauth2/validate", {
    headers: {
      Authorization: "OAuth " + accessToken
    }
  });
  if (!response.ok) {
    throw new Error("Token validation failed.");
  }
  return response.json();
}

function renderSession(session) {
  if (session.authenticated) {
    statusEl.textContent = "Authenticated as " + session.login + ".";
    return;
  }
  statusEl.textContent = "Not authenticated.";
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("GET " + url + " failed.");
  }
  return response.json();
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error("POST " + url + " failed.");
  }
  return response.json();
}
