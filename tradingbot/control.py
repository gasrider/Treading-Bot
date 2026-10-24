"""Steuerdateien zwischen Dashboard und Bot.

Das Dashboard schreibt nur Dateien in state/, der Bot liest sie bei jedem Durchlauf.
So braucht das Dashboard keinen Zugriff auf die Börsen-Schlüssel.
"""
import json
import uuid
from pathlib import Path

STATE_DIR = Path("state")
KILL_SWITCH = STATE_DIR / "STOP"        # anlegen = alles verkaufen, Handel stoppen
RESUME_FILE = STATE_DIR / "RESUME"      # anlegen = Stopp/Notbremse aufheben
PAUSE_FILE = STATE_DIR / "PAUSE"        # vorhanden = keine neuen Käufe (Stops bleiben aktiv)
COMMANDS_DIR = STATE_DIR / "commands"   # einzelne Aufträge, z. B. eine Position schließen


def queue_command(action: str, **params) -> Path:
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    path = COMMANDS_DIR / f"{uuid.uuid4().hex}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"action": action, **params}), encoding="utf-8")
    tmp.replace(path)
    return path


def take_commands() -> list[dict]:
    """Alle wartenden Aufträge lesen und entfernen (älteste zuerst)."""
    if not COMMANDS_DIR.exists():
        return []
    commands = []
    for path in sorted(COMMANDS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            commands.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        path.unlink(missing_ok=True)
    return commands
