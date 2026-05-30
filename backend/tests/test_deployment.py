"""Deployment configuration correctness tests.

These tests assert that the static config files (Caddyfile, docker-compose.yml,
Dockerfiles, nginx.conf, .env.example) are structurally correct and contain
the directives required for the production stack to work.  No external tools
(docker, caddy, nginx) need to be installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ── helpers ───────────────────────────────────────────────────────────────────

def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _exists(rel: str) -> bool:
    return (REPO / rel).is_file()


# ── Caddyfile ─────────────────────────────────────────────────────────────────

class TestCaddyfile:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read("Caddyfile")

    def test_caddyfile_exists(self):
        assert _exists("Caddyfile")

    def test_uses_domain_env_var_with_localhost_default(self):
        assert "{$DOMAIN:localhost}" in self._text

    def test_proxies_to_frontend(self):
        assert "reverse_proxy frontend:80" in self._text

    def test_no_hardcoded_domain(self):
        # The active (non-comment) config lines must not contain a literal domain.
        active_lines = [l for l in self._text.splitlines() if l.strip() and not l.strip().startswith("#")]
        active = "\n".join(active_lines)
        assert "example.com" not in active


# ── docker-compose.yml ────────────────────────────────────────────────────────

class TestDockerCompose:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read("docker-compose.yml")

    def test_compose_file_exists(self):
        assert _exists("docker-compose.yml")

    def test_has_backend_service(self):
        assert "backend:" in self._text

    def test_has_frontend_service(self):
        assert "frontend:" in self._text

    def test_has_caddy_service(self):
        assert "caddy:" in self._text

    def test_backend_only_exposes_not_publishes(self):
        # Backend must not bind a host port; only reachable on the docker network
        lines = self._text.splitlines()
        in_backend = False
        backend_has_ports = False
        for line in lines:
            if line.strip() == "backend:":
                in_backend = True
            elif line.strip().endswith(":") and not line.strip().startswith("#") and in_backend:
                in_backend = False
            if in_backend and "ports:" in line:
                backend_has_ports = True
        assert not backend_has_ports, "backend should use expose:, not ports:"

    def test_frontend_only_exposes_not_publishes(self):
        lines = self._text.splitlines()
        in_frontend = False
        frontend_has_ports = False
        for line in lines:
            if line.strip() == "frontend:":
                in_frontend = True
            elif line.strip().endswith(":") and not line.strip().startswith("#") and in_frontend:
                in_frontend = False
            if in_frontend and "ports:" in line:
                frontend_has_ports = True
        assert not frontend_has_ports, "frontend should use expose:, not ports:"

    def test_caddy_publishes_80_and_443(self):
        assert '"80:80"' in self._text or "'80:80'" in self._text or "80:80" in self._text
        assert '"443:443"' in self._text or "'443:443'" in self._text or "443:443" in self._text

    def test_caddy_has_udp_443_for_http3(self):
        assert "443:443/udp" in self._text

    def test_caddy_mounts_caddyfile(self):
        assert "Caddyfile:/etc/caddy/Caddyfile" in self._text

    def test_caddy_has_persistent_data_volume(self):
        assert "caddy_data" in self._text

    def test_caddy_depends_on_frontend(self):
        # Quick structural check — caddy block must reference frontend
        caddy_idx = self._text.find("caddy:")
        after_caddy = self._text[caddy_idx:]
        assert "frontend" in after_caddy

    def test_frontend_depends_on_backend_healthy(self):
        assert "service_healthy" in self._text

    def test_backend_has_healthcheck(self):
        assert "healthcheck:" in self._text

    def test_volumes_section_declares_caddy_data(self):
        # The top-level volumes: section must list caddy_data
        volumes_idx = self._text.rfind("volumes:")
        assert volumes_idx != -1
        after_volumes = self._text[volumes_idx:]
        assert "caddy_data" in after_volumes

    def test_uses_image_tag_env_var(self):
        assert "IMAGE_TAG" in self._text

    def test_domain_env_var_forwarded_to_caddy(self):
        assert "DOMAIN" in self._text


# ── Dockerfile (backend) ──────────────────────────────────────────────────────

class TestDockerfileBackend:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read("Dockerfile")

    def test_backend_dockerfile_exists(self):
        assert _exists("Dockerfile")

    def test_uses_multistage_build(self):
        assert self._text.count("FROM ") >= 2

    def test_exposes_port_8000(self):
        assert "EXPOSE 8000" in self._text

    def test_runs_backend_module(self):
        assert "backend.run" in self._text


# ── Dockerfile.frontend ───────────────────────────────────────────────────────

class TestDockerfileFrontend:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read("Dockerfile.frontend")

    def test_frontend_dockerfile_exists(self):
        assert _exists("Dockerfile.frontend")

    def test_uses_node_builder_stage(self):
        assert "node:" in self._text.lower()

    def test_uses_nginx_runtime_stage(self):
        assert "nginx:" in self._text.lower()

    def test_copies_nginx_conf(self):
        assert "nginx.conf" in self._text

    def test_exposes_port_80(self):
        assert "EXPOSE 80" in self._text

    def test_multistage_build(self):
        assert self._text.count("FROM ") >= 2


# ── frontend/nginx.conf ───────────────────────────────────────────────────────

class TestNginxConf:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read("frontend/nginx.conf")

    def test_nginx_conf_exists(self):
        assert _exists("frontend/nginx.conf")

    def test_spa_fallback_serves_index_html(self):
        assert "try_files" in self._text
        assert "/index.html" in self._text

    def test_proxies_api_to_backend(self):
        assert "proxy_pass http://backend:8000" in self._text

    def test_websocket_upgrade_headers(self):
        assert "Upgrade" in self._text
        assert "Connection" in self._text
        assert 'proxy_set_header Upgrade $http_upgrade' in self._text

    def test_websocket_location_block(self):
        assert "location /ws/" in self._text

    def test_api_location_covers_notes_audio_health(self):
        assert "notes" in self._text
        assert "audio" in self._text
        assert "health" in self._text

    def test_static_assets_cache_headers(self):
        assert "Cache-Control" in self._text
        assert "immutable" in self._text


# ── .env.example ─────────────────────────────────────────────────────────────

class TestEnvExample:
    @pytest.fixture(autouse=True)
    def content(self):
        self._text = _read(".env.example")

    def test_env_example_exists(self):
        assert _exists(".env.example")

    def test_has_domain_variable(self):
        assert "DOMAIN=" in self._text

    def test_has_dockerhub_backend_image(self):
        assert "DOCKERHUB_BACKEND_IMAGE=" in self._text

    def test_has_dockerhub_frontend_image(self):
        assert "DOCKERHUB_FRONTEND_IMAGE=" in self._text

    def test_has_image_tag(self):
        assert "IMAGE_TAG=" in self._text

    def test_has_api_token(self):
        assert "NOTES_API_TOKEN=" in self._text

    def test_has_ai_provider(self):
        assert "AI_PROVIDER=" in self._text

    def test_has_whisper_model(self):
        assert "WHISPER_MODEL=" in self._text
