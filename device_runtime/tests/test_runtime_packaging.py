"""Standalone packaging smoke tests for the Raspberry runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import venv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = PROJECT_ROOT / "device_runtime"


def test_runtime_package_installs_and_bootstraps_without_backend_repo(tmp_path: Path) -> None:
    standalone_root = tmp_path / "device_runtime_standalone"
    standalone_root.mkdir()

    for relative_path in [
        ".env.example",
        "README.md",
        "deploy/device-runtime.service",
        "pyproject.toml",
        "requirements-base.txt",
        "requirements-raspi.txt",
        "scripts/deploy_raspberry.sh",
        "scripts/install_raspberry.sh",
        "scripts/run_runtime.sh",
        "scripts/smoke_check.sh",
    ]:
        (standalone_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUNTIME_ROOT / relative_path, standalone_root / relative_path)
    shutil.copytree(RUNTIME_ROOT / "src", standalone_root / "src")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    python_bin = venv_dir / "bin" / "python"

    install_env = os.environ.copy()
    install_env.pop("PYTHONPATH", None)
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--no-build-isolation", "--no-deps", "."],
        cwd=standalone_root,
        env=install_env,
        check=True,
        capture_output=True,
        text=True,
    )

    smoke_code = textwrap.dedent(
        """
        import importlib.util
        import json
        from pathlib import Path

        import device_runtime
        from device_runtime.entrypoints.raspi_main import build_hello_payload, build_runtime

        assert importlib.util.find_spec("backend") is None
        runtime = build_runtime(
            {
                "DEVICE_ID": "raspi-packaging-smoke",
                "DEVICE_WS_URL": "ws://127.0.0.1:8000/ws",
            }
        )
        hello = build_hello_payload(runtime)
        package_root = Path(next(iter(device_runtime.__path__))).resolve()
        print(
            json.dumps(
                {
                    "device_id": runtime.snapshot.device_id,
                    "hello_type": hello["type"],
                    "package_root": str(package_root),
                }
            )
        )
        """
    )
    smoke_env = os.environ.copy()
    smoke_env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(python_bin), "-c", smoke_code],
        cwd=tmp_path,
        env=smoke_env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["device_id"] == "raspi-packaging-smoke"
    assert payload["hello_type"] == "device.hello"
    assert "site-packages" in payload["package_root"]
    assert str(PROJECT_ROOT) not in payload["package_root"]


def test_runtime_package_installs_smoke_console_script(tmp_path: Path) -> None:
    standalone_root = tmp_path / "device_runtime_standalone"
    standalone_root.mkdir()

    for relative_path in [
        ".env.example",
        "README.md",
        "pyproject.toml",
        "requirements-base.txt",
        "requirements-raspi.txt",
    ]:
        shutil.copy2(RUNTIME_ROOT / relative_path, standalone_root / relative_path)
    shutil.copytree(RUNTIME_ROOT / "src", standalone_root / "src")

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    python_bin = venv_dir / "bin" / "python"
    smoke_bin = venv_dir / "bin" / "device-runtime-smoke"

    install_env = os.environ.copy()
    install_env.pop("PYTHONPATH", None)
    subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--no-build-isolation", "--no-deps", "."],
        cwd=standalone_root,
        env=install_env,
        check=True,
        capture_output=True,
        text=True,
    )

    smoke_env = os.environ.copy()
    smoke_env.pop("PYTHONPATH", None)
    smoke_env["DEVICE_ID"] = "raspi-smoke"
    smoke_env["DEVICE_WS_URL"] = "ws://127.0.0.1:8000/ws"
    result = subprocess.run(
        [str(smoke_bin), "--skip-network", "--json"],
        cwd=tmp_path,
        env=smoke_env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["device_id"] == "raspi-smoke"
    assert payload["network_ok"] is None
    assert payload["hello_type"] == "device.hello"


def test_runtime_shell_wrappers_prefer_local_env_file(tmp_path: Path) -> None:
    runtime_root = tmp_path / "device_runtime"
    scripts_dir = runtime_root / "scripts"
    venv_bin = runtime_root / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    venv_bin.mkdir(parents=True)

    for relative_path in ["scripts/run_runtime.sh", "scripts/smoke_check.sh"]:
        shutil.copy2(RUNTIME_ROOT / relative_path, runtime_root / relative_path)

    (runtime_root / ".env").write_text(
        "DEVICE_ID=raspi-local\nDEVICE_WS_URL=ws://192.168.1.50:8000/ws\n",
        encoding="utf-8",
    )
    (venv_bin / "device-runtime").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "print(json.dumps({'device_id': os.environ['DEVICE_ID'], 'ws_url': os.environ['DEVICE_WS_URL']}))\n",
        encoding="utf-8",
    )
    (venv_bin / "device-runtime-smoke").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "print(json.dumps({'device_id': os.environ['DEVICE_ID'], 'ws_url': os.environ['DEVICE_WS_URL']}))\n",
        encoding="utf-8",
    )
    (venv_bin / "device-runtime").chmod(0o755)
    (venv_bin / "device-runtime-smoke").chmod(0o755)

    result = subprocess.run(
        [str(scripts_dir / "run_runtime.sh")],
        cwd=runtime_root,
        env={"PATH": os.environ["PATH"]},
        check=True,
        capture_output=True,
        text=True,
    )
    smoke_result = subprocess.run(
        [str(scripts_dir / "smoke_check.sh")],
        cwd=runtime_root,
        env={"PATH": os.environ["PATH"]},
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout.strip()) == {
        "device_id": "raspi-local",
        "ws_url": "ws://192.168.1.50:8000/ws",
    }
    assert json.loads(smoke_result.stdout.strip()) == {
        "device_id": "raspi-local",
        "ws_url": "ws://192.168.1.50:8000/ws",
    }


def test_runtime_shell_wrappers_export_vendor_driver_path_to_pythonpath(tmp_path: Path) -> None:
    runtime_root = tmp_path / "device_runtime"
    scripts_dir = runtime_root / "scripts"
    venv_bin = runtime_root / ".venv" / "bin"
    scripts_dir.mkdir(parents=True)
    venv_bin.mkdir(parents=True)

    for relative_path in ["scripts/run_runtime.sh", "scripts/smoke_check.sh"]:
        shutil.copy2(RUNTIME_ROOT / relative_path, runtime_root / relative_path)

    driver_root = tmp_path / "Whisplay" / "Driver"
    driver_root.mkdir(parents=True)
    (runtime_root / ".env").write_text(
        "DEVICE_ID=raspi-local\n"
        "DEVICE_WS_URL=ws://192.168.1.50:8000/ws\n"
        f"DEVICE_WHISPLAY_DRIVER_PATH={driver_root}\n",
        encoding="utf-8",
    )
    script_body = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "print(json.dumps({'pythonpath': os.environ.get('PYTHONPATH', '')}))\n"
    )
    (venv_bin / "device-runtime").write_text(script_body, encoding="utf-8")
    (venv_bin / "device-runtime-smoke").write_text(script_body, encoding="utf-8")
    (venv_bin / "device-runtime").chmod(0o755)
    (venv_bin / "device-runtime-smoke").chmod(0o755)

    result = subprocess.run(
        [str(scripts_dir / "run_runtime.sh")],
        cwd=runtime_root,
        env={"PATH": os.environ["PATH"]},
        check=True,
        capture_output=True,
        text=True,
    )
    smoke_result = subprocess.run(
        [str(scripts_dir / "smoke_check.sh")],
        cwd=runtime_root,
        env={"PATH": os.environ["PATH"]},
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout.strip())["pythonpath"].split(":")[0] == str(driver_root)
    assert json.loads(smoke_result.stdout.strip())["pythonpath"].split(":")[0] == str(driver_root)


def test_raspberry_shell_scripts_have_valid_bash_syntax() -> None:
    for relative_path in [
        "scripts/deploy_raspberry.sh",
        "scripts/install_raspberry.sh",
        "scripts/run_runtime.sh",
        "scripts/smoke_check.sh",
    ]:
        subprocess.run(
            ["bash", "-n", str(RUNTIME_ROOT / relative_path)],
            cwd=RUNTIME_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
