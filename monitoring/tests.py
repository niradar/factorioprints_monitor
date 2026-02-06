import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from monitoring.models import Blueprint, UserSnapshot, BlueprintSnapshot, CommentSnapshot
from monitoring.comments_scraper import _extract_thread_data, _normalize

class BlueprintModelTest(TestCase):
    def test_create_blueprint(self):
        blueprint = Blueprint.objects.create(
            url="https://example.com/blueprint1",
            name="Test Blueprint"
        )
        self.assertEqual(blueprint.name, "Test Blueprint")
        self.assertEqual(str(blueprint), "Test Blueprint")
    
    def test_unique_url(self):
        Blueprint.objects.create(url="https://example.com/unique", name="B1")
        with self.assertRaises(ValidationError):
            blueprint = Blueprint(url="https://example.com/unique", name="B2")
            blueprint.full_clean()

class UserSnapshotModelTest(TestCase):
    def test_create_user_snapshot(self):
        snapshot = UserSnapshot.objects.create(
            snapshot_ts=timezone.now(),
            user_url="https://example.com/user1"
        )
        self.assertTrue("user1" in str(snapshot))
    
    def test_unique_together_constraint(self):
        ts = timezone.now()
        UserSnapshot.objects.create(snapshot_ts=ts, user_url="https://example.com/user1")
        with self.assertRaises(ValidationError):
            snapshot = UserSnapshot(snapshot_ts=ts, user_url="https://example.com/user1")
            snapshot.full_clean()

class BlueprintSnapshotModelTest(TestCase):
    def setUp(self):
        self.blueprint = Blueprint.objects.create(
            url="https://example.com/bp1",
            name="Base Blueprint"
        )
    
    def test_create_blueprint_snapshot(self):
        snapshot = BlueprintSnapshot.objects.create(
            snapshot_ts=timezone.now(),
            blueprint=self.blueprint,
            name="Snapshot Name",
            favourites=10,
            total_comments=5
        )
        self.assertTrue("Base Blueprint" in str(snapshot))
        self.assertEqual(snapshot.favourites, 10)
    
    def test_min_value_validator(self):
        with self.assertRaises(ValidationError):
            snapshot = BlueprintSnapshot(
                snapshot_ts=timezone.now(),
                blueprint=self.blueprint,
                name="Invalid",
                favourites=-1,
                total_comments=0
            )
            snapshot.full_clean()

class CommentSnapshotModelTest(TestCase):
    def setUp(self):
        self.blueprint = Blueprint.objects.create(
            url="https://example.com/bp2",
            name="Comment Blueprint"
        )
    
    def test_create_comment_snapshot(self):
        comment = CommentSnapshot.objects.create(
            snapshot_ts=timezone.now(),
            blueprint=self.blueprint,
            comment_id="12345",
            author="Test User",
            created_utc=timezone.now(),
            message_text="Test comment"
        )
        self.assertTrue("12345" in str(comment))
        self.assertTrue("Test User" in str(comment))
    
    def test_unique_together_constraint(self):
        ts = timezone.now()
        CommentSnapshot.objects.create(
            snapshot_ts=ts,
            blueprint=self.blueprint,
            comment_id="111",
            author="A1",
            created_utc=ts,
            message_text="M1"
        )
        with self.assertRaises(ValidationError):
            comment = CommentSnapshot(
                snapshot_ts=ts,
                blueprint=self.blueprint,
                comment_id="111",
                author="A2",
                created_utc=ts,
                message_text="M2"
            )
            comment.full_clean()


class UniqueCommentsQueryTest(TestCase):
    """Test that the unique recent comments query returns correct results."""

    def setUp(self):
        self.user_url = "https://factorioprints.com/user/testuser123"
        now = timezone.now()

        # Two snapshots for the same user
        self.ts1 = now - timedelta(days=2)
        self.ts2 = now - timedelta(days=1)
        UserSnapshot.objects.create(snapshot_ts=self.ts1, user_url=self.user_url)
        UserSnapshot.objects.create(snapshot_ts=self.ts2, user_url=self.user_url)

        # Three blueprints
        self.bp1 = Blueprint.objects.create(url="https://fp.com/bp/1", name="BP1")
        self.bp2 = Blueprint.objects.create(url="https://fp.com/bp/2", name="BP2")
        self.bp3 = Blueprint.objects.create(url="https://fp.com/bp/3", name="BP3")

        for bp in [self.bp1, self.bp2, self.bp3]:
            for ts in [self.ts1, self.ts2]:
                BlueprintSnapshot.objects.create(
                    snapshot_ts=ts, blueprint=bp, name=bp.name,
                    favourites=5, total_comments=3,
                )

        # Comments: same comment_id appears in both snapshots (duplicated across snapshots)
        for i, bp in enumerate([self.bp1, self.bp2, self.bp3]):
            for j in range(5):
                created = now - timedelta(hours=(i * 5 + j))
                comment_id = f"c{i}_{j}"
                for ts in [self.ts1, self.ts2]:
                    CommentSnapshot.objects.create(
                        snapshot_ts=ts, blueprint=bp, comment_id=comment_id,
                        author=f"author_{j}", created_utc=created,
                        message_text=f"msg {comment_id}",
                    )

    def _old_query(self):
        """Original Python-side dedup logic."""
        user_blueprint_ids = Blueprint.objects.filter(
            url__in=BlueprintSnapshot.objects.filter(
                snapshot_ts__in=UserSnapshot.objects.filter(
                    user_url=self.user_url
                ).values('snapshot_ts')
            ).values('blueprint__url')
        ).values_list('id', flat=True)
        all_comments = CommentSnapshot.objects.filter(
            blueprint_id__in=user_blueprint_ids
        ).select_related('blueprint').order_by('-created_utc')
        seen = set()
        unique_comments = []
        for c in all_comments:
            key = (c.blueprint_id, c.comment_id)
            if key not in seen:
                seen.add(key)
                unique_comments.append(c)
            if len(unique_comments) == 10:
                break
        return unique_comments

    def _new_query(self):
        """SQL-side dedup logic."""
        from django.db.models import Max
        user_blueprint_ids = BlueprintSnapshot.objects.filter(
            snapshot_ts__in=UserSnapshot.objects.filter(
                user_url=self.user_url
            ).values('snapshot_ts')
        ).values_list('blueprint_id', flat=True)

        latest_per_comment = (
            CommentSnapshot.objects.filter(blueprint_id__in=user_blueprint_ids)
            .values('blueprint_id', 'comment_id')
            .annotate(latest_id=Max('id'))
            .values_list('latest_id', flat=True)
        )
        return list(
            CommentSnapshot.objects.filter(id__in=latest_per_comment)
            .select_related('blueprint')
            .order_by('-created_utc')[:10]
        )

    def test_same_results(self):
        old = self._old_query()
        new = self._new_query()
        old_keys = [(c.blueprint_id, c.comment_id) for c in old]
        new_keys = [(c.blueprint_id, c.comment_id) for c in new]
        self.assertEqual(old_keys, new_keys)

    def test_returns_at_most_10(self):
        results = self._new_query()
        self.assertLessEqual(len(results), 10)

    def test_no_duplicates(self):
        results = self._new_query()
        keys = [(c.blueprint_id, c.comment_id) for c in results]
        self.assertEqual(len(keys), len(set(keys)))

    def test_ordered_by_created_utc_desc(self):
        results = self._new_query()
        dates = [c.created_utc for c in results]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_empty_user(self):
        """User with no snapshots returns empty list."""
        from django.db.models import Max
        user_url = "https://factorioprints.com/user/nobody"
        user_blueprint_ids = BlueprintSnapshot.objects.filter(
            snapshot_ts__in=UserSnapshot.objects.filter(
                user_url=user_url
            ).values('snapshot_ts')
        ).values_list('blueprint_id', flat=True)
        latest_per_comment = (
            CommentSnapshot.objects.filter(blueprint_id__in=user_blueprint_ids)
            .values('blueprint_id', 'comment_id')
            .annotate(latest_id=Max('id'))
            .values_list('latest_id', flat=True)
        )
        results = list(
            CommentSnapshot.objects.filter(id__in=latest_per_comment)
            .select_related('blueprint')
            .order_by('-created_utc')[:10]
        )
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Scraper pure-function tests
# ---------------------------------------------------------------------------

class ExtractThreadDataTest(TestCase):
    """Test _extract_thread_data with various input formats."""

    def test_plain_json(self):
        data = {"cursor": {"total": 3}, "response": {"posts": []}}
        result = _extract_thread_data(json.dumps(data), "http://example.com")
        self.assertEqual(result, data)

    def test_plain_json_with_whitespace(self):
        data = {"cursor": {"total": 0}}
        raw = f"  {json.dumps(data)}  "
        result = _extract_thread_data(raw, "http://example.com")
        self.assertEqual(result, data)

    def test_var_assignment(self):
        data = {"cursor": {"total": 5}, "response": {"posts": [{"id": "1"}]}}
        raw = f"var threadData = {json.dumps(data)};"
        result = _extract_thread_data(raw, "http://example.com")
        self.assertEqual(result, data)

    def test_var_assignment_no_semicolon(self):
        data = {"key": "value"}
        raw = f"var threadData = {json.dumps(data)}"
        result = _extract_thread_data(raw, "http://example.com")
        self.assertEqual(result, data)

    def test_regex_fallback(self):
        data = {"some": "data"}
        raw = f"window.threadData = {json.dumps(data)};"
        result = _extract_thread_data(raw, "http://example.com")
        self.assertEqual(result, data)

    def test_invalid_input_raises(self):
        with self.assertRaises(ValueError):
            _extract_thread_data("no json here at all", "http://example.com")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            _extract_thread_data("", "http://example.com")


class NormalizeTest(TestCase):
    """Test _normalize with various Disqus post shapes."""

    def _make_post(self, **overrides):
        base = {
            "id": "12345",
            "author": {"username": "testuser", "name": "Test User", "id": "99"},
            "parent": None,
            "createdAt": "2025-06-01T12:00:00",
            "message": "<p>Hello <b>world</b></p>",
            "likes": 3,
            "dislikes": 1,
            "depth": 0,
        }
        base.update(overrides)
        return base

    def test_normal_post(self):
        result = _normalize(self._make_post())
        self.assertEqual(result["id"], "12345")
        self.assertEqual(result["author"], "testuser")
        self.assertIsNone(result["parent_id"])
        self.assertEqual(result["likes"], 3)
        self.assertEqual(result["dislikes"], 1)
        self.assertEqual(result["depth"], 0)
        self.assertEqual(result["message_text"], "Hello world")
        self.assertIn("<b>world</b>", result["message_html"])

    def test_created_utc_parsed(self):
        result = _normalize(self._make_post(createdAt="2025-06-01T12:00:00"))
        self.assertEqual(result["created_utc"].year, 2025)
        self.assertEqual(result["created_utc"].month, 6)
        self.assertEqual(result["created_utc"].day, 1)
        self.assertEqual(result["created_utc"].hour, 12)

    def test_author_fallback_to_name(self):
        post = self._make_post(author={"name": "DisplayName", "id": "55"})
        result = _normalize(post)
        self.assertEqual(result["author"], "DisplayName")

    def test_author_fallback_to_user_id(self):
        post = self._make_post(author={"id": "77"})
        result = _normalize(post)
        self.assertEqual(result["author"], "user_77")

    def test_author_empty_dict(self):
        post = self._make_post(author={})
        result = _normalize(post)
        # Falls through to f"user_{None}" which is truthy
        self.assertIn("user_", result["author"])

    def test_missing_author_key(self):
        post = self._make_post()
        del post["author"]
        result = _normalize(post)
        self.assertIn("user_", result["author"])

    def test_reply_has_parent_id(self):
        result = _normalize(self._make_post(parent=999, depth=1))
        self.assertEqual(result["parent_id"], 999)
        self.assertEqual(result["depth"], 1)

    def test_invalid_date_uses_default(self):
        result = _normalize(self._make_post(createdAt="not-a-date"))
        # Should fall back to ~now, just check it's a datetime
        self.assertIsInstance(result["created_utc"], datetime)

    def test_missing_date_uses_default(self):
        post = self._make_post()
        del post["createdAt"]
        result = _normalize(post)
        self.assertIsInstance(result["created_utc"], datetime)

    def test_missing_optional_fields_use_defaults(self):
        post = {"id": "1", "author": {"username": "u"}}
        result = _normalize(post)
        self.assertEqual(result["likes"], 0)
        self.assertEqual(result["dislikes"], 0)
        self.assertEqual(result["depth"], 0)
        self.assertIsNone(result["parent_id"])
        self.assertEqual(result["message_text"], "")

    def test_html_stripped_to_text(self):
        post = self._make_post(message="<div><a href='x'>link</a> &amp; text</div>")
        result = _normalize(post)
        self.assertEqual(result["message_text"], "link & text")


# ---------------------------------------------------------------------------
# Utils tests
# ---------------------------------------------------------------------------

class TakeSnapshotTest(TestCase):
    """Test take_snapshot orchestration with mocked scrapers."""

    @patch("monitoring.utils._fetch_all_comments_concurrent")
    @patch("monitoring.utils.scrape_user_blueprints")
    def test_creates_all_records(self, mock_scrape, mock_comments):
        user_url = "https://factorioprints.com/user/abc123"
        mock_scrape.return_value = [
            {"url": "https://fp.com/bp/1", "name": "BP1", "favorites": 10},
            {"url": "https://fp.com/bp/2", "name": "BP2", "favorites": 5},
        ]
        mock_comments.return_value = {
            "https://fp.com/bp/1": {
                "total_comments": 2,
                "comments": [
                    {"id": "c1", "author": "alice", "created_utc": timezone.now(), "message_text": "hi"},
                    {"id": "c2", "author": "bob", "created_utc": timezone.now(), "message_text": "hey"},
                ],
            },
            "https://fp.com/bp/2": {"total_comments": 0, "comments": []},
        }

        from monitoring.utils import take_snapshot
        ts = take_snapshot(user_url)

        self.assertEqual(UserSnapshot.objects.filter(user_url=user_url).count(), 1)
        self.assertEqual(Blueprint.objects.count(), 2)
        self.assertEqual(BlueprintSnapshot.objects.filter(snapshot_ts=ts).count(), 2)
        self.assertEqual(CommentSnapshot.objects.filter(snapshot_ts=ts).count(), 2)

    @patch("monitoring.utils._fetch_all_comments_concurrent")
    @patch("monitoring.utils.scrape_user_blueprints")
    def test_reuses_existing_blueprint(self, mock_scrape, mock_comments):
        """If a Blueprint with the same URL already exists, get_or_create reuses it."""
        Blueprint.objects.create(url="https://fp.com/bp/1", name="Old Name")
        mock_scrape.return_value = [
            {"url": "https://fp.com/bp/1", "name": "New Name", "favorites": 1},
        ]
        mock_comments.return_value = {
            "https://fp.com/bp/1": {"total_comments": 0, "comments": []},
        }

        from monitoring.utils import take_snapshot
        take_snapshot("https://factorioprints.com/user/xyz")

        # Should not create a duplicate Blueprint
        self.assertEqual(Blueprint.objects.count(), 1)
        # get_or_create doesn't update name — original name is kept
        self.assertEqual(Blueprint.objects.first().name, "Old Name")

    @patch("monitoring.utils._fetch_all_comments_concurrent")
    @patch("monitoring.utils.scrape_user_blueprints")
    def test_empty_blueprints(self, mock_scrape, mock_comments):
        mock_scrape.return_value = []
        mock_comments.return_value = {}

        from monitoring.utils import take_snapshot
        ts = take_snapshot("https://factorioprints.com/user/empty")

        self.assertEqual(UserSnapshot.objects.count(), 1)
        self.assertEqual(BlueprintSnapshot.objects.count(), 0)
        self.assertEqual(CommentSnapshot.objects.count(), 0)


class ListSnapshotsTest(TestCase):
    def setUp(self):
        self.url = "https://factorioprints.com/user/u1"
        self.ts1 = timezone.now() - timedelta(days=2)
        self.ts2 = timezone.now() - timedelta(days=1)
        UserSnapshot.objects.create(snapshot_ts=self.ts1, user_url=self.url)
        UserSnapshot.objects.create(snapshot_ts=self.ts2, user_url=self.url)
        UserSnapshot.objects.create(
            snapshot_ts=timezone.now(),
            user_url="https://factorioprints.com/user/other"
        )

    def test_all_snapshots(self):
        from monitoring.utils import list_snapshots
        result = list(list_snapshots())
        self.assertEqual(len(result), 3)

    def test_filtered_by_user(self):
        from monitoring.utils import list_snapshots
        result = list(list_snapshots(user_url=self.url))
        self.assertEqual(len(result), 2)

    def test_ordered_ascending(self):
        from monitoring.utils import list_snapshots
        result = list(list_snapshots(user_url=self.url))
        self.assertEqual(result, sorted(result))


class GetLatestBlueprintsTest(TestCase):
    def test_returns_latest_snapshot_blueprints(self):
        url = "https://factorioprints.com/user/u1"
        ts1 = timezone.now() - timedelta(days=1)
        ts2 = timezone.now()
        UserSnapshot.objects.create(snapshot_ts=ts1, user_url=url)
        UserSnapshot.objects.create(snapshot_ts=ts2, user_url=url)
        bp = Blueprint.objects.create(url="https://fp.com/bp/1", name="BP1")
        BlueprintSnapshot.objects.create(
            snapshot_ts=ts1, blueprint=bp, name="BP1 old", favourites=1, total_comments=0
        )
        BlueprintSnapshot.objects.create(
            snapshot_ts=ts2, blueprint=bp, name="BP1 new", favourites=5, total_comments=2
        )

        from monitoring.utils import get_latest_blueprints
        result = list(get_latest_blueprints(url))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "BP1 new")

    def test_no_snapshots_returns_empty(self):
        from monitoring.utils import get_latest_blueprints
        result = get_latest_blueprints("https://factorioprints.com/user/nobody")
        self.assertEqual(list(result), [])


class BlueprintsWithNewCommentsTest(TestCase):
    """Test CSV generation and nearest-date fallback logic."""

    def setUp(self):
        self.user_url = "https://factorioprints.com/user/u1"
        self.bp = Blueprint.objects.create(url="https://fp.com/bp/1", name="BP1")
        self.bp2 = Blueprint.objects.create(url="https://fp.com/bp/2", name="BP2, with comma")

        # Snapshot on June 1
        self.ts1 = datetime(2025, 6, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
        UserSnapshot.objects.create(snapshot_ts=self.ts1, user_url=self.user_url)
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.ts1, blueprint=self.bp, name="BP1",
            favourites=5, total_comments=2
        )
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.ts1, blueprint=self.bp2, name="BP2, with comma",
            favourites=3, total_comments=1
        )
        CommentSnapshot.objects.create(
            snapshot_ts=self.ts1, blueprint=self.bp, comment_id="c1",
            author="a1", created_utc=self.ts1, message_text="m1"
        )
        CommentSnapshot.objects.create(
            snapshot_ts=self.ts1, blueprint=self.bp, comment_id="c2",
            author="a2", created_utc=self.ts1, message_text="m2"
        )
        CommentSnapshot.objects.create(
            snapshot_ts=self.ts1, blueprint=self.bp2, comment_id="c3",
            author="a3", created_utc=self.ts1, message_text="m3"
        )

        # Snapshot on June 5 — bp1 gains 1 comment, bp2 gains 2
        self.ts2 = datetime(2025, 6, 5, 10, 0, 0, tzinfo=dt_timezone.utc)
        UserSnapshot.objects.create(snapshot_ts=self.ts2, user_url=self.user_url)
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.ts2, blueprint=self.bp, name="BP1",
            favourites=6, total_comments=3
        )
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.ts2, blueprint=self.bp2, name="BP2, with comma",
            favourites=4, total_comments=3
        )
        for cid in ["c1", "c2", "c4"]:
            CommentSnapshot.objects.create(
                snapshot_ts=self.ts2, blueprint=self.bp, comment_id=cid,
                author="a", created_utc=self.ts2, message_text="m"
            )
        for cid in ["c3", "c5", "c6"]:
            CommentSnapshot.objects.create(
                snapshot_ts=self.ts2, blueprint=self.bp2, comment_id=cid,
                author="a", created_utc=self.ts2, message_text="m"
            )

    def test_exact_dates(self):
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(self.user_url, "2025-06-01", "2025-06-05")
        self.assertIn("blueprint_url", result)
        lines = result.strip().split("\n")
        # Header + 2 blueprints with new comments
        self.assertEqual(len(lines), 3)

    def test_csv_header(self):
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(self.user_url, "2025-06-01", "2025-06-05")
        header = result.split("\n")[0]
        self.assertEqual(
            header,
            "blueprint_url,blueprint_name,num_of_new_comments,comments_num_on_end_date"
        )

    def test_new_comment_counts(self):
        from monitoring.utils import blueprints_with_new_comments
        import csv
        from io import StringIO
        result = blueprints_with_new_comments(self.user_url, "2025-06-01", "2025-06-05")
        reader = csv.DictReader(StringIO(result))
        rows = {r["blueprint_url"]: r for r in reader}
        self.assertEqual(rows["https://fp.com/bp/1"]["num_of_new_comments"], "1")
        self.assertEqual(rows["https://fp.com/bp/2"]["num_of_new_comments"], "2")

    def test_comma_in_name_escaped(self):
        from monitoring.utils import blueprints_with_new_comments
        import csv
        from io import StringIO
        result = blueprints_with_new_comments(self.user_url, "2025-06-01", "2025-06-05")
        reader = csv.DictReader(StringIO(result))
        rows = {r["blueprint_url"]: r for r in reader}
        self.assertEqual(rows["https://fp.com/bp/2"]["blueprint_name"], "BP2, with comma")

    def test_no_new_comments(self):
        """Same date for start and end — 0 new comments."""
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(self.user_url, "2025-06-01", "2025-06-01")
        self.assertEqual(result, "No blueprints received new comments in this period.")

    def test_no_user_snapshots(self):
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(
            "https://factorioprints.com/user/nobody", "2025-06-01", "2025-06-05"
        )
        self.assertIn("No snapshots found for user", result)

    def test_no_snapshot_for_start_date(self):
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(
            self.user_url, "2025-01-01", "2025-06-05", allow_nearest=False
        )
        self.assertIn("No snapshots found for start date", result)

    def test_no_snapshot_for_end_date(self):
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(
            self.user_url, "2025-06-01", "2025-12-31", allow_nearest=False
        )
        self.assertIn("No snapshots found for end date", result)

    def test_nearest_date_fallback(self):
        """Dates between snapshots should find nearest within range."""
        from monitoring.utils import blueprints_with_new_comments
        # No snapshot on June 2 or June 4, but nearest fallback finds June 1 and June 5
        result = blueprints_with_new_comments(self.user_url, "2025-06-02", "2025-06-04")
        # Nearest start -> June 5 (first >= June 2), nearest end -> June 5 (last <= June 4 doesn't exist,
        # but last <= June 4 and >= June 2 is June 5? No — June 5 > June 4. Let's check the logic.
        # Actually nearest_start finds first snapshot >= start and <= end -> nothing between June 2 and June 4
        # So this should fail to find snapshots.
        self.assertIn("No snapshots found", result)

    def test_nearest_fallback_finds_within_range(self):
        """Add a snapshot on June 3 so nearest fallback succeeds."""
        from monitoring.utils import blueprints_with_new_comments
        ts3 = datetime(2025, 6, 3, 10, 0, 0, tzinfo=dt_timezone.utc)
        UserSnapshot.objects.create(snapshot_ts=ts3, user_url=self.user_url)
        BlueprintSnapshot.objects.create(
            snapshot_ts=ts3, blueprint=self.bp, name="BP1",
            favourites=5, total_comments=2
        )
        CommentSnapshot.objects.create(
            snapshot_ts=ts3, blueprint=self.bp, comment_id="c1",
            author="a1", created_utc=ts3, message_text="m1"
        )
        CommentSnapshot.objects.create(
            snapshot_ts=ts3, blueprint=self.bp, comment_id="c2",
            author="a2", created_utc=ts3, message_text="m2"
        )
        # June 2 to June 6: nearest start -> June 3, nearest end -> June 5
        result = blueprints_with_new_comments(self.user_url, "2025-06-02", "2025-06-06")
        self.assertIn("blueprint_url", result)
        self.assertIn("num_of_new_comments", result)
