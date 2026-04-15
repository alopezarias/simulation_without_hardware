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
        [str(python_bin), "-m", "pip", "install", "setuptools", "wheel"],
        cwd=standalone_root,
        env=install_env,
        check=True,
        capture_output=True,
        text=True,
    )
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
        [str(python_bin), "-m", "pip", "install", "setuptools", "wheel"],
        cwd=standalone_root,
        env=install_env,
        check=True,
        capture_output=True,
        text=True,
    )
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


def test_install_raspberry_bootstraps_pisugar_support_from_installer(tmp_path: Path) -> None:
    source_root = tmp_path / "device_runtime_source"
    install_root = tmp_path / "device_runtime_install"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()

    for relative_path in [
        ".env.example",
        "README.md",
        "deploy/device-runtime.service",
        "pyproject.toml",
        "requirements-base.txt",
        "requirements-raspi.txt",
        "scripts/install_raspberry.sh",
        "scripts/run_runtime.sh",
        "scripts/smoke_check.sh",
    ]:
        (source_root / relative_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUNTIME_ROOT / relative_path, source_root / relative_path)
    shutil.copytree(RUNTIME_ROOT / "src", source_root / "src")

    stub_scripts = {
        "id": "#!/bin/sh\ncase \"${1:-}\" in\n  -u) printf '0\\n' ;;\n  -un) printf 'pi\\n' ;;\n  -gn) printf 'pi\\n' ;;\n  *) /usr/bin/id \"$@\" ;;\nesac\n",
        "systemctl": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_SYSTEMCTL_LOG\"\nif [ \"${1:-}\" = 'list-unit-files' ]; then\n  exit 1\nfi\nexit 0\n",
        "chown": "#!/bin/sh\nexit 0\n",
        "apt-get": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_APT_LOG\"\nexit 0\n",
        "debconf-set-selections": "#!/bin/sh\ncat >> \"$TEST_DEBCONF_LOG\"\n",
        "raspi-config": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_RASPI_CONFIG_LOG\"\nexit 0\n",
        "modprobe": "#!/bin/sh\nexit 0\n",
        "wget": "#!/bin/sh\ndest=''\nwhile [ \"$#\" -gt 0 ]; do\n  if [ \"$1\" = '-O' ]; then\n    dest=\"$2\"\n    shift 2\n    continue\n  fi\n  shift\ndone\ncp \"$TEST_PISUGAR_INSTALLER\" \"$dest\"\nchmod +x \"$dest\"\n",
    }
    for name, body in stub_scripts.items():
        path = fakebin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    fake_installer = tmp_path / "pisugar-installer.sh"
    fake_installer.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" > \"$TEST_PISUGAR_INSTALLER_ARGS\"\n"
        "mkdir -p \"$(dirname \"$DEVICE_RUNTIME_PISUGAR_DEFAULTS_PATH\")\"\n"
        "printf 'DAEMON_ARGS=\\\"--tcp 0.0.0.0:8423\\\"\\n' > \"$DEVICE_RUNTIME_PISUGAR_DEFAULTS_PATH\"\n"
        "touch \"$TEST_PISUGAR_INSTALL_MARKER\"\n",
        encoding="utf-8",
    )
    fake_installer.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fakebin}:{env['PATH']}",
            "SUDO_USER": "pi",
            "DEVICE_RUNTIME_INSTALL_ROOT": str(install_root),
            "DEVICE_RUNTIME_SERVICE_PATH": str(tmp_path / "device-runtime.service"),
            "DEVICE_RUNTIME_INSTALL_RASPI_EXTRAS": "0",
            "DEVICE_RUNTIME_ENABLE_SERVICE": "1",
            "DEVICE_RUNTIME_RESTART_SERVICE": "1",
            "DEVICE_RUNTIME_INSTALL_PISUGAR": "1",
            "DEVICE_RUNTIME_PISUGAR_MODEL": "PiSugar 3",
            "DEVICE_RUNTIME_PISUGAR_DEFAULTS_PATH": str(tmp_path / "etc/default/pisugar-server"),
            "TEST_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
            "TEST_APT_LOG": str(tmp_path / "apt.log"),
            "TEST_DEBCONF_LOG": str(tmp_path / "debconf.log"),
            "TEST_RASPI_CONFIG_LOG": str(tmp_path / "raspi-config.log"),
            "TEST_PISUGAR_INSTALLER": str(fake_installer),
            "TEST_PISUGAR_INSTALLER_ARGS": str(tmp_path / "pisugar-installer-args.log"),
            "TEST_PISUGAR_INSTALL_MARKER": str(tmp_path / "pisugar-installed"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )

    subprocess.run(
        ["bash", str(source_root / "scripts/install_raspberry.sh")],
        cwd=source_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert (tmp_path / "pisugar-installed").exists()
    assert "-c release" in (tmp_path / "pisugar-installer-args.log").read_text(encoding="utf-8")
    assert "--model 'PiSugar 3'" in (tmp_path / "etc/default/pisugar-server").read_text(encoding="utf-8")
    service_text = (tmp_path / "device-runtime.service").read_text(encoding="utf-8")
    assert "After=network-online.target pisugar-server.service" in service_text
    systemctl_log = (tmp_path / "systemctl.log").read_text(encoding="utf-8")
    assert "enable pisugar-server.service" in systemctl_log
    assert "restart pisugar-server.service" in systemctl_log
    assert "enable device-runtime.service" in systemctl_log
