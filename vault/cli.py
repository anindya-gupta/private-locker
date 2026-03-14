"""
CLI interface for Vault using Typer + Rich.

Commands:
  vault init       - First-time setup
  vault unlock     - Unlock the vault
  vault chat       - Interactive chat mode
  vault store      - Store a document file
  vault cred       - Manage credentials
  vault facts      - List stored facts
  vault docs       - List stored documents
  vault lock       - Lock the vault
  vault serve      - Start the web UI server
  vault backup     - Create encrypted backup
  vault restore    - Restore from backup
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from vault.config import VaultConfig, config
from vault.security.encryption import derive_all_keys, generate_verification_token
from vault.security.session import session

console = Console()
app = typer.Typer(
    name="vault",
    help="Vault — Your secure personal AI assistant.",
    no_args_is_help=True,
)


def _get_agent():
    from vault.agent import VaultAgent
    agent = VaultAgent(config, session)
    agent.initialize()
    return agent


def _ensure_initialized():
    if not config.salt_path.exists():
        rprint("[red]Vault not initialized. Run 'vault init' first.[/red]")
        raise typer.Exit(1)


def _ensure_unlocked():
    _ensure_initialized()
    if session.is_locked:
        rprint("[yellow]Vault is locked. Unlocking...[/yellow]")
        salt = config.salt_path.read_bytes()
        token = config.token_path.read_bytes()
        session.configure(salt, token, config.session_timeout)
        password = getpass.getpass("Master password: ")
        if not session.unlock(password):
            rprint("[red]Incorrect password.[/red]")
            raise typer.Exit(1)
        rprint("[green]Unlocked.[/green]")


@app.command()
def init():
    """Initialize a new Vault with a master password."""
    if config.salt_path.exists():
        rprint("[yellow]Vault already initialized at:[/yellow]", str(config.vault_dir))
        overwrite = Prompt.ask("Reinitialize? This will DESTROY all data", choices=["yes", "no"], default="no")
        if overwrite != "yes":
            raise typer.Exit(0)

    rprint(Panel("Welcome to [bold]Vault[/bold] — your secure personal AI", border_style="blue"))
    rprint()
    rprint("Choose a strong master password. This is the ONLY way to access your data.")
    rprint("[dim]If you forget it, your data is permanently lost.[/dim]")
    rprint()

    while True:
        password = getpass.getpass("Create master password: ")
        if len(password) < 8:
            rprint("[red]Password must be at least 8 characters.[/red]")
            continue
        confirm = getpass.getpass("Confirm master password: ")
        if password != confirm:
            rprint("[red]Passwords don't match. Try again.[/red]")
            continue
        break

    rprint("[dim]Deriving encryption keys (this takes a moment)...[/dim]")
    keys = derive_all_keys(password)
    token = generate_verification_token(password, keys.salt)

    config.ensure_dirs()
    config.salt_path.write_bytes(keys.salt)
    config.token_path.write_bytes(token)
    config.save()

    session.configure(keys.salt, token, config.session_timeout)
    session.unlock(password)

    agent = _get_agent()
    agent.shutdown()

    rprint()
    rprint(Panel("[green]Vault initialized successfully![/green]\n\n"
                 f"Data directory: {config.vault_dir}\n"
                 "Run [bold]vault chat[/bold] to start chatting.",
                 border_style="green"))


@app.command()
def unlock():
    """Unlock the vault."""
    _ensure_initialized()
    salt = config.salt_path.read_bytes()
    token = config.token_path.read_bytes()
    session.configure(salt, token, config.session_timeout)
    password = getpass.getpass("Master password: ")
    if session.unlock(password):
        rprint("[green]Vault unlocked.[/green]")
    else:
        rprint("[red]Incorrect password.[/red]")
        raise typer.Exit(1)


@app.command()
def lock():
    """Lock the vault."""
    session.lock()
    rprint("[green]Vault locked.[/green]")


@app.command()
def chat():
    """Start an interactive chat session with Vault."""
    _ensure_unlocked()
    agent = _get_agent()

    rprint(Panel("[bold]Vault Chat[/bold] — Type your message, 'quit' to exit, 'lock' to lock.",
                 border_style="blue"))

    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                user_input = Prompt.ask("\n[bold blue]You[/bold blue]")
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("quit", "exit", "bye"):
                break
            if user_input.strip().lower() in ("lock", "lock vault"):
                session.lock()
                rprint("[green]Vault locked. Goodbye![/green]")
                break

            try:
                response = loop.run_until_complete(agent.process(user_input))
                rprint(f"\n[bold green]Vault[/bold green]: {response.text}")
            except PermissionError:
                rprint("[red]Session expired. Please unlock again.[/red]")
                break
            except Exception as e:
                rprint(f"[red]Error: {e}[/red]")
    finally:
        loop.close()
        agent.shutdown()


@app.command()
def store(
    file: Path = typer.Argument(..., help="Path to file to store"),
    name: str = typer.Option("", help="Name for the document"),
):
    """Store a document in the vault."""
    _ensure_unlocked()

    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    agent = _get_agent()
    loop = asyncio.new_event_loop()
    try:
        file_data = file.read_bytes()
        display_name = name or file.name
        response = loop.run_until_complete(
            agent.process(display_name, file_data=file_data, file_name=file.name)
        )
        rprint(f"\n[green]{response.text}[/green]")
    finally:
        loop.close()
        agent.shutdown()


@app.command()
def docs():
    """List all stored documents."""
    _ensure_unlocked()
    agent = _get_agent()

    docs_list = agent.db.list_documents(session.keys.db_key)
    if not docs_list:
        rprint("[dim]No documents stored yet.[/dim]")
        return

    table = Table(title="Stored Documents")
    table.add_column("Name", style="cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Tags", style="dim")

    for d in docs_list:
        table.add_row(d["name"], d["category"], ", ".join(d.get("tags", [])))

    console.print(table)
    agent.shutdown()


@app.command()
def cred(
    action: str = typer.Argument("list", help="Action: list, add, get, delete"),
    service: str = typer.Option("", help="Service name"),
):
    """Manage stored credentials."""
    _ensure_unlocked()
    agent = _get_agent()
    keys = session.keys

    if action == "list":
        creds = agent.cred_manager.list_all(keys.cred_key)
        if not creds:
            rprint("[dim]No credentials stored.[/dim]")
            return
        table = Table(title="Stored Credentials")
        table.add_column("Service", style="cyan")
        table.add_column("Username", style="green")
        table.add_column("URL", style="dim")
        for c in creds:
            table.add_row(c["service"], c.get("username", "N/A"), c.get("url", ""))
        console.print(table)

    elif action == "add":
        svc = service or Prompt.ask("Service name")
        username = Prompt.ask("Username (optional)", default="")
        password = getpass.getpass("Password (optional): ")
        url = Prompt.ask("URL (optional)", default="")
        agent.cred_manager.store(
            service=svc,
            cred_key=keys.cred_key,
            username=username or None,
            password=password or None,
            url=url or None,
        )
        rprint(f"[green]Credential for '{svc}' stored.[/green]")

    elif action == "get":
        svc = service or Prompt.ask("Service name")
        c = agent.cred_manager.get(svc, keys.cred_key)
        if c:
            rprint(Panel(
                CredentialManager_format(c),
                title=f"Credentials: {c['service']}",
                border_style="green",
            ))
        else:
            rprint(f"[red]No credentials for '{svc}'.[/red]")

    elif action == "delete":
        svc = service or Prompt.ask("Service name")
        c = agent.cred_manager.get(svc, keys.cred_key)
        if c:
            confirm = Prompt.ask(f"Delete credentials for '{svc}'?", choices=["yes", "no"])
            if confirm == "yes":
                agent.cred_manager.delete(c["id"])
                rprint(f"[green]Deleted credentials for '{svc}'.[/green]")
        else:
            rprint(f"[red]No credentials for '{svc}'.[/red]")

    agent.shutdown()


def CredentialManager_format(cred: dict) -> str:
    from vault.processors.credentials import CredentialManager
    return CredentialManager.format_credential(cred, mask_password=False)


@app.command()
def facts():
    """List all stored personal facts."""
    _ensure_unlocked()
    agent = _get_agent()
    all_facts = agent.memory.list_all(session.keys.db_key)
    if not all_facts:
        rprint("[dim]No facts stored yet. Tell me something about yourself![/dim]")
    else:
        rprint(MemoryManager_format(all_facts))
    agent.shutdown()


def MemoryManager_format(facts_list: list) -> str:
    from vault.processors.memory import MemoryManager
    return MemoryManager.format_facts(facts_list)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to listen on"),
):
    """Start the Vault web UI server."""
    _ensure_initialized()
    rprint(Panel(
        f"Starting Vault web UI at [bold]http://{host}:{port}[/bold]\n"
        "Press Ctrl+C to stop.",
        border_style="blue",
    ))
    import uvicorn
    uvicorn.run("vault.main:app", host=host, port=port, reload=False)


@app.command()
def mcp():
    """Start Vault as an MCP server (for Claude, Cursor, etc.)."""
    _ensure_initialized()
    rprint(Panel(
        "[bold]Starting Vault MCP Server[/bold]\n\n"
        "Vault is now available as MCP tools for Claude, Cursor, and other MCP clients.\n"
        "Add this to your MCP client config to connect.",
        border_style="blue",
    ))
    from vault.mcp_server import run_mcp_server
    run_mcp_server()


@app.command()
def backup(
    output: Optional[Path] = typer.Option(None, help="Output path for backup file"),
):
    """Create an encrypted backup of the vault."""
    _ensure_unlocked()
    from vault.backup import create_backup
    rprint("[dim]Creating backup...[/dim]")
    path = create_backup(config, output)
    rprint(f"[green]Backup saved to:[/green] {path}")
    rprint(f"[dim]Size: {path.stat().st_size / 1024:.1f} KB[/dim]")


@app.command()
def restore(
    backup_file: Path = typer.Argument(..., help="Path to .vbak backup file"),
):
    """Restore vault from an encrypted backup."""
    if not backup_file.exists():
        rprint(f"[red]Backup file not found: {backup_file}[/red]")
        raise typer.Exit(1)

    rprint(f"[yellow]This will REPLACE your current vault data with the backup.[/yellow]")
    confirm = Prompt.ask("Continue?", choices=["yes", "no"], default="no")
    if confirm != "yes":
        raise typer.Exit(0)

    from vault.backup import restore_backup
    restore_backup(backup_file, config)
    rprint("[green]Vault restored from backup. Please unlock to verify.[/green]")


def main():
    app()


if __name__ == "__main__":
    main()
