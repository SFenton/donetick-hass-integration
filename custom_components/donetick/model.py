"""Donetick models."""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
  
)


_LOGGER = logging.getLogger(__name__)

@dataclass
class DonetickMember:
    """Donetick circle member model."""
    id: int
    user_id: int
    circle_id: int
    role: str
    is_active: bool
    username: str
    display_name: str
    image: Optional[str] = None
    points: int = 0
    points_redeemed: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    @classmethod
    def from_json(cls, data: dict) -> "DonetickMember":
        """Create a DonetickMember from JSON data."""
        return cls(
            id=data["id"],
            user_id=data["userId"],
            circle_id=data["circleId"],
            role=data["role"],
            is_active=data["isActive"],
            username=data["username"],
            display_name=data["displayName"],
            image=data.get("image"),
            points=data.get("points", 0),
            points_redeemed=data.get("pointsRedeemed", 0),
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt")
        )
    
    @classmethod
    def from_json_list(cls, data: List[dict]) -> List["DonetickMember"]:
        """Create a list of DonetickMembers from JSON data."""
        return [cls.from_json(member) for member in data]

@dataclass
class DonetickAssignee:
    """Donetick assignee model."""
    user_id: int


@dataclass
class DonetickLabel:
    """Donetick label model."""
    id: int
    name: str
    color: str


@dataclass
class DonetickSubTask:
    """Donetick subtask model."""
    id: int
    name: str
    order_id: int = 0
    completed_at: Optional[datetime] = None
    completed_by: int = 0
    parent_id: Optional[int] = None


@dataclass
class DonetickTask:
    """Donetick task model with full properties."""
    id: int
    name: str
    next_due_date: Optional[datetime]
    status: int
    priority: int
    labels: Optional[str]
    is_active: bool
    frequency_type: str
    frequency: int
    frequency_metadata: Optional[dict] = None
    assigned_to: Optional[int] = None
    description: Optional[str] = None
    # Extended properties available with JWT auth
    circle_id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_rolling: bool = False
    assign_strategy: Optional[str] = None
    notification: bool = False
    notification_metadata: Optional[dict] = None
    labels_v2: Optional[List[DonetickLabel]] = None
    assignees: Optional[List[DonetickAssignee]] = None
    sub_tasks: Optional[List[DonetickSubTask]] = None
    points: Optional[int] = None
    completion_window: Optional[int] = None
    require_approval: bool = False
    is_private: bool = False
    
    @classmethod
    def from_json(cls, data: dict) -> "DonetickTask":
        """Create a DonetickTask from JSON data."""
        # Handle assignedTo field - could be in different formats
        assigned_to = None
        if data.get("assignedTo"):
            if isinstance(data["assignedTo"], int):
                assigned_to = data["assignedTo"]
        
        # Parse dates
        next_due_date = None
        if data.get("nextDueDate"):
            try:
                next_due_date = datetime.fromisoformat(data["nextDueDate"].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        created_at = None
        if data.get("createdAt"):
            try:
                created_at = datetime.fromisoformat(data["createdAt"].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
                
        updated_at = None
        if data.get("updatedAt"):
            try:
                updated_at = datetime.fromisoformat(data["updatedAt"].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        # Parse assignees
        assignees = None
        if data.get("assignees"):
            assignees = [
                DonetickAssignee(user_id=a.get("userId", a.get("user_id", 0)))
                for a in data["assignees"]
            ]
        
        # Parse labels v2
        labels_v2 = None
        if data.get("labelsV2"):
            labels_v2 = [
                DonetickLabel(
                    id=l.get("id", 0),
                    name=l.get("name", ""),
                    color=l.get("color", "")
                )
                for l in data["labelsV2"]
            ]
        
        # Parse subtasks
        sub_tasks = None
        if data.get("subTasks"):
            sub_tasks = [
                DonetickSubTask(
                    id=s.get("id", 0),
                    name=s.get("name", ""),
                    order_id=s.get("orderId", 0),
                    completed_at=datetime.fromisoformat(s["completedAt"].replace('Z', '+00:00')) if s.get("completedAt") else None,
                    completed_by=s.get("completedBy", 0),
                    parent_id=s.get("parentId"),
                )
                for s in data["subTasks"]
            ]
        
        # Parse frequency metadata - could be string (legacy) or dict
        frequency_metadata = None
        freq_meta = data.get("frequencyMetadata") or data.get("frequencyMetadataV2")
        if freq_meta:
            if isinstance(freq_meta, dict):
                frequency_metadata = freq_meta
            elif isinstance(freq_meta, str):
                try:
                    import json
                    frequency_metadata = json.loads(freq_meta)
                except:
                    pass
        
        return cls(
            id=data["id"],
            name=data["name"],
            next_due_date=next_due_date,
            status=data.get("status", 0),
            priority=data.get("priority", 0),
            labels=data.get("labels"),
            is_active=data.get("isActive", True),
            frequency_type=data.get("frequencyType", "once"),
            frequency=data.get("frequency", 0),
            frequency_metadata=frequency_metadata,
            assigned_to=assigned_to,
            description=data.get("description"),
            circle_id=data.get("circleId"),
            created_by=data.get("createdBy"),
            created_at=created_at,
            updated_at=updated_at,
            is_rolling=data.get("isRolling", False),
            assign_strategy=data.get("assignStrategy"),
            notification=data.get("notification", False),
            notification_metadata=data.get("notificationMetadata") or data.get("notificationMetadataV2"),
            labels_v2=labels_v2,
            assignees=assignees,
            sub_tasks=sub_tasks,
            points=data.get("points"),
            completion_window=data.get("completionWindow"),
            require_approval=data.get("requireApproval", False),
            is_private=data.get("isPrivate", False),
        )
    
    @classmethod
    def from_json_list(cls, data: List[dict]) -> List["DonetickTask"]:
        """Create a list of DonetickTasks from JSON data."""
        return [cls.from_json(task) for task in data]

@dataclass 
class DonetickThing:
    """Donetick thing model."""
    id: int
    name: str
    type: str  # text, number, boolean, action
    state: str
    user_id: int
    circle_id: int
    updated_at: Optional[str] = None
    created_at: Optional[str] = None
    thing_chores: Optional[List] = None
    
    @classmethod
    def from_json(cls, data: dict) -> "DonetickThing":
        """Create a DonetickThing from JSON data."""
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            state=str(data["state"]),
            user_id=data["userID"],
            circle_id=data["circleId"],
            updated_at=data.get("updatedAt"),
            created_at=data.get("createdAt"),
            thing_chores=data.get("thingChores")
        )
    
    @classmethod
    def from_json_list(cls, data: List[dict]) -> List["DonetickThing"]:
        """Create a list of DonetickThings from JSON data."""
        return [cls.from_json(thing) for thing in data]
    
