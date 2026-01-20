# models.py
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

class ConversationStage(Enum):
    """Conversation stages for lead qualification"""
    COLD_OUTREACH = "cold_outreach"
    INITIAL_RESPONSE = "initial_response"
    INTEREST_SHOWN = "interest_shown"
    QUALIFICATION = "qualification"
    DEMO_SCHEDULED = "demo_scheduled"
    PROPOSAL_SENT = "proposal_sent"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    NURTURE = "nurture"

class MessageIntent(Enum):
    """Message intent classification"""
    POSITIVE_RESPONSE = "positive_response"
    NEGATIVE_RESPONSE = "negative_response"
    QUESTION = "question"
    REQUEST_INFO = "request_info"
    SCHEDULE_MEETING = "schedule_meeting"
    PRICE_INQUIRY = "price_inquiry"
    OBJECTION = "objection"
    REFERRAL = "referral"
    OUT_OF_OFFICE = "out_of_office"
    NOT_INTERESTED = "not_interested"
    SPAM = "spam"
    MEETING_CONFIRMATION = "meeting_confirmation"
    PROVIDE_EMAIL = "provide_email"

@dataclass
class ConversationMetrics:
    """Metrics for conversation analysis"""
    lead_score: int = 0
    engagement_score: int = 0
    response_time_avg: float = 0.0
    message_count: int = 0
    last_interaction: str = ""
    stage: ConversationStage = ConversationStage.COLD_OUTREACH
    intent: MessageIntent = MessageIntent.POSITIVE_RESPONSE
    priority: str = "medium"
    tags: List[str] = field(default_factory=list)

@dataclass
class Contact:
    """Enhanced contact information"""
    name: str
    linkedin_url: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    industry: str = ""
    connections: str = ""
    profile_data: Dict[str, Any] = field(default_factory=dict)
    conversation_id: str = ""
    first_message_date: str = ""
    last_message_date: str = ""