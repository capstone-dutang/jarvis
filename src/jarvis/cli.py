"""JARVIS CLI — thin client over REST API.

Commands:
  jarvis init                        — MCP auto-register + workspace connect
  jarvis login                       — OAuth browser auth
  jarvis logout                      — Clear local token
  jarvis whoami                      — Show current user
  jarvis workspace create <name>     — Create workspace
  jarvis workspace list              — List workspaces
  jarvis workspace use <name>        — Set active workspace
  jarvis workspace invite <email>    — Invite member
  jarvis recall <query>              — Natural language recall
  jarvis status                      — Workspace summary
"""

import json
import webbrowser
from pathlib import Path

import httpx
import typer

app = typer.Typer(name="jarvis", help="JARVIS — AI memory CLI")
workspace_app = typer.Typer(help="Workspace management")
app.add_typer(workspace_app, name="workspace")

CONFIG_DIR = Path.home() / ".jarvis"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict[str, str]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())  # type: ignore[no-any-return]
    return {}


def _save_config(config: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def _get_server() -> str:
    config = _load_config()
    return config.get("server", "http://localhost:8000")


def _get_headers() -> dict[str, str]:
    config = _load_config()
    token = config.get("access_token", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api(method: str, path: str, **kwargs: object) -> httpx.Response:
    url = f"{_get_server()}{path}"
    with httpx.Client() as client:
        resp = client.request(method, url, headers=_get_headers(), **kwargs)  # type: ignore[arg-type]
    return resp


# ── Auth Commands ──


@app.command()
def init(server: str = "http://localhost:8000") -> None:
    """Initialize JARVIS CLI and connect to server."""
    config = _load_config()
    config["server"] = server
    _save_config(config)

    # Health check
    try:
        resp = httpx.get(f"{server}/health")
        if resp.status_code == 200:
            typer.echo(f"Connected to JARVIS at {server}")
        else:
            typer.echo(f"Server responded with {resp.status_code}", err=True)
    except httpx.ConnectError:
        typer.echo(f"Cannot reach {server}. Is the server running?", err=True)
        raise typer.Exit(1) from None


@app.command()
def login() -> None:
    """Login via browser-based OAuth flow."""
    server = _get_server()
    # Register client
    resp = httpx.post(
        f"{server}/oauth/register",
        json={"client_name": "jarvis-cli", "redirect_uris": ["http://localhost:9876/callback"]},
    )
    if resp.status_code != 201:
        typer.echo(f"Client registration failed: {resp.text}", err=True)
        raise typer.Exit(1)

    client_data = resp.json()
    client_id = client_data["client_id"]

    # Open browser for authorization
    auth_url = f"{server}/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri=http://localhost:9876/callback&state=cli"
    typer.echo(f"Opening browser for login: {auth_url}")
    webbrowser.open(auth_url)
    typer.echo("After authorizing, paste the 'code' parameter from the redirect URL:")
    code = typer.prompt("Authorization code")

    # Exchange code for token
    resp = httpx.post(
        f"{server}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:9876/callback",
            "client_id": client_id,
        },
    )
    if resp.status_code != 200:
        typer.echo(f"Token exchange failed: {resp.text}", err=True)
        raise typer.Exit(1)

    tokens = resp.json()
    config = _load_config()
    config["access_token"] = tokens["access_token"]
    config["refresh_token"] = tokens.get("refresh_token", "")
    config["client_id"] = client_id
    _save_config(config)
    typer.echo("Logged in successfully.")


@app.command()
def logout() -> None:
    """Clear local credentials."""
    config = _load_config()
    config.pop("access_token", None)
    config.pop("refresh_token", None)
    _save_config(config)
    typer.echo("Logged out.")


@app.command()
def whoami() -> None:
    """Show current authenticated user."""
    config = _load_config()
    if "access_token" not in config:
        typer.echo("Not logged in. Run 'jarvis login' first.")
        raise typer.Exit(1)
    typer.echo("Authenticated (token present).")


# ── Workspace Commands ──


@workspace_app.command("create")
def workspace_create(name: str) -> None:
    """Create a new workspace."""
    resp = _api("POST", "/api/v1/workspaces", json={"name": name})
    if resp.status_code == 201:
        ws = resp.json()
        typer.echo(f"Created workspace: {ws['name']} (id: {ws['id']})")
        config = _load_config()
        config["workspace_id"] = ws["id"]
        _save_config(config)
    else:
        typer.echo(f"Failed: {resp.text}", err=True)


@workspace_app.command("list")
def workspace_list() -> None:
    """List all workspaces."""
    resp = _api("GET", "/api/v1/workspaces")
    if resp.status_code == 200:
        for ws in resp.json():
            typer.echo(f"  {ws['name']} — {ws['id']}")
    else:
        typer.echo(f"Failed: {resp.text}", err=True)


@workspace_app.command("use")
def workspace_use(workspace_id: str) -> None:
    """Set active workspace."""
    config = _load_config()
    config["workspace_id"] = workspace_id
    _save_config(config)
    typer.echo(f"Active workspace: {workspace_id}")


@workspace_app.command("invite")
def workspace_invite(email: str, role: str = "contributor") -> None:
    """Invite a user to the active workspace."""
    config = _load_config()
    ws_id = config.get("workspace_id")
    if not ws_id:
        typer.echo("No active workspace. Run 'jarvis workspace use <id>' first.", err=True)
        raise typer.Exit(1)

    resp = _api("POST", f"/api/v1/workspaces/{ws_id}/members", json={"email": email, "role": role})
    if resp.status_code == 201:
        typer.echo(f"Invited {email} as {role}")
    else:
        typer.echo(f"Failed: {resp.text}", err=True)


# ── Memory Commands ──


@app.command()
def recall(query: str) -> None:
    """Recall memories with natural language query."""
    config = _load_config()
    ws_id = config.get("workspace_id")
    if not ws_id:
        typer.echo("No active workspace. Run 'jarvis workspace use <id>' first.", err=True)
        raise typer.Exit(1)

    resp = _api("POST", "/api/v1/memory/recall", json={"workspace_id": ws_id, "query": query})
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        if not results:
            typer.echo("No memories found.")
            return
        for r in results:
            tag = "[grounded]" if r["grounded"] else "[low_trust]"
            typer.echo(f"  {r['entity']} {r['predicate']} {r['object_value']} {tag}")
            if r.get("evidence"):
                typer.echo(f"    source: {r['evidence']['excerpt'][:100]}...")
    else:
        typer.echo(f"Failed: {resp.text}", err=True)


@app.command()
def status() -> None:
    """Show workspace summary."""
    config = _load_config()
    ws_id = config.get("workspace_id")
    if not ws_id:
        typer.echo("No active workspace.", err=True)
        raise typer.Exit(1)

    resp = _api("POST", "/api/v1/memory/initialize", json={"workspace_id": ws_id})
    if resp.status_code == 200:
        data = resp.json()
        typer.echo(f"Workspace: {data['workspace_name']}")
        if data.get("recent_summary"):
            typer.echo(f"Recent: {data['recent_summary']}")
    else:
        typer.echo(f"Failed: {resp.text}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
