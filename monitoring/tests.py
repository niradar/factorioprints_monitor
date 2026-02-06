from datetime import timedelta

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from monitoring.models import Blueprint, UserSnapshot, BlueprintSnapshot, CommentSnapshot

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
