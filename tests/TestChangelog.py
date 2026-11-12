import os
import unittest

import pyappify
from pyappify import changelog, standalone

from TestStandalone import StandaloneRepoTestCase, git, tag_versions, write

CHANGELOG_EN = """\
# Changelog

## v1.2.0 (2026-11-12)
### Added
- Dark theme
- Faster duplicate
  detection

## [1.1.0] - 2026-10-01
- English 1.1.0 note

# Footer
- not a note
"""

CHANGELOG_JA = """\
# 更新履歴

## v1.2.0
- ダークテーマ
- 重複検出の高速化
"""


class TestChangelogParsing(unittest.TestCase):
    def test_parses_versions_notes_and_continuations(self):
        notes = changelog.parse_changelog(CHANGELOG_EN)

        self.assertEqual(
            {"1.2.0": ["Dark theme", "Faster duplicate detection"], "1.1.0": ["English 1.1.0 note"]},
            notes,
        )

    def test_version_key_ignores_brackets_and_v_prefix(self):
        self.assertEqual("1.2.0", changelog.version_key("[v1.2.0]"))
        self.assertEqual("1.2.0", changelog.version_key("v1.2.0"))
        self.assertEqual("version-2", changelog.version_key("version-2"))

    def test_paths(self):
        self.assertEqual("CHANGELOG.md", changelog.changelog_path("en"))
        self.assertEqual("CHANGELOG.zh-CN.md", changelog.changelog_path("zh-CN"))

    def test_missing_translation_falls_back_to_english_per_version(self):
        changelogs = {
            "en": changelog.parse_changelog(CHANGELOG_EN),
            "ja": changelog.parse_changelog(CHANGELOG_JA),
        }

        self.assertEqual(
            ["ダークテーマ", "重複検出の高速化", "English 1.1.0 note"],
            changelog.notes_for_versions(changelogs, ["v1.2.0", "v1.1.0", "v1.0.0"], "ja"),
        )
        self.assertEqual(
            ["Dark theme", "Faster duplicate detection"],
            changelog.notes_for_versions(changelogs, ["v1.2.0"], "ko"),
        )


class TestCalculateLocalizedNotes(unittest.TestCase):
    def test_uses_localized_notes_for_the_locale(self):
        versions = [
            {"version": "v1.2.0", "update_note": ["Dark theme"],
             "localized_notes": {"en": ["Dark theme"], "zh-CN": ["深色主题"]}},
            {"version": "v1.1.0", "update_note": ["commit note"]},
            {"version": "v1.0.0", "update_note": ["initial"]},
        ]

        self.assertEqual(
            ["深色主题", "commit note"],
            pyappify.calculate_update_notes(versions, "v1.1.0", "v1.2.0", language="zh_CN"),
        )
        self.assertEqual(
            ["Dark theme", "commit note"],
            pyappify.calculate_update_notes(versions, "v1.1.0", "v1.2.0"),
        )
        self.assertEqual(["Dark theme"], pyappify.entry_notes(versions[0], "ja_JP"))


class TestStandaloneChangelog(StandaloneRepoTestCase):
    """v1.2.0 adds changelogs with entries for v1.2.0 and v1.1.0."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.remote, "CHANGELOG.md"), CHANGELOG_EN)
        write(os.path.join(self.remote, "CHANGELOG.ja.md"), CHANGELOG_JA)
        self.add_remote_release("v1.2.0")
        self.commit_file("app.txt", "next\n", "Unreleased work")

    def versions(self, language):
        return standalone.get_version_list(
            release_only=False, repo_path=self.clone, language=language
        )

    def test_tag_notes_come_from_the_changelog_in_the_language(self):
        versions = tag_versions(self.versions("ja"))

        self.assertEqual("v1.2.0", versions[0]["version"])
        self.assertEqual(["ダークテーマ", "重複検出の高速化"], versions[0]["update_note"])
        self.assertEqual(
            ["Dark theme", "Faster duplicate detection"], versions[0]["localized_notes"]["en"]
        )
        self.assertEqual(versions[0]["localized_notes"]["en"], versions[0]["localized_notes"]["ko"])

    def test_tags_without_a_changelog_keep_commit_messages(self):
        versions = tag_versions(self.versions("ja"))

        self.assertEqual("v1.1.0", versions[1]["version"])
        self.assertCountEqual(["Release 1.1.0", "Side change"], versions[1]["update_note"])
        self.assertNotIn("localized_notes", versions[1])

    def test_branches_keep_commit_messages(self):
        branch = [v for v in self.versions("ja") if v["kind"] == "branch"][0]

        self.assertIn("Unreleased work", branch["update_note"])
        self.assertNotIn("localized_notes", branch)

    def test_update_notes_cover_every_version_in_the_range(self):
        self.versions("en")  # fetches v1.2.0
        with standalone.open_repo(self.clone) as repo:
            notes = standalone.notes_for_update(
                repo,
                standalone.tag_commit(repo, "v1.2.0"),
                standalone.tag_commit(repo, "v1.0.0"),
                "ja",
            )

        self.assertEqual(["ダークテーマ", "重複検出の高速化", "English 1.1.0 note"], notes)

    def test_languages_without_an_entry_use_commit_messages(self):
        write(os.path.join(self.remote, "CHANGELOG.ja.md"), "## v1.3.0\n- 日本語のみ\n" + CHANGELOG_JA)
        self.add_remote_release("v1.3.0")

        latest = tag_versions(self.versions("ko"))[0]

        self.assertEqual("v1.3.0", latest["version"])
        self.assertEqual(["日本語のみ"], latest["localized_notes"]["ja"])
        self.assertEqual(["Release v1.3.0", "Unreleased work"], latest["update_note"])

    def test_downgrade_shows_the_target_entry(self):
        git(self.clone, "fetch", "-q", "--tags", "origin")
        with standalone.open_repo(self.clone) as repo:
            target = standalone.tag_commit(repo, "v1.2.0")
            notes = standalone.notes_for_update(
                repo, target, repo.refs[b"refs/remotes/origin/main"], "en"
            )

        self.assertEqual(["Dark theme", "Faster duplicate detection"], notes)


if __name__ == "__main__":
    unittest.main()
