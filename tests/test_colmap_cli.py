from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QProcess

from core.colmap_cli import (
    build_colmap_command,
    colmap_batch_qprocess_native_arguments,
    colmap_process_environment,
    prefer_official_windows_launcher,
)


def test_colmap_executable_command_stays_direct() -> None:
    assert build_colmap_command("colmap.exe", "version") == ["colmap.exe", "version"]


def _write_official_wrapper(path: Path, condition: str = '%ARGUMENTS%') -> None:
    path.write_text(
        "\n".join(
            (
                "@echo off",
                "rem official COLMAP wrapper",
                "",
                "set SCRIPT_PATH=%~dp0",
                "set PATH=%SCRIPT_PATH%\\bin;%PATH%",
                "set QT_PLUGIN_PATH=%SCRIPT_PATH%\\plugins;%QT_PLUGIN_PATH%",
                "set ARGUMENTS=%*",
                f'if "{condition}"=="" set ARGUMENTS=gui',
                '"%SCRIPT_PATH%\\bin\\colmap" %ARGUMENTS%',
            )
        ),
        encoding="utf-8",
    )


@pytest.mark.skipif(os.name != "nt", reason="official COLMAP.bat handling is Windows-specific")
def test_stock_official_wrapper_uses_direct_executable_and_package_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "COLMAP package"
    executable = package / "bin" / "colmap.exe"
    launcher = package / "COLMAP.bat"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    _write_official_wrapper(launcher)
    original_wrapper = launcher.read_text(encoding="utf-8")
    monkeypatch.setenv("Path", "existing-bin")
    monkeypatch.setenv("QT_PLUGIN_PATH", "existing-plugins")

    assert prefer_official_windows_launcher(str(executable)) == str(launcher)
    command = build_colmap_command(str(executable), "version")
    assert command == [str(executable.resolve()), "version"]
    environment = colmap_process_environment(command)
    path_key = next(key for key in environment if key.lower() == "path")
    assert environment[path_key] == str(executable.parent) + os.pathsep + "existing-bin"
    assert environment["QT_PLUGIN_PATH"] == str(package / "plugins") + os.pathsep + "existing-plugins"
    assert launcher.read_text(encoding="utf-8") == original_wrapper

    assert build_colmap_command(str(executable)) == [str(executable.resolve()), "gui"]


@pytest.mark.skipif(os.name != "nt", reason="official COLMAP.bat handling is Windows-specific")
@pytest.mark.parametrize("condition", ["%ARGUMENTS%", "%~1"])
def test_stock_and_manually_fixed_official_wrappers_are_recognized(tmp_path: Path, condition: str) -> None:
    package = tmp_path / "COLMAP package"
    executable = package / "bin" / "colmap.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    _write_official_wrapper(package / "COLMAP.bat", condition)

    model_path = "C:/COLMAP package/مدل with spaces/images"
    assert build_colmap_command(
        str(package / "COLMAP.bat"),
        "feature_extractor",
        "--ImageReader.camera_params",
        "1920,1920,1919.5,1919.5",
        model_path,
    ) == [
        str(executable.resolve()),
        "feature_extractor",
        "--ImageReader.camera_params",
        "1920,1920,1919.5,1919.5",
        model_path,
    ]


@pytest.mark.skipif(os.name != "nt", reason="official COLMAP.bat handling is Windows-specific")
@pytest.mark.parametrize("customization", ["argument", "comment"])
def test_unrecognized_official_looking_or_custom_wrapper_stays_batch(tmp_path: Path, customization: str) -> None:
    package = tmp_path / "COLMAP package"
    executable = package / "bin" / "colmap.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    launcher = package / "COLMAP.bat"
    _write_official_wrapper(launcher)
    source = launcher.read_text(encoding="utf-8")
    if customization == "argument":
        source = source.replace("set ARGUMENTS=%*", "set ARGUMENTS=custom")
    else:
        source += "\nrem custom & set CUSTOM=1\n"
    launcher.write_text(source, encoding="utf-8")

    command = build_colmap_command(str(launcher), "gui")
    assert Path(command[0]).name.lower() == "cmd.exe"
    assert command[5:] == [str(launcher), "gui"]
    assert colmap_process_environment(command) == dict(os.environ)


@pytest.mark.skipif(os.name != "nt", reason="batch launcher execution is Windows-specific")
def test_colmap_batch_launcher_preserves_spaces_and_metacharacters(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "COLMAP&package" / "COLMAP.bat"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("@echo off\necho \"[%~1]\" \"[%~2]\" \"[%~3]\"\n", encoding="utf-8")

    command = build_colmap_command(str(launcher), "feature_extractor", "value with spaces", "value&meta")
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert '"[feature_extractor]" "[value with spaces]" "[value&meta]"' in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="batch launcher execution is Windows-specific")
def test_colmap_batch_launcher_runs_through_qprocess(
    tmp_path: Path,
) -> None:
    QCoreApplication.instance() or QCoreApplication([])
    launcher = tmp_path / "COLMAP & package" / "COLMAP.bat"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("@echo off\necho [%~1] [%~2]\n", encoding="utf-8")
    command = build_colmap_command(str(launcher), "gui", "model with spaces")

    process = QProcess()
    process.setProgram(command[0])
    native_arguments = colmap_batch_qprocess_native_arguments(command)
    assert native_arguments is not None
    process.setNativeArguments(native_arguments)
    process.start()

    assert process.waitForStarted(3000), process.errorString()
    assert process.waitForFinished(3000), process.errorString()
    output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
    error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
    assert process.exitCode() == 0, output + error
    assert "[gui] [model with spaces]" in output
