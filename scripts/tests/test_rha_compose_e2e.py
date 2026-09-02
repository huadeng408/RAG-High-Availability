from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deployments" / "docker-compose.rha-e2e.yaml"


class RhaComposeE2ETest(unittest.TestCase):
    def test_runner_resolves_host_paths_before_starting_compose(self) -> None:
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
        bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
        if bash is None:
            self.skipTest("bash is required to exercise the E2E runner")

        def to_shell_path(path: Path) -> str:
            completed = subprocess.run(
                [
                    bash,
                    "-lc",
                    'if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; '
                    'elif command -v wslpath >/dev/null 2>&1; then wslpath -a "$1"; '
                    'else printf "%s\\n" "$1"; fi',
                    "rha-e2e-test",
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.strip()

        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            bin_dir = temp_dir / "bin"
            marker_path = temp_dir / "compose-started"
            invocation_log = temp_dir / "invocations.log"
            report_path = temp_dir / "report.json"
            bin_dir.mkdir()

            stubs = {
                "docker": """#!/usr/bin/env bash
printf '%s\\n' "docker $*" >> "$RHA_TEST_LOG"
if [[ " $* " == *" up "* ]]; then
  : > "$RHA_TEST_COMPOSE_MARKER"
fi
""",
                "wslpath": """#!/usr/bin/env bash
if [[ -e "$RHA_TEST_COMPOSE_MARKER" ]]; then
  echo "host path bridge unavailable after compose startup" >&2
  exit 9
fi
printf '%s\\n' "${@: -1}"
""",
                "python.exe": """#!/usr/bin/env bash
printf '%s\\n' "python $*" >> "$RHA_TEST_LOG"
""",
                "curl": "#!/usr/bin/env bash\nexit 0\n",
                "envsubst": "#!/usr/bin/env bash\ncat\n",
            }
            for name, contents in stubs.items():
                path = bin_dir / name
                path.write_text(contents, encoding="utf-8", newline="\n")
                path.chmod(0o755)

            root_shell = to_shell_path(ROOT)
            bin_shell = to_shell_path(bin_dir)
            marker_shell = to_shell_path(marker_path)
            log_shell = to_shell_path(invocation_log)
            report_shell = to_shell_path(report_path)
            command = " ".join(
                [
                    f"PATH={shlex.quote(bin_shell)}:/usr/bin:/bin",
                    f"RHA_TEST_COMPOSE_MARKER={shlex.quote(marker_shell)}",
                    f"RHA_TEST_LOG={shlex.quote(log_shell)}",
                    f"RHA_E2E_PYTHON={shlex.quote(bin_shell + '/python.exe')}",
                    f"RHA_E2E_REPORT={shlex.quote(report_shell)}",
                    "RHA_E2E_ADMIN_PASSWORD=compose-admin-password",
                    "bash",
                    shlex.quote(root_shell + "/scripts/run_rha_e2e.sh"),
                ]
            )
            completed = subprocess.run(
                [bash, "-lc", command],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            invocations = invocation_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(line.startswith("python ") for line in invocations), 2)
            self.assertTrue(any(" compose " in f" {line} " and " up " in f" {line} " for line in invocations))
            self.assertTrue(any(" compose " in f" {line} " and " down " in f" {line} " for line in invocations))

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
        self.assertEqual(orchestrator_env["RHA_IMAGE_OCR_URL"], "http://model-stub:8010/image/ocr")
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
