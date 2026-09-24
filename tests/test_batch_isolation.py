import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import batch_generate as batch
import batch_retry_tts as retry
import generate_video_from_report as generate


class BatchIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, self.previous_cwd)

    def args(self, config):
        return SimpleNamespace(
            config=str(config), intro=None, genre="daily_brief",
            output="result.mp4", voice=None, rate=None, max_videos=0,
        )

    def test_same_second_batch_commands_read_their_own_scripts(self):
        def save(name):
            work = self.root / name
            work.mkdir()
            args = self.args(work / "_config.yaml")
            result = generate._save_script_json({"title": name}, args)
            return name, work, generate._topic_main_cmd(result, args)

        with patch.object(generate, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 23, 12, 41, 36)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(save, ["A", "B"]))

        # Read only after BOTH writers finish: the original code reads B for A.
        for name, work, command in results:
            script = Path(command[command.index("--script-json") + 1])
            self.assertEqual(script, work / "script.json")
            self.assertEqual(json.loads(script.read_text(encoding="utf-8"))["title"], name)
            self.assertEqual(retry._resolve_script(work, set()), script)
        archives = list(Path("output").glob("script_*.json"))
        self.assertEqual(len(archives), 2)
        self.assertEqual(
            {json.loads(p.read_text(encoding="utf-8"))["title"] for p in archives},
            {"A", "B"},
        )

    def test_non_batch_scripts_saved_in_same_second_remain_distinct(self):
        with patch.object(generate, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 23, 12, 41, 36)
            paths = [generate._save_script_json({"title": n}, self.args("config.yaml"))
                     for n in ["A", "B"]]
        self.assertNotEqual(*paths)
        self.assertEqual([json.loads(p.read_text(encoding="utf-8"))["title"] for p in paths], ["A", "B"])

    def test_repeated_and_truncated_topics_have_distinct_outputs_and_work_dirs(self):
        config = self.root / "config.yaml"
        config.write_text("tts:\n  output_dir: output/audio\n", encoding="utf-8")
        topics = ["x" * 40 + "A", "x" * 40 + "B", "x" * 40 + "A"]
        with patch.object(batch, "THIS_DIR", self.root), \
                patch.object(batch, "datetime") as clock, \
                patch.object(batch.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run:
            clock.now.return_value = datetime(2026, 9, 23, 12, 41, 19)
            results = [batch.run_single_topic(t, str(config), [], i, 3)
                       for i, t in enumerate(topics, 1)]
        self.assertEqual(len({r["output"] for r in results}), 3)
        configs = [Path(c.args[0][c.args[0].index("--config") + 1]) for c in run.call_args_list]
        self.assertEqual(len(set(configs)), 3)
        for path, result in zip(configs, results):
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent.name, Path(result["output"]).stem)
            self.assertEqual(retry._parse_work_ts(path.parent.name), "20260923_124119")

    def test_retry_matches_legacy_and_unique_archive_names(self):
        for suffix in ["", "_" + "a" * 32]:
            name = "topic_20260923_124136" + suffix
            self.assertEqual(retry._parse_work_ts(name), "20260923_124136")
            self.assertEqual(retry._topic_prefix(name), "topic")
            match = retry.SCRIPT_TS_RE.match("script_20260923_124136" + suffix + ".json")
            self.assertIsNotNone(match)
            self.assertEqual(match.group(1), "20260923_124136")


if __name__ == "__main__":
    unittest.main()
