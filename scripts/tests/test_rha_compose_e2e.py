from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deployments" / "docker-compose.rha-e2e.yaml"


class RhaComposeE2ETest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "docker is required to render the E2E compose file")
    def test_compose_uses_production_ingestion_and_exposes_alias_readback(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "RHA_E2E_PASSWORD": "compose-test-password",
                "RHA_E2E_JWT_SECRET": "compose-test-jwt",
                "RHA_E2E_MINIO_USER": "compose-test-minio",
                "RHA_E2E_MINIO_PASSWORD": "compose-test-minio-password",
                "RHA_E2E_INTERNAL_TOKEN": "compose-test-internal",
                "RHA_E2E_CONFIG_PATH": str(ROOT / "configs" / "config.rha-docker-e2e.yaml"),
            }
        )
        completed = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        config = json.loads(completed.stdout)

        orchestrator_env = config["services"]["orchestrator"]["environment"]
        self.assertEqual(orchestrator_env["RHA_INGESTION_MODE"], "production")
        published_ports = {
            str(port.get("published"))
            for port in config["services"]["es"].get("ports", [])
        }
        self.assertIn("9200", published_ports)
        self.assertIn("/var/lib/mysql", config["services"]["mysql"].get("tmpfs", []))
        self.assertIn(
            "/usr/share/elasticsearch/data",
            config["services"]["es"].get("tmpfs", []),
        )


if __name__ == "__main__":
    unittest.main()
