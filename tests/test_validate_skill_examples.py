from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-skill-examples.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_skill_examples", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_skill_examples"] = module
    spec.loader.exec_module(module)
    return module


validator = load_validator()


class SkillValidatorTests(unittest.TestCase):
    def _skill(self, root: Path, name: str, body: str | None = None) -> Path:
        skill_dir = root / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            body
            or f"---\nname: {name}\ndescription: Test skill.\n---\n\n# Test\n",
            encoding="utf-8",
        )
        return skill_dir

    def _messages(self, findings) -> list[str]:
        return [finding.message for finding in findings]

    def test_oversized_skill_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(Path(tmp), "sample-skill")
            text = "---\nname: sample-skill\ndescription: Test.\n---\n" + "\n".join(
                f"line {i}" for i in range(501)
            )
            (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
            findings = []

            validator.validate_skill_structure(skill_dir, findings)

            self.assertIn("SKILL.md must be under 500 lines", self._messages(findings))

    def test_invalid_skill_name_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(Path(tmp), "Bad_Name")
            findings = []

            validator.validate_skill_structure(skill_dir, findings)

            self.assertTrue(any("skill directory name" in msg for msg in self._messages(findings)))

    def test_missing_and_mismatched_frontmatter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(
                Path(tmp),
                "sample-skill",
                "---\nname: other-skill\n---\n\n# Test\n",
            )
            findings = []

            validator.validate_skill_structure(skill_dir, findings)

            messages = self._messages(findings)
            self.assertIn("frontmatter missing 'description'", messages)
            self.assertTrue(any("does not match directory" in msg for msg in messages))

    def test_allowed_public_metadata_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(
                Path(tmp),
                "sample-skill",
                "---\n"
                "name: sample-skill\n"
                "description: Test.\n"
                "license: Apache-2.0\n"
                "metadata:\n"
                "  version: 1.0.0\n"
                "  author: Hydrolix\n"
                "---\n\n# Test\n",
            )
            findings = []

            validator.validate_skill_structure(skill_dir, findings)

            self.assertEqual([], findings)

    def test_broken_local_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(Path(tmp), "sample-skill")
            (skill_dir / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Test.\n---\n\n"
                "[Missing](references/missing.md)\n",
                encoding="utf-8",
            )
            findings = []

            validator.validate_local_links(skill_dir, findings)

            self.assertTrue(any("broken local markdown link" in msg for msg in self._messages(findings)))

    def test_long_reference_without_navigation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(Path(tmp), "sample-skill")
            ref_dir = skill_dir / "references"
            ref_dir.mkdir()
            (ref_dir / "long.md").write_text("# Long\n\n" + "\n".join("text" for _ in range(101)), encoding="utf-8")
            findings = []

            validator.validate_reference_navigation(skill_dir, findings)

            self.assertTrue(any("over 100 lines" in msg for msg in self._messages(findings)))

    def test_bare_repo_local_python_command_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = self._skill(Path(tmp), "sample-skill")
            (skill_dir / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Test.\n---\n\n"
                "```bash\npython scripts/tool.py --help\n```\n",
                encoding="utf-8",
            )
            findings = []

            validator.validate_repo_python_commands(skill_dir, findings)

            self.assertTrue(any("uv run python" in msg for msg in self._messages(findings)))

    def test_readme_lists_missing_and_omits_existing_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._skill(root, "sample-skill")
            (root / "README.md").write_text(
                "| Skill | Description |\n"
                "|-------|-------------|\n"
                "| [ghost-skill](skills/ghost-skill/) | Missing |\n",
                encoding="utf-8",
            )
            findings = []

            validator.validate_readme_skill_table(root, [skill_dir], findings)

            messages = self._messages(findings)
            self.assertTrue(any("missing 'sample-skill'" in msg for msg in messages))
            self.assertTrue(any("missing skill directory 'ghost-skill'" in msg for msg in messages))

    def test_generated_site_skills_json_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._skill(root, "sample-skill")
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "generate-site.sh").write_text(
                "#!/bin/bash\n"
                "set -e\n"
                "mkdir -p site\n"
                "cat > site/skills.json <<'JSON'\n"
                '{"skills":[]}\n'
                "JSON\n"
                "echo '<html>missing card</html>' > site/index.html\n",
                encoding="utf-8",
            )
            findings = []

            validator.validate_site(root, [skill_dir], findings)

            messages = self._messages(findings)
            self.assertTrue(any("does not match current skills" in msg for msg in messages))
            self.assertTrue(any("missing skill 'sample-skill'" in msg for msg in messages))

    def test_missing_or_stale_openai_yaml_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = self._skill(root, "missing-meta")
            stale = self._skill(root, "stale-meta")
            agent_dir = stale / "agents"
            agent_dir.mkdir()
            (agent_dir / "openai.yaml").write_text(
                "interface:\n"
                "  display_name: Stale\n"
                "  short_description: Test\n"
                "  default_prompt: Use $other-skill.\n",
                encoding="utf-8",
            )
            findings = []

            validator.validate_openai_metadata([missing, stale], findings)

            messages = self._messages(findings)
            self.assertTrue(any("missing agents/openai.yaml" in msg for msg in messages))
            self.assertTrue(any("default_prompt must reference $stale-meta" in msg for msg in messages))


if __name__ == "__main__":
    unittest.main()
