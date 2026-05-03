# Twitch OBS Overlay

Local OBS overlay for `biotachyonic` Twitch chat with:

- Dracula-style chat display
- sound on each chat message
- live viewer count from Twitch Helix
- current chatters count from Twitch Helix
- browser OAuth setup without storing a Twitch client secret

## Twitch app setup

Create a Twitch Developer application and set this OAuth redirect URL exactly:

```text
https://127.0.0.1:3757/setup.html
```

Copy the app's **Client ID**. You do not need the client secret for this overlay.

The chatters count requires the authenticated Twitch account to be the broadcaster
or a moderator for the configured channel, and it requests this scope:

```text
moderator:read:chatters
```

## Run

From this folder:

```bash
./start-overlay-server.sh
```

Then open:

```text
https://127.0.0.1:3757/setup
```

Save the channel and Client ID, then click **Connect Twitch**.
After the Client ID is saved, this URL starts the Twitch login immediately:

```text
https://127.0.0.1:3757/setup?connect=1
```

## OBS

Add a Browser Source with this URL:

```text
https://127.0.0.1:3757/overlay
```

Suggested OBS browser source size:

```text
620 x 900
```

The OAuth access token is kept in the local server's memory. Restarting the
server forgets it, so open the setup page and connect again after a restart.

The generated certificate is self-signed. Your browser may show a local
certificate warning the first time you open the setup page. Accept the local
exception or replace `localhost.pem` and `localhost-key.pem` with a trusted
certificate if OBS refuses to load the HTTPS overlay.
