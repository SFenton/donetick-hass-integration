"""Unit tests for custom_components.donetick.model module."""
import pytest
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.donetick.model import (
    DonetickTask,
    DonetickThing,
    DonetickMember,
    DonetickLabel,
    DonetickSubTask,
    DonetickAssignee,
)


class TestDonetickLabel:
    """Tests for DonetickLabel dataclass."""

    def test_creation(self):
        """Test creating a label directly."""
        label = DonetickLabel(id=1, name="Cleaning", color="#ff0000")
        
        assert label.id == 1
        assert label.name == "Cleaning"
        assert label.color == "#ff0000"

    def test_creation_defaults(self):
        """Test creating label with required fields only."""
        label = DonetickLabel(id=2, name="Test", color="")
        
        assert label.id == 2
        assert label.name == "Test"
        assert label.color == ""


class TestDonetickSubTask:
    """Tests for DonetickSubTask dataclass."""

    def test_creation_not_completed(self):
        """Test creating subtask that is not completed."""
        subtask = DonetickSubTask(
            id=101,
            name="Wipe counters",
            order_id=1,
            completed_at=None,
            completed_by=0,
            parent_id=1,
        )
        
        assert subtask.id == 101
        assert subtask.name == "Wipe counters"
        assert subtask.order_id == 1
        assert subtask.completed_at is None
        assert subtask.completed_by == 0
        assert subtask.parent_id == 1

    def test_creation_completed(self):
        """Test creating subtask that is completed."""
        completed_time = datetime(2025, 1, 10, 9, 0, 0, tzinfo=timezone.utc)
        subtask = DonetickSubTask(
            id=102,
            name="Clean dishes",
            order_id=2,
            completed_at=completed_time,
            completed_by=42,
            parent_id=1,
        )
        
        assert subtask.id == 102
        assert subtask.name == "Clean dishes"
        assert subtask.completed_at == completed_time
        assert subtask.completed_by == 42

    def test_creation_with_defaults(self):
        """Test creating subtask with defaults."""
        subtask = DonetickSubTask(id=103, name="Test")
        
        assert subtask.id == 103
        assert subtask.name == "Test"
        assert subtask.order_id == 0
        assert subtask.completed_at is None
        assert subtask.completed_by == 0
        assert subtask.parent_id is None


class TestDonetickAssignee:
    """Tests for DonetickAssignee dataclass."""

    def test_creation(self):
        """Test creating an assignee."""
        assignee = DonetickAssignee(user_id=42)
        
        assert assignee.user_id == 42


class TestDonetickTask:
    """Tests for DonetickTask model."""

    def test_from_json_complete(self, sample_chore_json):
        """Test parsing complete chore JSON."""
        task = DonetickTask.from_json(sample_chore_json)
        
        assert task.id == 1
        assert task.name == "Clean Kitchen"
        assert task.description == "Wipe down counters and clean dishes"
        assert task.frequency_type == "weekly"
        assert task.frequency == 1
        assert task.frequency_metadata == {"days": ["monday", "friday"]}
        # next_due_date is parsed to datetime
        assert isinstance(task.next_due_date, datetime)
        assert task.next_due_date.year == 2025
        assert task.next_due_date.month == 1
        assert task.next_due_date.day == 15
        assert task.assigned_to == 42
        assert task.assign_strategy == "round_robin"
        assert task.is_active is True
        assert task.is_rolling is False
        assert task.notification is True
        assert task.labels == "cleaning,kitchen"
        assert task.circle_id == 100
        assert task.created_by == 42
        assert task.status == 0
        assert task.priority == 2
        assert task.points == 10
        assert task.completion_window == 3600
        assert task.require_approval is False
        assert task.is_private is False
        
        # Check assignees
        assert len(task.assignees) == 2
        assert task.assignees[0].user_id == 42
        assert task.assignees[1].user_id == 43
        
        # Check labels_v2
        assert len(task.labels_v2) == 1
        assert task.labels_v2[0].id == 1
        assert task.labels_v2[0].name == "Cleaning"
        
        # Check sub_tasks (note: it's sub_tasks, not subtasks)
        assert len(task.sub_tasks) == 2
        assert task.sub_tasks[0].name == "Wipe counters"
        assert task.sub_tasks[1].name == "Clean dishes"

    def test_from_json_minimal(self):
        """Test parsing minimal chore JSON."""
        json_data = {"id": 5, "name": "Simple Task"}
        task = DonetickTask.from_json(json_data)
        
        assert task.id == 5
        assert task.name == "Simple Task"
        assert task.description is None
        assert task.frequency_type == "once"
        assert task.frequency == 0
        assert task.frequency_metadata is None
        assert task.next_due_date is None
        assert task.assigned_to is None
        assert task.assignees is None
        assert task.assign_strategy is None
        assert task.is_active is True  # Default True
        assert task.is_rolling is False
        assert task.notification is False
        assert task.notification_metadata is None
        assert task.labels is None
        assert task.labels_v2 is None
        assert task.circle_id is None
        assert task.created_by is None
        assert task.status == 0
        assert task.priority == 0
        assert task.points is None
        assert task.completion_window is None
        assert task.require_approval is False
        assert task.is_private is False
        assert task.sub_tasks is None

    def test_from_json_null_assignees(self):
        """Test parsing chore with null assignees."""
        json_data = {
            "id": 6,
            "name": "Task with null assignees",
            "assignees": None,
        }
        task = DonetickTask.from_json(json_data)
        
        assert task.assignees is None

    def test_from_json_null_subtasks(self):
        """Test parsing chore with null subtasks."""
        json_data = {
            "id": 7,
            "name": "Task with null subtasks",
            "subTasks": None,
        }
        task = DonetickTask.from_json(json_data)
        
        assert task.sub_tasks is None

    def test_from_json_null_labels_v2(self):
        """Test parsing chore with null labelsV2."""
        json_data = {
            "id": 8,
            "name": "Task with null labels",
            "labelsV2": None,
        }
        task = DonetickTask.from_json(json_data)
        
        assert task.labels_v2 is None

    def test_from_json_inactive_task(self):
        """Test parsing inactive chore."""
        json_data = {
            "id": 9,
            "name": "Inactive Task",
            "isActive": False,
        }
        task = DonetickTask.from_json(json_data)
        
        assert task.is_active is False

    def test_from_json_all_frequency_types(self):
        """Test parsing chores with different frequency types."""
        frequency_types = [
            "once",
            "daily",
            "weekly",
            "monthly",
            "yearly",
            "interval",
            "day_of_week",
            "day_of_month",
            "trigger",
        ]
        
        for freq_type in frequency_types:
            json_data = {
                "id": 10,
                "name": f"Task with {freq_type}",
                "frequencyType": freq_type,
            }
            task = DonetickTask.from_json(json_data)
            assert task.frequency_type == freq_type

    def test_from_json_next_due_date_parsing(self):
        """Test parsing different date formats."""
        # ISO format with Z
        json_data = {
            "id": 11,
            "name": "Test Task",
            "nextDueDate": "2025-06-15T14:30:00Z",
        }
        task = DonetickTask.from_json(json_data)
        assert task.next_due_date is not None
        assert task.next_due_date.year == 2025
        assert task.next_due_date.month == 6
        assert task.next_due_date.day == 15

    def test_from_json_next_due_date_null(self):
        """Test parsing null next_due_date."""
        json_data = {
            "id": 12,
            "name": "No Due Date Task",
            "nextDueDate": None,
        }
        task = DonetickTask.from_json(json_data)
        assert task.next_due_date is None

    def test_from_json_frequency_metadata_dict(self):
        """Test parsing frequencyMetadata as dict."""
        json_data = {
            "id": 13,
            "name": "Weekly Task",
            "frequencyMetadata": {"days": ["monday", "wednesday"]},
        }
        task = DonetickTask.from_json(json_data)
        assert task.frequency_metadata == {"days": ["monday", "wednesday"]}

    def test_from_json_frequency_metadata_v2(self):
        """Test parsing frequencyMetadataV2 field."""
        json_data = {
            "id": 14,
            "name": "Task with V2 metadata",
            "frequencyMetadataV2": {"interval": 3},
        }
        task = DonetickTask.from_json(json_data)
        assert task.frequency_metadata == {"interval": 3}

    def test_from_json_frequency_metadata_string(self):
        """Test parsing frequencyMetadata as JSON string."""
        json_data = {
            "id": 15,
            "name": "Task with string metadata",
            "frequencyMetadata": '{"custom": true}',
        }
        task = DonetickTask.from_json(json_data)
        assert task.frequency_metadata == {"custom": True}

    def test_from_json_assigned_to_integer(self):
        """Test parsing assignedTo as integer."""
        json_data = {
            "id": 16,
            "name": "Assigned Task",
            "assignedTo": 42,
        }
        task = DonetickTask.from_json(json_data)
        assert task.assigned_to == 42

    def test_from_json_assigned_to_null(self):
        """Test parsing null assignedTo."""
        json_data = {
            "id": 17,
            "name": "Unassigned Task",
            "assignedTo": None,
        }
        task = DonetickTask.from_json(json_data)
        assert task.assigned_to is None

    def test_from_json_list(self, sample_chore_json, sample_chore_lite_json):
        """Test parsing list of chores."""
        json_list = [sample_chore_json, sample_chore_lite_json]
        tasks = DonetickTask.from_json_list(json_list)
        
        assert len(tasks) == 2
        assert tasks[0].id == 1
        assert tasks[0].name == "Clean Kitchen"
        assert tasks[1].id == 2
        assert tasks[1].name == "Take out trash"

    def test_from_json_empty_list(self):
        """Test parsing empty chore list."""
        tasks = DonetickTask.from_json_list([])
        assert tasks == []


class TestDonetickThing:
    """Tests for DonetickThing model."""

    def test_from_json_complete(self, sample_thing_json):
        """Test parsing complete thing JSON."""
        thing = DonetickThing.from_json(sample_thing_json)
        
        assert thing.id == 1
        assert thing.name == "Kitchen Light"
        assert thing.type == "boolean"
        assert thing.state == "on"
        assert thing.user_id == 42
        assert thing.circle_id == 100
        assert thing.updated_at == "2025-01-10T08:00:00Z"
        assert thing.created_at == "2024-01-01T00:00:00Z"

    def test_from_json_number_type(self):
        """Test parsing number type thing."""
        json_data = {
            "id": 2,
            "userID": 42,
            "circleId": 100,
            "name": "Room Temperature",
            "state": "72",
            "type": "number",
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.id == 2
        assert thing.name == "Room Temperature"
        assert thing.type == "number"
        assert thing.state == "72"

    def test_from_json_text_type(self):
        """Test parsing text type thing."""
        json_data = {
            "id": 3,
            "userID": 42,
            "circleId": 100,
            "name": "Status Message",
            "state": "All systems operational",
            "type": "text",
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.id == 3
        assert thing.name == "Status Message"
        assert thing.type == "text"
        assert thing.state == "All systems operational"

    def test_from_json_action_type(self):
        """Test parsing action type thing."""
        json_data = {
            "id": 4,
            "userID": 42,
            "circleId": 100,
            "name": "Trigger Button",
            "state": "triggered",
            "type": "action",
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.id == 4
        assert thing.name == "Trigger Button"
        assert thing.type == "action"
        assert thing.state == "triggered"

    def test_from_json_empty_state(self):
        """Test parsing thing with empty state."""
        json_data = {
            "id": 5,
            "userID": 42,
            "circleId": 100,
            "name": "Empty Thing",
            "state": "",
            "type": "text",
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.state == ""

    def test_from_json_numeric_state_converted_to_string(self):
        """Test that numeric state is converted to string."""
        json_data = {
            "id": 6,
            "userID": 42,
            "circleId": 100,
            "name": "Numeric Thing",
            "state": 123,
            "type": "number",
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.state == "123"
        assert isinstance(thing.state, str)

    def test_from_json_with_thing_chores(self):
        """Test parsing thing with linked chores."""
        json_data = {
            "id": 7,
            "userID": 42,
            "circleId": 100,
            "name": "Linked Thing",
            "state": "on",
            "type": "boolean",
            "thingChores": [{"choreId": 1}, {"choreId": 2}],
        }
        thing = DonetickThing.from_json(json_data)
        
        assert thing.thing_chores == [{"choreId": 1}, {"choreId": 2}]

    def test_from_json_list(self, sample_things_list):
        """Test parsing list of things."""
        things = DonetickThing.from_json_list(sample_things_list)
        
        assert len(things) == 3
        assert things[0].name == "Kitchen Light"
        assert things[1].name == "Room Temperature"

    def test_from_json_empty_list(self):
        """Test parsing empty thing list."""
        things = DonetickThing.from_json_list([])
        assert things == []


class TestDonetickMember:
    """Tests for DonetickMember model."""

    def test_from_json_complete(self, sample_circle_member_json):
        """Test parsing complete member JSON."""
        member = DonetickMember.from_json(sample_circle_member_json)
        
        assert member.id == 1
        assert member.user_id == 42
        assert member.circle_id == 100
        assert member.role == "admin"
        assert member.is_active is True
        assert member.username == "johndoe"
        assert member.display_name == "John Doe"
        assert member.image == "https://example.com/avatar.jpg"
        assert member.points == 150
        assert member.points_redeemed == 50
        assert member.created_at == "2024-01-01T00:00:00Z"
        assert member.updated_at == "2025-01-01T00:00:00Z"

    def test_from_json_minimal(self):
        """Test parsing minimal member JSON."""
        json_data = {
            "id": 2,
            "userId": 43,
            "circleId": 100,
            "role": "member",
            "isActive": True,
            "username": "user2",
            "displayName": "User 2",
        }
        member = DonetickMember.from_json(json_data)
        
        assert member.id == 2
        assert member.user_id == 43
        assert member.image is None
        assert member.points == 0
        assert member.points_redeemed == 0

    def test_from_json_inactive_member(self):
        """Test parsing inactive member."""
        json_data = {
            "id": 3,
            "userId": 44,
            "circleId": 100,
            "role": "member",
            "isActive": False,
            "username": "inactive",
            "displayName": "Inactive User",
        }
        member = DonetickMember.from_json(json_data)
        
        assert member.is_active is False

    def test_from_json_no_image(self):
        """Test parsing member without image."""
        json_data = {
            "id": 4,
            "userId": 45,
            "circleId": 100,
            "role": "member",
            "isActive": True,
            "username": "noimage",
            "displayName": "No Image User",
            "image": None,
        }
        member = DonetickMember.from_json(json_data)
        
        assert member.image is None

    def test_from_json_role_types(self):
        """Test parsing different role types."""
        for role in ["admin", "member", "owner"]:
            json_data = {
                "id": 5,
                "userId": 46,
                "circleId": 100,
                "role": role,
                "isActive": True,
                "username": f"{role}_user",
                "displayName": f"{role.title()} User",
            }
            member = DonetickMember.from_json(json_data)
            assert member.role == role

    def test_from_json_list(self, sample_circle_members_list):
        """Test parsing list of members."""
        members = DonetickMember.from_json_list(sample_circle_members_list)
        
        assert len(members) == 3
        assert members[0].username == "johndoe"
        assert members[1].username == "janedoe"
        assert members[2].username == "inactive_user"

    def test_from_json_empty_list(self):
        """Test parsing empty member list."""
        members = DonetickMember.from_json_list([])
        assert members == []
