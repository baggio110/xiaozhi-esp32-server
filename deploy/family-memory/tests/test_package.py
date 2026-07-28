from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "family-memory"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "family-memory-images-ci.yml"
RELEASE_WORKFLOW = (
    ROOT / ".github" / "workflows" / "family-memory-images-release.yml"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FamilyMemoryPackageContractTest(unittest.TestCase):
    def test_official_two_image_structure_is_preserved(self):
        server = read(PACKAGE / "Dockerfile.server")
        web = read(PACKAGE / "Dockerfile.web")

        self.assertIn("COPY main/xiaozhi-server/", server)
        self.assertIn("main/manager-web", web)
        self.assertIn("main/manager-api", web)
        self.assertEqual(read(PACKAGE / "compose.family-memory.yml").count("image:"), 2)

    def test_compose_override_only_replaces_images(self):
        compose = read(PACKAGE / "compose.family-memory.yml")
        forbidden = (
            "volumes:",
            "ports:",
            "networks:",
            "environment:",
            "container_name:",
            "password",
            "mysql",
            "redis",
        )

        for token in forbidden:
            self.assertNotIn(token, compose.lower())
        self.assertIn("${FAMILY_MEMORY_SERVER_IMAGE:?", compose)
        self.assertIn("${FAMILY_MEMORY_WEB_IMAGE:?", compose)

    def test_ci_has_read_only_permissions_and_never_pushes(self):
        workflow = read(CI_WORKFLOW)

        self.assertRegex(workflow, r"permissions:\s*\n\s+contents: read")
        self.assertNotIn("packages: write", workflow)
        self.assertEqual(workflow.count("push: false"), 2)
        self.assertNotIn("docker push ", workflow)
        self.assertIn("family-memory-v0.9.6", workflow)
        self.assertIn("pull_request:", workflow)

    def test_release_is_fixed_tag_only_and_pushes_after_validation(self):
        workflow = read(RELEASE_WORKFLOW)

        self.assertIn("'v0.9.6-family-memory.*'", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertRegex(workflow, r"permissions:\s*\n\s+contents: read\s*\n\s+packages: write")
        self.assertIn("secrets.GITHUB_TOKEN", workflow)
        self.assertGreater(workflow.index("docker push "), workflow.index("verify-images.sh"))
        self.assertGreater(workflow.index("docker push "), workflow.index("verify-compose.sh"))

    def test_every_third_party_action_is_pinned_to_full_sha(self):
        for workflow_path in (CI_WORKFLOW, RELEASE_WORKFLOW):
            uses_lines = [
                line.strip()
                for line in read(workflow_path).splitlines()
                if line.strip().startswith("uses:")
            ]
            self.assertTrue(uses_lines)
            for line in uses_lines:
                self.assertRegex(line, r"^uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")

    def test_no_custom_image_or_workflow_uses_latest_or_arm64(self):
        paths = (
            PACKAGE / "Dockerfile.server",
            PACKAGE / "Dockerfile.web",
            PACKAGE / "compose.family-memory.yml",
            CI_WORKFLOW,
            RELEASE_WORKFLOW,
        )
        source = "\n".join(read(path) for path in paths).lower()

        self.assertNotIn("latest", source)
        self.assertNotIn("linux/arm64", source)
        self.assertIn("linux/amd64", source)

    def test_server_image_is_pinned_and_excludes_runtime_residue(self):
        dockerfile = read(PACKAGE / "Dockerfile.server")
        verifier = read(PACKAGE / "verify-images.sh")

        self.assertIn('"powermem==0.5.3"', dockerfile)
        self.assertIn("rm -rf data tests .test_tmp", dockerfile)
        self.assertIn("docker run --rm -i --read-only", verifier)
        for suffix in ("*.db", "*.sqlite", "*.wal", "*.shm", "*.pyc"):
            self.assertIn(suffix, dockerfile)

    def test_images_require_oci_identity_and_base_labels(self):
        for name in ("Dockerfile.server", "Dockerfile.web"):
            dockerfile = read(PACKAGE / name)
            for label in (
                "org.opencontainers.image.source",
                "org.opencontainers.image.revision",
                "org.opencontainers.image.version",
                "org.opencontainers.image.base.name",
            ):
                self.assertIn(label, dockerfile)

    def test_workflow_paths_are_family_memory_scoped(self):
        workflow = read(CI_WORKFLOW)
        for path in (
            "deploy/family-memory/**",
            "main/xiaozhi-server/core/family_identity/**",
            "main/manager-api/src/main/java/xiaozhi/modules/familymemory/**",
            "main/manager-web/src/views/FamilyMemory.vue",
        ):
            self.assertIn(path, workflow)

    def test_package_contains_no_embedded_credentials(self):
        source = "\n".join(
            read(path)
            for path in PACKAGE.rglob("*")
            if path.is_file()
        )
        patterns = (
            r"ghp_[A-Za-z0-9]{20,}",
            r"github_pat_[A-Za-z0-9_]{20,}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        )

        for pattern in patterns:
            self.assertIsNone(re.search(pattern, source))

    def test_quick_start_keeps_data_and_identity_database(self):
        readme = read(PACKAGE / "README.md")

        for requirement in (
            "v0.9.6",
            "PowerMem `0.5.3`",
            "data",
            "MySQL",
            "uploadfile",
            "family_identity.db",
            "device_id",
            "家庭记忆关闭",
            "环境检查",
            "恢复官方镜像",
        ):
            self.assertIn(requirement, readme)


if __name__ == "__main__":
    unittest.main()
