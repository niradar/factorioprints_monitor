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
