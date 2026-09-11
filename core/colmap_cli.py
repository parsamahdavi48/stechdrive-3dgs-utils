"""COLMAP launcher handling shared by GUI and internal jobs."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

WINDOWS_BATCH_SUFFIXES = {".bat", ".cmd"}
CMD_META_CHARACTERS = "^&|<>()"
CMD_BATCH_ARGUMENT_PREFIX = ("/d", "/v:off", "/s", "/c")


def _escape_unquoted_cmd_argument(argument: str) -> str:
    if any(character.isspace() for character in argument):
        return argument
    escaped = argument
    for character in CMD_META_CHARACTERS:
        escaped = escaped.replace(character, f"^{character}")
    return escaped


def _unescape_cmd_argument(argument: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(argument):
        if (
            argument[index] == "^"
            and index + 1 < len(argument)
            and argument[index + 1] in CMD_META_CHARACTERS
        ):
            result.append(argument[index + 1])
            index += 2
        else:
            result.append(argument[index])
            index += 1
    return "".join(result)


def colmap_batch_qprocess_native_arguments(command: Sequence[str]) -> str | None:
    """Return cmd.exe native arguments when Qt's generic quoting is insufficient."""

    if len(command) < 6 or tuple(part.lower() for part in command[1:5]) != CMD_BATCH_ARGUMENT_PREFIX:
        return None
    if Path(command[0]).name.lower() not in {"cmd", "cmd.exe"}:
        return None
    values = [_unescape_cmd_argument(value) for value in command[5:]]
    command_line = " ".join(f'"{value}"' for value in values)
    return " ".join((*CMD_BATCH_ARGUMENT_PREFIX, f'"{command_line}"'))


def prefer_official_windows_launcher(executable: str) -> str:
    """Prefer the package-level COLMAP.bat next to an official Windows build."""

    path = Path(executable)
    if os.name != "nt" or path.suffix.lower() != ".exe" or path.name.lower() != "colmap.exe":
        return executable
    if not path.is_file():
        return executable

    candidates = (path.parent / "COLMAP.bat", path.parent.parent / "COLMAP.bat")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return executable


def _active_batch_lines(path: Path) -> list[str] | None:
    """Return non-comment batch lines, or None when the wrapper cannot be read."""

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return None
    active = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r"^rem(?:\s|$)", line, re.I) and not any(char in line for char in "&|<>^"):
            continue
        active.append(line)
    return active


def _official_windows_colmap_executable(launcher: str) -> Path | None:
    """Resolve the known-safe official wrapper to its packaged executable.

    The official Windows wrapper has an unsafe empty-argument conditional.
    Match its active lines deliberately narrowly so custom launchers
    retain their existing cmd.exe behavior.
    """

    if os.name != "nt":
        return None
    wrapper = Path(launcher)
    if wrapper.suffix.lower() != ".bat" or wrapper.name.lower() != "colmap.bat" or not wrapper.is_file():
        return None
    executable = wrapper.parent / "bin" / "colmap.exe"
    if not executable.is_file():
        return None

    lines = _active_batch_lines(wrapper)
    if lines is None or len(lines) != 7:
        return None
    patterns = (
        r"^@echo\s+off$",
        r"^set\s+SCRIPT_PATH=%~dp0$",
        r"^set\s+PATH=%SCRIPT_PATH%\\bin;%PATH%$",
        r"^set\s+QT_PLUGIN_PATH=%SCRIPT_PATH%\\plugins;%QT_PLUGIN_PATH%$",
        r"^set\s+ARGUMENTS=%\*$",
        r'^if\s+"(?:%ARGUMENTS%|%~1)"==""\s+set\s+ARGUMENTS=gui$',
        r'^"%SCRIPT_PATH%\\bin\\colmap"\s+%ARGUMENTS%$',
    )
    if all(re.fullmatch(pattern, line, re.I) for pattern, line in zip(patterns, lines, strict=True)):
        return executable.resolve()
    return None


def _recognized_official_colmap_executable(command: Sequence[str]) -> Path | None:
    if os.name != "nt" or not command:
        return None
    executable = Path(command[0])
    if executable.suffix.lower() != ".exe" or executable.name.lower() != "colmap.exe" or not executable.is_file():
        return None
    wrapper = executable.parent.parent / "COLMAP.bat"
    resolved = _official_windows_colmap_executable(str(wrapper))
    return executable.resolve() if resolved == executable.resolve() else None


def colmap_process_environment(command: Sequence[str]) -> dict[str, str]:
    """Return inherited process environment for a recognized official executable."""

    environment = dict(os.environ)
    executable = _recognized_official_colmap_executable(command)
    if executable is None:
        return environment

    def prepend(name: str, value: str) -> None:
        existing_name = next((key for key in environment if key.lower() == name.lower()), name)
        existing_value = environment.get(existing_name, "")
        environment[existing_name] = value if not existing_value else value + os.pathsep + existing_value

    package = executable.parent.parent
    prepend("PATH", str(executable.parent))
    prepend("QT_PLUGIN_PATH", str(package / "plugins"))
    return environment


def build_colmap_command(launcher: str, *arguments: str | Path) -> list[str]:
    """Build a process command for a COLMAP executable or Windows batch launcher."""

    launcher_text = prefer_official_windows_launcher(str(launcher))
    args = [str(argument) for argument in arguments]
    executable = _official_windows_colmap_executable(launcher_text)
    if executable is not None:
        return [str(executable), *(args or ["gui"])]
    if os.name == "nt" and Path(launcher_text).suffix.lower() in WINDOWS_BATCH_SUFFIXES:
        command_processor = os.environ.get("COMSPEC") or "cmd.exe"
        command_arguments = [_escape_unquoted_cmd_argument(value) for value in (launcher_text, *args)]
        return [command_processor, *CMD_BATCH_ARGUMENT_PREFIX, *command_arguments]
    return [launcher_text, *args]
