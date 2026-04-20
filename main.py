#!/usr/bin/env python3
import argparse
import json
import secrets
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for, current_app

from config import config
from twitch_auth import TwitchAuthStore
from twitch_client import TwitchClient
from viewer_service import ViewerService, twitch_safe_tags


def _resolve_project_dir() -> Path:
    starts = [Path(__file__).resolve().parent, Path.cwd()]
    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / "templates" / "index.html").is_file() and (candidate / "static" / "styles.css").is_file():
                return candidate
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project_dir()

app = Flask(
    __name__,
    template_folder=str(PROJECT_DIR / "templates"),
    static_folder=str(PROJECT_DIR / "static"),
    static_url_path="/static",
)
app.secret_key = "steam-twitch-viewer-local"

_background_jobs = {}
_background_job_counter = 0


def _external_url_for(endpoint: str, **values) -> str:
    return url_for(endpoint, _external=True, **values)


def _twitch_client_without_app_token() -> TwitchClient:
    return TwitchClient(client_id=config.TWITCH_CLIENT_ID, client_secret="", ensure_app_token=False)


def _wants_json_response() -> bool:
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
    )


def _load_valid_user_token() -> Optional[dict]:
    store = TwitchAuthStore()
    token = store.load()
    if not token:
        return None

    if token.get("expires_at") and time.time() < float(token["expires_at"]) - 60:
        return token

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return token if token.get("access_token") else None

    try:
        refreshed = TwitchClient().refresh_user_token(refresh_token)
        store.save(refreshed)
        return store.load()
    except Exception as e:
        print(f"Could not refresh Twitch user token: {e}")
        return token if token.get("access_token") else None


def _twitch_auth_status() -> dict:
    token = _load_valid_user_token()
    if not token:
        return {"connected": False}

    return {
        "connected": True,
        "login": token.get("login"),
        "user_id": token.get("user_id"),
        "scope": token.get("scope", []),
    }


def _start_background_job(include_zero: bool, force_refresh: bool) -> str:
    global _background_job_counter
    import uuid
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    _background_job_counter += 1

    def run_job(job_id: str, include_zero: bool, force_refresh: bool):
        try:
            service = ViewerService()
            job = _background_jobs[job_id]

            def on_game(game: Optional[dict], stats: dict):
                if game:
                    job["games"].append(game)
                job["stats"] = dict(stats)

            result = service.process_games_incremental(
                include_zero=include_zero,
                force_refresh=force_refresh,
                on_game=on_game,
            )
            job["done"] = True
            job["stats"] = result
            job["error"] = None
        except Exception as e:
            job = _background_jobs.get(job_id, {})
            job["done"] = True
            job.setdefault("games", [])
            job.setdefault("stats", {})
            job["error"] = str(e)
            _background_jobs[job_id] = job

    _background_jobs[job_id] = {
        "done": False,
        "games": [],
        "stats": {},
        "processed": 0,
        "total_owned": 0,
        "error": None,
    }
    thread = threading.Thread(
        target=run_job,
        args=(job_id, include_zero, force_refresh),
        daemon=True,
    )
    thread.start()
    return job_id


@app.template_filter("compact_number")
def compact_number(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "0"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,}"


@app.route("/")
def index():
    include_zero = request.args.get("include_zero") == "1"
    force_refresh = request.args.get("force_refresh") == "1"
    auth_status = _twitch_auth_status()
    channel_info = {}
    stats = {
        "total_owned": 0,
        "processed": 0,
        "matched": 0,
        "shown": 0,
        "owned_cache_ttl": config.OWNED_GAMES_CACHE_TTL,
        "viewer_cache_ttl": config.VIEWER_COUNT_CACHE_TTL,
    }

    try:
        config.validate()
        service = ViewerService()
        if not force_refresh and service.can_render_from_cache(limit=None, include_zero=include_zero):
            result = service.get_games_with_viewers(
                limit=None,
                include_zero=include_zero,
                force_refresh=False,
            )
            try:
                twitch_client = TwitchClient()
                channel_info = twitch_client.get_channel_info(auth_status.get("user_id")) if auth_status.get("user_id") else {}
            except Exception:
                channel_info = {}

            return render_template(
                "index.html",
                games=result["games"],
                stats=result,
                include_zero=include_zero,
                force_refresh=force_refresh,
                channel_info=channel_info,
                twitch_auth=auth_status,
                can_update_stream=auth_status.get("connected", False),
                error=None,
                job_id=None,
            )

        job_id = _start_background_job(include_zero, force_refresh)
    except Exception as e:
        return render_template(
            "index.html",
            games=[],
            stats=stats,
            include_zero=include_zero,
            force_refresh=force_refresh,
            channel_info={},
            twitch_auth=auth_status,
            can_update_stream=auth_status.get("connected", False),
            error=str(e),
            job_id=None,
        )

    return render_template(
        "index.html",
        games=[],
        stats=stats,
        include_zero=include_zero,
        force_refresh=force_refresh,
        channel_info=channel_info,
        twitch_auth=auth_status,
        can_update_stream=auth_status.get("connected", False),
        error=None,
        job_id=job_id,
    )


@app.route("/refresh")
def refresh():
    args = {}
    if request.args.get("include_zero") == "1":
        args["include_zero"] = "1"
    if request.args.get("force_refresh") == "1":
        args["force_refresh"] = "1"
    return redirect(url_for("index", **args))


@app.route("/api/progress")
def api_progress():
    import uuid
    job_id = request.args.get("job_id")
    offset = request.args.get("offset", default=0, type=int)
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    job = _background_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404

    if job.get("error"):
        return jsonify({
            "status": "error",
            "error": job["error"],
            "stats": job.get("stats", {}),
        })

    if not job.get("done"):
        return jsonify({
            "status": "processing",
            "games": job.get("games", [])[offset:],
            "next_offset": len(job.get("games", [])),
            "stats": job.get("stats", {}),
        })

    return jsonify({
        "status": "done",
        "games": job.get("games", [])[offset:],
        "next_offset": len(job.get("games", [])),
        "stats": job.get("stats", {}),
    })


@app.route("/auth/twitch")
def twitch_login():
    try:
        config.validate()
        state = secrets.token_urlsafe(24)
        session["twitch_oauth_state"] = state
        redirect_uri = _external_url_for("twitch_callback")
        print(f"[TWITCH_LOGIN] SERVER_NAME: {current_app.config.get('SERVER_NAME')}", flush=True)
        print(f"[TWITCH_LOGIN] Redirect URI: {redirect_uri}", flush=True)
        auth_url = TwitchClient().get_authorization_url(redirect_uri, state)
        print(f"[TWITCH_LOGIN] Auth URL (first 200 chars): {auth_url[:200]}...", flush=True)
        return redirect(auth_url)
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("index"))


@app.route("/auth/twitch/callback")
def twitch_callback():
    print(f"[TWITCH_CALLBACK] Incoming request URL: {request.url}", flush=True)
    print(f"[TWITCH_CALLBACK] Request args: {dict(request.args)}", flush=True)
    
    error = request.args.get("error")
    if error:
        flash(f"Twitch authorization failed: {error}", "error")
        return redirect(url_for("index"))

    state = request.args.get("state")
    if not state or state != session.get("twitch_oauth_state"):
        flash("Twitch authorization state did not match. Try connecting again.", "error")
        return redirect(url_for("index"))

    code = request.args.get("code")
    if not code:
        flash("Twitch did not return an authorization code.", "error")
        return redirect(url_for("index"))

    try:
        twitch_client = TwitchClient()
        redirect_uri = _external_url_for("twitch_callback")
        print(f"[TWITCH_CALLBACK] Using redirect_uri for token exchange: {redirect_uri}", flush=True)
        token = twitch_client.exchange_code_for_user_token(code, redirect_uri)
        validation = twitch_client.validate_user_token(token["access_token"])
        token.update({
            "user_id": validation.get("user_id"),
            "login": validation.get("login"),
            "scope": validation.get("scopes", []),
        })
        TwitchAuthStore().save(token)
        session.pop("twitch_oauth_state", None)
        flash(f"Connected Twitch account {token.get('login', '')}.", "success")
    except Exception as e:
        print(f"[TWITCH_CALLBACK] Error during token exchange: {e}", flush=True)
        flash(str(e), "error")

    return redirect(url_for("index"))


@app.route("/auth/twitch/logout", methods=["POST"])
def twitch_logout():
    TwitchAuthStore().clear()
    flash("Disconnected Twitch account.", "success")
    return redirect(url_for("index"))


@app.route("/stream/update", methods=["POST"])
def update_stream():
    game_id = request.form.get("game_id", "").strip()
    title = request.form.get("title", "").strip()
    broadcaster_language = request.form.get("broadcaster_language", "").strip()
    tags_raw = request.form.get("tags", "").strip()
    is_branded_content = request.form.get("is_branded_content") == "1"

    updates = {}
    if game_id:
        updates["game_id"] = game_id
    if title:
        updates["title"] = title
    if broadcaster_language:
        updates["broadcaster_language"] = broadcaster_language
    if tags_raw:
        updates["tags"] = twitch_safe_tags([tag.strip() for tag in tags_raw.split(",") if tag.strip()])
    else:
        updates["tags"] = []
    if "is_branded_content" in request.form:
        updates["is_branded_content"] = is_branded_content

    success = False
    message = "Stream info updated."
    status_code = 200
    wants_json = _wants_json_response()

    try:
        config.validate()
        token = _load_valid_user_token()
        if not token or not token.get("access_token"):
            raise ValueError("Connect Twitch before updating stream info.")

        twitch_client = _twitch_client_without_app_token()
        broadcaster_id = token.get("user_id") or twitch_client.resolve_broadcaster_id(config.TWITCH_BROADCASTER_ID)
        twitch_client.update_channel_info(
            broadcaster_id=broadcaster_id,
            access_token=token["access_token"],
            updates=updates,
        )
        success = True
        if not wants_json:
            flash(message, "success")
    except Exception as e:
        message = str(e)
        status_code = 400
        if not wants_json:
            flash(message, "error")

    if wants_json:
        return jsonify({
            "success": success,
            "message": message,
            "updates": updates if success else {},
        }), status_code

    args = {}
    for key in ("include_zero",):
        value = request.form.get(key)
        if value:
            args[key] = value
    return redirect(url_for("index", **args))


@app.route("/debug/redirect")
def debug_redirect():
    """Debug endpoint to check redirect URI configuration"""
    info = {
        "server_name": current_app.config.get("SERVER_NAME"),
        "static_folder": current_app.static_folder,
        "request_host": request.host,
        "request_url": request.url,
        "redirect_uri": _external_url_for("twitch_callback"),
        "twitch_callback_url": url_for("twitch_callback", _external=True),
    }
    return json.dumps(info, indent=2)


def parse_args():
    parser = argparse.ArgumentParser(description="Show owned Steam games with current Twitch viewers")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind the Flask app to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind the Flask app to")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")
    return parser.parse_args()


def open_browser(host: str, port: int):
    url = f"http://{host}:{port}/"
    webbrowser.open(url)


def main():
    args = parse_args()
    if not args.no_browser:
        threading.Timer(1.0, open_browser, args=(args.host, args.port)).start()

    # Set SERVER_NAME to ensure consistent redirect URIs for OAuth
    # Convert bind addresses to localhost for OAuth redirects
    if args.host in ['127.0.0.1', '0.0.0.0']:
        server_host = 'localhost'
    else:
        server_host = args.host
    app.config['SERVER_NAME'] = f"{server_host}:{args.port}"
    
    print(f"[STARTUP] Flask app starting on {args.host}:{args.port}", flush=True)
    print(f"[STARTUP] SERVER_NAME configured as: {app.config['SERVER_NAME']}", flush=True)
    print(f"[STARTUP] Static folder: {app.static_folder}", flush=True)
    print(f"[STARTUP] Twitch redirect URI will be: http://{server_host}:{args.port}/auth/twitch/callback", flush=True)
    
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
