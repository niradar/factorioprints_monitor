import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from monitoring.models import Blueprint, UserSnapshot, BlueprintSnapshot, CommentSnapshot, SnapshotRun
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

    def test_util_matches_inline_query(self):
        from monitoring.utils import get_recent_unique_comments
        util = [(c.blueprint_id, c.comment_id) for c in get_recent_unique_comments(self.user_url, limit=10)]
        new = [(c.blueprint_id, c.comment_id) for c in self._new_query()]
        self.assertEqual(util, new)

    def test_util_no_limit_returns_all_unique(self):
        from monitoring.utils import get_recent_unique_comments
        results = list(get_recent_unique_comments(self.user_url))
        self.assertEqual(len(results), 15)  # 3 blueprints x 5 unique comments
        keys = [(c.blueprint_id, c.comment_id) for c in results]
        self.assertEqual(len(keys), len(set(keys)))

    def test_util_limit_applied(self):
        from monitoring.utils import get_recent_unique_comments
        self.assertEqual(len(list(get_recent_unique_comments(self.user_url, limit=5))), 5)

    def test_util_empty_user_returns_empty(self):
        from monitoring.utils import get_recent_unique_comments
        self.assertEqual(
            list(get_recent_unique_comments("https://factorioprints.com/user/nobody")), []
        )


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
        # get_or_create doesn't update name - original name is kept
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


class GetBlueprintsOverviewTest(TestCase):
    """The Δ-window column on the Blueprints list: favourites gained vs the
    nearest snapshot at/before `latest - baseline_days`, plus the baseline ts."""

    def setUp(self):
        self.url = "https://factorioprints.com/user/u1"
        self.bp = Blueprint.objects.create(url="https://fp.com/bp/1", name="BP1")
        self.now = timezone.now()
        # favourites: 30 days ago = 10, yesterday = 18, today = 20.
        for days_ago, fav in [(30, 10), (1, 18), (0, 20)]:
            ts = self.now - timedelta(days=days_ago)
            UserSnapshot.objects.create(snapshot_ts=ts, user_url=self.url)
            BlueprintSnapshot.objects.create(
                snapshot_ts=ts, blueprint=self.bp, name="BP1", favourites=fav, total_comments=0
            )

    def test_no_snapshots_returns_empty_tuple(self):
        from monitoring.utils import get_blueprints_overview
        rows, baseline_ts = get_blueprints_overview("https://factorioprints.com/user/nobody")
        self.assertEqual(rows, [])
        self.assertIsNone(baseline_ts)

    def test_today_window_measures_against_yesterday(self):
        from monitoring.utils import get_blueprints_overview
        rows, baseline_ts = get_blueprints_overview(self.url, baseline_days=1)
        self.assertEqual(rows[0]["fav_delta"], 2)  # 20 - 18
        self.assertEqual(baseline_ts, self.now - timedelta(days=1))

    def test_30d_window_measures_against_oldest(self):
        from monitoring.utils import get_blueprints_overview
        rows, baseline_ts = get_blueprints_overview(self.url, baseline_days=30)
        self.assertEqual(rows[0]["fav_delta"], 10)  # 20 - 10
        self.assertEqual(baseline_ts, self.now - timedelta(days=30))

    def test_no_baseline_in_window_gives_none(self):
        from monitoring.utils import get_blueprints_overview
        # No snapshot is 90+ days old, so there is nothing to diff against.
        rows, baseline_ts = get_blueprints_overview(self.url, baseline_days=90)
        self.assertIsNone(rows[0]["fav_delta"])
        self.assertIsNone(baseline_ts)


class NewBlueprintDeltaTest(TestCase):
    """A blueprint that first appears within the window (on an account that has
    older history) should count all its favourites as new - baseline 0, not '-'.
    Mirrors the real case: an existing user publishes a new blueprint that already
    has favourites by the first scan."""

    def setUp(self):
        self.url = "https://factorioprints.com/user/u1"
        self.now = timezone.now()
        # BP1 has full history; BP2 first appears 7 days ago (mid-window).
        self.bp1 = Blueprint.objects.create(url="https://fp.com/bp/1", name="BP1")
        self.bp2 = Blueprint.objects.create(url="https://fp.com/bp/2", name="BP2")

        def snap(days_ago, favs):
            ts = self.now - timedelta(days=days_ago)
            UserSnapshot.objects.create(snapshot_ts=ts, user_url=self.url)
            for bp, fav in favs.items():
                BlueprintSnapshot.objects.create(
                    snapshot_ts=ts, blueprint=bp, name=bp.name, favourites=fav, total_comments=0
                )

        snap(30, {self.bp1: 10})                       # BP2 does not exist yet
        snap(7, {self.bp1: 15, self.bp2: 2})           # BP2 first seen here with 2
        snap(0, {self.bp1: 20, self.bp2: 9})           # latest

    def _delta(self, rows, name):
        return next(r["fav_delta"] for r in rows if r["name"] == name)

    def test_new_bp_counts_full_favs_when_it_predates_only_the_old_baseline(self):
        from monitoring.utils import get_blueprints_overview
        # 30d baseline is 30 days ago, before BP2 existed -> BP2 is new: 0 -> 9.
        rows, _ = get_blueprints_overview(self.url, baseline_days=30)
        self.assertEqual(self._delta(rows, "BP1"), 10)  # 20 - 10 (normal)
        self.assertEqual(self._delta(rows, "BP2"), 9)   # 9 - 0 (new in window)

    def test_new_bp_uses_real_baseline_once_it_existed_at_baseline(self):
        from monitoring.utils import get_blueprints_overview
        # 7d baseline is when BP2 already existed (2 favs) -> normal delta 7.
        rows, _ = get_blueprints_overview(self.url, baseline_days=7)
        self.assertEqual(self._delta(rows, "BP1"), 5)   # 20 - 15
        self.assertEqual(self._delta(rows, "BP2"), 7)   # 9 - 2

    def test_new_account_never_fabricates_a_delta(self):
        from monitoring.utils import get_blueprints_overview
        # Fresh account: only today's snapshot exists, no baseline for the window.
        fresh = "https://factorioprints.com/user/fresh"
        bp = Blueprint.objects.create(url="https://fp.com/bp/9", name="BP9")
        UserSnapshot.objects.create(snapshot_ts=self.now, user_url=fresh)
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.now, blueprint=bp, name="BP9", favourites=12, total_comments=0
        )
        rows, baseline_ts = get_blueprints_overview(fresh, baseline_days=7)
        self.assertIsNone(rows[0]["fav_delta"])  # first scan is baseline, not a gain
        self.assertIsNone(baseline_ts)


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

        # Snapshot on June 5 - bp1 gains 1 comment, bp2 gains 2
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
        """Same date for start and end - 0 new comments."""
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
        """Window 06-02..06-04: both boundaries resolve to the 06-01 snapshot
        (latest on or before each date), so there are no new comments."""
        from monitoring.utils import blueprints_with_new_comments
        result = blueprints_with_new_comments(self.user_url, "2025-06-02", "2025-06-04")
        self.assertEqual(result, "No blueprints received new comments in this period.")

    def test_baseline_uses_latest_snapshot_on_or_before_start(self):
        """Regression: a start date with no snapshot in-range must diff against the
        latest snapshot on or before it (06-01), not collapse onto the end snapshot."""
        from monitoring.utils import blueprints_with_new_comments
        import csv
        from io import StringIO
        # 06-03..06-05: only 06-05 is in range, but baseline must be 06-01.
        result = blueprints_with_new_comments(self.user_url, "2025-06-03", "2025-06-05")
        self.assertIn("blueprint_url", result)
        rows = {r["blueprint_url"]: r for r in csv.DictReader(StringIO(result))}
        self.assertEqual(rows["https://fp.com/bp/1"]["num_of_new_comments"], "1")
        self.assertEqual(rows["https://fp.com/bp/2"]["num_of_new_comments"], "2")

    def test_start_before_all_snapshots_counts_all_as_new(self):
        """If the start date precedes every snapshot, the baseline is empty and all
        comments present at the end count as new."""
        from monitoring.utils import blueprints_with_new_comments
        import csv
        from io import StringIO
        result = blueprints_with_new_comments(self.user_url, "2025-01-01", "2025-06-05")
        rows = {r["blueprint_url"]: r for r in csv.DictReader(StringIO(result))}
        self.assertEqual(rows["https://fp.com/bp/1"]["num_of_new_comments"], "3")
        self.assertEqual(rows["https://fp.com/bp/2"]["num_of_new_comments"], "3")

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


# ---------------------------------------------------------------------------
# Snapshot trigger tests (background thread + server-side cooldown)
# ---------------------------------------------------------------------------

from django.urls import reverse


class IsInCooldownTest(TestCase):
    def setUp(self):
        self.user_url = "https://factorioprints.com/user/u1"

    def test_no_snapshots_not_in_cooldown(self):
        from monitoring.views import is_in_cooldown
        self.assertFalse(is_in_cooldown(self.user_url))

    def test_recent_snapshot_in_cooldown(self):
        from monitoring.views import is_in_cooldown
        UserSnapshot.objects.create(snapshot_ts=timezone.now(), user_url=self.user_url)
        self.assertTrue(is_in_cooldown(self.user_url))

    def test_old_snapshot_not_in_cooldown(self):
        from monitoring.views import is_in_cooldown
        UserSnapshot.objects.create(
            snapshot_ts=timezone.now() - timedelta(hours=2), user_url=self.user_url
        )
        self.assertFalse(is_in_cooldown(self.user_url))

    def test_other_user_snapshot_ignored(self):
        from monitoring.views import is_in_cooldown
        UserSnapshot.objects.create(
            snapshot_ts=timezone.now(),
            user_url="https://factorioprints.com/user/other",
        )
        self.assertFalse(is_in_cooldown(self.user_url))


class StartSnapshotAsyncTest(TransactionTestCase):
    """Real-thread tests - use TransactionTestCase so the worker thread's
    separate DB connection can see/commit rows (a TestCase transaction would
    hide the run row from the thread)."""

    def _make_run(self, user_url="https://factorioprints.com/user/u1"):
        return SnapshotRun.objects.create(user_url=user_url, status=SnapshotRun.RUNNING)

    @patch("monitoring.views.take_snapshot")
    def test_runs_take_snapshot_in_background_thread(self, mock_take):
        mock_take.return_value = timezone.now()
        from monitoring.views import start_snapshot_async
        run = self._make_run()
        thread = start_snapshot_async("https://factorioprints.com/user/u1", run.id)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        mock_take.assert_called_once_with("https://factorioprints.com/user/u1")

    @patch("monitoring.views.take_snapshot")
    def test_thread_is_daemon(self, mock_take):
        mock_take.return_value = timezone.now()
        from monitoring.views import start_snapshot_async
        run = self._make_run()
        thread = start_snapshot_async("https://factorioprints.com/user/u1", run.id)
        self.assertTrue(thread.daemon)
        thread.join(timeout=5)

    @patch("monitoring.views.take_snapshot")
    def test_success_marks_run_success(self, mock_take):
        ts = timezone.now()
        mock_take.return_value = ts
        from monitoring.views import start_snapshot_async
        run = self._make_run()
        thread = start_snapshot_async("https://factorioprints.com/user/u1", run.id)
        thread.join(timeout=5)
        run.refresh_from_db()
        self.assertEqual(run.status, SnapshotRun.SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.snapshot_ts, ts)

    @patch("monitoring.views.take_snapshot", side_effect=RuntimeError("boom"))
    def test_failure_marks_run_failed_and_logs(self, mock_take):
        """A failing snapshot must not crash silently - it's logged and recorded."""
        from monitoring.views import start_snapshot_async
        run = self._make_run()
        with self.assertLogs("monitoring.views", level="ERROR") as cm:
            thread = start_snapshot_async("https://factorioprints.com/user/u1", run.id)
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        run.refresh_from_db()
        self.assertEqual(run.status, SnapshotRun.FAILED)
        self.assertIn("boom", run.error)
        self.assertTrue(any("Snapshot failed" in line for line in cm.output))


class TakeSnapshotViewTest(TestCase):
    def setUp(self):
        self.fp_user_id = "u1"
        self.user_url = "https://factorioprints.com/user/u1"
        self.url = reverse("take_snapshot", args=[self.fp_user_id])
        self.inbox_url = reverse("inbox", args=[self.fp_user_id])

    @patch("monitoring.views.start_snapshot_async")
    def test_starts_snapshot_when_idle(self, mock_start):
        from django.contrib.messages import get_messages
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        # A RUNNING SnapshotRun is created and its id passed to the worker
        mock_start.assert_called_once()
        called_url, called_run_id = mock_start.call_args.args
        self.assertEqual(called_url, self.user_url)
        self.assertTrue(
            SnapshotRun.objects.filter(
                id=called_run_id, user_url=self.user_url, status=SnapshotRun.RUNNING
            ).exists()
        )
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("started" in m for m in msgs))

    @patch("monitoring.views.start_snapshot_async")
    def test_skips_when_in_cooldown(self, mock_start):
        from django.contrib.messages import get_messages
        UserSnapshot.objects.create(snapshot_ts=timezone.now(), user_url=self.user_url)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        mock_start.assert_not_called()
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("skipped" in m for m in msgs))

    @patch("monitoring.views.start_snapshot_async")
    def test_skips_when_already_running(self, mock_start):
        from django.contrib.messages import get_messages
        SnapshotRun.objects.create(user_url=self.user_url, status=SnapshotRun.RUNNING)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        mock_start.assert_not_called()
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("already running" in m for m in msgs))

    @patch("monitoring.views.start_snapshot_async")
    def test_redirects_to_inbox(self, mock_start):
        resp = self.client.post(self.url)
        self.assertEqual(resp.url, self.inbox_url)


class IsSnapshotRunningTest(TestCase):
    def setUp(self):
        self.user_url = "https://factorioprints.com/user/u1"

    def test_no_runs(self):
        from monitoring.views import is_snapshot_running
        self.assertFalse(is_snapshot_running(self.user_url))

    def test_running_recent(self):
        from monitoring.views import is_snapshot_running
        SnapshotRun.objects.create(user_url=self.user_url, status=SnapshotRun.RUNNING)
        self.assertTrue(is_snapshot_running(self.user_url))

    def test_success_not_running(self):
        from monitoring.views import is_snapshot_running
        SnapshotRun.objects.create(user_url=self.user_url, status=SnapshotRun.SUCCESS)
        self.assertFalse(is_snapshot_running(self.user_url))

    def test_failed_not_running(self):
        from monitoring.views import is_snapshot_running
        SnapshotRun.objects.create(user_url=self.user_url, status=SnapshotRun.FAILED)
        self.assertFalse(is_snapshot_running(self.user_url))

    def test_stale_running_ignored(self):
        from monitoring.views import is_snapshot_running
        run = SnapshotRun.objects.create(user_url=self.user_url, status=SnapshotRun.RUNNING)
        # started_at is auto_now_add; force it stale via update (bypasses auto_now_add)
        SnapshotRun.objects.filter(id=run.id).update(
            started_at=timezone.now() - timedelta(hours=2)
        )
        self.assertFalse(is_snapshot_running(self.user_url))

    def test_other_user_running_ignored(self):
        from monitoring.views import is_snapshot_running
        SnapshotRun.objects.create(
            user_url="https://factorioprints.com/user/other", status=SnapshotRun.RUNNING
        )
        self.assertFalse(is_snapshot_running(self.user_url))


class SnapshotRunModelTest(TestCase):
    def test_defaults(self):
        run = SnapshotRun.objects.create(user_url="https://factorioprints.com/user/u1")
        self.assertEqual(run.status, SnapshotRun.RUNNING)
        self.assertIsNotNone(run.started_at)
        self.assertIsNone(run.finished_at)
        self.assertIsNone(run.snapshot_ts)
        self.assertEqual(run.error, "")

    def test_ordering_latest_first(self):
        url = "https://factorioprints.com/user/u1"
        old = SnapshotRun.objects.create(user_url=url)
        SnapshotRun.objects.filter(id=old.id).update(
            started_at=timezone.now() - timedelta(hours=1)
        )
        new = SnapshotRun.objects.create(user_url=url)
        self.assertEqual(SnapshotRun.objects.first().id, new.id)

    def test_str_contains_status(self):
        run = SnapshotRun.objects.create(user_url="https://factorioprints.com/user/u1")
        self.assertIn("running", str(run))


class MonitoredUserUrlsTest(TestCase):
    def test_union_of_snapshots_and_settings(self):
        from monitoring.models import UserSettings
        from monitoring.utils import monitored_user_urls

        ts = timezone.now()
        UserSnapshot.objects.create(snapshot_ts=ts, user_url="https://fp.com/user/a")
        # an account configured in settings but never snapshotted is still monitored
        UserSettings.objects.create(user_url="https://fp.com/user/b")
        self.assertEqual(
            monitored_user_urls(),
            ["https://fp.com/user/a", "https://fp.com/user/b"],
        )

    def test_empty(self):
        from monitoring.utils import monitored_user_urls
        self.assertEqual(monitored_user_urls(), [])


class DeleteUserAccountTest(TestCase):
    def setUp(self):
        from monitoring.models import CommentStatus, UserSettings

        self.url = "https://factorioprints.com/user/del"
        self.ts = timezone.now()
        UserSnapshot.objects.create(snapshot_ts=self.ts, user_url=self.url)
        self.bp = Blueprint.objects.create(url="https://fp.com/bp/del1", name="DelBP")
        BlueprintSnapshot.objects.create(
            snapshot_ts=self.ts, blueprint=self.bp, name="DelBP", favourites=1, total_comments=1
        )
        CommentSnapshot.objects.create(
            snapshot_ts=self.ts, blueprint=self.bp, comment_id="c1",
            author="x", created_utc=self.ts, message_text="hi",
        )
        CommentStatus.objects.create(blueprint=self.bp, comment_id="c1", handled=True)
        UserSettings.objects.create(user_url=self.url, display_name="Del")
        SnapshotRun.objects.create(user_url=self.url, status=SnapshotRun.SUCCESS)

    def test_removes_all_traces(self):
        from monitoring.models import CommentStatus, UserSettings
        from monitoring.utils import delete_user_account

        delete_user_account(self.url)

        self.assertFalse(UserSnapshot.objects.filter(user_url=self.url).exists())
        self.assertFalse(UserSettings.objects.filter(user_url=self.url).exists())
        self.assertFalse(SnapshotRun.objects.filter(user_url=self.url).exists())
        # the exclusive blueprint and its cascaded rows are gone
        self.assertFalse(Blueprint.objects.filter(id=self.bp.id).exists())
        self.assertFalse(BlueprintSnapshot.objects.filter(snapshot_ts=self.ts).exists())
        self.assertFalse(CommentSnapshot.objects.filter(snapshot_ts=self.ts).exists())
        self.assertFalse(CommentStatus.objects.filter(blueprint_id=self.bp.id).exists())

    def test_keeps_blueprint_shared_with_another_account(self):
        """A blueprint also captured under another account survives; only this
        account's own snapshot rows for it are removed."""
        from monitoring.utils import delete_user_account

        other_url = "https://factorioprints.com/user/keep"
        other_ts = self.ts + timedelta(days=1)
        UserSnapshot.objects.create(snapshot_ts=other_ts, user_url=other_url)
        BlueprintSnapshot.objects.create(
            snapshot_ts=other_ts, blueprint=self.bp, name="DelBP", favourites=2, total_comments=0
        )

        delete_user_account(self.url)

        # blueprint kept because the other account still references it
        self.assertTrue(Blueprint.objects.filter(id=self.bp.id).exists())
        # the other account's snapshot row is intact; the deleted account's is gone
        self.assertTrue(BlueprintSnapshot.objects.filter(snapshot_ts=other_ts).exists())
        self.assertFalse(BlueprintSnapshot.objects.filter(snapshot_ts=self.ts).exists())


class RemoveAccountViewTest(TestCase):
    def setUp(self):
        from monitoring.models import UserSettings

        self.fp_user_id = "del"
        self.url = "https://factorioprints.com/user/del"
        self.endpoint = reverse("remove_account", args=[self.fp_user_id])
        UserSnapshot.objects.create(snapshot_ts=timezone.now(), user_url=self.url)
        UserSettings.objects.create(user_url=self.url, display_name="Del")

    def test_post_deletes_and_redirects_home(self):
        resp = self.client.post(self.endpoint)
        self.assertRedirects(resp, reverse("home"), fetch_redirect_response=False)
        self.assertFalse(UserSnapshot.objects.filter(user_url=self.url).exists())

    def test_get_is_noop_redirect(self):
        resp = self.client.get(self.endpoint)
        self.assertEqual(resp.status_code, 302)
        # nothing deleted on a GET
        self.assertTrue(UserSnapshot.objects.filter(user_url=self.url).exists())
