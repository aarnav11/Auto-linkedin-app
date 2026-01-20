import os
import json
import time
import logging
import random
import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import asdict
import hashlib
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from enum import Enum
# Import your data models
from models import Contact, ConversationMetrics, ConversationStage, MessageIntent

logger = logging.getLogger(__name__)

class InboxPlatform(Enum):
    LINKEDIN = "linkedin"
    SALES_NAVIGATOR = "sales_navigator"

class EnhancedAIInbox:
    """LinkedHelper 2 style AI inbox with advanced features"""
    
    def __init__(self, gemini_model=None, client_instance=None):
        self.model = gemini_model
        self.client = client_instance
        self.conversations_db = "conversations_db.json"
        self.leads_db = "leads_db.json"
        self.templates_db = "message_templates.json"
        self.settings_file = "inbox_settings.json"
        self.replied_db = "replied_contacts.json" 
        
        self.conversations = self.load_json_db(self.conversations_db, {})
        self.leads = self.load_json_db(self.leads_db, {})
        self.templates = self.load_json_db(self.templates_db, self.get_default_templates())
        self.settings = self.load_json_db(self.settings_file, self.get_default_settings())
        
        self.response_strategies = self.build_response_strategies()
        self.active_inbox_sessions = {}

    def get_selectors(self, platform: InboxPlatform) -> Dict[str, List[str]]:
        """Return CSS selectors based on the platform"""
        if platform == InboxPlatform.SALES_NAVIGATOR:
            return {
                # 1. Conversation List Items (Left Sidebar)
                "conversation_items": [
                    "div.artdeco-entity-lockup",            # From your finding (most stable)
                    ".artdeco-entity-lockup__content",      # From your finding
                    "li[data-test-thread-list-item]",       # Standard fallback
                    "div[data-x-thread-list-item]",         
                    "li.artdeco-list__item"
                ],
                # 2. Message Bubbles (The container for a single message)
                "message_containers": [
                    "div.message-content",                  # From your finding
                    "li.thread-history__list-item",
                    "div.thread-history__list-item",
                    "[data-test-thread-history-message]",
                    "div._message-padding--medium_zovuu6"   # From your finding (dynamic hash)
                ],
                # 3. Message Text (Inside the bubble)
                "message_body": [
                    "p.t-14.white-space-pre-wrap",          # From your finding
                    ".message-content p",
                    ".thread-history__message-body",
                    "[data-test-thread-message-body]"
                ],
                # 4. Sender Name (Crucial for "Me" vs "Them" detection)
                "sender_name": [
                    ".thread-history__sender-name",
                    "[data-test-thread-message-sender]",
                    "span.t-14.t-bold",                     # Generic bold text often used for names
                    "h3.t-14.t-bold"
                ],
                # 5. Input Box (Where we type)
                "input_box": [
                    "textarea[name='message']",             # From your finding (Very stable)
                    "textarea._message-field_jrrmou",       # From your finding
                    "textarea[placeholder*='Type your message']",
                    ".compose-form__textarea"
                ],
                # 6. Send Button
                "send_button": [
                    "button._primary_ps32ck",               # From your finding
                    "button._button_ps32ck",                # From your finding
                    "button[type='button']._primary_ps32ck",
                    "button.compose-form__send-button",
                    "[data-test-compose-form-send-button]"
                ],
                # 7. Participant Name (Header of the chat)
                "participant_name": [
                    "[data-test-thread-participant-name]",
                    ".thread-list-item__participant-name",
                    "span.thread-list-item__title",
                    ".artdeco-entity-lockup__title"
                ]
            }
        else:
            # Standard LinkedIn Selectors (Unchanged)
            return {
                "conversation_items": [
                    "li.msg-conversation-listitem",
                    "li.msg-conversation-card__row", 
                    "div.msg-conversation-card"
                ],
                "message_containers": [
                    "li.msg-s-message-list__event",
                    ".msg-s-message-group",
                    ".msg-s-event-listitem"
                ],
                "message_body": [
                    ".msg-s-event-listitem__body",
                    ".msg-s-message-group__body p"
                ],
                "sender_name": [
                    ".msg-s-message-group__name",
                    ".msg-s-event-listitem__sender-name"
                ],
                "participant_name": [
                    ".msg-entity-lockup__entity-title",
                    ".msg-conversation-listitem__participant-names"
                ],
                "input_box": [
                    ".msg-form__contenteditable",
                    "div[role='textbox'][contenteditable='true']"
                ],
                "send_button": [
                    "button.msg-form__send-button"
                ]
            }
    
    def load_json_db(self, filename: str, default_data: Any) -> Any:
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load {filename}: {e}")
        return default_data
    
    def save_json_db(self, filename: str, data: Any):
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Could not save {filename}: {e}")

    def mark_contact_as_replied(self, name: str, linkedin_url: str):
        """
        Updates local records to indicate this contact has replied.
        This is critical for stopping follow-up sequences (process_non_responders).
        """
        try:
            # 1. Update client's in-memory active campaigns if available
            # This prevents the running campaign loop from emailing them if it checks memory
            if self.client and hasattr(self.client, 'active_campaigns'):
                for campaign_id, campaign in self.client.active_campaigns.items():
                    if 'contacts_processed' in campaign:
                        for contact in campaign['contacts_processed']:
                            # Match by URL (preferred) or Name
                            is_match = False
                            if linkedin_url and contact.get('LinkedIn_profile') and linkedin_url in contact.get('LinkedIn_profile'):
                                is_match = True
                            elif name and contact.get('Name') == name:
                                is_match = True
                            
                            if is_match:
                                contact['has_replied'] = True
                                logger.info(f"✅ Sync: Marked {name} as replied in active campaign {campaign_id}")

            # 2. Persist to a JSON file so process_non_responders can read it later 
            # (even after restart or in different threads)
            replied_data = self.load_json_db(self.replied_db, {})
            
            # Use URL as key if available, otherwise name
            key = linkedin_url if linkedin_url else name
            
            if key:
                replied_data[key] = {
                    "name": name,
                    "linkedin_url": linkedin_url,
                    "replied_at": datetime.now().isoformat()
                }
                self.save_json_db(self.replied_db, replied_data)
                logger.debug(f"💾 Persisted reply status for {name}")

        except Exception as e:
            logger.error(f"Failed to mark contact as replied: {e}")
        
    def navigate_to_messaging_safe(self, driver, platform: InboxPlatform, retries=3):
        """Navigate to the correct inbox URL based on platform"""
        url = "https://www.linkedin.com/messaging"
        if platform == InboxPlatform.SALES_NAVIGATOR:
            url = "https://www.linkedin.com/sales/inbox"

        logger.info(f"📨 Navigating to {platform.value} inbox: {url}")
        
        for attempt in range(1, retries + 1):
            try:
                if url not in driver.current_url:
                    driver.get(url)
                
                # --- UPDATED: Use actual selectors that exist on the page ---
                if platform == InboxPlatform.SALES_NAVIGATOR:
                    # Wait for the conversation list items (these ACTUALLY exist)
                    WebDriverWait(driver, 20).until(
                        EC.any_of(
                            # Primary: Conversation items in the sidebar
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.artdeco-entity-lockup")),
                            # Alternate: Content wrapper
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".artdeco-entity-lockup__content")),
                            # Fallback: Just check we're on Sales Nav
                            EC.presence_of_element_located((By.CSS_SELECTOR, "textarea[name='message']"))
                        )
                    )
                else:
                    # Standard LinkedIn inbox
                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "ul.msg-conversations-container__conversations-list"))
                    )
                
                time.sleep(2)
                logger.info("✅ Successfully loaded messaging page.")
                return True
                
            except Exception as e:
                logger.warning(f"⚠️ Attempt {attempt}/{retries} failed: {e}")
                
                # Check for Sales Nav access issues
                if platform == InboxPlatform.SALES_NAVIGATOR and attempt == 1:
                    try:
                        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                        if any(keyword in page_text for keyword in ["upgrade", "sales navigator required", "premium"]):
                            logger.error("❌ Sales Navigator subscription required but not available")
                            return False
                    except:
                        pass
                
                if attempt < retries:
                    driver.refresh()
                    time.sleep(random.uniform(3, 6))
        
        return False

    
    def get_default_templates(self) -> Dict[str, Dict[str, str]]:
        """Default message templates"""
        return {
            "positive_followup": {
                "template": "Hi {name}, thanks for your positive response! I'd love to learn more about {company}'s current challenges with {industry_topic}. Would you be open to a brief 15-minute call this week?",
                "triggers": ["interested", "sounds good", "tell me more", "yes"]
            },
            "objection_handling": {
                "template": "I understand {name}. Many {title}s at companies like {company} have similar concerns. What if I could show you how we've helped similar companies overcome this exact challenge in just 15 minutes?",
                "triggers": ["not interested", "no budget", "no time", "already have"]
            },
            "demo_request": {
                "template": "Hi {name}, I'd be happy to show you exactly how this works for {company}. I have a few slots available this week - would Tuesday or Wednesday work better for a quick 20-minute demo?",
                "triggers": ["demo", "show me", "how does it work", "see it in action"]
            },
            "pricing_inquiry": {
                "template": "Hi {name}, great question! Our pricing depends on {company}'s specific needs. I'd love to understand your requirements better so I can provide accurate pricing. Could we schedule a brief call?",
                "triggers": ["price", "cost", "how much", "pricing", "budget"]
            },
            "referral_request": {
                "template": "Thanks {name}! I appreciate that. Do you know anyone at {company} or in your network who might benefit from this? I'd be happy to provide value to your connections as well.",
                "triggers": ["not the right person", "talk to", "someone else handles"]
            }
        }
    
    def get_default_settings(self) -> Dict[str, Any]:
        """Default inbox processing settings - IMPROVED VERSION"""
        return {
            "auto_reply_enabled": True,
            "max_daily_replies": 50,
            "min_lead_score": 25,  # LOWERED from 30 to 25
            "response_delay_min": 5,
            "response_delay_max": 60,
            "working_hours": {"start": 9, "end": 17},
            "blacklist_keywords": ["spam", "unsubscribe", "remove", "not interested"],
            "priority_keywords": ["urgent", "asap", "important", "meeting", "demo", "call"],
            "qualification_questions": [
                "What's your current process for {topic}?",
                "What challenges are you facing with {topic}?",
                "What's your timeline for making a decision?",
                "Who else would be involved in this decision?"
            ]
        }
    
    def build_response_strategies(self) -> Dict[str, Dict[str, str]]:
        """Build response strategies matrix"""
        return {
            "cold_outreach": {
                "positive_response": "followup_interest",
                "question": "answer_and_qualify", 
                "request_info": "provide_info_and_demo",
                "objection": "handle_objection",
                "not_interested": "soft_nurture"
            },
            "initial_response": {
                "positive_response": "qualify_needs",
                "question": "answer_and_schedule",
                "request_info": "send_resources",
                "schedule_meeting": "propose_times",
                "price_inquiry": "qualify_budget"
            },
            "interest_shown": {
                "positive_response": "schedule_demo",
                "question": "detailed_answer", 
                "schedule_meeting": "confirm_meeting",
                "price_inquiry": "custom_proposal",
                "objection": "overcome_objection"
            },
            "qualification": {
                "positive_response": "move_to_demo",
                "question": "qualify_further",
                "schedule_meeting": "demo_meeting",
                "objection": "address_concerns",
                "request_info": "detailed_proposal"
            }
        }
    

    def classify_message_with_ai(self, message: str) -> Optional[MessageIntent]:
        """Use AI to classify message intent with improved priority logic"""
        # Define the prompt with strict hierarchy rules
        prompt = f"""Analyze this LinkedIn message and classify its intent.

Message: "{message}"

Classify into ONE of these categories:
- positive_response: Shows interest, agreement, or positive engagement
- negative_response: Clearly not interested, wants to unsubscribe
- question: Asking questions about the product/service
- request_info: Wants more information, resources, or details
- schedule_meeting: Wants to schedule a call, meeting, or demo
- meeting_confirmation: Confirms a specific time/date for a meeting OR asks for a calendar invite
- provide_email: The user is ONLY providing their email address (e.g., "my email is example@test.com")
- price_inquiry: Asking about pricing, costs, or budget
- objection: Has concerns, objections, or challenges
- referral: Suggesting to talk to someone else
- out_of_office: Auto-reply indicating they're away
- spam: Spam or irrelevant message

PRIORITY RULES:
1. If the user asks for a meeting invite to be sent to an email, classify as 'meeting_confirmation', NOT 'provide_email'.
2. If the user suggests a specific time/date, classify as 'meeting_confirmation'.
3. If the message contains an email but is also a question, classify as 'question'.

Reply with only the category name."""

        try:
            response = self.model.generate_content(prompt)
            intent_str = response.text.strip().lower()
            
            # Map the string response to your Enum
            for intent in MessageIntent:
                if intent.value in intent_str:
                    return intent
                    
        except Exception as e:
            logger.error(f"AI classification error: {e}")
        
        return None
    
    def analyze_message_intent(self, message: str) -> MessageIntent:
        """Analyze message intent prioritizing AI, with keyword fallback"""
        message_lower = message.lower()

        # 1. PRIMARY: AI-based classification
        # We try this first because it understands context (e.g., "Send invite to naveen@...")
        if self.model:
            try:
                ai_intent = self.classify_message_with_ai(message)
                if ai_intent:
                    return ai_intent
            except Exception as e:
                logger.debug(f"AI intent classification failed, falling back to keywords: {e}")

        # 2. FALLBACK: Regex for safety (if AI fails or is slow)
        # Catches: "Tuesday at 2pm", "12:00 PM IST"
        time_pattern = r'\d{1,2}(?::\d{2})?\s*(?:am|pm|ist|est|pst|utc|gmt|cet)'
        if re.search(time_pattern, message_lower) and any(w in message_lower for w in ['tomorrow', 'today', 'next week']):
            return MessageIntent.MEETING_CONFIRMATION

        # 3. FALLBACK: Keyword-based classification
        # (Keep your existing intent_keywords dictionary here as the final safety net)
        intent_keywords = {
            MessageIntent.MEETING_CONFIRMATION: ["booked", "confirmed", "works for me", "send invite"],
            MessageIntent.PROVIDE_EMAIL: ["@"],
            MessageIntent.POSITIVE_RESPONSE: ["yes", "interested", "sounds good"],
            MessageIntent.NEGATIVE_RESPONSE: ["no", "not interested", "remove"],
            # ... keep the rest of your keywords ...
        }
        
        for intent, keywords in intent_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                return intent
        
        return MessageIntent.POSITIVE_RESPONSE
    
    
    
    def calculate_lead_score(self, contact: Contact, conversation_history: List[Dict[str, str]], 
                       metrics: ConversationMetrics) -> int:
        """Calculate lead score (0-100) - IMPROVED VERSION"""
        score = 30  # Start with base score instead of 0
        
        # Valid conversation exists bonus
        if len(conversation_history) > 0:
            score += 10
        
        # Company size indicators
        if contact.connections:
            conn_match = re.search(r'(\d+)', contact.connections)
            if conn_match:
                conn_count = int(conn_match.group(1))
                if conn_count > 500:
                    score += 15
                elif conn_count > 200:
                    score += 10
                elif conn_count > 100:
                    score += 5
        
        # Title/seniority scoring
        if contact.title:
            title_lower = contact.title.lower()
            senior_titles = ["ceo", "cto", "cfo", "vp", "vice president", "director", "head", "manager", "founder", "owner", "president"]
            if any(title in title_lower for title in senior_titles):
                score += 20
            else:
                score += 5  # Any title is better than none
        
        # Industry relevance
        if contact.industry:
            relevant_industries = ["technology", "software", "saas", "fintech", "healthcare", "consulting", "services"]
            if any(industry in contact.industry.lower() for industry in relevant_industries):
                score += 15
        
        # Conversation engagement - they initiated or responded
        if len(conversation_history) >= 1:
            score += 5
        if len(conversation_history) > 1:
            score += 10
        if len(conversation_history) > 3:
            score += 10
        
        # Response patterns - look for positive signals
        positive_indicators = ["interested", "tell me more", "sounds good", "yes", "demo", "meeting", "call", "schedule", "discuss", "learn more"]
        recent_messages = conversation_history[-3:] if len(conversation_history) >= 3 else conversation_history
        
        for msg in recent_messages:
            if msg.get('sender') != 'You':
                msg_text = msg.get('message', '').lower()
                positive_count = sum(1 for indicator in positive_indicators if indicator in msg_text)
                score += positive_count * 5
        
        # Message quality (length indicates thoughtfulness)
        if recent_messages:
            avg_length = sum(len(msg.get('message', '')) for msg in recent_messages if msg.get('sender') != 'You') / max(len([m for m in recent_messages if m.get('sender') != 'You']), 1)
            if avg_length > 100:
                score += 10
            elif avg_length > 50:
                score += 5
        
        # Question asked (shows interest)
        for msg in recent_messages:
            if msg.get('sender') != 'You' and '?' in msg.get('message', ''):
                score += 10
                break
        
        return min(score, 100)

    def should_auto_reply(self, metrics: ConversationMetrics, last_message: str) -> bool:
        """Determine if message should get auto-reply - IMPROVED VERSION"""
        settings = self.settings
        
        # Check if auto-reply is enabled
        if not settings.get('auto_reply_enabled', True):
            logger.info("Auto-reply disabled in settings")
            return False
        
        # CRITICAL: Lower the minimum lead score threshold
        min_score = settings.get('min_lead_score', 25)  # Changed from 30 to 25
        if metrics.lead_score < min_score:
            logger.info(f"Lead score {metrics.lead_score} below minimum {min_score}")
            return False
        
        # Check blacklisted keywords
        blacklist = settings.get('blacklist_keywords', [])
        if any(keyword in last_message.lower() for keyword in blacklist):
            logger.info("Message contains blacklisted keyword")
            return False
        
        # Check for spam
        if metrics.intent == MessageIntent.SPAM:
            logger.info("Message detected as spam")
            return False
        
        # Check for out of office
        if metrics.intent == MessageIntent.OUT_OF_OFFICE:
            logger.info("Out of office auto-reply detected")
            return False
        
        # Check for negative responses (don't reply to rejections)
        if metrics.intent == MessageIntent.NEGATIVE_RESPONSE:
            logger.info("Negative response detected")
            return False
        
        logger.info(f"✅ Auto-reply approved (score: {metrics.lead_score})")
        return True
    
    def determine_conversation_stage(self, conversation_history: List[Dict[str, str]], 
                                   current_intent: MessageIntent) -> ConversationStage:
        """Determine current conversation stage"""
        if not conversation_history:
            return ConversationStage.COLD_OUTREACH
        
        # Count messages from each party
        user_messages = [msg for msg in conversation_history if msg.get('sender') == 'You']
        their_messages = [msg for msg in conversation_history if msg.get('sender') != 'You']
        
        # Stage progression logic
        if len(their_messages) == 0:
            return ConversationStage.COLD_OUTREACH
        elif len(their_messages) == 1:
            return ConversationStage.INITIAL_RESPONSE
        elif current_intent in [MessageIntent.SCHEDULE_MEETING, MessageIntent.REQUEST_INFO]:
            return ConversationStage.INTEREST_SHOWN
        elif current_intent == MessageIntent.PRICE_INQUIRY:
            return ConversationStage.QUALIFICATION
        elif "demo" in conversation_history[-1].get('message', '').lower():
            return ConversationStage.DEMO_SCHEDULED
        elif len(their_messages) > 3:
            return ConversationStage.QUALIFICATION
        else:
            return ConversationStage.INITIAL_RESPONSE
    
    def generate_smart_response(self, contact: Contact, conversation_history: List[Dict[str, str]], 
                              metrics: ConversationMetrics) -> str:
        """Generate intelligent response based on context"""
        if not conversation_history:
            return "Thank you for connecting! I'll be in touch soon."
        
        last_message = conversation_history[-1].get('message', '')
        stage = metrics.stage.value
        intent = metrics.intent.value
        

        try:
            if not self.client:
                logger.warning("No client instance, cannot perform Google actions.")
                # We continue to standard AI response instead of raising an exception to ensure reply
            else:
                # 1. User wants to schedule a meeting 
                if intent == MessageIntent.SCHEDULE_MEETING.value:
                    logger.info("Intent: SCHEDULE_MEETING. Fetching calendar slots...")
                    slots = self.client.get_calendar_slots(duration_minutes=30, days_ahead=7)
                    if slots:
                        # Format slots for the AI
                        formatted_slots = [
                            datetime.fromisoformat(s).strftime('%A, %B %d at %I:%M %p %Z') for s in slots
                        ]
                        slot_context = (
                            "I've checked the calendar and found some available times. "
                            f"Here are the next few slots: \n- " + "\n- ".join(formatted_slots) +
                            "\n\nDo any of these work for you? Or feel free to suggest another time."
                        )
                        return self.generate_ai_response(
                            contact, conversation_history, metrics, 
                            strategy="propose_meeting_times",
                            extra_context=slot_context
                        )
                    else:
                        logger.warning("No free slots found, using generic AI response.")
                        return self.generate_ai_response(contact, conversation_history, metrics, "propose_meeting_generic")

                # 2. User confirms a meeting time
                elif intent == MessageIntent.MEETING_CONFIRMATION.value:
                    logger.info("Intent: MEETING_CONFIRMATION. Attempting to book meeting...")
                    return self.handle_booking_confirmation(contact, conversation_history)

                # 3. User provides an email (likely for a proposal)
                elif intent == MessageIntent.PROVIDE_EMAIL.value:
                    logger.info("Intent: PROVIDE_EMAIL. Attempting to send proposal...")
                    return self.handle_email_sending(contact, conversation_history)

        except Exception as e:
            logger.error(f"Error during smart response action: {e}")
        
        # Get response strategy
        strategy = self.response_strategies.get(stage, {}).get(intent, "general_response")
        
        # Use AI for personalized response
        if self.model:
            try:
                return self.generate_ai_response(contact, conversation_history, metrics, strategy)
            except Exception as e:
                logger.error(f"AI response generation failed: {e}")
        
        # Fallback to template-based response
        return self.generate_template_response(contact, last_message, strategy)
    
    def generate_ai_response(self, contact: Contact, conversation_history: List[Dict[str, str]], 
                           metrics: ConversationMetrics, strategy: str, extra_context: str = "") -> str:
        """Generate AI response with full context"""
        COMPANY_DETAILS = (
    "At Espial Solutions, we specialize in digital marketing services, including SEO, Social Media Marketing, and PPC Campaign Management. "
    "We help businesses like {company} increase their online visibility and generate qualified leads through our innovative marketing strategies and data-driven approach."
).format(company=contact.company or "theirs")
        
        STANDARD_PROPOSAL_SNIPPET = (
    "Our standard package includes comprehensive keyword optimization, social media management, and targeted ad campaign setup — "
    "designed for teams who want to get started quickly and see measurable results. "
    "Are you currently exploring an SEO, SMM, or PPC plan so we can share the most relevant details?"
)
        # Format conversation history
        formatted_history = "\n".join([
            f"{msg['sender']}: {msg['message']}" for msg in conversation_history[-5:]
        ])

        context_block = f"ADDITIONAL CONTEXT TO USE:\n{extra_context}\n" if extra_context else ""
        
        prompt = f"""You are a professional LinkedIn sales assistant. Generate a personalized response based on the context below.

CONTACT INFORMATION:
- Name: {contact.name}
- Company: {contact.company}
- Title: {contact.title}
- Industry: {contact.industry}

CONVERSATION CONTEXT:
- Stage: {metrics.stage.value}
- Intent: {metrics.intent.value}
- Lead Score: {metrics.lead_score}/100

RECENT MESSAGES:
{formatted_history}
YOUR RESOURCES (Use these when intent matches):
1.  **Company Details:** "{COMPANY_DETAILS}"
2.  **Standard Proposal Info:** "{STANDARD_PROPOSAL_SNIPPET}"
{context_block}
RESPONSE STRATEGY: {strategy}

GUIDELINES:
1. Be professional yet personable
2. Address their specific message/question
3. Match their communication style and tone
4. Keep it concise (2-3 sentences max)
5.  **If Strategy is 'propose_meeting_times':** Use the "ADDITIONAL CONTEXT" (the list of times) to propose the meeting. Ask them to confirm one or suggest another.
6.  **If Strategy is 'propose_meeting_generic':** Propose a call and ask for their availability.
7.  **If Intent is 'request_info':** Use the 'Company Details' resource.
8.  **If Intent is 'price_inquiry':** Use the 'Standard Proposal Info' resource and ask for the best email to send details to.
9. Use their name naturally.
Generate a response:"""

        try:
            response = self.model.generate_content(prompt)
            ai_message = response.text.strip()
            
            # Clean up response
            ai_message = re.sub(r'^(Response:|Reply:)\s*', '', ai_message, flags=re.IGNORECASE)
            ai_message = ai_message.strip('"\'')
            
            # Ensure reasonable length
            if len(ai_message) > 400:
                ai_message = ai_message[:397] + "..."

            return ai_message
            
        except Exception as e:
            logger.error(f"AI response generation failed: {e}")
            # Check specifically for Quota errors
            if "429" in str(e) or "Quota" in str(e) or "QUOTA_EXCEEDED" in str(e):
                logger.critical("⚠️ GEMINI QUOTA EXCEEDED. Switching to template fallback.")
            
            # FALLBACK: Use the template generator instead of returning empty/error
            return self.generate_template_response(
                contact, 
                conversation_history[-1].get('message', '') if conversation_history else '', 
                strategy
            )
    
    def generate_template_response(self, contact: Contact, last_message: str, strategy: str) -> str:
        """Generate template-based response"""
        # Find matching template
        for template_name, template_data in self.templates.items():
            triggers = template_data.get('triggers', [])
            if any(trigger in last_message.lower() for trigger in triggers):
                template = template_data['template']
                return template.format(
                    name=contact.name.split()[0] if contact.name else "there",
                    company=contact.company or "your company",
                    title=contact.title or "professional",
                    industry_topic="your industry"
                )
        
        # Default response
        name = contact.name.split()[0] if contact.name else "there"
        return f"Hi {name}, thanks for your message! I'll review this and get back to you with a thoughtful response soon."
    
    
    def _save_processed_conversations_enhanced(self, filename: str, conversations_data: dict):
        """Save processed conversations with enhanced metadata"""
        try:
            data = {
                'date': datetime.now().strftime("%Y-%m-%d"),
                'conversations_data': conversations_data,
                'saved_at': datetime.now().isoformat()
            }
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"💾 Saved {len(conversations_data)} processed conversation records to {filename}")
        except Exception as e:
            logger.error(f"❌ Could not save processed conversations: {e}")
    
    def prioritize_conversations(self, conversations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort conversations by priority score"""
        def priority_score(conv):
            metrics = conv.get('metrics', {})
            score = 0
            
            # Lead score weight
            score += metrics.get('lead_score', 0) * 0.4
            
            # Engagement score weight  
            score += metrics.get('engagement_score', 0) * 0.3
            
            # Recency weight (more recent = higher score)
            last_interaction = metrics.get('last_interaction', '')
            if last_interaction:
                try:
                    last_time = datetime.fromisoformat(last_interaction)
                    hours_ago = (datetime.now() - last_time).total_seconds() / 3600
                    recency_score = max(0, 100 - hours_ago)  # Decreases over time
                    score += recency_score * 0.3
                except:
                    pass
            
            return score
        
        return sorted(conversations, key=priority_score, reverse=True)
    
    def extract_contact_info_enhanced(self, driver, conversation_details: Dict[str, Any]) -> Contact:
        """Extract enhanced contact information"""
        from selenium.webdriver.common.by import By
        
        contact = Contact(
            name=conversation_details.get('participant_name', 'Unknown'),
            company=conversation_details.get('participant_headline', '').split(' at ')[-1] if ' at ' in conversation_details.get('participant_headline', '') else '',
            title=conversation_details.get('participant_headline', '').split(' at ')[0] if ' at ' in conversation_details.get('participant_headline', '') else conversation_details.get('participant_headline', '')
        )
        
        # Try to extract more profile data from the current conversation view
        try:
            # Look for profile link in conversation
            profile_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/in/']")
            for link in profile_links:
                href = link.get_attribute('href')
                if '/in/' in href and 'linkedin.com' in href:
                    contact.linkedin_url = href
                    break
            
            # Extract additional info if available
            info_elements = driver.find_elements(By.CSS_SELECTOR, ".msg-thread-headline__info-text")
            for element in info_elements:
                text = element.text.strip()
                if "connections" in text.lower():
                    contact.connections = text
                elif "mutual" in text.lower():
                    contact.profile_data['mutual_connections'] = text
                    
        except Exception as e:
            logger.debug(f"Error extracting enhanced contact info: {e}")
        
        return contact
    
    def find_all_conversations(self, driver, platform: InboxPlatform) -> List:
        """Find conversation items using platform-specific selectors"""
        selectors = self.get_selectors(platform)["conversation_items"]
        
        for selector in selectors:
            try:
                items = driver.find_elements(By.CSS_SELECTOR, selector)
                visible_items = [item for item in items if item.is_displayed()]
                if visible_items:
                    logger.info(f"✅ Found {len(visible_items)} conversations ({platform.value}) using: {selector}")
                    return visible_items
            except Exception:
                continue
        
        logger.warning(f"❌ No conversations found for {platform.value}")
        return []

    def save_conversation_data(self, conversation_id: str, contact: Contact, 
                         conversation_history: List[Dict[str, str]], metrics: ConversationMetrics):
        """Save conversation data to local database"""
        # Convert metrics to dict, handling enums
        metrics_dict = asdict(metrics)
        metrics_dict['stage'] = metrics.stage.value if hasattr(metrics.stage, 'value') else str(metrics.stage)
        metrics_dict['intent'] = metrics.intent.value if hasattr(metrics.intent, 'value') else str(metrics.intent)
        
        conversation_data = {
            'conversation_id': conversation_id,
            'contact': asdict(contact),
            'conversation_history': conversation_history,
            'metrics': metrics_dict,
            'last_updated': datetime.now().isoformat(),
            'created_at': self.conversations.get(conversation_id, {}).get('created_at', datetime.now().isoformat())
        }
        
        self.conversations[conversation_id] = conversation_data
        self.save_json_db(self.conversations_db, self.conversations)

    def extract_conversation_details_from_driver(self, driver) -> Dict[str, Any]:
        """Extract conversation details directly from driver (Updated for Sales Nav)"""
        from selenium.webdriver.common.by import By
        conversation_details = {}
        
        try:
            # Wait briefly for header to settle
            time.sleep(1)
            
            # --- UPDATED SELECTORS FOR SALES NAV & STANDARD ---
            name_selectors = [
                # Sales Navigator Specific
                "[data-test-thread-participant-name]", 
                ".thread-header__title",
                "span.thread-list-item__title",
                ".artdeco-entity-lockup__title",
                
                # Standard LinkedIn
                "h2.msg-entity-lockup__entity-title",
                "a.msg-thread__link-to-profile",
                ".msg-overlay-conversation-bubble__participant-name",
                ".msg-conversation-container__participant-names h2"
            ]
            
            for selector in name_selectors:
                try:
                    # Try to find the element
                    if selector.startswith("//"):
                        name_elements = driver.find_elements(By.XPATH, selector)
                    else:
                        name_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    for el in name_elements:
                        if el.is_displayed():
                            name_text = el.text.strip()
                            if name_text and len(name_text) > 0:
                                conversation_details['participant_name'] = name_text
                                logger.info(f"✅ Extracted name: {name_text}")
                                break
                    if 'participant_name' in conversation_details:
                        break
                except:
                    continue
            
            # Fallback if name is still missing
            if 'participant_name' not in conversation_details:
                logger.warning("❌ Could not extract participant name")
                conversation_details['participant_name'] = "Unknown"
            
            # Extract headline (Optional but helpful)
            headline_selectors = [
                ".thread-header__subtitle",          # Sales Nav
                ".msg-entity-lockup__headline",      # Standard
                ".msg-thread__link-to-profile-subtitle"
            ]
            
            for selector in headline_selectors:
                try:
                    headline_element = driver.find_element(By.CSS_SELECTOR, selector)
                    headline_text = headline_element.text.strip()
                    if headline_text:
                        conversation_details['participant_headline'] = headline_text
                        break
                except:
                    continue
                    
        except Exception as e:
            logger.error(f"Error extracting conversation details: {e}")
        
        return conversation_details

    def process_inbox_enhanced(self, driver, user_name: str, max_replies: int = 20, 
                           session_id: str = None, client_instance=None, 
                           platform_str: str = "linkedin") -> Dict[str, Any]:
        """
        STRICT LOGIC:
        1. Check ALL messages (Read/Unread) and save latest message.
        2. Compare with DB:
        - NEW MESSAGE from THEM → Check Intent → Ask Approval (except MEETING_SCHEDULE)
        - NO NEW MESSAGE + I was last sender + 3+ days → Follow Up (max 3, then blacklist)
        """
        try:
            platform = InboxPlatform(platform_str)
        except ValueError:
            platform = InboxPlatform.LINKEDIN

        logger.info(f"🤖 Starting inbox processing for: {platform.value}")
        self.client = client_instance
        
        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())

        if session_id not in self.active_inbox_sessions:
            self.active_inbox_sessions[session_id] = {
                'status': 'running',
                'stop_requested': False,
                'user_action': None
            } 
        # Initialize session tracking
        if session_id not in self.active_inbox_sessions:
            self.active_inbox_sessions[session_id] = {
                'status': 'running',
                'awaiting_confirmation': False,
                'current_conversation': None,
                'user_action': None,
                'stop_requested': False,
            }

        # Load persistent state database
        db_file = f"{platform.value}_inbox_db.json"
        inbox_db = self.load_json_db(db_file, {})

        processed_count = 0
        scan_attempt = 0
        
        while True:
            # Check for stop request
            if self.active_inbox_sessions[session_id].get('stop_requested'):
                logger.info("🛑 Stop requested by user, halting inbox processing.")
                break

            scan_attempt += 1
            logger.info(f"\n--- Inbox Scan Attempt {scan_attempt} (Total Processed: {processed_count}) ---")

            # Navigate to messaging
            if not self.navigate_to_messaging_safe(driver,platform):
                logger.error("Failed to navigate to messaging. Retrying in 60s...")
                self._wait_with_stop_check(session_id, 60)
                continue

            # Get ALL conversations (not just unread)
            all_conversations = self.find_all_conversations(driver, platform)
            conversations_to_scan = all_conversations[:15]  # Top 15

            if not conversations_to_scan:
                logger.info("🏁 No conversations found. Waiting 60s.")
                self._wait_with_stop_check(session_id, 60)
                continue

            actions_taken = False

            for idx, conv_item in enumerate(conversations_to_scan):
                if self.active_inbox_sessions[session_id].get('stop_requested'):
                    break

                # Generate unique conversation ID
                conv_id = self._generate_conversation_id(conv_item, idx)
                if not conv_id:
                    continue

                # SKIP BLACKLISTED IMMEDIATELY
                if inbox_db.get(conv_id, {}).get('status') == 'blacklisted':
                    continue

                try:
                    # OPEN THE CONVERSATION
                    logger.info(f"🎯 Processing conversation: {conv_id[:20]}...")
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", 
                        conv_item
                    )
                    time.sleep(0.5)
                    
                    try:
                        conv_item.click()
                    except:
                        driver.execute_script("arguments[0].click();", conv_item)
                    
                    # Wait for conversation to load
                    if platform == InboxPlatform.SALES_NAVIGATOR:
                        WebDriverWait(driver, 15).until(
                            EC.any_of(
                                # Sales Nav Message List Container
                                EC.presence_of_element_located((By.CSS_SELECTOR, "ul.thread-history__list")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".thread-history")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, "textarea[name='message']")) # Fallback: Input box
                            )
                        )
                    else:
                        # Standard LinkedIn
                        WebDriverWait(driver, 10).until(
                            EC.any_of(
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".msg-s-message-list")),
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".msg-thread"))
                            )
                        )
                    time.sleep(1.5)

                    # EXTRACT CONVERSATION DATA
                    conversation_history = self.get_complete_conversation_history_from_driver(driver, platform, user_name)
                    if not conversation_history:
                        continue

                    last_msg_obj = conversation_history[-1]
                    current_msg_text = last_msg_obj.get('message', '').strip()
                    current_sender = last_msg_obj.get('sender', 'Unknown')
                    
                    # Extract contact info
                    details = self.extract_conversation_details_from_driver(driver)
                    contact = self.extract_contact_info_enhanced(driver, details)

                    # COMPARE WITH DATABASE
                    stored_data = inbox_db.get(conv_id, {})
                    stored_msg_text = stored_data.get('latest_message_text', '')
                    
                    # === CASE A: NEW MESSAGE DETECTED ===
                    if current_msg_text != stored_msg_text:
                        logger.info(f"📬 New message detected from: {current_sender}")
                        
                        # Update DB immediately
                        inbox_db[conv_id] = {
                            'latest_message_text': current_msg_text,
                            'last_message_date': datetime.now().isoformat(),
                            'last_sender': current_sender,
                            'follow_up_count': 0,  # Reset on new activity
                            'status': 'active',
                            'contact_name': contact.name
                        }
                        self.save_json_db(db_file, inbox_db)

                        # If I sent the last message, just update DB and move on
                        if current_sender == 'You' or current_sender == user_name:
                            logger.info("👉 Last message was from me. DB updated. Moving on.")
                            continue

                        # THEY sent the last message → Analyze Intent
                        intent = self.analyze_message_intent(current_msg_text)
                        logger.info(f"📊 Processing: {contact.name} | Intent: {intent.value}")

                        # Calculate metrics
                        metrics = ConversationMetrics(
                            intent=intent,
                            message_count=len(conversation_history),
                            last_interaction=datetime.now().isoformat()
                        )
                        metrics.lead_score = self.calculate_lead_score(contact, conversation_history, metrics)
                        metrics.stage = self.determine_conversation_stage(conversation_history, intent)

                        # STEP 2: CHECK INTENT
                        if intent in [MessageIntent.SCHEDULE_MEETING, MessageIntent.MEETING_CONFIRMATION]:
                            # NO APPROVAL NEEDED - Auto-handle meetings
                            logger.info("🔥 Intent is MEETING. Auto-handling without approval.")
                            reply = self.generate_smart_response(contact, conversation_history, metrics)
                            
                            if self.send_chat_message_enhanced(driver, reply, platform):
                                logger.info(f"✅ Auto-replied to {contact.name}")
                                inbox_db[conv_id]['latest_message_text'] = reply
                                inbox_db[conv_id]['last_sender'] = 'You'
                                inbox_db[conv_id]['last_message_date'] = datetime.now().isoformat()
                                self.save_json_db(db_file, inbox_db)
                                processed_count += 1
                                actions_taken = True
                        else:
                            # ALL OTHER INTENTS → ASK FOR APPROVAL
                            logger.info(f"✋ Intent is {intent.value}. Asking for approval.")
                            suggested_reply = self.generate_smart_response(contact, conversation_history, metrics)
                            
                            approved, final_message = self._ask_for_approval(
                                session_id, contact, current_msg_text, 
                                suggested_reply, intent.value, driver, platform,
                                conversation_history=conversation_history
                            )
                            
                            if approved and final_message:
                                inbox_db[conv_id]['latest_message_text'] = final_message
                                inbox_db[conv_id]['last_sender'] = 'You'
                                inbox_db[conv_id]['last_message_date'] = datetime.now().isoformat()
                                self.save_json_db(db_file, inbox_db)
                                processed_count += 1
                                actions_taken = True

                    # === CASE B: NO NEW MESSAGE → CHECK FOLLOW-UP ===
                    else:
                        last_sender_was_me = stored_data.get('last_sender') in ['You', user_name]
                        
                        if last_sender_was_me:
                            last_date_str = stored_data.get('last_message_date')
                            if not last_date_str:
                                continue
                            
                            last_date = datetime.fromisoformat(last_date_str)
                            days_diff = (datetime.now() - last_date).days
                            
                            # RULE: 3+ DAYS OLD
                            if days_diff >= 3:
                                current_fu_count = stored_data.get('follow_up_count', 0)
                                
                                # RULE: MAX 3 FOLLOW-UPS
                                if current_fu_count < 3:
                                    logger.info(f"⏰ {days_diff} days since last message to {contact.name}. Follow-up #{current_fu_count + 1}")
                                    
                                    fu_message = self.generate_followup_message(contact, current_fu_count + 1)
                                    
                                    # ASK FOR APPROVAL
                                    approved, final_message = self._ask_for_approval(
                                        session_id, contact, 
                                        f"[NO REPLY FOR {days_diff} DAYS]", 
                                        fu_message, "follow_up", driver, platform
                                    )
                                    
                                    if approved and final_message:
                                        inbox_db[conv_id]['latest_message_text'] = final_message
                                        inbox_db[conv_id]['last_message_date'] = datetime.now().isoformat()
                                        inbox_db[conv_id]['follow_up_count'] = current_fu_count + 1
                                        self.save_json_db(db_file, inbox_db)
                                        processed_count += 1
                                        actions_taken = True
                                else:
                                    # BLACKLIST AFTER 3 FOLLOW-UPS
                                    logger.info(f"💀 Max follow-ups reached for {contact.name}. Blacklisting.")
                                    inbox_db[conv_id]['status'] = 'blacklisted'
                                    self.save_json_db(db_file, inbox_db)

                except Exception as e:
                    logger.error(f"Error processing conversation {conv_id}: {e}")
                    continue

            # Save state after each cycle
            self.save_json_db(db_file, inbox_db)
            
            # Wait before next scan
            wait_time = 30 if actions_taken else 60
            logger.info(f"🏁 No new actionable conversations. Waiting {wait_time}s.")
            self._wait_with_stop_check(session_id, wait_time)

        # Return final results
        return {
            'success': True,
            'total_processed': processed_count,
            'session_id': session_id
        }
        
    def _ask_for_approval(self, session_id: str, contact, their_message: str, 
                      suggested_reply: str, intent: str, driver, platform,
                      conversation_history=None) -> tuple:
        """
        Send preview to dashboard and wait for user approval.
        Returns: (approved: bool, final_message: str or None)
        """
        logger.info(f"⏳ Awaiting user approval for {contact.name}...")
        
        # 1. Set up the preview state
        formatted_history = []
        if conversation_history:
            for msg in conversation_history:
                formatted_history.append({
                    'sender': msg.get('sender','Unknown'),
                    'text': msg.get('message',''),
                    'timestamp': msg.get('timestamp','')
                })
        
        self.active_inbox_sessions[session_id]['awaiting_confirmation'] = True
        self.active_inbox_sessions[session_id]['current_conversation'] = {
            'contact_name': contact.name,
            'contact_company': contact.company,
            'contact_title': contact.title,
            'their_message': their_message,
            'suggested_reply': suggested_reply,
            'intent': intent,
            'linkedin_url': contact.linkedin_url,
            'session_id': session_id # Ensure ID is passed for the UI
        }
        
        # 2. Report to dashboard so it appears in the UI
        self._report_inbox_preview_to_dashboard(session_id, 
            self.active_inbox_sessions[session_id]['current_conversation'])
        
        # 3. Wait Loop (Timeout 10 minutes)
        start_time = time.time()
        timeout = 600 
        
        logger.info(f"👉 GO TO DASHBOARD: Please approve/edit reply for {contact.name}")

        while time.time() - start_time < timeout:
            # Check if user stopped the task via dashboard
            if self.active_inbox_sessions[session_id].get('stop_requested'):
                logger.info("🛑 Stop requested during approval wait.")
                break
            
            # Check if client received an action task from the server
            user_action = self.active_inbox_sessions[session_id].get('user_action')
            
            if user_action:
                action = user_action.get('action')
                logger.info(f"👍 Received user action from dashboard: {action}")
                
                # Reset state
                self.active_inbox_sessions[session_id]['awaiting_confirmation'] = False
                self.active_inbox_sessions[session_id]['current_conversation'] = None
                self.active_inbox_sessions[session_id]['user_action'] = None
                
                if action == 'send':
                    # Use the message returned from dashboard (allows editing)
                    final_msg = user_action.get('message', suggested_reply)
                    
                    # Attempt to send
                    if self.send_chat_message_enhanced(driver, final_msg, platform):
                        logger.info(f"✅ Message sent to {contact.name}")
                        return (True, final_msg)
                    else:
                        logger.error(f"❌ Failed to send message to {contact.name}")
                        return (False, None)
                        
                elif action == 'skip':
                    logger.info(f"⏭️ Skipped {contact.name} by user choice.")
                    return (False, None)
                    
                elif action == 'blacklist':
                    logger.info(f"🚫 Blacklisting {contact.name} by user choice.")
                    # Caller logic handles the DB update for blacklisting
                    return (False, 'BLACKLIST')
            
            # Wait small amount before checking again
            # The Client logic polls the server every 15s, so we just wait here
            time.sleep(2) 
        
        # Timeout handler
        logger.info(f"⏰ Approval timeout for {contact.name}. Skipping.")
        self.active_inbox_sessions[session_id]['awaiting_confirmation'] = False
        self.active_inbox_sessions[session_id]['current_conversation'] = None
        return (False, None)
    
    def _wait_with_stop_check(self, session_id: str, seconds: int):
        """Wait for specified seconds, checking for stop request every second."""
        for _ in range(seconds):
            if self.active_inbox_sessions.get(session_id, {}).get('stop_requested'):
                return
            time.sleep(1)
    
    def generate_followup_message(self, contact, followup_number: int) -> str:
        """Generate follow-up messages based on count."""
        first_name = contact.name.split()[0] if contact.name else "there"
        
        if followup_number == 1:
            return (f"Hi {first_name}, just bumping this up in case it got buried. "
                    f"I'd love to hear your thoughts when you have a moment.")
        elif followup_number == 2:
            return (f"Hi {first_name}, I know things get busy. Is this something "
                    f"that's still relevant for {contact.company or 'you'}? "
                    f"If not, just let me know and I'll stop following up.")
        elif followup_number == 3:
            return (f"Hi {first_name}, this will be my last follow-up. I assume "
                    f"now isn't the right time. Feel free to reach out if your "
                    f"priorities change in the future. Best of luck!")
        
        return f"Hi {first_name}, following up on my previous message."
            
    def _generate_conversation_id(self, conv_item, idx):
        """Generate unique conversation ID"""
        from selenium.webdriver.common.by import By
        conv_id = None
        try:
            # Try multiple strategies to generate ID
            # Strategy 1: data-conversation-id attribute
            conv_id = conv_item.get_attribute('data-conversation-id')
            if conv_id:
                return conv_id

            # Strategy 2: Thread ID from URL
            try:
                links = conv_item.find_elements(By.TAG_NAME, 'a')
                for link in links:
                    href = link.get_attribute('href')
                    if href and '/messaging/thread/' in href:
                        thread_match = re.search(r'/messaging/thread/([^/?]+)', href)
                        if thread_match:
                            return f"thread_{thread_match.group(1)}"
            except:
                pass

            try:
                current_url = conv_item.find_element(By.TAG_NAME, "a").get_attribute("href")
                if "thread" in current_url:
                    # Extract ID from end of URL
                    import re
                    match = re.search(r'thread/([^/?]+)', current_url)
                    if match:
                        return f"sn_{match.group(1)}"
            except:
                pass

            # Strategy 3: Hash from content
            try:
                name_text = ""
                preview_text = ""
                
                for selector in [".msg-conversation-listitem__participant-names", ".msg-conversation-card__participant-names"]:
                    try:
                        name_elem = conv_item.find_element(By.CSS_SELECTOR, selector)
                        name_text = name_elem.text.strip()
                        if name_text:
                            break
                    except:
                        continue
                
                for selector in [".msg-conversation-listitem__message-preview", ".msg-conversation-card__message-preview"]:
                    try:
                        preview_elem = conv_item.find_element(By.CSS_SELECTOR, selector)
                        preview_text = preview_elem.text.strip()
                        if preview_text:
                            break
                    except:
                        continue
                
                if name_text or preview_text:
                    content = f"{name_text}_{preview_text}"[:100]
                    return f"content_{hashlib.md5(content.encode('utf-8')).hexdigest()}"
            except:
                pass

            # Fallback
            return f"fallback_{idx}_{int(time.time())}"
            
        except:
            return f"error_{idx}_{int(time.time())}"

    
    def _report_inbox_preview_to_dashboard(self, session_id: str, preview_data: Dict[str, Any]):
        """Helper method to report the preview to the server."""
        try:
            if not self.client or not hasattr(self.client, 'config'):
                logger.warning("No client instance available for preview reporting.")
                return
            
            dashboard_url = self.client.config.get('dashboard_url')
            if not dashboard_url:
                logger.warning("Dashboard URL not configured in client.")
                return

            endpoint = f"{dashboard_url.rstrip('/')}/api/inbox_preview"
            payload = {'session_id': session_id, 'preview': preview_data}
            headers = self.client._get_auth_headers()
            
            import requests
            response = requests.post(endpoint, json=payload, headers=headers, timeout=20)
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully reported inbox preview for session {session_id} to dashboard.")
            else:
                logger.warning(f"⚠️ Inbox preview report failed: {response.status_code} - {response.text[:200]}")
                
        except Exception as e:
            logger.error(f"Could not report inbox preview: {e}", exc_info=True)
    
    def handle_inbox_action(self, session_id: str, action_data: Dict[str, Any]):

        if session_id in self.active_inbox_sessions:
            logger.info(f"✅ Setting user action for session {session_id}: {action_data.get('action')}")
                # This is the crucial step that signals the waiting loop
            self.active_inbox_sessions[session_id]['user_action'] = action_data
        else:
            logger.warning(f"⚠️ Could not find active inbox session for ID: {session_id} to handle action.")
            logger.debug(f"Currently active sessions: {list(self.active_inbox_sessions.keys())}")

    def stop_inbox_session(self, session_id: str):
        """Stop an active inbox session"""
        if session_id in self.active_inbox_sessions:
            self.active_inbox_sessions[session_id]['stop_requested'] = True
            logger.info(f"🛑 Stop requested for inbox session {session_id}")
        
    def get_complete_conversation_history_from_driver(self, driver, platform: InboxPlatform, user_name: str = "You") -> List[Dict[str, str]]:
        """Extract messages using platform-specific selectors"""
        selectors = self.get_selectors(platform)
        conversation = []
        
        try:
            # 1. Get Message Containers
            message_containers = []
            for sel in selectors["message_containers"]:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    message_containers = elements
                    break

            if not message_containers:
                logger.warning("No message containers found.")
                return []

            # 2. Extract Data from Containers
            for container in message_containers:
                try:
                    # Parse Sender
                    sender = "Unknown"
                    for sender_sel in selectors["sender_name"]:
                        try:
                            el = container.find_element(By.CSS_SELECTOR, sender_sel)
                            sender = el.text.strip()
                            if sender: break
                        except: continue

                    if "you" in sender.lower() or user_name.lower() in sender.lower():
                        sender = "You"

                    # Parse Body
                    content = ""
                    for body_sel in selectors["message_body"]:
                        try:
                            el = container.find_element(By.CSS_SELECTOR, body_sel)
                            content = el.text.strip()
                            if content: break
                        except: continue
                    
                    if content:
                        conversation.append({"sender": sender, "message": content, "timestamp": ""})
                except:
                    continue
            
            return conversation
        except Exception as e:
            logger.error(f"Error extracting history: {e}")
            return []
    
    def get_conversation_at_index(self, driver, index):
        """Safely get a conversation element at a specific index"""
        try:
            conversations = self.find_all_conversations(driver)
            if index < len(conversations):
                return conversations[index]
        except Exception as e:
            logger.error(f"Could not get conversation at index {index}: {e}")
        return None
    

    def send_chat_message_enhanced(self, driver, message, platform: InboxPlatform):
        """Send message using platform-specific selectors"""
        selectors = self.get_selectors(platform)
        
        try:
            # 1. Find Input
            message_input = None
            for sel in selectors["input_box"]:
                try:
                    message_input = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                    break
                except: continue
            
            if not message_input:
                logger.error("Could not find message input box")
                return False

            # 2. Type Message
            message_input.click()
            time.sleep(0.5)
            message_input.clear()
            for char in message:
                message_input.send_keys(char)
                time.sleep(random.uniform(0.01, 0.05)) # Faster typing for Sales Nav usually works better
            
            time.sleep(1)

            # 3. Find Send Button
            send_button = None
            for sel in selectors["send_button"]:
                try:
                    send_button = driver.find_element(By.CSS_SELECTOR, sel)
                    if send_button.is_enabled():
                        break
                except: continue

            if send_button:
                send_button.click()
                time.sleep(2)
                return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
        
    def debug_conversations(self, driver) -> Dict[str, Any]:
        """
        Debug helper to understand what conversations exist and why they're being filtered.
        """
        from selenium.webdriver.common.by import By
        
        logger.info("🔍 DEBUG MODE: Analyzing all conversations...")
        
        if not self.navigate_to_messaging_safe(driver):
            return {"error": "Could not navigate to messaging"}
        
        time.sleep(3)
        
        all_conversations = self.find_all_conversations(driver)
        
        debug_info = {
            "total_found": len(all_conversations),
            "conversations": []
        }
        
        for idx, conv_item in enumerate(all_conversations[:10]):  # Check first 10
            try:
                conv_debug = {
                    "index": idx,
                    "visible": conv_item.is_displayed(),
                    "classes": conv_item.get_attribute('class') or 'N/A',
                    "aria_label": conv_item.get_attribute('aria-label') or 'N/A',
                    "id": conv_item.get_attribute('id') or 'N/A',
                    "unread_indicators": {},
                    "links": []
                }
                
                # Check for unread indicators
                conv_debug["unread_indicators"]["has_unread_class"] = 'unread' in conv_debug["classes"].lower()
                conv_debug["unread_indicators"]["has_unread_in_label"] = 'unread' in conv_debug["aria_label"].lower()
                
                # Check for unread badge
                try:
                    unread_badges = conv_item.find_elements(By.CSS_SELECTOR, 
                        '.msg-conversation-card__unread-count, [data-test-id="unread-indicator"], .artdeco-entity-lockup__badge')
                    conv_debug["unread_indicators"]["unread_badge_count"] = len(unread_badges)
                except:
                    conv_debug["unread_indicators"]["unread_badge_count"] = 0
                
                # Check for bold text (often indicates unread)
                try:
                    bold_elements = conv_item.find_elements(By.CSS_SELECTOR, 'strong, b, [class*="bold"]')
                    conv_debug["unread_indicators"]["has_bold_text"] = len(bold_elements) > 0
                except:
                    conv_debug["unread_indicators"]["has_bold_text"] = False
                
                # Get all links
                try:
                    links = conv_item.find_elements(By.TAG_NAME, 'a')
                    for link in links[:3]:  # First 3 links
                        href = link.get_attribute('href')
                        if href:
                            conv_debug["links"].append(href)
                except:
                    pass
                
                # Try to get participant name
                try:
                    name_selectors = [
                        ".msg-conversation-listitem__participant-names",
                        ".msg-conversation-card__participant-names",
                        ".artdeco-entity-lockup__title"
                    ]
                    for selector in name_selectors:
                        try:
                            name_elem = conv_item.find_element(By.CSS_SELECTOR, selector)
                            conv_debug["participant_name"] = name_elem.text.strip()
                            break
                        except:
                            continue
                except:
                    conv_debug["participant_name"] = "Could not extract"
                
                # Calculate if we think it's unread
                is_unread = (
                    conv_debug["unread_indicators"]["has_unread_class"] or
                    conv_debug["unread_indicators"]["has_unread_in_label"] or
                    conv_debug["unread_indicators"]["unread_badge_count"] > 0 or
                    conv_debug["unread_indicators"]["has_bold_text"]
                )
                conv_debug["appears_unread"] = is_unread
                
                debug_info["conversations"].append(conv_debug)
                
            except Exception as e:
                logger.error(f"Error debugging conversation #{idx}: {e}")
                debug_info["conversations"].append({
                    "index": idx,
                    "error": str(e)
                })
        
        logger.info(f"📊 SUMMARY:")
        logger.info(f"  Total conversations found: {debug_info['total_found']}")
        
        unread_count = sum(1 for c in debug_info["conversations"] if c.get("appears_unread", False))
        logger.info(f"  Conversations that appear unread: {unread_count}")
        
        try:
            with open("conversation_debug.json", "w", encoding="utf-8") as f:
                json.dump(debug_info, f, indent=2, ensure_ascii=False)
            logger.info(f"  Debug info saved to: conversation_debug.json")
        except Exception as e:
            logger.warning(f"Could not save debug info: {e}")
        
        return debug_info
    
    def handle_booking_confirmation(self, contact: Contact, conversation_history: List[Dict[str, str]]) -> str:
        if not self.client: return "My apologies, I'm having trouble accessing the calendar."

        logger.info(f"Handling booking confirmation for {contact.name}")
        
        # Get the last few messages for context
        history_text = "\n".join([f"{msg['sender']}: {msg['message']}" for msg in conversation_history[-3:]])
        
        # Get the very last message text to check for specific email provided now
        last_message_text = conversation_history[-1].get('message', '')

        # 1. Use AI to extract the date and time
        prompt = f"""
        Read the conversation and extract the specific date and time the user confirmed for a meeting.
        Today's date is: {datetime.now().strftime('%A, %B %d, %Y')}
        Conversation: {history_text}
        Return ONLY the start time in ISO 8601 format. If error, return "ERROR".
        """
        
        try:
            response = self.model.generate_content(prompt)
            start_time_str = response.text.strip()

            if "ERROR" in start_time_str or len(start_time_str) < 15:
                return "Thanks! Just to confirm, what is the full date and time that works for you?"

            # 2. Logic to find the Attendee Email
            attendee_email = None
            
            # A. Priority: Check if they provided an email in the text (Regex)
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            found_emails = re.findall(email_pattern, last_message_text)
            
            if found_emails:
                attendee_email = found_emails[0]
                logger.info(f"📧 Extracted email from message text: {attendee_email}")
            else:
                # B. Fallback: Check stored profile data
                attendee_email = contact.profile_data.get('email')
                logger.info(f"📧 Using profile email: {attendee_email}")

            # 3. Book the meeting
            start_dt = datetime.fromisoformat(start_time_str)
            end_dt = start_dt + timedelta(minutes=30)
            
            # Create a description so YOU know who this is
            description = f"LinkedIn Contact: {contact.name}\nProfile: {contact.linkedin_url}\n\nContext: Booked via AI Inbox."

            details = {
                "summary": f"Call with {contact.name} ({contact.company or 'LinkedIn'})",
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "attendee_email": attendee_email, # This will now pass the extracted email
                "description": description
            }
            
            booking_result = self.client.book_calendar_event(details)
            
            if booking_result.get('success'):
                meet_link = booking_result.get('meet_link', 'Link in invite')
                
                # Different response depending on if we found an email to invite
                if attendee_email:
                     return f"Great! I've sent a calendar invite to {attendee_email} for {start_dt.strftime('%A at %I:%M %p')}. Looking forward to it!"
                else:
                     # If no email found anywhere, confirm time and send link manually
                     return (
                        f"Perfect! I've booked that for {start_dt.strftime('%A, %B %d at %I:%M %p')}. "
                        f"Here is the Google Meet link for our chat: {meet_link}"
                     )
            else:
                logger.error(f"Booking failed: {booking_result.get('error')}")
                return "My apologies, I ran into an error trying to book that time."

        except Exception as e:
            logger.error(f"Error in handle_booking_confirmation: {e}")
            return "I'm having a system error. Could you please repeat the time?"

    def handle_email_sending(self, contact: Contact, conversation_history: List[Dict[str, str]]) -> str:
        """
        Uses AI to parse an email from the last message, then sends the proposal.
        """
        if not self.client: return "My apologies, I'm having trouble with my email system right now."

        logger.info(f"Handling email sending for {contact.name}")
        last_message = conversation_history[-1].get('message', '')

        # 1. Use AI to extract the email
        prompt = f"""
        Extract the email address from this message.
        Message: "{last_message}"
        Return ONLY the email address. If no email is found, return "ERROR".
        """
        
        try:
            response = self.model.generate_content(prompt)
            email = response.text.strip()

            if "ERROR" in email or "@" not in email:
                logger.warning(f"AI could not extract email: {email}")
                return "Thanks! I couldn't quite catch that. Could you please type out your email address again?"

            logger.info(f"AI extracted email: {email}")

            # 2. Get proposal template and send
            proposal_subject = f"Proposal for {contact.company}"
            proposal_body = self.templates.get('standard_proposal', {}).get('template', 
                "Hi {name},\n\nAs promised, here is some more information on our services..."
            ).format(name=contact.name.split()[0], company=contact.company)
            
            details = {
                "to_email": email,
                "subject": proposal_subject,
                "body": proposal_body
            }
            
            email_result = self.client.send_email(details)
            
            if email_result.get('success'):
                logger.info(f"Sent proposal to {email}")
                return f"Perfect, thank you. I've just sent the proposal to {email}. Please let me know if you have any questions!"
            else:
                logger.error(f"Email sending failed: {email_result.get('error')}")
                return f"My apologies, I ran into an error trying to send the email to {email}. Could you please confirm the address is correct?"

        except Exception as e:
            logger.error(f"Error in handle_email_sending: {e}")
            return "I'm having a system error. Could you please repeat your email address?"