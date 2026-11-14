"""Static manual-workflow boundaries; no Docker, provider or forge execution."""

from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def workflow_parts(text):
    """Read only the repository's fixed literal step layout, not general YAML."""
    if "\t" in text or text.count("    steps:\n") != 1:
        raise ValueError("unsupported workflow structure")
    header, body = text.split("    steps:\n")
    header = "\n".join(line for line in header.splitlines()
                       if line.strip() and not line.lstrip().startswith("#"))
    steps = re.split(r"(?=^      - )", body, flags=re.MULTILINE)
    if steps[0] or not all(step.startswith("      - ") for step in steps[1:]):
        raise ValueError("unsupported workflow steps")
    return header, [step.rstrip("\n") for step in steps[1:]]


def execution_contract(text, publication):
    header, steps = workflow_parts(text)
    _, original_steps = workflow_parts(publication)
    # No inputs, extra triggers/jobs, job permission override or environment.
    # Formatting outside this small supported layout needs an explicit review.
    if not re.fullmatch(
        r"name: [^\n]+\n"
        r"on:\n  workflow_dispatch:\n"
        r"permissions:\n  contents: read\n"
        r"concurrency:\n  group: managed-publication-qualification\n  cancel-in-progress: false\n"
        r"jobs:\n  managed-publication-execution:\n    name: [^\n]+\n"
        r"    if: github\.repository_id == '1453104676' && github\.ref == 'refs/heads/main'\n"
        r"    runs-on: ubuntu-latest\n    timeout-minutes: 25", header
    ):
        raise ValueError("manual permission and admission boundary changed")
    if re.search(r"(?m)^\s*env:|GITHUB_TOKEN|GH_TOKEN|GITHUB_PUBLICATION_TOKEN|github\.token|secrets\.", text):
        raise ValueError("execution workflow must not introduce credentials")
    publisher = next((index for index, step in enumerate(original_steps)
                      if step.startswith("      - name: Publish and replay from the original private captures\n")), None)
    if publisher != 7 or len(steps) != 8 or steps[:-1] != original_steps[:publisher]:
        raise ValueError("execution must reuse the approved setup and execute steps")
    if sum(step.startswith("      - uses: actions/checkout@v4\n") for step in steps[:-1]) != 3 \
            or sum("          persist-credentials: false\n" in step + "\n" for step in steps[:-1]) != 3:
        raise ValueError("every source checkout must omit persisted credentials")
    if sum("check_publication_stack.py execute" in step for step in steps[:-1]) != 1:
        raise ValueError("exactly one existing execute invocation is required")
    expected_upload = "\n".join([
        "      - name: Retain only safe aggregate execution evidence",
        "        if: always()",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: managed-publication-execution-evidence",
        "          path: |",
        "            ${{ runner.temp }}/managed-publication-trial/execution-report.json",
        "            ${{ runner.temp }}/managed-publication-trial/failure-report.json",
        "          if-no-files-found: warn",
        "          retention-days: 14",
    ])
    if steps[-1] != expected_upload:
        raise ValueError("only the two safe execution reports may be retained")


class PublicationExecutionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.execution = (WORKFLOWS / "managed-publication-execution.yml").read_text()
        cls.publication = (WORKFLOWS / "managed-publication.yml").read_text()

    def reject_changes(self, changes):
        for before, after in changes:
            with self.subTest(change=(before, after)):
                self.assertIn(before, self.execution)
                changed = self.execution.replace(before, after, 1)
                with self.assertRaises(ValueError):
                    execution_contract(changed, self.publication)

    def test_manual_execution_reuses_the_approved_unprivileged_steps(self):
        execution_contract(self.execution, self.publication)

    def test_automatic_inputs_foreign_scope_and_cancellation_are_rejected(self):
        self.reject_changes([
            ("  workflow_dispatch:", "  push:\n  workflow_dispatch:"),
            ("  workflow_dispatch:", "  workflow_dispatch:\n    inputs:\n      command:\n        required: true"),
            ("github.repository_id == '1453104676'", "github.repository_id == '1'"),
            ("github.ref == 'refs/heads/main'", "github.ref == 'refs/heads/other'"),
            ("group: managed-publication-qualification", "group: separate-publication"),
            ("cancel-in-progress: false", "cancel-in-progress: true"),
            ("    steps:", "    steps:\n    steps:"),
        ])

    def test_permission_escalation_and_credential_environment_are_rejected(self):
        self.reject_changes([
            ("  contents: read", "  contents: write"),
            ("  contents: read", "  contents: read\n  pull-requests: write"),
            ("    steps:", "    permissions:\n      contents: write\n    steps:"),
            ("    steps:", "    env:\n      GITHUB_PUBLICATION_TOKEN: ${{ github.token }}\n    steps:"),
            ("        run: docker info", "        env:\n          GH_TOKEN: ${{ secrets.FORGE_TOKEN }}\n        run: docker info"),
            ("          persist-credentials: false", "          persist-credentials: true"),
        ])

    def test_pin_image_execute_or_additional_command_drift_is_rejected(self):
        self.reject_changes([
            ("ref: ${{ github.sha }}", "ref: main"),
            ("ref: 0dd5bf4e42b3677c3f2f564655bc87a97c4bf973", "ref: main"),
            ("ref: 6480e36355bf0d5de4e25a560d40e665fd76038c", "ref: main"),
            ("PYTHON_BASE=python:3.12-slim", "PYTHON_BASE=python:latest"),
            ("check_publication_stack.py execute", "check_publication_stack.py publish"),
            ("check_publication_stack.py execute", "check_publication_stack.py observe"),
            ("check_publication_stack.py execute", "different_harness.py execute"),
            ("      - name: Retain only safe aggregate execution evidence",
             "      - run: python zpm/tests/contracts/check_publication_stack.py publish\n"
             "      - name: Retain only safe aggregate execution evidence"),
            ("        run: docker info", "        run: |\n          docker info\n          python unreviewed.py"),
        ])

    def test_private_or_publication_artifact_exports_are_rejected(self):
        prefix = "            ${{ runner.temp }}/managed-publication-trial/"
        self.reject_changes([
            (prefix + "execution-report.json", prefix + name)
            for name in ("publication-report.json", "handoff.json", "installation/", "*.json", "publisher-first.log")
        ] + [
            ("        if: always()", "        if: success()"),
            ("          retention-days: 14", "          retention-days: 14\n          include-hidden-files: true"),
        ])
