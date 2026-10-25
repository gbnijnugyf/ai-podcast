import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import daily_podcast as daily
from daily_storage import DailyStore, checkpoint_path, save_json
from github_output import digest
import batch_generate as batch
import batch_retry_tts as retry


DAY = "2026-10-20"


def candidate(number, published="2026-10-20T12:00:00+08:00"):
    return {"url": f"https://www.36kr.com/p/{number}", "title": "企业并购",
            "published_at": published, "date_evidence": "页面发布日期",
            "text": "财经原文内容" * 50, "heat_evidence": "榜单第 3"}


def score(number, heat=8, controversy=8, finance=True):
    return {"url": f"https://www.36kr.com/p/{number}", "heat": heat,
            "controversy": controversy, "finance": finance, "reason": "测试判断"}


class MemoryStore:
    def __init__(self):
        self.files = {}
        self.client = Mock()
        self.client.upload.side_effect = self.upload
        self.checkpoint = Mock()
        self.restore = Mock(return_value=False)
        self.published = False

    def upload(self, tag, paths, publish):
        for path in paths:
            if path.name in self.files and self.files[path.name] != path.read_bytes():
                raise RuntimeError("Conflict")
            self.files[path.name] = path.read_bytes()
        self.published = publish
        return {"release": "private-release", "draft": not publish}

    def assets(self, tag):
        return {name: {"name": name, "size": len(data)} for name, data in self.files.items()}

    def release(self, tag):
        return {"draft": not self.published, "html_url": "private-release"} if self.files else None

    def download(self, asset, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.files[asset["name"]])
        return path

    def json_asset(self, asset):
        return json.loads(self.files[asset["name"]])

    def used_urls(self):
        return set()


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore()
        self.pipeline = daily.DailyPipeline(self.root, DAY, self.store)
        self.article = {
            **candidate(1), "fallback": "today_hot_controversial",
            "job_date": DAY, "source_commit": "a" * 40,
            "article_text_sha256": daily.text_digest(candidate(1)["text"]),
        }
        self.summary = {"source": daily.SUMMARY_SOURCE,
                        "article_url": self.article["url"],
                        "article_text_sha256": self.article["article_text_sha256"],
                        "summary": "2026年10月20日，甲公司董事长在北京宣布收购乙公司。"}
        self.store.files = {
            "article.json": json.dumps(self.article).encode(),
            "summary.json": json.dumps(self.summary).encode(),
        }
        self.config = {"ffmpeg": {"path": "ffmpeg", "ffprobe_path": "ffprobe"},
                       "slides": {}, "tts": {}, "video": {}, "render": {}, "pexels": {}}

    def stage(self, name):
        save_json(self.pipeline.work / "_pipeline.json", {"stage": name})
        save_json(self.pipeline.work / "script.json", {"title": "test", "chapters": []})

    def generated(self):
        self.stage("succeeded")
        save_json(self.pipeline.work / "slides/topic_bg/media_sources.json", {
            "version": 1, "online": 1, "total": 1, "ratio": 1.0,
            "items": [{"block": 1, "source": "pexels", "online": True}],
        })
        video = self.root / "output/video" / f"daily-{DAY}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"fake video used only in control-flow tests")

    def run_pipeline(self, child):
        with patch.object(daily, "runtime_config", return_value=(self.root / "runtime.yaml", self.config)), \
                patch.object(daily, "run_child", side_effect=child) as run, \
                patch.object(daily, "validate_video", return_value={"sha256": "test", "size": 44}) as validate, \
                patch.object(daily.time, "sleep") as sleep:
            result = self.pipeline.run()
        return result, run, validate, sleep

    def test_today_fallback_wins_over_older_hot_article(self):
        items = [candidate(1), candidate(2, "2026-10-19T12:00:00+08:00")]
        selected = daily.choose_article(items, [score(1, 3, 2), score(2, 10, 10)], DAY, set())
        self.assertEqual(selected["url"], items[0]["url"])
        self.assertEqual(selected["fallback"], "today_best")

    def test_recent_fallback_excludes_used_urls_and_keeps_date(self):
        items = [candidate(1), candidate(2, "2026-10-18T12:00:00+08:00")]
        selected = daily.choose_article(items, [score(1), score(2)], DAY, {items[0]["url"]})
        self.assertEqual(selected["fallback"], "recent_3_days")
        self.assertEqual(selected["published_at"], items[1]["published_at"])
        self.assertEqual(selected["article_text_sha256"], daily.text_digest(items[1]["text"]))

    def test_future_and_old_articles_rejected(self):
        items = [candidate(1, "2026-10-21T12:00:00+08:00"),
                 candidate(2, "2026-10-16T12:00:00+08:00")]
        with self.assertRaises(ValueError):
            daily.validate_candidates(items, DAY)

    def test_publication_date_uses_shanghai_timezone(self):
        items = daily.validate_candidates([candidate(1, "2026-10-19T18:00:00+00:00")], DAY)
        self.assertEqual(daily.choose_article(items, [score(1)], DAY, set())["fallback"],
                         "today_hot_controversial")

    def test_missing_timezone_and_fake_domain_rejected(self):
        for article in (candidate(1, "2026-10-20T12:00:00"), {**candidate(1), "url": "https://evil.test/p/1"}):
            with self.assertRaises(ValueError):
                daily.validate_candidates([article], DAY)

    def test_ranker_cannot_introduce_unknown_article(self):
        with self.assertRaises(ValueError):
            daily.choose_article([candidate(1)], [score(2)], DAY, set())

    def test_no_finance_articles_does_not_invent_fallback(self):
        with self.assertRaises(RuntimeError):
            daily.choose_article([candidate(1)], [score(1, finance=False)], DAY, set())

    def test_summary_rejects_wrong_provenance_multisentence_and_overlength(self):
        changes = [
            {"source": "another-model"},
            {"summary": "第一句。第二句。"},
            {"summary": "这是一句！"},
            {"summary": "字" * 301 + "。"},
            {"article_url": candidate(2)["url"]},
            {"article_text_sha256": "0" * 64},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                daily.validate_summary({**self.summary, **change}, self.article)
        self.assertEqual(daily.validate_summary(self.summary, self.article), self.summary)

    def test_success_generates_once_then_validates_and_publishes(self):
        def child(*args):
            self.assertFalse(self.store.published)
            self.generated()
            return 0
        result, run, validate, sleep = self.run_pipeline(child)
        self.assertTrue(result["completed"])
        self.assertEqual(run.call_count, 1)
        self.assertIn("--no-avatar", run.call_args.args[0])
        validate.assert_called_once()
        sleep.assert_not_called()
        self.assertTrue(self.store.published)
        self.assertEqual(json.loads(self.store.files["manifest.json"])["media"]["ratio"], 1.0)
        self.assertEqual((self.root / "topics.txt").read_text(encoding="utf-8").strip(),
                         self.summary["summary"])

    def test_tts_failure_retries_only_own_work_and_reuses_script(self):
        def child(command, *args):
            if "batch_generate.py" in command[1]:
                self.stage("tts_failed")
                return 1
            self.assertEqual(command[command.index("--work-dir") + 1], str(self.pipeline.work))
            self.generated()
            return 0
        result, run, _, sleep = self.run_pipeline(child)
        self.assertTrue(result["completed"])
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(60)
        self.store.checkpoint.assert_called_once()

    def test_non_tts_failure_never_enters_tts_retry(self):
        def child(*args):
            self.stage("background")
            return 1
        with self.assertRaisesRegex(RuntimeError, "outside TTS"):
            self.run_pipeline(child)
        self.assertFalse(self.store.published)

    def test_retry_budget_is_exactly_six_after_first_attempt(self):
        def child(*args):
            self.stage("tts_failed")
            return 1
        with patch.object(daily, "runtime_config", return_value=(self.root / "config.yaml", self.config)), \
                patch.object(daily, "run_child", side_effect=child) as run, \
                patch.object(daily.time, "sleep") as sleep, \
                self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            self.pipeline.run()
        self.assertEqual(run.call_count, 7)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(daily.RETRY_DELAYS))
        self.assertEqual(self.store.checkpoint.call_count, 7)
        self.assertFalse(self.store.published)

    def test_resume_on_new_machine_restores_and_regenerates_config_only(self):
        def restore(*args):
            self.stage("tts_failed")
            return True
        self.store.restore.side_effect = restore
        def child(command, *args):
            self.assertIn("batch_retry_tts.py", command[1])
            job = json.loads((self.pipeline.work / "_job.json").read_text())
            self.assertTrue(job["output"].startswith(str(self.root)))
            self.generated()
            return 0
        result, run, _, _ = self.run_pipeline(child)
        self.assertTrue(result["completed"])
        self.assertEqual(run.call_count, 1)
        self.store.restore.assert_called_once()

    def test_resume_partial_remote_upload_does_not_repeat_generation(self):
        self.store.files["video.mp4"] = b"already uploaded video"
        self.store.files["media.json"] = json.dumps({
            "version": 1, "online": 1, "total": 1, "ratio": 1.0, "items": [],
        }).encode()
        result, run, _, _ = self.run_pipeline(Mock(side_effect=AssertionError("must not generate")))
        self.assertTrue(result["completed"])
        run.assert_not_called()

    def test_invalid_video_never_publishes(self):
        self.generated()
        with patch.object(daily, "runtime_config", return_value=(self.root / "config.yaml", self.config)), \
                patch.object(daily, "validate_video", side_effect=RuntimeError("bad video")), \
                self.assertRaisesRegex(RuntimeError, "bad video"):
            self.pipeline.run()
        self.store.client.upload.assert_not_called()

    def test_published_release_missing_video_is_not_success(self):
        self.store.files["manifest.json"] = b"{}"
        self.store.published = True
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            self.pipeline.run()

    def test_completed_run_is_idempotent_and_checks_video_hash(self):
        self.generated()
        video = self.root / "output/video" / f"daily-{DAY}.mp4"
        self.store.files["video.mp4"] = video.read_bytes()
        self.store.files["manifest.json"] = json.dumps({
            "video": {"size": video.stat().st_size, "sha256": digest(video)},
        }).encode()
        self.store.published = True
        with patch.object(daily, "run_child") as run:
            self.assertTrue(self.pipeline.run()["already_done"])
            run.assert_not_called()
        self.store.files["video.mp4"] = b"x" * video.stat().st_size
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.pipeline.run()

    def test_existing_batch_job_is_not_overwritten(self):
        (self.root / "output/batch/daily-test").mkdir(parents=True)
        with patch.object(batch, "THIS_DIR", self.root), \
                self.assertRaisesRegex(ValueError, "already exists"):
            batch.run_single_topic("topic", "missing.yaml", [], 1, 1, "daily-test", True)

    def test_scoped_retry_refuses_other_directories(self):
        with patch.object(sys, "argv", ["batch_retry_tts.py", "--work-dir", str(self.root)]), \
                self.assertRaises(SystemExit) as exit:
            retry.main()
        self.assertEqual(exit.exception.code, 2)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DailyStore.__new__(DailyStore)
        self.store.client = Mock()
        self.store.client.prefix = "/repos/test/output"

    def test_white_list_excludes_configs_logs_cookies_and_traversal(self):
        for name in ("_config.yaml", "_job.json", "cookies.json", "run.log",
                     "../script.json", "/script.json", "tts/../script.json", "tts\\slide_001.mp3"):
            self.assertFalse(checkpoint_path(name), name)
        for name in ("script.json", "_pipeline.json", "tts/tts_cache.json",
                     "tts/slide_001.mp3", "slides/topic_bg/bg_001.jpg",
                     "slides/topic_bg/media_sources.json"):
            self.assertTrue(checkpoint_path(name), name)

    def test_checkpoint_sanitizes_cache_and_excludes_config(self):
        work = self.root / "work"
        save_json(work / "script.json", {"title": "test", "chapters": []})
        (work / "_config.yaml").write_text("api_key: must-not-leak")
        save_json(work / "tts/tts_cache.json", {
            "fingerprint": {"voice": "v", "rate": "+0%", "narrations": ["text"]},
            "slides": [{"audio": "C:/old/machine/audio.mp3", "timestamps": []}],
            "unexpected": "must-not-leak",
        })
        self.store.checkpoint("podcast-2026-10-20-daily", work, self.root / "staging")
        paths = self.store.client.upload.call_args.args[1]
        self.assertTrue(paths[-1].name.startswith("checkpoint-"))
        for path in paths:
            self.assertNotIn("must-not-leak", path.read_text())
            self.assertNotIn("C:/old/machine", path.read_text())
        manifest = json.loads(paths[-1].read_text())
        self.assertEqual({f["path"] for f in manifest["files"]}, {"script.json", "tts/tts_cache.json"})

    def test_restore_rejects_path_traversal_before_downloading(self):
        self.store.assets = Mock(return_value={"checkpoint-20261020T120000-abcdef12.json": {}})
        self.store.json_asset = Mock(return_value={"version": 1, "files": [{"path": "../config.yaml"}]})
        self.store.download = Mock()
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            self.store.restore("tag", self.root / "work", self.root / "staging")
        self.store.download.assert_not_called()

    def test_restore_checks_manifest_hash(self):
        name = "checkpoint-20261020T120000-abcdef12.json"
        asset_name = "cp-" + "0" * 64 + ".json"
        self.store.assets = Mock(return_value={name: {}, asset_name: {}})
        self.store.json_asset = Mock(return_value={"version": 1, "files": [{
            "path": "script.json", "asset": asset_name, "sha256": "0" * 64, "size": 2,
        }]})
        def download(asset, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}")
        self.store.download = Mock(side_effect=download)
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.store.restore("tag", self.root / "work", self.root / "staging")
        self.assertFalse((self.root / "work/script.json").exists())

    def test_interrupted_restore_leaves_no_partial_job_and_can_retry(self):
        import hashlib
        content = b'{"stage": "tts_failed"}'
        checksum = hashlib.sha256(content).hexdigest()
        asset_name = f"cp-{checksum}.json"
        snapshot = "checkpoint-20261020T120000-abcdef12.json"
        self.store.assets = Mock(return_value={snapshot: {}, asset_name: {}})
        self.store.json_asset = Mock(return_value={"version": 1, "files": [
            {"path": name, "asset": asset_name, "sha256": checksum, "size": len(content)}
            for name in ("_pipeline.json", "script.json")
        ]})
        calls = []
        def download(asset, path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("synthetic interrupted download")
            path.write_bytes(content)
        self.store.download = Mock(side_effect=download)
        work = self.root / "work"
        with self.assertRaises(OSError):
            self.store.restore("tag", work, self.root / "staging")
        self.assertFalse(work.exists())
        self.assertTrue(self.store.restore("tag", work, self.root / "staging"))
        self.assertEqual((work / "script.json").read_bytes(), content)

    def test_lock_never_steals_existing_job(self):
        self.store.client.request.return_value = {"sha": "existing"}
        with self.assertRaisesRegex(RuntimeError, "locked"):
            with self.store.lock(DAY):
                self.fail("must not enter")
        self.assertEqual(self.store.client.request.call_count, 1)


class VideoValidationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_real_short_video_and_audio_only_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "test.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=128x72:r=10",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1.2",
                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(video),
            ], check=True, timeout=30, capture_output=True)
            config = {"ffmpeg": {"path": "ffmpeg", "ffprobe_path": "ffprobe"}}
            result = daily.validate_video(video, config)
            self.assertGreaterEqual(result["duration_seconds"], 1)
            audio = Path(directory) / "test.m4a"
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-c:a", "copy", str(audio)],
                           check=True, timeout=30, capture_output=True)
            with self.assertRaisesRegex(RuntimeError, "audio, video"):
                daily.validate_video(audio, config)

    def test_real_topic_pipeline_marks_tts_failure(self):
        import main
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            script = work / "script.json"
            save_json(script, {"title": "测试", "opening": "", "chapters": [
                {"blocks": [{"narration": "测试正文", "keyword": "finance"}]},
            ]})
            config = {"tts": {"voice": "v"}, "slides": {"output_dir": str(work / "slides")}}
            args = SimpleNamespace(config=str(work / "_config.yaml"), script_json=str(script),
                                   voice=None, rate=None, bg_dir=None, max_videos=0)
            with patch("src.background_searcher.BackgroundSearcher") as backgrounds, \
                    patch("src.tts.TTSEngine") as tts:
                backgrounds.return_value.download_for_script.return_value = ["image.jpg"]
                tts.return_value.synthesize_slides.side_effect = RuntimeError("synthetic failure")
                with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                    main._run_topic_pipeline(args, config)
            self.assertEqual(json.loads((work / "_pipeline.json").read_text())["stage"], "tts_failed")

    def test_intro_uses_configured_linux_font(self):
        from src.composer import VideoComposer
        composer = VideoComposer.__new__(VideoComposer)
        composer.config = {"slides": {"font_path": "/usr/share/fonts/noto/font.ttc"}}
        composer.ffmpeg = "ffmpeg"
        composer.fps = 30
        composer._run_ffmpeg = Mock()
        composer._make_intro_segment("intro.mp4", 1.0, "output.mp4", title_text="测试")
        draw_command = composer._run_ffmpeg.call_args.args[0]
        self.assertIn("fontfile=/usr/share/fonts/noto/font.ttc", draw_command[draw_command.index("-vf") + 1])


if __name__ == "__main__":
    unittest.main()
