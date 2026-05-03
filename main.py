#!/usr/bin/env python3
import argparse
import ast
import json
import os
import re
import secrets
import threading
import time
import webbrowser
import requests
from pathlib import Path
from typing import Optional, Set
from urllib.parse import urlparse

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for, current_app

from config import config, default_steam_install_path
from twitch_auth import TwitchAuthStore
from twitch_client import TwitchClient, clear_app_token_cache
from viewer_service import ViewerService, twitch_safe_tags
from collector import Collector
from history_store import HistoryStore
from running_game import detect_current_game
from retro_client import SYSTEM_DISPLAY_NAMES


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
_collector: Optional[Collector] = None
_history_store: Optional[HistoryStore] = None

SETTINGS_SECTIONS = [
    {
        "title": "Steam",
        "description": "Required for the owned-games library and current-game detection.",
        "fields": [
            {
                "name": "STEAM_USER_ID",
                "label": "Steam profile name or ID",
                "help": "Use your Steam custom profile name, profile URL, SteamID64, or numeric Steam userdata folder. Example: biotachyonic.",
                "required": True,
                "check_steam_username": True,
            },
            {
                "name": "STEAM_API_KEY",
                "label": "Steam Web API key",
                "help": "Open the Steam key page, sign in, enter localhost when Steam asks for a domain, copy the key, and paste it here.",
                "secret": True,
                "required": True,
                "link": "https://steamcommunity.com/dev/apikey",
                "link_label": "Get key",
            },
            {
                "name": "STEAM_INSTALL_PATH",
                "label": "Steam install path",
                "help": "Used for local config lookups and launched/recent game detection.",
                "type": "directory",
            },
        ],
    },
    {
        "title": "Twitch",
        "description": "Create a Twitch application, then paste its Client ID and generated Client Secret here.",
        "fields": [
            {
                "name": "TWITCH_CLIENT_ID",
                "label": "Twitch app Client ID",
                "help": "In the Twitch Developer Console, create an app, open Manage, and copy the Client ID value into this field.",
                "required": True,
                "link": "https://dev.twitch.tv/console/apps/create",
                "link_label": "Create app",
            },
            {
                "name": "TWITCH_REDIRECT_URI",
                "label": "OAuth redirect URL",
                "help": "Default is this app's local Twitch callback URL. Paste the exact URL into your Twitch application's OAuth Redirect URLs list.",
                "type": "copyable_url",
            },
            {
                "name": "TWITCH_CLIENT_SECRET",
                "label": "Twitch client secret",
                "help": "In that same Twitch app, click New Secret, copy the value immediately, and paste it here.",
                "secret": True,
                "required": True,
                "link": "https://dev.twitch.tv/docs/authentication/register-app/",
                "link_label": "Instructions",
            },
            {
                "name": "TWITCH_BROADCASTER_ID",
                "label": "Twitch username",
                "help": "Optional fallback for stream updates. Use your Twitch login name, for example biotachyonic. The Connect Twitch button usually fills this automatically.",
                "placeholder": "Your Twitch Username",
                "blank_display": True,
            },
        ],
    },
    {
        "title": "PC Platforms",
        "description": "Choose which PC libraries to include.",
        "fields": [
            {
                "name": "ENABLED_PLATFORMS",
                "label": "Enabled PC platforms",
                "type": "checkbox_group",
                "options": [
                    {"value": "steam", "label": "Steam"},
                    {"value": "gog", "label": "GOG"},
                    {"value": "epic", "label": "Epic Games"},
                ],
                "help": "Steam requires your Steam profile and Web API key above. GOG and Epic use local launcher data when available.",
            },
            {
                "name": "GOG_DB_PATH",
                "label": "GOG Galaxy storage folder",
                "help": "Optional. Pick the folder that contains galaxy-2.0.db.",
                "type": "directory",
                "path_filename": "galaxy-2.0.db",
            },
            {
                "name": "EPIC_CATALOG_PATH",
                "label": "Epic catalog folder",
                "help": "Optional. Pick the folder that contains catcache.bin.",
                "type": "directory",
                "path_filename": "catcache.bin",
            },
            {
                "name": "EPIC_INSTALLS_PATH",
                "label": "Epic manifests folder",
                "help": "Optional override for Epic install data.",
                "type": "directory",
            },
        ],
    },
    {
        "title": "Retro Platforms",
        "description": "Choose which retro libraries to include after building cache/retro_games.json.",
        "fields": [
            {
                "name": "RETRO_SYSTEMS",
                "label": "Enabled retro platforms",
                "type": "checkbox_group",
                "options": [
                    {"value": value, "label": label}
                    for value, label in SYSTEM_DISPLAY_NAMES.items()
                ],
                "help": "Run retro_collector.py first to create the local retro catalog.",
            },
        ],
    },
    {
        "title": "AI And Cache",
        "description": "Optional local LLM and cache tuning.",
        "fields": [
            {
                "name": "OLLAMA_BASE_URL",
                "label": "Ollama base URL",
                "help": "Default is http://127.0.0.1:11434.",
            },
            {
                "name": "OLLAMA_MODEL",
                "label": "Ollama model",
                "help": "Blank auto-selects the first installed Ollama model.",
            },
            {
                "name": "OLLAMA_TIMEOUT",
                "label": "Ollama timeout seconds",
                "type": "number",
            },
            {
                "name": "OWNED_GAMES_CACHE_TTL",
                "label": "Owned-games cache TTL",
                "type": "number",
            },
            {
                "name": "VIEWER_COUNT_CACHE_TTL",
                "label": "Viewer-count cache TTL",
                "type": "number",
            },
            {
                "name": "CACHE_DIR",
                "label": "Cache directory",
                "type": "directory",
            },
            {
                "name": "ASSET_CACHE_DIR",
                "label": "Asset cache directory",
                "type": "directory",
            },
            {
                "name": "COLLECTION_INTERVAL",
                "label": "History collection interval",
                "type": "number",
            },
            {
                "name": "HISTORY_RETENTION_DAYS",
                "label": "History retention days",
                "type": "number",
            },
            {
                "name": "HISTORY_DB_PATH",
                "label": "History database folder",
                "help": "Folder where viewer_history.db is stored.",
                "type": "directory",
                "path_filename": "viewer_history.db",
            },
        ],
    },
]

SETTINGS_FIELDS = [field for section in SETTINGS_SECTIONS for field in section["fields"]]
ENV_FILE = PROJECT_DIR / ".env"
LOCALHOST_CERT_FILE = PROJECT_DIR / "twitch-obs-overlay" / "localhost.pem"
LOCALHOST_KEY_FILE = PROJECT_DIR / "twitch-obs-overlay" / "localhost-key.pem"


def _parse_platforms_arg(name: str = "deferred_platforms") -> Optional[Set[str]]:
    values = request.args.getlist(name)
    if not values and name in request.cookies:
        values = [request.cookies.get(name, "")]
    if not values:
        return None

    platforms: Set[str] = set()
    for value in values:
        for part in str(value or "").split(","):
            platform = part.strip().lower()
            if platform:
                platforms.add(platform)
    return platforms


def _external_url_for(endpoint: str, **values) -> str:
    return url_for(endpoint, _external=True, **values)


def _https_external_url_for(endpoint: str, **values) -> str:
    return url_for(endpoint, _external=True, _scheme="https", **values)


def _twitch_client_without_app_token() -> TwitchClient:
    return TwitchClient(client_id=config.TWITCH_CLIENT_ID, client_secret="", ensure_app_token=False)


def _default_twitch_redirect_uri() -> str:
    return _https_external_url_for("twitch_callback")


def _twitch_redirect_uri() -> str:
    return config.TWITCH_REDIRECT_URI or _default_twitch_redirect_uri()


def _wants_json_response() -> bool:
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
    )


def _setting_value(name: str) -> str:
    if name == "RETRO_SYSTEMS":
        return os.getenv("RETRO_SYSTEMS", "")
    if name == "ENABLED_PLATFORMS":
        return ",".join(config.ENABLED_PLATFORMS)

    value = getattr(config, name, os.getenv(name, ""))
    if name == "TWITCH_REDIRECT_URI" and not value:
        try:
            value = _default_twitch_redirect_uri()
        except RuntimeError:
            value = ""
    if name == "STEAM_INSTALL_PATH" and not value:
        value = default_steam_install_path()
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _settings_template_sections():
    sections = []
    for section in SETTINGS_SECTIONS:
        fields = []
        for field in section["fields"]:
            value = _setting_value(field.get("env_name", field["name"]))
            field_for_template = dict(field)
            field_for_template["value"] = "" if field.get("secret") or field.get("blank_display") else value
            field_for_template["has_value"] = bool(value)
            field_for_template["checked"] = value.lower() in ("1", "true", "yes", "on")
            if field.get("type") == "checkbox_group":
                selected = {part.strip().lower() for part in value.split(",") if part.strip()}
                if field["name"] == "RETRO_SYSTEMS" and value.strip().lower() == "all":
                    selected = {option["value"] for option in field.get("options", [])}
                field_for_template["selected_values"] = selected
                field_for_template["options"] = [
                    {**option, "checked": option["value"] in selected}
                    for option in field.get("options", [])
                ]
            fields.append(field_for_template)
        sections.append({**section, "fields": fields})
    return sections


def _dotenv_escape(value: str) -> str:
    value = str(value or "")
    if value == "":
        return ""
    if re.search(r"\s|#|=|['\"]", value):
        return json.dumps(value)
    return value


def _read_local_env_values() -> dict:
    if not ENV_FILE.exists():
        return {}

    values = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in ("'", '"') and value[-1:] == value[0]:
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                value = value[1:-1]
        values[key] = value
    return values


def _steam_vanity_from_input(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""

    if "://" in value:
        parse_value = value
    elif "steamcommunity.com/" in value.lower():
        parse_value = f"https://{value}"
    else:
        parse_value = f"https://steamcommunity.com/id/{value}"

    parsed = urlparse(parse_value)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc and "steamcommunity.com" in parsed.netloc.lower():
        if len(parts) >= 2 and parts[0].lower() == "profiles" and parts[1].isdigit():
            return parts[1]
        if len(parts) >= 2 and parts[0].lower() == "id":
            return parts[1]
    return value.strip().strip("/")


def _looks_like_steam_api_key(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Fa-f0-9]{32}", str(value or "").strip()))


def _steam_account_id_from_steamid64(steamid64: str) -> Optional[str]:
    try:
        value = int(steamid64)
    except (TypeError, ValueError):
        return None
    if value > config.STEAMID64_BASE:
        return str(value - config.STEAMID64_BASE)
    return None


def _steamid64_from_account_id(account_id: str) -> Optional[str]:
    try:
        value = int(account_id)
    except (TypeError, ValueError):
        return None
    if value > 0 and value < config.STEAMID64_BASE:
        return str(value + config.STEAMID64_BASE)
    return None


def _steam_api_key_from_input(value: str) -> str:
    value = str(value or "").strip()
    if _looks_like_steam_api_key(value):
        return value
    saved_key = config.STEAM_API_KEY or ""
    if _looks_like_steam_api_key(saved_key):
        return saved_key
    if value:
        raise ValueError("Steam Web API key does not look valid. It should be the key from Steam's API key page.")
    return ""


def _normalize_steam_user_id(value: str, api_key: str) -> tuple[str, Optional[str]]:
    value = str(value or "").strip()
    if not value:
        return "", None

    vanity = _steam_vanity_from_input(value)
    if vanity.isdigit():
        return vanity, None

    api_key = _steam_api_key_from_input(api_key)
    if not api_key:
        raise ValueError("Enter a Steam Web API key before saving a Steam profile name.")

    response = requests.get(
        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
        params={
            "key": api_key,
            "vanityurl": vanity,
            "url_type": 1,
            "format": "json",
        },
        timeout=15,
    )
    if response.status_code in (401, 403):
        raise ValueError("Steam rejected the Web API key. Open the Steam key page, copy the key, and paste it into Settings.")
    if response.status_code >= 400:
        raise ValueError(f"Steam username check failed with HTTP {response.status_code}.")
    payload = response.json().get("response", {})
    if int(payload.get("success", 0)) == 1 and payload.get("steamid"):
        return str(payload["steamid"]), f"Resolved Steam profile {vanity} to SteamID64 {payload['steamid']}."

    message = payload.get("message") or "Steam could not find that profile name."
    raise ValueError(f"Could not resolve Steam profile {vanity}: {message}")


def _write_local_env(values: dict):
    existing = _read_local_env_values()
    merged = {key: str(existing.get(key) or "") for key in existing}
    merged.update(values)

    sections = [
        ("Steam Configuration", ["STEAM_USER_ID", "STEAM_API_KEY", "STEAM_INSTALL_PATH"]),
        ("Twitch Configuration", ["TWITCH_CLIENT_ID", "TWITCH_REDIRECT_URI", "TWITCH_CLIENT_SECRET", "TWITCH_BROADCASTER_ID"]),
        ("Platform Sources", ["ENABLED_PLATFORMS", "GOG_DB_PATH", "EPIC_CATALOG_PATH", "EPIC_INSTALLS_PATH", "RETRO_SYSTEMS"]),
        ("AI And Cache", [
            "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT", "OWNED_GAMES_CACHE_TTL",
            "VIEWER_COUNT_CACHE_TTL", "CACHE_DIR", "ASSET_CACHE_DIR", "COLLECTION_INTERVAL",
            "HISTORY_RETENTION_DAYS", "HISTORY_DB_PATH",
        ]),
    ]

    lines = [
        "# Local settings for Steam Twitch Viewer Dashboard",
        "# This file is ignored by git. Do not commit real credentials.",
        "",
    ]
    written = set()
    for title, keys in sections:
        lines.append(f"# {title}")
        for key in keys:
            if key in merged:
                lines.append(f"{key}={_dotenv_escape(merged[key])}")
                written.add(key)
        lines.append("")

    extra_keys = [key for key in sorted(merged) if key not in written and key]
    if extra_keys:
        lines.append("# Other Settings")
        for key in extra_keys:
            lines.append(f"{key}={_dotenv_escape(merged[key])}")
        lines.append("")

    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")


def _settings_fields_for_section(section_title: str) -> list:
    for section in SETTINGS_SECTIONS:
        if section["title"] == section_title:
            return section["fields"]
    return []


def _settings_values_from_form(section_title: str) -> dict:
    values = {}
    for field in _settings_fields_for_section(section_title):
        name = field.get("env_name", field["name"])
        if field.get("type") == "checkbox":
            values[name] = "true" if request.form.get(name) == "1" else "false"
            continue
        if field.get("type") == "checkbox_group":
            selected = [
                value.strip().lower()
                for value in request.form.getlist(field["name"])
                if value.strip()
            ]
            allowed = {option["value"] for option in field.get("options", [])}
            values[name] = ",".join(value for value in selected if value in allowed)
            continue

        value = request.form.get(field["name"], "").strip()
        if field.get("path_filename") and value and os.path.isdir(os.path.expanduser(value)):
            value = os.path.join(value, field["path_filename"])
        if name == "STEAM_API_KEY" and value and not _looks_like_steam_api_key(value) and _setting_value(name):
            value = _setting_value(name)
        if (field.get("secret") or field.get("blank_display")) and not value and _setting_value(name):
            value = _setting_value(name)
        values[name] = value
    return values


def _section_title_from_form() -> str:
    key = request.form.get("settings_section", "").strip()
    section_titles = {section["title"] for section in SETTINGS_SECTIONS}
    if key not in section_titles:
        raise ValueError("Choose a settings section to save.")
    return key


def _ollama_url(path: str) -> str:
    return f"{config.OLLAMA_BASE_URL}/{path.lstrip('/')}"


def _ollama_error(message: str, status_code: int = 502):
    return jsonify({"error": message}), status_code


def _resolve_ollama_model() -> str:
    if config.OLLAMA_MODEL:
        return config.OLLAMA_MODEL

    response = requests.get(
        _ollama_url("/api/tags"),
        timeout=min(config.OLLAMA_TIMEOUT, 10),
    )
    response.raise_for_status()
    models = response.json().get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an unexpected model list response.")
    if not models:
        raise RuntimeError(
            "Ollama is reachable, but no models are installed. Pull a model with 'ollama pull <name>' or set OLLAMA_MODEL in .env."
        )

    model_name = models[0].get("name") if isinstance(models[0], dict) else None
    if not model_name:
        raise RuntimeError("Ollama returned a model list without a usable model name.")
    return model_name


def _extract_json_from_llm_content(content: str):
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
            return parsed
        except json.JSONDecodeError:
            continue

    raise ValueError("LLM did not return valid JSON.")


def _tags_from_parsed_llm_json(parsed):
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []

    for key in ("tags", "twitch_tags", "stream_tags"):
        value = parsed.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return _tags_from_llm_text(value)
    return []


def _tags_from_llm_text(content: str):
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    cleaned = re.sub(r"```(?:json)?|```", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "\n", cleaned, flags=re.IGNORECASE)

    tags = []
    for part in re.split(r"[\n,;]+", cleaned):
        part = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", part).strip()
        if ":" in part:
            part = part.rsplit(":", 1)[1].strip()
        part = part.strip(" \t\r\n'\"`[]{}()")
        if part:
            tags.append(part)
    return tags


def _extract_tags_from_llm_content(content: str):
    try:
        parsed = _extract_json_from_llm_content(content)
        tags = _tags_from_parsed_llm_json(parsed)
        if tags:
            return tags
    except ValueError:
        pass

    tags = _tags_from_llm_text(content)
    if tags:
        return tags
    raise ValueError("LLM did not return tags.")


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


def _start_background_job(include_zero: bool, force_refresh: bool, deferred_platforms: Optional[Set[str]] = None) -> str:
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
                deferred_platforms_getter=lambda: set(job.get("deferred_platforms", set())),
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
        "deferred_platforms": set(deferred_platforms or set()),
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
    deferred_platforms = _parse_platforms_arg() or set()
    auth_status = _twitch_auth_status()
    is_first_run = config.FIRST_RUN
    onboarding_sections = _onboarding_template_sections() if is_first_run else []
    twitch_redirect_uri = _twitch_redirect_uri()
    default_twitch_redirect_uri = _default_twitch_redirect_uri()
    channel_info = {}
    stats = {
        "total_owned": 0,
        "processed": 0,
        "matched": 0,
        "shown": 0,
        "owned_cache_ttl": config.OWNED_GAMES_CACHE_TTL,
        "viewer_cache_ttl": config.VIEWER_COUNT_CACHE_TTL,
        "platform_owned": {},
        "platform_processed": {},
        "platform_matched": {},
        "platform_shown": {},
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
                is_first_run=is_first_run,
                onboarding_sections=onboarding_sections,
                twitch_redirect_uri=twitch_redirect_uri,
                default_twitch_redirect_uri=default_twitch_redirect_uri,
            )

        job_id = _start_background_job(include_zero, force_refresh, deferred_platforms=deferred_platforms)
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
            is_first_run=is_first_run,
            onboarding_sections=onboarding_sections,
            twitch_redirect_uri=twitch_redirect_uri,
            default_twitch_redirect_uri=default_twitch_redirect_uri,
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
        is_first_run=is_first_run,
        onboarding_sections=onboarding_sections,
        twitch_redirect_uri=twitch_redirect_uri,
        default_twitch_redirect_uri=default_twitch_redirect_uri,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        try:
            section_title = _section_title_from_form()
            values = _settings_values_from_form(section_title)
            steam_resolution_message = None
            if section_title == "Steam":
                values["STEAM_USER_ID"], steam_resolution_message = _normalize_steam_user_id(
                    values.get("STEAM_USER_ID", ""),
                    values.get("STEAM_API_KEY", ""),
                )
            _write_local_env(values)
            os.environ.update(values)
            config.reload()
            clear_app_token_cache()
            flash(f"{section_title} saved to local .env.", "success")
            if steam_resolution_message:
                flash(steam_resolution_message, "success")
            try:
                config.validate()
            except Exception as e:
                flash(str(e), "error")
            return redirect(url_for("settings"))
        except Exception as e:
            flash(f"Could not save settings: {e}", "error")

    validation_error = None
    try:
        config.validate()
    except Exception as e:
        validation_error = str(e)

    return render_template(
        "settings.html",
        sections=_settings_template_sections(),
        env_file=str(ENV_FILE),
        validation_error=validation_error,
        twitch_redirect_uri=_twitch_redirect_uri(),
        default_twitch_redirect_uri=_default_twitch_redirect_uri(),
    )


@app.route("/settings/check-steam-username", methods=["POST"])
def settings_check_steam_username():
    username = request.form.get("steam_user_id", "").strip()
    api_key = request.form.get("steam_api_key", "").strip() or config.STEAM_API_KEY or ""

    try:
        original = username
        steam_id, message = _normalize_steam_user_id(username, api_key)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    if not steam_id:
        return jsonify({"success": False, "error": "Enter a Steam profile name or ID first."}), 400

    account_id = _steam_account_id_from_steamid64(steam_id)
    steamid64 = steam_id if account_id else _steamid64_from_account_id(steam_id)
    if steamid64 and not account_id:
        account_id = steam_id

    if account_id and steamid64:
        message = (
            f"Account ID {account_id} maps to SteamID64 {steamid64}. "
            "This app can use either value."
        )
    elif message is None:
        message = f"Steam ID: {steam_id}"

    should_update_input = not original.isdigit() and bool(steamid64)

    return jsonify({
        "success": True,
        "steamid64": steamid64 or steam_id,
        "account_id": account_id,
        "value": steamid64 or steam_id,
        "should_update_input": should_update_input,
        "message": message,
    })


@app.route("/settings/pick-directory", methods=["POST"])
def settings_pick_directory():
    field = request.form.get("field", "").strip()
    directory_fields = {
        field["name"]
        for field in SETTINGS_FIELDS
        if field.get("type") == "directory"
    }
    if field not in directory_fields:
        return jsonify({"error": "Unsupported directory field."}), 400

    current = request.form.get("current", "").strip()
    if not current and field == "STEAM_INSTALL_PATH":
        current = default_steam_install_path()
    initial_dir = os.path.expanduser(current)
    if initial_dir and not os.path.isdir(initial_dir):
        initial_dir = os.path.dirname(initial_dir)
    if not os.path.isdir(initial_dir):
        initial_dir = os.path.expanduser("~")

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="Select Steam install folder",
            initialdir=initial_dir,
            mustexist=True,
        )
        root.destroy()
    except Exception as e:
        return jsonify({"error": f"Could not open directory picker: {e}"}), 500

    if not selected:
        return jsonify({"path": ""})
    return jsonify({"path": selected})


ONBOARDING_SECTIONS = [
    section
    for section in SETTINGS_SECTIONS
    if section["title"] != "AI And Cache"
]


def _onboarding_template_sections():
    all_sections = _settings_template_sections()
    return [
        section
        for section in all_sections
        if section["title"] != "AI And Cache"
    ]


@app.route("/onboarding/save", methods=["POST"])
def onboarding_save():
    try:
        section_title = request.form.get("settings_section", "").strip()
        valid_titles = {s["title"] for s in ONBOARDING_SECTIONS}
        if section_title not in valid_titles:
            return jsonify({"success": False, "error": f"Unknown section: {section_title}"}), 400

        values = _settings_values_from_form(section_title)
        steam_resolution_message = None
        if section_title == "Steam":
            values["STEAM_USER_ID"], steam_resolution_message = _normalize_steam_user_id(
                values.get("STEAM_USER_ID", ""),
                values.get("STEAM_API_KEY", ""),
            )
        _write_local_env(values)
        os.environ.update(values)
        config.reload()
        clear_app_token_cache()

        extra = {}
        if steam_resolution_message:
            extra["steam_resolution_message"] = steam_resolution_message

        validation_error = None
        try:
            config.validate()
        except Exception as e:
            validation_error = str(e)

        return jsonify({
            "success": True,
            "message": f"{section_title} saved.",
            "validation_error": validation_error,
            **extra,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/onboarding/done", methods=["POST"])
def onboarding_done():
    try:
        values = {"FIRST_RUN": "false"}
        _write_local_env(values)
        os.environ.update(values)
        config.reload()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/onboarding/check-steam-username", methods=["POST"])
def onboarding_check_steam_username():
    return settings_check_steam_username()


@app.route("/refresh")
def refresh():
    args = {}
    if request.args.get("include_zero") == "1":
        args["include_zero"] = "1"
    if request.args.get("force_refresh") == "1":
        args["force_refresh"] = "1"
    deferred_platforms = _parse_platforms_arg()
    if deferred_platforms is not None:
        args["deferred_platforms"] = ",".join(sorted(deferred_platforms))
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

    deferred_platforms = _parse_platforms_arg()
    if deferred_platforms is not None:
        job["deferred_platforms"] = deferred_platforms

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


@app.route("/api/current-game")
def api_current_game():
    """Detect the game the user is currently playing.

    Tries the Steam Web API first, then a window-title / process-name scan
    against the cached owned-games list.
    """
    try:
        service = ViewerService()
        owned = service.cache.get("owned_games", {}).get("games", []) or []
        detection = detect_current_game(owned)
        if not detection.appid:
            return jsonify({"game": None, "source": detection.source})

        # Build a minimal game payload the frontend can use to populate the
        # stream-update modal even when the matching card isn't on the page.
        game = {
            "appid": detection.appid,
            "platform": detection.platform or "steam",
            "name": detection.name or "",
        }
        for g in owned:
            if str(g.get("appid")) == detection.appid and (
                detection.platform is None or g.get("platform") == detection.platform
            ):
                game["name"] = g.get("name") or game["name"]
                game["platform"] = g.get("platform") or game["platform"]
                break
        return jsonify({"game": game, "source": detection.source, "raw": detection.raw})
    except Exception as e:
        return jsonify({"game": None, "source": "error", "error": str(e)}), 500


_SPONSOR_LIVE_LOGIN = os.environ.get("SPONSOR_LOGIN", "biotachyonic")
_SPONSOR_LIVE_TTL = 60  # seconds
_SPONSOR_DECAPI_BASE = "https://decapi.me/twitch"
_sponsor_live_cache = {"checked_at": 0.0, "payload": None}
_sponsor_live_lock = threading.Lock()


def _decapi_text(path: str, timeout: float = 5.0) -> Optional[str]:
    try:
        resp = requests.get(f"{_SPONSOR_DECAPI_BASE}/{path}", timeout=timeout)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    return (resp.text or "").strip()


def _decapi_value_or_empty(text: Optional[str]) -> str:
    """Normalize decapi text — empty when it's an offline/error message."""
    if not text:
        return ""
    lowered = text.lower()
    if "offline" in lowered or "not live" in lowered or "user not found" in lowered:
        return ""
    return text


def _check_sponsor_live(login: str) -> dict:
    uptime = _decapi_text(f"uptime/{login}")
    if not uptime or "offline" in uptime.lower() or "not found" in uptime.lower():
        return {"live": False, "login": login}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        viewers_f = pool.submit(_decapi_text, f"viewercount/{login}")
        game_f = pool.submit(_decapi_text, f"game/{login}")
        title_f = pool.submit(_decapi_text, f"title/{login}")
        viewers_raw = viewers_f.result()
        game_raw = game_f.result()
        title_raw = title_f.result()

    try:
        viewers = int(viewers_raw) if viewers_raw and viewers_raw.lstrip("-").isdigit() else 0
    except ValueError:
        viewers = 0

    return {
        "live": True,
        "login": login,
        "uptime": uptime,
        "viewer_count": max(viewers, 0),
        "game_name": _decapi_value_or_empty(game_raw),
        "title": _decapi_value_or_empty(title_raw),
    }


@app.route("/api/sponsor-live")
def api_sponsor_live():
    """Return whether the sponsor channel is live. Cached ~60s server-side.

    Backed by decapi.me so distributing the app does not require any Twitch
    API credentials.
    """
    now = time.time()
    with _sponsor_live_lock:
        if now - _sponsor_live_cache["checked_at"] < _SPONSOR_LIVE_TTL and _sponsor_live_cache["payload"] is not None:
            return jsonify(_sponsor_live_cache["payload"])

    try:
        payload = _check_sponsor_live(_SPONSOR_LIVE_LOGIN)
    except Exception as e:
        payload = {"live": False, "login": _SPONSOR_LIVE_LOGIN, "error": str(e)}

    with _sponsor_live_lock:
        _sponsor_live_cache["checked_at"] = now
        _sponsor_live_cache["payload"] = payload

    return jsonify(payload)


@app.route("/auth/twitch")
def twitch_login():
    try:
        config.validate()
        state = secrets.token_urlsafe(24)
        session["twitch_oauth_state"] = state
        redirect_uri = _twitch_redirect_uri()
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
        redirect_uri = _twitch_redirect_uri()
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
    appid = request.form.get("appid", "").strip()
    platform = request.form.get("platform", "steam").strip() or "steam"
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
        if appid and updates.get("tags"):
            try:
                ViewerService().save_game_source_tags(appid, platform, "ai", updates["tags"])
            except Exception as e:
                print(f"Could not persist stream tags for {platform}_{appid}: {e}")
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


@app.route("/api/generate-tags", methods=["POST"])
def generate_tags():
    """Generate Twitch stream tags using local LLM."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    game_name = data.get("game_name", "")
    appid = str(data.get("appid", "")).strip()
    platform = str(data.get("platform", "steam")).strip() or "steam"
    steam_tags = data.get("steam_tags", [])
    game_description = str(data.get("game_description", "")).strip()
    
    # Construct prompt for LLM
    prompt = f"""You are a Twitch stream tag assistant. Generate relevant tags for a Twitch stream about the game "{game_name}".

Game details:
- Name: {game_name}
- Steam tags: {', '.join(steam_tags) if steam_tags else 'None'}
- Full store description: {game_description if game_description else 'No description provided.'}

Twitch tags help viewers discover streams. Tags must follow these rules:
1. Maximum 10 tags (but aim for 5-8 relevant tags)
2. Each tag must be lowercase, alphanumeric, and may contain underscores or hyphens (no spaces)
3. Tags should be comma-separated
4. Tags should be relevant to the game, genre, gameplay style, mood, or stream content (e.g., "firstplaythrough", "nobackseating", "horror", "indie", "multiplayer")
5. Do not include offensive or inappropriate tags.
6. Output ONLY a JSON array of tag strings, like ["tag1", "tag2", "tag3"]

Provide tags that would help attract viewers on Twitch."""
    
    try:
        model = _resolve_ollama_model()

        ollama_response = requests.post(
            _ollama_url("/api/chat"),
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that outputs JSON arrays."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "keep_alive": "0",
                "format": "json",
                "think": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 256,
                },
            },
            timeout=config.OLLAMA_TIMEOUT
        )
        ollama_response.raise_for_status()
        result = ollama_response.json()
        content = result["message"]["content"]

        # Safety: explicit unload in case keep_alive didn't free VRAM
        try:
            requests.post(
                _ollama_url("/api/generate"),
                json={"model": model, "keep_alive": "0"},
                timeout=min(config.OLLAMA_TIMEOUT, 10),
            )
        except Exception:
            pass

        raw_tags = _extract_tags_from_llm_content(content)
        
        # Clean and validate tags using twitch_safe_tags
        validated_tags = twitch_safe_tags(raw_tags)
        if validated_tags:
            if appid:
                try:
                    ViewerService().save_game_source_tags(appid, platform, "ai", validated_tags)
                except Exception as e:
                    print(f"Could not persist AI tags for {platform}_{appid}: {e}")
            return jsonify({"tags": validated_tags})
        else:
            return jsonify({"error": "No valid tags generated"}), 500
    except requests.exceptions.ConnectionError:
        return _ollama_error(
            f"Could not connect to Ollama at {config.OLLAMA_BASE_URL}. "
            "Start Ollama ('ollama serve'), or set OLLAMA_BASE_URL in .env if Ollama is running on another host."
        )
    except requests.exceptions.Timeout:
        return _ollama_error(
            f"Timed out waiting for Ollama at {config.OLLAMA_BASE_URL}. "
            "Try a smaller/faster model or increase OLLAMA_TIMEOUT in .env."
        )
    except requests.exceptions.HTTPError as e:
        detail = e.response.text[:500] if e.response is not None else str(e)
        return _ollama_error(f"Ollama request failed: {detail}")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return _ollama_error(f"Could not parse Ollama response: {e}")
    except Exception as e:
        return _ollama_error(str(e))

@app.route("/api/generate-title", methods=["POST"])
def generate_title():
    """Generate an SEO-friendly Twitch stream title using the local LLM."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    game_name = str(data.get("game_name", "")).strip()
    appid = str(data.get("appid", "")).strip()
    platform = str(data.get("platform", "steam")).strip() or "steam"
    platform_tags = data.get("platform_tags") or data.get("steam_tags") or []
    game_description = str(data.get("game_description", "")).strip()
    current_title = str(data.get("current_title", "")).strip()
    extra_context = str(data.get("extra_context", "")).strip()[:400]

    if not game_name:
        return jsonify({"error": "game_name is required"}), 400

    extra_context_block = ""
    extra_context_rule = "4. No session notes were provided; do not invent a stream activity."
    if extra_context:
        extra_context_block = (
            "Priority stream context:\n"
            f"- Session notes from the streamer: {extra_context}\n"
        )
        extra_context_rule = (
            "4. Session notes are required context, not a suggestion. The title must clearly mention the "
            "streamer's activity/objective from those notes. If the notes name a specific item, resource, "
            "boss, location, build, challenge, or goal, preserve that specific detail when it fits the "
            "140-character limit."
        )

    prompt = f"""You are a Twitch SEO assistant. Write ONE Twitch stream title for the game "{game_name}" that maximizes browse-page visibility and search discoverability.

Game details:
- Name: {game_name}
- Tags / genres: {', '.join(platform_tags) if platform_tags else 'None provided'}
- Store description: {game_description if game_description else 'None provided'}
- Current title (if any): {current_title or 'None'}

{extra_context_block}
Rules for the title:
1. Hard limit 140 characters. Aim for 60-110.
2. Lead with the game name or a strong hook keyword someone would actually search for.
3. Include 1-3 high-discoverability keywords (genre, mode, hook) that match the game.
{extra_context_rule}
5. No clickbait, no all-caps screaming, no spammy emoji walls (one tasteful emoji is fine).
6. No banned Twitch terms, no profanity.
7. Plain text. No surrounding quotes.

Output ONLY a JSON object: {{"title": "<the title>"}}"""

    try:
        model = _resolve_ollama_model()

        ollama_response = requests.post(
            _ollama_url("/api/chat"),
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that outputs JSON objects."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "keep_alive": "0",
                "format": "json",
                "think": False,
                "options": {
                    "temperature": 0.6,
                    "num_predict": 256,
                },
            },
            timeout=config.OLLAMA_TIMEOUT,
        )
        ollama_response.raise_for_status()
        result = ollama_response.json()
        content = result["message"]["content"]

        try:
            requests.post(
                _ollama_url("/api/generate"),
                json={"model": model, "keep_alive": "0"},
                timeout=min(config.OLLAMA_TIMEOUT, 10),
            )
        except Exception:
            pass

        parsed = _extract_json_from_llm_content(content) or {}
        title = ""
        if isinstance(parsed, dict):
            title = str(parsed.get("title") or "").strip()
        if not title:
            return _ollama_error("LLM did not return a title.")

        # Strip any quotes the model added and clamp length.
        title = title.strip().strip("\"'`")
        if len(title) > 140:
            title = title[:140].rstrip()

        if appid and not extra_context:
            try:
                ViewerService().save_game_generated_title(appid, platform, title)
            except Exception as e:
                print(f"Could not persist generated title for {platform}_{appid}: {e}")

        return jsonify({"title": title})
    except requests.exceptions.ConnectionError:
        return _ollama_error(
            f"Could not connect to Ollama at {config.OLLAMA_BASE_URL}. "
            "Start Ollama ('ollama serve'), or set OLLAMA_BASE_URL in .env if Ollama is running on another host."
        )
    except requests.exceptions.Timeout:
        return _ollama_error(
            f"Timed out waiting for Ollama at {config.OLLAMA_BASE_URL}. "
            "Try a smaller/faster model or increase OLLAMA_TIMEOUT in .env."
        )
    except requests.exceptions.HTTPError as e:
        detail = e.response.text[:500] if e.response is not None else str(e)
        return _ollama_error(f"Ollama request failed: {detail}")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return _ollama_error(f"Could not parse Ollama response: {e}")
    except Exception as e:
        return _ollama_error(str(e))


@app.route("/debug/redirect")
def debug_redirect():
    """Debug endpoint to check redirect URI configuration"""
    info = {
        "server_name": current_app.config.get("SERVER_NAME"),
        "static_folder": current_app.static_folder,
        "request_host": request.host,
        "request_url": request.url,
        "redirect_uri": _default_twitch_redirect_uri(),
        "configured_twitch_redirect_uri": _twitch_redirect_uri(),
        "twitch_callback_url": url_for("twitch_callback", _external=True),
    }
    return json.dumps(info, indent=2)


# ── History / analytics API ──────────────────────────────────────────────

@app.route("/api/history/series")
def history_series():
    game_id = request.args.get("game_id", "").strip()
    days = request.args.get("days", default=7, type=int)
    if not game_id:
        return jsonify({"error": "game_id required"}), 400
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    series = _history_store.get_series(game_id, days=days)
    return jsonify({"game_id": game_id, "days": days, "data": series})


@app.route("/api/history/heatmap")
def history_heatmap():
    game_id = request.args.get("game_id", "").strip()
    days = request.args.get("days", default=30, type=int)
    if not game_id:
        return jsonify({"error": "game_id required"}), 400
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    result = _history_store.get_heatmap(game_id, days=days)
    result["game_name"] = _history_store.get_game_name(game_id)
    return jsonify(result)


@app.route("/api/history/games")
def history_games():
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    games = _history_store.get_tracked_games()
    return jsonify({"games": games})


@app.route("/api/history/status")
def history_status():
    if not _collector:
        return jsonify({"running": False, "message": "collector not initialized"})
    return jsonify(_collector.status())


@app.route("/api/history/collect", methods=["POST"])
def history_collect():
    if not _collector:
        return jsonify({"success": False, "message": "collector not initialized"}), 500
    result = _collector.trigger_snapshot()
    return jsonify(result)


@app.route("/api/history/all-series")
def history_all_series():
    days = request.args.get("days", default=7, type=int)
    current_window_minutes = request.args.get(
        "current_window_minutes", default=0, type=int
    )
    min_avg_viewers = request.args.get("min_avg_viewers", default=0.0, type=float)
    min_avg_discovery = request.args.get("min_avg_discovery", default=0.0, type=float)
    max_avg_streams = request.args.get("max_avg_streams", default=0.0, type=float)
    limit = request.args.get("limit", default=0, type=int)
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    series = _history_store.get_all_series(
        days=days,
        current_window_minutes=current_window_minutes,
        min_avg_viewers=min_avg_viewers,
        min_avg_discovery=min_avg_discovery,
        max_avg_streams=max_avg_streams,
        limit=limit,
    )
    return jsonify({
        "days": days,
        "current_window_minutes": current_window_minutes,
        "min_avg_viewers": min_avg_viewers,
        "min_avg_discovery": min_avg_discovery,
        "max_avg_streams": max_avg_streams,
        "limit": limit,
        "games": series,
    })


@app.route("/api/history/opportunities")
def history_opportunities():
    if not _history_store:
        return jsonify({"error": "history store not initialized"}), 500
    min_peak = request.args.get("min_peak", default=50, type=int)
    max_avg_vps = request.args.get("max_avg_vps", default=200.0, type=float)
    min_live_fraction = request.args.get("min_live_fraction", default=0.1, type=float)
    min_snapshots = request.args.get("min_snapshots", default=30, type=int)
    sort_by = request.args.get("sort_by", default="avg_viewers_when_live", type=str)
    limit = request.args.get("limit", default=50, type=int)
    games = _history_store.get_opportunities(
        min_peak=min_peak,
        max_avg_viewers_per_stream=max_avg_vps,
        min_live_fraction=min_live_fraction,
        min_snapshots=min_snapshots,
        sort_by=sort_by,
        limit=limit,
    )
    return jsonify({
        "min_peak": min_peak,
        "max_avg_vps": max_avg_vps,
        "min_live_fraction": min_live_fraction,
        "min_snapshots": min_snapshots,
        "sort_by": sort_by,
        "limit": limit,
        "games": games,
    })


def parse_args():
    parser = argparse.ArgumentParser(description="Show owned Steam games with current Twitch viewers")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind the Flask app to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind the Flask app to")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--http", action="store_true", help="Run without local HTTPS. Twitch OAuth app registration requires an https:// redirect URL.")
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")
    return parser.parse_args()


def open_browser(host: str, port: int, scheme: str = "https"):
    browser_host = "localhost" if host in ["127.0.0.1", "0.0.0.0"] else host
    url = f"{scheme}://{browser_host}:{port}/"
    webbrowser.open(url)


def _ssl_context_for_args(args):
    if args.http:
        return None
    if LOCALHOST_CERT_FILE.is_file() and LOCALHOST_KEY_FILE.is_file():
        return (str(LOCALHOST_CERT_FILE), str(LOCALHOST_KEY_FILE))
    return "adhoc"


def main():
    global _collector, _history_store
    args = parse_args()
    ssl_context = _ssl_context_for_args(args)
    scheme = "http" if args.http else "https"
    if not args.no_browser:
        threading.Timer(1.0, open_browser, args=(args.host, args.port, scheme)).start()

    # Set SERVER_NAME to ensure consistent redirect URIs for OAuth
    # Convert bind addresses to localhost for OAuth redirects
    if args.host in ['127.0.0.1', '0.0.0.0']:
        server_host = 'localhost'
    else:
        server_host = args.host
    app.config['SERVER_NAME'] = f"{server_host}:{args.port}"

    # Initialize history store and collector
    _history_store = HistoryStore(config.HISTORY_DB_PATH)

    def _any_background_jobs_running() -> bool:
        for job in _background_jobs.values():
            if not job.get("done", False):
                return True
        return False

    _collector = Collector(can_collect=lambda: not _any_background_jobs_running())
    _collector.start()

    print(f"[STARTUP] Flask app starting on {scheme}://{args.host}:{args.port}", flush=True)
    print(f"[STARTUP] SERVER_NAME configured as: {app.config['SERVER_NAME']}", flush=True)
    print(f"[STARTUP] Static folder: {app.static_folder}", flush=True)
    print(f"[STARTUP] Twitch redirect URI will be: https://{server_host}:{args.port}/auth/twitch/callback", flush=True)
    print(f"[STARTUP] History collector started (interval: {config.COLLECTION_INTERVAL}s)", flush=True)

    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False, threaded=True, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
