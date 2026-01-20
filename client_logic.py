import json
import time
import threading
import csv
from datetime import datetime, date
from flask import Flask, request, jsonify
import requests
from linkedin_automation import LinkedInAutomation
import logging
import os
import uuid
from collections import defaultdict
from tkinter import ttk, messagebox, scrolledtext
import signal
import atexit
import random
import google.generativeai as genai
import re
from typing import List, Dict, Any
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional
# Import all functions from LinkedIn_automation_script.py
from urllib.parse import quote_plus
import tempfile
import platform
import shutil
from typing import List, Dict, Any
import sys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Import from your new modules
from ai_inbox import EnhancedAIInbox
from gui import create_config_gui, show_status_gui
from linkedin_automation import LinkedInAutomation 
# Note: You might need to adjust other internal method calls inside the class.
# The following is a simplified structure.

logger = logging.getLogger(__name__)

class EnhancedLinkedInAutomationClient:
    def __init__(self):
        self.config_file = "client_config.json"
        self.config = self.load_or_create_config()
        self.driver = None
        self.wait = None
        self.temp_profile_dir = None
        self.browser_lock = threading.Lock()
        
        # Exit if config creation was cancelled
        if self.config is None:
            logger.error("❌ Configuration setup was cancelled or failed.")
            sys.exit(1)

        # KEY CHANGE: Ensure a unique client_id exists and save it if new
        config_updated = False
        if 'client_id' not in self.config or not self.config['client_id']:
            self.config['client_id'] = str(uuid.uuid4())
            config_updated = True
            logger.info(f"✨ Generated new unique client ID: {self.config['client_id']}")

        if config_updated:
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=2)
                logger.info("✅ Saved new client ID to configuration file.")
            except Exception as e:
                logger.error(f"⚠️ Could not save new client ID to config: {e}")

        self.email = self.config.get('linkedin_email')
        self.password = self.config.get('linkedin_password')
        
        # Initialize Gemini AI first
        try:
            gemini_api_key = self.config.get('gemini_api_key')
            if not gemini_api_key:
                logger.error("❌ No Gemini API key found in configuration")
                self.model = None
            else:
                genai.configure(api_key=gemini_api_key)
                self.model = genai.GenerativeModel('gemini-2.5-flash-lite')
                logger.info("✅ Gemini AI initialized successfully")
        except Exception as e:
            logger.error(f"❌ Gemini AI initialization failed: {e}")
            self.model = None

        # Now, initialize EnhancedAIInbox with the created model
        self.enhanced_inbox = EnhancedAIInbox(gemini_model=self.model, client_instance=self)
        
        self.automation_instances = {}
        self.active_campaigns = defaultdict(lambda: {
            'user_action': None, 
            'awaiting_confirmation': False,
            'current_contact': None,
            'status': 'idle'
        })
        self.running = False
        self.active_searches = defaultdict(lambda: {
            "status": "idle", # idle | running | completed | failed
            "keywords": "",
            "max_invites": 0,
            "invites_sent": 0,
            "progress": 0,
            "stop_requested": False,
            "start_time": None,
            "end_time": None,
            "driver_errors": 0
        })

        poll_interval=int(self.config.get('poll_interval_seconds',15))
        try:
            self.start_polling(poll_interval_seconds=poll_interval)
        except Exception as e:
            logger.error(f"❌ Error starting polling: {e}")

    def load_or_create_config(self):
        """Load existing config or create new one via GUI"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                logger.info("✅ Configuration loaded successfully")
                return config
            except Exception as e:
                logger.error(f"❌ Error loading config: {e}")
        
        logger.info("📋 No configuration found, launching setup GUI...")
        return create_config_gui(self)

    def _get_auth_headers(self):
        """Return authorization headers for dashboard requests"""
        api_key = self.config.get('gemini_api_key') or self.config.get('client_api_key')
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    # ... (rest of the methods remain the same) ...
    def start_polling(self, poll_interval_seconds: int = 15):
        """Start polling the dashboard for tasks with heartbeat."""
        if getattr(self, "_polling_thread", None) and self._polling_thread.is_alive():
            logger.info("🔁 Polling already running.")
            return

        self._stop_polling = False
        self._poll_interval = max(5, int(poll_interval_seconds))
        
        # Start heartbeat
        self.start_heartbeat(interval_seconds=60)
        
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()
        logger.info(f"🔁 Started polling loop (interval {self._poll_interval}s).")


    def stop_polling(self):
        """Stop the polling loop and heartbeat."""
        self._stop_polling = True
        self.stop_heartbeat()
        
        if getattr(self, "_polling_thread", None):
            self._polling_thread.join(timeout=5)
        logger.info("🔁 Polling stopped.")

    def _polling_loop(self):
        """Enhanced polling loop with exponential backoff and jitter."""
        backoff_attempts = 0
        consecutive_failures = 0
        
        while not getattr(self, "_stop_polling", False):
            try:
                tasks = self.poll_once()
                
                # Reset failure counters on success
                backoff_attempts = 0
                consecutive_failures = 0
                
                if tasks:
                    logger.info(f"📥 Received {len(tasks)} tasks")
                    for task in tasks:
                        try:
                            self.handle_task(task)
                        except Exception as e:
                            logger.error(f"❌ Error handling task {task.get('id', 'unknown')}: {e}")
                            self.report_task_failure(task, str(e))

                time.sleep(self._poll_interval)
                
            except Exception as e:
                consecutive_failures += 1
                backoff_attempts += 1
                
                # Exponential backoff with jitter
                wait = min(300, (2 ** backoff_attempts) + random.random() * 3)
                
                if consecutive_failures <= 3:
                    logger.warning(f"⚠️ Polling error (attempt {consecutive_failures}): {e}")
                else:
                    logger.error(f"❌ Persistent polling error (attempt {consecutive_failures}): {e}")
                    
                logger.info(f"⏳ Backing off for {wait:.1f}s")
                time.sleep(wait)
                
                # Reset backoff if too many failures
                if consecutive_failures >= 10:
                    logger.warning("🔄 Too many consecutive failures, resetting backoff")
                    backoff_attempts = 0
            
    def report_task_failure(self, task, error_message):
        """Report task failure back to dashboard"""
        try:
            task_id = task.get('id', str(uuid.uuid4()))
            result = {
                "task_id": task_id,
                "type": task.get('type', 'unknown'),
                "success": False,
                "error": error_message,
                "timestamp": datetime.now().isoformat()
            }
            self.report_task_result(result)
        except Exception as e:
            logger.error(f"Failed to report task failure: {e}")

    def poll_once(self):
        """Request tasks from the dashboard. Returns list of tasks or empty list."""
        SERVER_BASE = self.config.get('dashboard_url') or "https://your-render-app.onrender.com"
        endpoint = f"{SERVER_BASE.rstrip('/')}/api/get-tasks"
        api_key = self.config.get('client_api_key') or self.config.get('gemini_api_key')

        if not api_key:
            logger.warning("No client API key configured; skipping poll.")
            return []

        payload = {
            'client_id': self.config.get('client_id') or self.config.get('instance_id') or str(uuid.uuid4()),
            'client_info': {
                'platform': platform.system(),
                'app_version': self.config.get('version', 'unknown')
            }
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            resp = requests.post(endpoint, json=payload, headers=self._get_auth_headers(), timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                tasks = data.get('tasks') or []
                logger.info(f"📥 Polled {len(tasks)} tasks from server.")
                return tasks
            elif resp.status_code == 204:
                return []
            else:
                logger.warning(f"Poll returned {resp.status_code}: {resp.text[:200]}")
                return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Poll request failed: {e}")
            return []
        
    def start_heartbeat(self, interval_seconds=60):
        """Start heartbeat to keep connection alive with dashboard"""
        if getattr(self, "_heartbeat_thread", None) and self._heartbeat_thread.is_alive():
            return
        
        self._stop_heartbeat = False
        self._heartbeat_interval = max(30, int(interval_seconds))
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        logger.info(f"💓 Started heartbeat (interval {self._heartbeat_interval}s)")

    def stop_heartbeat(self):
        """Stop the heartbeat"""
        self._stop_heartbeat = True
        if getattr(self, "_heartbeat_thread", None):
            self._heartbeat_thread.join(timeout=5)
        logger.info("💓 Heartbeat stopped")

    def _heartbeat_loop(self):
        """Send periodic ping to dashboard to show we're alive"""
        while not getattr(self, "_stop_heartbeat", False):
            try:
                self.send_heartbeat_ping()
                time.sleep(self._heartbeat_interval)
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                time.sleep(self._heartbeat_interval)

    def send_heartbeat_ping(self):
        """Send ping to dashboard and process returned actions."""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            if not SERVER_BASE:
                return
                
            endpoint = f"{SERVER_BASE.rstrip('/')}/api/client-ping"
            
            # ... (all the payload creation logic is fine) ...
            active_inbox_sessions = []
            if hasattr(self, 'enhanced_inbox') and self.enhanced_inbox:
                for session_id, session_data in self.enhanced_inbox.active_inbox_sessions.items():
                    if session_data.get('awaiting_confirmation'):
                        active_inbox_sessions.append({
                            'session_id': session_id,
                            'conversation': session_data.get('current_conversation')
                        })
            
            payload = {
                'client_id': self.config.get('client_id', str(uuid.uuid4())),
                'status': 'active',
                'timestamp': datetime.now().isoformat(),
                'client_info': {
                    'platform': platform.system(),
                    'version': '1.0'
                },
                'active_inbox_sessions': active_inbox_sessions
            }
            
            headers = self._get_auth_headers()
            
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            
            # --- THIS IS THE FIX ---
            # We no longer need to check for actions here.
            if resp.status_code in (200, 201):
                logger.debug("💓 Heartbeat ping successful")
            else:
                logger.warning(f"💓 Heartbeat ping returned {resp.status_code}: {resp.text[:200]}")
                
        except Exception as e:
            logger.debug(f"💓 Heartbeat ping failed: {e}")
        
    def report_task_started(self, task_id, task_type):
        """Report that a task has started"""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            if not SERVER_BASE:
                return
                
            endpoint = f"{SERVER_BASE.rstrip('/')}/api/task-status"
            api_key = self.config.get('client_api_key') or self.config.get('gemini_api_key')
            
            payload = {
                'task_id': task_id,
                'status': 'started',
                'task_type': task_type,
                'timestamp': datetime.now().isoformat()
            }
            
            headers = {
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "Content-Type": "application/json"
            }
            
            requests.post(endpoint, json=payload, headers=self._get_auth_headers(), timeout=10)
            logger.debug(f"📤 Reported task {task_id[:8]}... started")
            
        except Exception as e:
            logger.debug(f"Failed to report task started: {e}")

    def is_browser_alive(self):
        """Check if the Selenium WebDriver session is still active."""
        if not self.driver:
            return False
        try:
            # A lightweight way to check if the browser is still responsive
            _ = self.driver.window_handles
            return True
        except Exception:
            # This will catch errors if the browser has been closed
            return False

    def get_shared_driver(self):
        """
        Gets the shared browser instance. Creates a new one if it doesn't exist,
        if the user closed the window, or if the session is invalid.
        """
        if not self.is_browser_alive():
            logger.info("Browser not found or was closed. Initializing a new shared session...")
            
            # Ensure any old driver is fully closed
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass

            self.driver = self.initialize_browser() 
            if self.driver:
                self.wait = WebDriverWait(self.driver, 15)
                # Attempt to log in only when creating a new browser
                if self.login(): # Use the client's login method
                    self.user_name = self.get_user_profile_name(self.driver)
                else:
                    logger.error("❌ Failed to log in with the new browser instance. Cannot proceed.")
                    self.driver.quit()
                    self.driver = None
                    return None
            else:
                logger.error("❌ Failed to initialize the browser.")
                return None
        
        logger.info("✅ Re-using existing active browser session.")
        return self.driver

    def handle_task(self, task: dict):
        """Execute a single task with enhanced error handling and progress reporting."""
        task_id = task.get('id') or str(uuid.uuid4())
        ttype = task.get('type','').strip()
        params = task.get('params', {})
        
        logger.info(f"🧩 Starting task {task_id[:8]}... type={ttype}")
        
        result = {
            "task_id": task_id,
            "type": ttype,
            "success": False,
            "error": None,
            "payload": None,
            "start_time": datetime.now().isoformat()
        }
        
        try:
            self.report_task_started(task_id, ttype)
            
            driver = None
            if ttype in ("process_inbox", "send_message", "collect_profiles", "keyword_search", "outreach_campaign", "sync_network_stats"):
                driver = self.get_shared_driver()
                if not driver:
                    raise Exception("Failed to get valid browser session")

            # ENHANCED INBOX PROCESSING WITH PREVIEW
            if ttype == "process_inbox":
                process_id = params.get('process_id', task_id)

                # Start the inbox processing in a new thread to avoid blocking
                threading.Thread(
                    target=self.execute_inbox_task, 
                    args=(process_id,), 
                    daemon=True
                ).start()
                
                result['success'] = True
                result['payload'] = {'message': 'Inbox processing task has been started.', 'process_id': process_id}
                    
        # ENHANCED INBOX ACTION HANDLING - EXACTLY LIKE OUTREACH
            elif ttype == "process_inbox":
                process_id = params.get('process_id', task_id)
                threading.Thread(
                    target=self.execute_inbox_task, 
                    args=(process_id, "linkedin"), # Pass platform
                    daemon=True
                ).start()
                result['success'] = True
                result['payload'] = {'message': 'LinkedIn Inbox processing started.'}
                
            elif ttype == "process_sales_nav_inbox":
                process_id = params.get('process_id', task_id)
                threading.Thread(
                    target=self.execute_inbox_task, 
                    args=(process_id, "sales_navigator"), # Pass platform
                    daemon=True
                ).start()
                result['success'] = True
                result['payload'] = {'message': 'Sales Navigator Inbox processing started.'}
                
            elif ttype == 'inbox_action':
                params = task.get('params', {})
                session_id = params.get('session_id')
                action = params.get('action')

                logger.info(f"📥 Received inbox user action: '{action}' for session {session_id}")
                
                if session_id and self.enhanced_inbox:
                    # This call passes the user's decision to the waiting inbox process.
                    # The handle_inbox_action method will set a flag that the waiting loop can see.
                    self.enhanced_inbox.handle_inbox_action(session_id, params)
                    
                    result['success'] = True
                    result['payload'] = {'message': f"User action '{action}' for session '{session_id}' has been processed."}
                else:
                    result['error'] = 'Invalid session_id or inbox handler not initialized'
            # --- End of replacement ---
            elif ttype == 'stop_inbox_session':
                # Handle stop request for inbox session
                params = task.get('params', {})
                session_id = params.get('session_id')
                
                if session_id and self.enhanced_inbox:
                    self.enhanced_inbox.stop_inbox_session(session_id)
                    result['success'] = True
                    result['payload'] = {'message': f'Stop request sent to session {session_id}'}
                else:
                    result['error'] = 'Invalid session_id'

            elif ttype == 'stop_task':
                params = task.get('params', {})
                # Look for the new param, but fall back to the old one
                task_to_stop = params.get('task_to_stop') or params.get('task_id') 
                logger.info(f"🛑 Received STOP request for task: {task_to_stop}")

                if not task_to_stop:
                    raise Exception("No task_to_stop or task_id provided in stop_task action")

                # Check and stop active campaigns
                if task_to_stop in self.active_campaigns:
                    self.active_campaigns[task_to_stop]['stop_requested'] = True
                    logger.info(f"Set stop_requested flag for campaign {task_to_stop}")

                # Check and stop active inbox sessions
                elif self.enhanced_inbox and task_to_stop in self.enhanced_inbox.active_inbox_sessions:
                    self.enhanced_inbox.stop_inbox_session(task_to_stop)
                    logger.info(f"Called stop_inbox_session for {task_to_stop}")
                
                # Check and stop active keyword searches
                elif task_to_stop in self.active_searches:
                    self.active_searches[task_to_stop]['stop_requested'] = True
                    logger.info(f"Set stop_requested flag for search {task_to_stop}")
                
                else:
                    logger.warning(f"Could not find active task {task_to_stop} to stop. It might have already completed.")

                result['success'] = True
                result['payload'] = {'message': f"Stop request for {task_to_stop} processed."}
            
            elif ttype == "outreach_campaign" or ttype == "start_campaign":
                campaign_id = params.get('campaign_id', task_id)
                user_config = params.get('user_config', {})
                campaign_data = params.get('campaign_data', {})
                threading.Thread(target=self.execute_outreach_task, args=(campaign_id, user_config, campaign_data), daemon=True).start()
                result['success'] = True
                result['payload'] = {'message': 'Outreach campaign started', 'campaign_id': campaign_id}

            
            elif ttype == 'campaign_action':
                params = task.get('params', {})
                campaign_id = params.get('campaign_id')
                if campaign_id:
                    action = {
                        'action': params.get('action'),
                        'message': params.get('message'),
                        'contact_index': params.get('contact_index'),
                        'received_at': datetime.now().isoformat()
                    }
        # Ensure the campaign entry exists
                if campaign_id not in self.active_campaigns:
                    self.active_campaigns[campaign_id] = {
                        'awaiting_confirmation': False,
                        'user_action': None,
                        'current_contact_preview': None,
                        'status': 'unknown'
                    }
                self.active_campaigns[campaign_id]['user_action'] = action
                logger.info(f"📥 Applied campaign_action for {campaign_id}: {action['action']}")
                return {'success': True}              
            # --- THIS BLOCK IS NOW FIXED ---
            elif ttype == "keyword_search":
                # The main task_id IS the search_id for reporting
                # We pass the original task_id to the execution thread
                search_params = params.get('search_params', {})
                threading.Thread(target=self.execute_keyword_search_task, args=(task_id, search_params), daemon=True).start()
                
                result['success'] = True
                result['payload'] = {'message': 'Keyword search started', 'search_id': task_id}
            # --- END OF FIX ---
            elif ttype == "sync_network_stats":
                threading.Thread(target=self.execute_sync_network_stats_task, args=(task_id,), daemon=True).start()
                result['success'] = True
                result['payload'] = {'message': 'Network stats sync started', 'task_id': task_id}    
            
            elif ttype == 'process_non_responders':
                campaign_id = params.get('campaign_id')
                # Run in thread
                threading.Thread(target=self.process_non_responders, args=(campaign_id,), daemon=True).start()
                result['success'] = True
                result['payload'] = {'message': 'Started processing non-responders'}
            
            elif ttype == "process_sales_nav_inbox":
                process_id = params.get('process_id', task_id)
                threading.Thread(
                    target=self.execute_inbox_task, 
                    args=(process_id, "sales_navigator"), # Pass platform
                    daemon=True
                ).start()
                result['success'] = True
                result['payload'] = {'message': 'Sales Navigator Inbox processing started.'}
            
            elif ttype == "fetch_sales_nav_lists":
                threading.Thread(target=self.fetch_sales_nav_lists, args=(task_id,), daemon=True).start()
                result['success'] = True
                result['payload'] = {'message': 'Fetching Sales Nav lists...'}

            elif ttype == "sales_nav_outreach_campaign":
                campaign_id = params.get('campaign_id', task_id)
                user_config = params.get('user_config', {})
                campaign_params = params.get('campaign_params', {})
                threading.Thread(target=self.run_sales_nav_outreach_campaign, args=(campaign_id, user_config, campaign_params), daemon=True).start()
                result['success'] = True
                result['payload'] = {'message': 'Sales Nav campaign started', 'campaign_id': campaign_id}
            
            else:
                raise Exception(f"Unknown task type: {ttype}")
            
            logger.info(f"✅ Task {task_id[:8]}... logic completed for type {ttype}")
        
        except Exception as e:
            logger.exception(f"❌ Task {task_id[:8]}... failed: {e}")
            result['error'] = str(e)
            result['success'] = False
        
        finally:
            result['end_time'] = datetime.now().isoformat()
            # For threaded tasks, we report success immediately, the thread reports final status
            if ttype not in ["outreach_campaign", "start_campaign", "collect_profiles", "keyword_search", "campaign_action","sync_network_stats"]:
                try:
                    self.report_task_result(result)
                except Exception as report_e:
                    logger.error(f"Failed to report task result: {report_e}")
    
    def handle_inbox_action(self, session_id: str, action_data: Dict[str, Any]):
        """Handle inbox action from dashboard"""
        try:
            if not session_id:
                return {"success": False, "error": "No session_id provided"}
            
            logger.info(f"📥 Processing inbox action for session {session_id}: {action_data.get('action')}")
            
            # Pass the action directly to the enhanced inbox
            if hasattr(self, 'enhanced_inbox') and self.enhanced_inbox:
                self.enhanced_inbox.handle_inbox_action(session_id, action_data)
                return {"success": True, "message": "Action processed"}
            else:
                return {"success": False, "error": "Enhanced inbox not initialized"}
                
        except Exception as e:
            logger.error(f"Error handling inbox action: {e}")
            return {"success": False, "error": str(e)}

    def report_task_result(self, result):
        """Report task result back to dashboard"""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            report_url = f"{SERVER_BASE.rstrip('/')}/api/report-task"
            resp = requests.post(report_url, json=result, headers=self._get_auth_headers(), timeout=15)
            if resp.status_code == 200:
                logger.info(f"✅ Reported result for task {result.get('task_id')}")
            else:
                logger.warning(f"⚠️ Dashboard progress report returned status {resp.status_code}")
        except Exception as e:
            logger.error(f"Failed to report task result: {e}")

    def get_total_connection_count(self, driver) -> Optional[int]:
        """Scrape total LinkedIn connections count from user's profile page (stable 2025 method)."""
        try:
            logger.info("Syncing network stats: Navigating to user profile page...")

            # Step 1: Navigate to the user's profile
            driver.get("https://www.linkedin.com/in/")
            time.sleep(3)

            # Step 2: Wait for the connection count element
            selector = "span.link-without-visited-state span.t-bold"
            count_element = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )

            count_text = count_element.text.strip()
            logger.info(f"Found connection count text: '{count_text}'")

            # Step 3: Parse and clean numeric value
            count_digits = re.sub(r"[^\d]", "", count_text)
            if not count_digits:
                logger.warning("Could not parse numeric value from connections text.")
                return None

            count = int(count_digits)
            if "+" in count_text and count == 500:
                logger.info("Detected '500+' display, reporting 501 as proxy.")
                count = 501

            logger.info(f"✅ Successfully extracted total connections: {count}")
            return count

        except TimeoutException:
            logger.error("Timed out waiting for the connections count on profile page.")
            return None
        except Exception as e:
            logger.error(f"Error scraping connection count from profile page: {e}", exc_info=True)
            return None
   
    # --- NEW FUNCTION: Wrapper for the sync task ---
    def execute_sync_network_stats_task(self, task_id):
        """Thread-safe wrapper to execute the network stats sync task."""
        self.browser_lock.acquire()
        payload = {}
        error = None
        success = False
        try:
            logger.info(f"🔑 Browser lock acquired for network sync task {task_id}")
            driver = self.get_shared_driver()
            if not driver:
                raise Exception("Failed to get a valid browser session for network sync.")
            
            connection_count = self.get_total_connection_count(driver)
            
            if connection_count is not None:
                payload = {'total_connections': connection_count}
                success = True
                logger.info(f"✅ Successfully synced network stats. Total connections: {connection_count}")
            else:
                error = "Failed to scrape connection count."
                success = False

        except Exception as e:
            logger.error(f"❌ A critical error occurred in network sync task {task_id}: {e}", exc_info=True)
            error = str(e)
            success = False
        finally:
            logger.info(f"🔑 Browser lock released for network sync task {task_id}")
            self.browser_lock.release()
            
            # Report the final result to the server
            self.report_task_result({
                'task_id': task_id,
                'type': 'sync_network_stats',
                'success': success,
                'payload': payload,
                'error': error
            })
    def report_collection_results_to_dashboard(self, collection_id, results, final=False):
        """Report profile collection results back to dashboard."""
        try:
            dashboard_url = self.config.get('dashboard_url')
            if not dashboard_url:
                return

            endpoint = f"{dashboard_url}/api/collection_results"
            
            payload = {
                'collection_id': collection_id,
                'results': results
            }
            if final:
                payload['final'] = True

            response = requests.post(endpoint, json=payload, timeout=45, verify=True)
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully reported collection results for {collection_id}")
            else:
                logger.warning(f"⚠️ Dashboard collection report returned status {response.status_code}")

        except Exception as e:
            logger.error(f"Could not report collection results for {collection_id}: {e}")
    
    def report_search_results_to_dashboard(self, search_id, results):
        """Report search results back to dashboard with better error handling"""
        try:
            dashboard_url = self.config.get('dashboard_url')
            if not dashboard_url:
                return

            endpoint = f"{dashboard_url}/api/search_results"
            
            response = requests.post(endpoint, json={
                'search_id': search_id,
                'results': results
            }, timeout=30, verify=True)
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully reported search results for {search_id}")
            else:
                logger.warning(f"⚠️ Dashboard search report returned status {response.status_code}")

        except Exception as e:
            logger.debug(f"Could not report search results for {search_id}: {e}")

    def report_inbox_results_to_dashboard(self, process_id, results):
        """Report inbox processing results back to dashboard - FIXED VERSION"""
        try:
            dashboard_url = self.config.get('dashboard_url')
            if not dashboard_url:
                logger.debug("No dashboard URL configured")
                return

            endpoint = f"{dashboard_url}/api/inbox_results"
            
            # CRITICAL FIX: Ensure results are JSON serializable
            def make_serializable(obj):
                """Recursively make object JSON serializable"""
                if isinstance(obj, dict):
                    return {k: make_serializable(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [make_serializable(item) for item in obj]
                elif isinstance(obj, (datetime, date)):
                    return obj.isoformat()
                elif hasattr(obj, '__dict__'):
                    return make_serializable(obj.__dict__)
                elif isinstance(obj, Enum):
                    return obj.value
                elif hasattr(obj, 'value'):  # Additional check for enum-like objects
                    return obj.value
                else:
                    try:
                        json.dumps(obj)  # Test if it's already serializable
                        return obj
                    except (TypeError, ValueError):
                        return str(obj)
            
            # Convert results to serializable format
            serializable_results = make_serializable(results)
            
            # Add process metadata
            payload = {
                'process_id': process_id,
                'results': serializable_results,
                'timestamp': datetime.now().isoformat(),
                'client_id': self.config.get('client_id', str(uuid.uuid4()))
            }
            
            # Log what we're sending (truncated for debugging)
            logger.debug(f"Sending inbox results payload with {len(serializable_results.get('processed', []))} conversations")
            
            response = requests.post(
                endpoint, 
                json=payload,
                headers=self._get_auth_headers(), 
                timeout=30, 
                verify=True
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully reported inbox results for {process_id}")
                logger.info(f"  - Total processed: {serializable_results.get('total_processed', 0)}")
                logger.info(f"  - Auto-replied: {serializable_results.get('auto_replied', 0)}")
                logger.info(f"  - High priority: {serializable_results.get('high_priority', 0)}")
            else:
                logger.warning(f"⚠️ Dashboard inbox report returned status {response.status_code}")
                logger.debug(f"Response: {response.text[:500]}")
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON serialization error: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Could not report inbox results for {process_id}: {e}", exc_info=True)
    # ==============================================
    # ENHANCED LINKEDIN AUTOMATION FUNCTIONS
    # ==============================================

    def initialize_browser(self):
        """Initialize Chrome browser with PERSISTENT profile for session persistence"""
        from selenium import webdriver
        import os
        
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--start-maximized")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Use PERSISTENT profile directory instead of temporary
            app_data_dir = os.path.join(os.path.expanduser("~"), ".linkedin_automation")
            os.makedirs(app_data_dir, exist_ok=True)
            
            profile_dir = os.path.join(app_data_dir, "chrome_profile")
            
            # Create profile directory if it doesn't exist
            os.makedirs(profile_dir, exist_ok=True)
            
            options.add_argument(f"--user-data-dir={profile_dir}")
            
            # Store profile path for reference (don't set temp_profile_dir since it's persistent)
            self.persistent_profile_dir = profile_dir
            
            logger.info(f"🔧 Using persistent Chrome profile: {profile_dir}")
            
            # Additional options for better session persistence
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-extensions-except")
            options.add_argument("--disable-plugins-discovery")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            
            driver = webdriver.Chrome(options=options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("✅ Browser initialized with persistent profile")
            return driver
            
        except Exception as e:
            logger.error(f"❌ Browser initialization failed: {e}")
            raise

    def human_delay(self, min_seconds=1, max_seconds=3):
        """Add human-like delays"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    def type_like_human(self, element, text):
        """Type text with human-like delays"""
        element.clear()
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))

    def login(self):
        """Enhanced login with better session persistence detection"""
        try:
            logger.info("🔐 Checking LinkedIn session...")
            
            # First, try to go to LinkedIn feed to check for existing session
            self.driver.get("https://www.linkedin.com/feed")
            time.sleep(3)
            
            # Check if already logged in
            if self._is_logged_in():
                logger.info("✅ Found existing session - already logged in!")
                return True
            
            # If not logged in, try LinkedIn homepage first
            logger.info("🔄 No active session found, checking login page...")
            self.driver.get("https://www.linkedin.com")
            time.sleep(2)
            
            # Check again after going to homepage (sometimes redirects if logged in)
            if self._is_logged_in():
                logger.info("✅ Session restored from homepage redirect!")
                return True
            
            # Navigate to login page
            logger.info("🔑 Navigating to login page...")
            self.driver.get("https://www.linkedin.com/login")
            
            try:
                self.wait.until(EC.presence_of_element_located((By.ID, "username")))
            except TimeoutException:
                logger.error("❌ Login page did not load properly")
                return False
            
            self.human_delay(1.5, 3)
            
            # Type email
            username_field = self.driver.find_element(By.ID, "username")
            logger.info("✏️ Typing email...")
            self.type_like_human(username_field, self.email)
            
            self.human_delay(1, 2)
            
            # Type password
            password_field = self.driver.find_element(By.ID, "password")
            logger.info("✏️ Typing password...")
            self.type_like_human(password_field, self.password)
            
            # Click Login
            login_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            self.safe_click(self.driver, login_button)
            
            # Wait for login success with longer timeout for 2FA
            try:
                WebDriverWait(self.driver, 45).until(lambda d: self._is_logged_in())
                logger.info("✅ LinkedIn login successful! Session will be saved for next time.")
                self.human_delay(2, 4)
                return True
                
            except TimeoutException:
                current_url = self.driver.current_url
                if "checkpoint" in current_url or "challenge" in current_url:
                    logger.warning("⚠️ 2FA/Security challenge detected")
                    logger.info("⏳ Please complete the security challenge manually...")
                    logger.info("✋ Waiting up to 5 minutes for manual completion...")
                    
                    # Extended wait for manual 2FA completion
                    for i in range(300):  # 5 minutes
                        time.sleep(1)
                        if self._is_logged_in():
                            logger.info("✅ Security challenge completed successfully!")
                            logger.info("💾 Session saved - no login required next time!")
                            return True
                        
                        # Show progress every 30 seconds
                        if i % 30 == 0 and i > 0:
                            logger.info(f"⏳ Still waiting... ({i//60}m {i%60}s elapsed)")
                    
                    logger.error("❌ Security challenge timeout")
                    return False
                
                logger.error("❌ Login failed or timed out")
                return False
                
        except Exception as e:
            logger.error(f"❌ Login exception: {e}")
            return False

    def _is_logged_in(self):
        """Enhanced login status check with more indicators"""
        try:
            current_url = self.driver.current_url
            
            # Check URL patterns that indicate successful login
            logged_in_patterns = [
                "linkedin.com/feed",
                "linkedin.com/in/",
                "linkedin.com/mynetwork",
                "linkedin.com/jobs", 
                "linkedin.com/messaging",
                "linkedin.com/notifications"
            ]
            
            if any(pattern in current_url for pattern in logged_in_patterns):
                return True
            
            # Check for navigation elements
            nav_selectors = [
                "[data-test-id='global-nav']",
                ".global-nav",
                ".global-nav__nav",
                "nav.global-nav",
                ".global-nav__primary-items"
            ]
            
            for selector in nav_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and len(elements) > 0:
                        return True
                except:
                    continue
            
            # Check for profile elements
            profile_selectors = [
                ".global-nav__primary-item--profile",
                ".global-nav__me-photo", 
                "[data-test-id='nav-profile-photo']",
                "button[aria-label*='View profile']"
            ]
            
            for selector in profile_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and len(elements) > 0:
                        return True
                except:
                    continue
            
            # Check for search box (appears when logged in)
            try:
                search_elements = self.driver.find_elements(By.CSS_SELECTOR, "input[placeholder*='Search']")
                if search_elements:
                    return True
            except:
                pass
            
            return False
            
        except Exception as e:
            logger.debug(f"Login check error: {e}")
            return False
    
    def get_user_profile_name(self, driver) -> Optional[str]:
        """Get the logged-in user's name with multiple fallback strategies"""
        logger.info("🔎 Attempting to get user's profile name with enhanced strategies...")
        
        # STRATEGY 1: "Me" button dropdown (most reliable)
        try:
            # FIX: Use a more general selector that is less likely to change
            me_button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button.global-nav__primary-link[aria-label*='Me'], button[data-test-id='nav-me-dropdown-trigger']"))
            )
            driver.execute_script("arguments[0].click();", me_button)
            time.sleep(1.5)

            # FIX: Wait for a non-empty span to ensure the name has loaded
            name_element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.global-nav__me-content a.global-nav__me-profile-link span:not(:empty), .global-nav__me-name"))
            )
            name = name_element.text.strip()
            
            # Close the dropdown
            driver.execute_script("arguments[0].click();", me_button)
            time.sleep(0.5)

            if name and len(name) > 1:
                logger.info(f"✅ Got name from nav dropdown: {name}")
                return name
        except Exception as e:
            logger.debug(f"Strategy 1 (Nav Dropdown) failed: {e}")
            try:
                # Attempt to close dropdown if it failed mid-way
                body = driver.find_element(By.TAG_NAME, 'body')
                body.click()
            except: pass

        # STRATEGY 2: Fallback to profile photo alt text (very reliable)
        try:
            # FIX: Use a more specific selector for the profile photo image
            photo_element = driver.find_element(By.CSS_SELECTOR, "img.global-nav__me-photo-image, img.global-nav__me-photo")
            alt_text = photo_element.get_attribute('alt')
            if alt_text and "View profile" not in alt_text:
                 logger.info(f"✅ Got name from profile photo alt text: {alt_text}")
                 return alt_text
        except Exception as e:
            logger.debug(f"Strategy 2 (Profile Photo) failed: {e}")
            
        logger.warning("❌ All strategies failed to get user profile name.")
        return None


    def extract_profile_data(self, driver):
        """Extract profile data from LinkedIn profile page"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import NoSuchElementException, TimeoutException
        
        profile_data = {}
        try:
            # NEW: Wait for the main profile heading to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.t-24"))
            )

            # Extract name
            try:
                # SELECTOR UPDATED based on your HTML: h1 with class t-24
                name_elem = driver.find_element(By.CSS_SELECTOR, "h1.t-24.v-align-middle.break-words")
                profile_data['extracted_name'] = name_elem.text.strip()
                logger.info(f"📝 Extracted name: {profile_data['extracted_name']}")
            except NoSuchElementException:
                logger.warning("Could not find name element, trying fallback.")
                try:
                    # Fallback for slightly different structures
                    name_elem = driver.find_element(By.CSS_SELECTOR, "h1")
                    profile_data['extracted_name'] = name_elem.text.strip()
                    logger.info(f"📝 Extracted name (fallback): {profile_data['extracted_name']}")
                except Exception as e:
                    logger.error(f"Failed to extract name: {e}")
                    profile_data['extracted_name'] = "Professional"


            # Extract headline
            try:
                # SELECTOR UPDATED based on your HTML: div with class text-body-medium
                headline_elem = driver.find_element(By.CSS_SELECTOR, "div.text-body-medium.break-words")
                headline_text = headline_elem.text.strip()
                if headline_text and headline_text != profile_data.get('extracted_name', ''):
                    profile_data['extracted_headline'] = headline_text
                    logger.info(f"💼 Extracted headline: {headline_text[:50]}...")
            except NoSuchElementException:
                logger.warning("Could not find headline element.")
                profile_data['extracted_headline'] = ""


            # Extract about section (Selector likely needs updating too)
            try:
                # This selector is probably also broken and will need to be inspected
                about_selectors = [
                    "[data-test-id='about-section'] .pv-shared-text-with-see-more span[aria-hidden='true']",
                    ".pv-about-section .pv-shared-text-with-see-more span",
                    # Add a new selector here if you find one
                ]
                
                for selector in about_selectors:
                    try:
                        about_elem = driver.find_element(By.CSS_SELECTOR, selector)
                        about_text = about_elem.text.strip()
                        if about_text:
                            profile_data['about_snippet'] = about_text[:150] + "..." if len(about_text) > 150 else about_text
                            logger.info(f"📄 Extracted about: {profile_data['about_snippet'][:50]}...")
                            break
                    except NoSuchElementException:
                        continue
            except Exception as e:
                logger.warning(f"Could not extract about section: {e}")

            # Set defaults
            if not profile_data.get('about_snippet'):
                profile_data['about_snippet'] = ""

        except Exception as e:
            # This is the error you were seeing before
            logger.warning(f"⚠️ Profile data extraction failed: {e}")
            profile_data = {
                'extracted_name': 'Professional',
                'extracted_headline': '',
                'about_snippet': ''
            }

        return profile_data

    def generate_message(self, name, company, role, service_1, service_2, profile_data=None):
        """Generate personalized message using AI"""
        if not self.model:
            fallback_msg = f"Hi {name}, I'm impressed by your work as {role} at {company}. I'd love to connect and learn more about your experience. Looking forward to connecting!"
            return fallback_msg[:280]

        actual_name = profile_data.get('extracted_name', name) if profile_data else name
        about_snippet = profile_data.get('about_snippet', '') if profile_data else ''

        MESSAGE_TEMPLATE = """Create a personalized LinkedIn connection message based on the profile information provided.

Profile Information:
- Name: {Name}
- Company: {Company}  
- Role: {Role}
- Services/Expertise: {service_1}, {service_2}
- About/Bio: {about_snippet}

Create a professional, engaging message under 280 characters that:
1. Addresses them by name (ONLY USE FIRST NAMES)
2. References their specific work/company
3. Mentions a relevant connection point
4. Has a clear call to action

Return ONLY the message text, no labels or formatting.
"""

        prompt = MESSAGE_TEMPLATE.format(
            Name=actual_name,
            Company=company,
            Role=role,
            service_1=service_1 or "your field",
            service_2=service_2 or "industry trends",
            about_snippet=about_snippet
        )

        for attempt in range(3):
            try:
                response = self.model.generate_content(prompt)
                message = response.text.strip()
                message = re.sub(r'^(Icebreaker:|Message:)\s*', '', message, flags=re.IGNORECASE)
                message = message.strip('"\'[]')
                
                if len(message) > 280:
                    message = message[:277] + "..."
                
                return message
                
            except Exception as e:
                if "429" in str(e) or "ResourceExhausted" in str(e):
                    wait_time = 30 * (attempt + 1)
                    logger.warning(f"⏳ Gemini rate limit hit. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ Gemini error: {e}")
                    break

        # Fallback message
        fallback_msg = f"Hi {actual_name}, I'm impressed by your {role} work at {company}. I'd love to connect and exchange insights. Looking forward to connecting!"
        return fallback_msg[:280]

    def safe_click(self, driver, element):
        """Safely click an element with fallback methods"""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.common.exceptions import ElementClickInterceptedException, ElementNotInteractableException
        
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", element)
            time.sleep(random.uniform(0.5, 1.5))
            element.click()
            return True
        except (ElementClickInterceptedException, ElementNotInteractableException):
            try:
                ActionChains(driver).move_to_element(element).pause(0.5).click().perform()
                return True
            except Exception as e:
                logger.warning(f"Click fallback failed: {e}")
                return False
        except Exception as e:
            logger.warning(f"Click failed: {e}")
            return False

    

    def find_element_safe(self, driver, selectors, timeout=10):
        """Find element using multiple selectors"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        
        for selector_type, selector in selectors:
            try:
                if selector_type == "xpath":
                    element = WebDriverWait(driver, timeout).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                else:
                    element = WebDriverWait(driver, timeout).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                return element
            except TimeoutException:
                continue
        return None

    def send_connection_request_with_note(self, driver, message, name):
        """Send connection request with personalized note"""
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import TimeoutException
        
        logger.info(f"🤝 Attempting to send connection request with note to {name}...")

        # Find Connect button
        connect_button_selectors = [
            ("css", "button.artdeco-button.artdeco-button--2.artdeco-button--primary[aria-label*='Connect']"),
            ("xpath", "//button[contains(@aria-label, 'Connect') and contains(@class, 'artdeco-button--primary')]"),
            ("xpath", "//button[.//span[text()='Connect']]"),
            ("css", "button[aria-label*='Connect'][class*='artdeco-button']")
        ]

        connect_button = self.find_element_safe(driver, connect_button_selectors, timeout=8)
        if not connect_button:
            logger.error("❌ Connect button not found")
            return False

        # Click Connect button
        if not self.safe_click(driver, connect_button):
            logger.error("❌ Failed to click Connect button")
            return False

        logger.info("✅ Connect button clicked")
        self.human_delay(2, 3)

        try:
            # Look for "Add a note" button
            add_note_selectors = [
                ("css", "button[aria-label='Add a note']"),
                ("xpath", "//button[@aria-label='Add a note']"),
                ("xpath", "//button[.//span[text()='Add a note']]"),
                ("css", "button[aria-label*='Add a note']"),
                ("xpath", "//button[contains(text(), 'Add a note')]")
            ]

            add_note_button = self.find_element_safe(driver, add_note_selectors, timeout=8)
            if not add_note_button:
                logger.info("❌ Add a note button not found - cannot send with note")
                return False

            # Click "Add a note"
            if not self.safe_click(driver, add_note_button):
                logger.error("❌ Failed to click Add a note button")
                return False

            logger.info("✅ Add a note clicked")
            self.human_delay(1, 2)

            # Find and fill note text area
            note_area_selectors = [
                ("css", "textarea[name='message']"),
                ("css", "#custom-message"),
                ("css", "textarea[aria-label*='note']"),
                ("css", ".connect-note-form textarea"),
                ("xpath", "//textarea[@name='message']")
            ]

            note_area = self.find_element_safe(driver, note_area_selectors, timeout=8)
            if not note_area:
                logger.error("❌ Could not find note text area")
                return False

            # Type the personalized message
            self.type_like_human(note_area, message)
            logger.info("✅ Personalized note added successfully")
            self.human_delay(1, 2)

            # Find and click Send button
            send_request_selectors = [
                ("css", "button[aria-label='Send now']"),
                ("xpath", "//button[@aria-label='Send now']"),
                ("css", "button[aria-label*='Send invitation']"),
                ("xpath", "//button[contains(@aria-label, 'Send')]"),
                ("xpath", "//button[.//span[text()='Send']]")
            ]

            send_button = self.find_element_safe(driver, send_request_selectors, timeout=10)
            if send_button and self.safe_click(driver, send_button):
                logger.info(f"✅ Connection request with note sent successfully to {name}!")
                self.human_delay(2, 4)
                return True
            else:
                logger.error("❌ Could not find or click send button")
                return False

        except Exception as e:
            logger.error(f"❌ Error sending connection request with note: {e}")
            return False

    def send_connection_request_without_note(self, driver, name):
        """Send connection request without personalized note"""
        from selenium.webdriver.common.by import By
        
        logger.info(f"🤝 Attempting to send connection request without note to {name}...")

        # Find Connect button (same logic as with note)
        connect_button_selectors = [
            ("css", "button.artdeco-button.artdeco-button--2.artdeco-button--primary[aria-label*='Connect']"),
            ("xpath", "//button[contains(@aria-label, 'Connect') and contains(@class, 'artdeco-button--primary')]"),
            ("xpath", "//button[.//span[text()='Connect']]"),
            ("css", "button[aria-label*='Connect'][class*='artdeco-button']")
        ]

        connect_button = self.find_element_safe(driver, connect_button_selectors, timeout=8)
        if not connect_button:
            logger.error("❌ Connect button not found")
            return False

        # Click Connect button
        if not self.safe_click(driver, connect_button):
            logger.error("❌ Failed to click Connect button")
            return False

        logger.info("✅ Connect button clicked")
        self.human_delay(2, 3)

        try:
            # Look for Send button (skip adding note)
            send_request_selectors = [
                ("css", "button[aria-label='Send now']"),
                ("xpath", "//button[@aria-label='Send now']"),
                ("css", "button[aria-label*='Send invitation']"),
                ("xpath", "//button[contains(@aria-label, 'Send') and contains(@class, 'artdeco-button--primary')]"),
                ("xpath", "//button[.//span[text()='Send']]"),
                ("css", "button.artdeco-button--primary[aria-label*='Send']")
            ]

            send_button = self.find_element_safe(driver, send_request_selectors, timeout=10)
            if send_button and self.safe_click(driver, send_button):
                logger.info(f"✅ Connection request without note sent successfully to {name}!")
                self.human_delay(2, 4)
                return True
            else:
                logger.error("❌ Could not find or click send button")
                return False

        except Exception as e:
            logger.error(f"❌ Error sending connection request without note: {e}")
            return False

    def send_direct_message(self, driver, message, name):
        """Send direct message to LinkedIn connection"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException
        from selenium.webdriver.common.action_chains import ActionChains
        
        logger.info(f"🔍 Attempting to locate Message button for {name}...")

        # --- UPDATED SELECTOR LIST ---
        # Prioritizing the new 'pvs-sticky-header-profile-actions__action' class you found
        message_button_selectors = [
            ("css", "button.pvs-sticky-header-profile-actions__action[aria-label*='Message']"),
            ("xpath", "//button[contains(@class, 'pvs-sticky-header-profile-actions__action') and contains(@aria-label, 'Message')]"),
            ("css", "button.artdeco-button--primary[aria-label*='Message']"),
            ("xpath", "//button[contains(@aria-label, 'Message') and contains(@class, 'artdeco-button--primary')]"),
            ("css", "button[data-control-name*='message']"),
            ("css", "button[aria-label*='Message']"),
            # Removed selectors based on text 'Message' as they are unreliable
        ]
        # --- END OF UPDATE ---

        msg_btn = None
        for selector_type, selector in message_button_selectors:
            try:
                if selector_type == "xpath":
                    msg_btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    msg_btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )

                if msg_btn and msg_btn.is_displayed() and msg_btn.is_enabled():
                    logger.info(f"✅ Message button found using: {selector}")
                    break
                else:
                    msg_btn = None
            except (TimeoutException, NoSuchElementException):
                continue

        if not msg_btn:
            logger.info("❌ No Message button found - user may not be a 1st degree connection")
            return False

        # Click message button
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", msg_btn)
            self.human_delay(1, 2)

            if not self.safe_click(driver, msg_btn):
                ActionChains(driver).move_to_element(msg_btn).click().perform()

            logger.info("✅ Message button clicked successfully")
            self.human_delay(2, 3)
        except Exception as e:
            logger.error(f"❌ Failed to click Message button: {e}")
            return False

        # Enhanced message composition
        compose_selectors = [
            ("css", ".msg-form__contenteditable"),
            ("css", "[data-test-id='message-composer-input']"),
            ("css", "div[role='textbox'][contenteditable='true']"),
            ("xpath", "//textarea[@aria-label='Write a message…']"),
            ("css", "div[contenteditable='true'][role='textbox']")
        ]

        compose_box = None
        for selector_type, selector in compose_selectors:
            try:
                if selector_type == "xpath":
                    compose_box = WebDriverWait(driver, 8).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    compose_box = WebDriverWait(driver, 8).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )

                if compose_box:
                    logger.info(f"✅ Message compose area found using: {selector}")
                    break
            except (TimeoutException, NoSuchElementException):
                continue

        if not compose_box:
            logger.error("❌ Could not find message compose area")
            return False

        # Type the message
        try:
            compose_box.click()
            self.human_delay(0.5, 1)
            compose_box.clear()

            # Type message character by character
            for char in message:
                compose_box.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            logger.info("✅ Message typed successfully")
            self.human_delay(1, 2)
        except Exception as e:
            logger.error(f"❌ Failed to type message: {e}")
            return False

        # Send the message
        send_button_selectors = [
            ("css", "button.msg-form__send-button[type='submit']"),
            ("css", "button[data-control-name='send_message']"),
            ("xpath", "//button[@type='submit' and .//span[text()='Send']]"),
            ("xpath", "//button[contains(@aria-label, 'Send') and @type='submit']"),
            ("css", "button[aria-label*='Send message']")
        ]

        send_btn = None
        for selector_type, selector in send_button_selectors:
            try:
                if selector_type == "xpath":
                    send_btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    send_btn = WebDriverWait(driver, 6).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )

                if send_btn and send_btn.is_enabled():
                    logger.info(f"✅ Send button found using: {selector}")
                    break
            except (TimeoutException, NoSuchElementException):
                continue

        if not send_btn or not send_btn.is_enabled():
            logger.error("❌ Send button not found or not enabled")
            return False

        try:
            if self.safe_click(driver, send_btn):
                logger.info(f"🎉 Message sent successfully to {name}!")
                self.human_delay(1, 2)
                return True
            else:
                logger.error("❌ Failed to click Send button")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")
            return False

    def send_message_with_priority(self, driver, message, name, company):
        """Send message using priority order: connection with note -> connection without note -> direct message"""
        logger.info(f"🚀 Starting outreach process for {name} at {company}")

        try:
            # Wait for page to load completely
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.common.exceptions import TimeoutException
            
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            self.human_delay(2, 4)
        except TimeoutException:
            logger.warning("⚠️ Page load timeout - proceeding anyway")

        # Extract profile data for better personalization
        profile_data = self.extract_profile_data(driver)

        # PRIORITY 1: Try connection request with note
        logger.info("🎯 Priority 1: Attempting connection request with personalized note...")
        if self.send_connection_request_with_note(driver, message, name):
            logger.info(f"✅ Successfully sent connection request with note to {name}")
            return True

        # PRIORITY 2: Try connection request without note
        logger.info("🎯 Priority 2: Attempting connection request without note...")
        if self.send_connection_request_without_note(driver, name):
            logger.info(f"✅ Successfully sent connection request without note to {name}")
            return True

        # PRIORITY 3: Try direct message
        logger.info("🎯 Priority 3: Attempting direct message...")
        if self.send_direct_message(driver, message, name):
            logger.info(f"✅ Successfully sent direct message to {name}")
            return True

        # If all methods fail
        logger.error(f"❌ All outreach methods failed for {name}")
        return False
    def scrape_sales_navigator_search(self, driver, search_url, max_profiles):
        """Scrapes profiles from a Sales Navigator search URL."""
        profiles = []
        collected_urls = set()
        
        try:
            logger.info(f"Navigating to Sales Navigator URL: {search_url}")
            driver.get(search_url)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".artdeco-list__item"))
            )
            time.sleep(3)

            page_count = 1
            while len(profiles) < max_profiles:
                logger.info(f"Scraping page {page_count}... (Collected {len(profiles)}/{max_profiles})")
                
                # Scroll to bottom to load all profiles on the page
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(2, 4))

                profile_elements = driver.find_elements(By.CSS_SELECTOR, "li.artdeco-list__item")
                
                if not profile_elements:
                    logger.info("No more profile elements found on page. Ending collection.")
                    break

                for element in profile_elements:
                    if len(profiles) >= max_profiles:
                        break
                    
                    try:
                        # Extract profile URL and Name
                        link_element = element.find_element(By.CSS_SELECTOR, "a.ember-view")
                        profile_url = link_element.get_attribute('href')
                        name = element.find_element(By.css_selector, ".artdeco-entity-lockup__title").text.strip()
                        
                        # Skip if already collected or not a valid profile link
                        if not profile_url or "/sales/lead/" not in profile_url or profile_url in collected_urls:
                            continue

                        # Extract Headline and Company
                        headline_element = element.find_element(By.CSS_SELECTOR, ".artdeco-entity-lockup__subtitle")
                        headline_parts = [e.text.strip() for e in headline_element.find_elements(By.TAG_NAME, 'span')]
                        headline = headline_parts[0] if headline_parts else "N/A"
                        company = headline_parts[1] if len(headline_parts) > 1 else "N/A"
                        
                        profiles.append({
                            "name": name,
                            "profile_url": profile_url,
                            "headline": headline,
                            "company": company,
                        })
                        collected_urls.add(profile_url)

                    except NoSuchElementException:
                        continue # Skip elements that are not profiles (e.g., ads, footers)
                    except Exception as e:
                        logger.warning(f"Could not parse a profile element: {e}")

                # Go to next page
                if len(profiles) < max_profiles:
                    try:
                        next_button = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Next']")
                        if not next_button.is_enabled():
                            logger.info("Next button is disabled. Reached the end of search results.")
                            break
                        
                        self.safe_click(driver, next_button)
                        WebDriverWait(driver, 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".artdeco-list__item"))
                        )
                        time.sleep(random.uniform(3, 5))
                        page_count += 1
                    except NoSuchElementException:
                        logger.info("No 'Next' button found. Reached the end of search results.")
                        break
                else:
                    break

        except TimeoutException:
            logger.error("Timed out waiting for Sales Navigator page to load.")
        except Exception as e:
            logger.error(f"An error occurred during scraping: {e}")
            
        return profiles
    

    def fetch_sales_nav_lists(self, task_id):
        """
        Navigates to Sales Nav, opens Saved Searches > Leads, and scrapes list details.
        """
        self.browser_lock.acquire()
        lists = []
        try:
            logger.info(f"🔑 Browser lock acquired for fetching Sales Nav lists {task_id}")
            driver = self.get_shared_driver()
            if not driver:
                raise Exception("Failed to get browser")

            # 1. Go to Sales Nav Home
            logger.info("Navigating to Sales Navigator Home...")
            driver.get("https://www.linkedin.com/sales/home")
            time.sleep(4)

            # 2. Open Saved Searches
            # User provided: data-x--link--saved-searches
            logger.info("Opening Saved Searches panel...")
            saved_searches_btn = self.find_element_safe(driver, [
                ("css", "button[data-x--link--saved-searches]"),
                ("xpath", "//button[contains(@class, '_button_ps32ck') and contains(text(), 'Saved searches')]")
            ])
            
            if not saved_searches_btn:
                raise Exception("Could not find 'Saved Searches' button")
            
            self.safe_click(driver, saved_searches_btn)
            time.sleep(2)

            # 3. Click "Leads" Tab
            # User provided: aria-label="Lead- View all lead saved searches"
            logger.info("Switching to 'Lead' lists tab...")
            leads_tab = self.find_element_safe(driver, [
                ("css", "button[aria-label*='Lead- View all lead saved searches']"),
                ("xpath", "//button[contains(text(), 'Lead')]")
            ])
            
            if leads_tab:
                self.safe_click(driver, leads_tab)
                time.sleep(2)

            # 4. Scrape List Titles and URLs
            # User provided list html: class containing _panel-link_yma0zx
            logger.info("Scraping list data...")
            list_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='/sales/lists/people']")
            
            for el in list_elements:
                try:
                    title = el.text.strip()
                    url = el.get_attribute("href")
                    # Clean up title if it contains counts (e.g., "My List (50)")
                    if "\n" in title:
                        title = title.split("\n")[0]
                    
                    if title and url:
                        lists.append({"name": title, "url": url})
                except:
                    continue
            
            logger.info(f"✅ Found {len(lists)} Sales Nav lists.")

            # Report results
            self.report_task_result({
                "task_id": task_id,
                "type": "fetch_sales_nav_lists",
                "success": True,
                "payload": {"lists": lists}
            })

        except Exception as e:
            logger.error(f"❌ Error fetching Sales Nav lists: {e}")
            self.report_task_result({
                "task_id": task_id,
                "type": "fetch_sales_nav_lists",
                "success": False,
                "error": str(e)
            })
        finally:
            self.browser_lock.release()

    def run_sales_nav_outreach_campaign(self, campaign_id, user_config, campaign_params):
        """
        Iterates a specific Sales Nav list, messages leads, generates AI content, 
        and waits for user approval via Dashboard.
        """
        self.browser_lock.acquire()
        try:
            list_url = campaign_params.get('list_url')
            max_contacts = int(campaign_params.get('max_contacts', 10))
            
            # Initialize campaign state in self.active_campaigns
            self.active_campaigns[campaign_id] = {
                'status': 'running',
                'progress': 0,
                'total': max_contacts,
                'successful': 0,
                'failed': 0,
                'skipped': 0,
                'stop_requested': False,
                'awaiting_confirmation': False,
                'current_contact_preview': None,
                'start_time': datetime.now().isoformat()
            }

            driver = self.get_shared_driver()
            
            logger.info(f"🚀 Starting Sales Nav Outreach on list: {list_url}")
            driver.get(list_url)
            time.sleep(5)

            processed_count = 0
            
            # Iterate through rows in the list
            # We look for rows that contain a "Message" button
            while processed_count < max_contacts:
                if self.active_campaigns[campaign_id].get('stop_requested'):
                    break

                # Re-fetch elements every loop to avoid stale elements
                # Typical Sales Nav list row selector
                rows = driver.find_elements(By.CSS_SELECTOR, "div.artdeco-list__item, tr.artdeco-list__item")
                
                # If we've processed rows on this page, we might need to scroll or paginate
                # For MVP, we iterate visible rows.
                
                if processed_count >= len(rows):
                    logger.info("Reached end of visible rows. (Pagination logic would go here)")
                    break

                row = rows[processed_count]
                
                try:
                    # 1. Extract Info
                    name_elem = row.find_element(By.CSS_SELECTOR, "[data-anonymize='person-name']")
                    name = name_elem.text.strip()
                    
                    try:
                        company_elem = row.find_element(By.CSS_SELECTOR, "[data-anonymize='company-name']")
                        company = company_elem.text.strip()
                    except:
                        company = "their company"
                    
                    try:
                        headline_elem = row.find_element(By.CSS_SELECTOR, "[data-anonymize='job-title']")
                        role = headline_elem.text.strip()
                    except:
                        role = "Professional"

                    logger.info(f"👉 Processing Lead: {name} at {company}")

                    # 2. Find and Click Message Button
                    # User provided: data-anchor-send-message
                    msg_btn = row.find_element(By.CSS_SELECTOR, "button[data-anchor-send-message]")
                    
                    # Scroll to button
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", msg_btn)
                    time.sleep(1)
                    
                    self.safe_click(driver, msg_btn)
                    time.sleep(3) # Wait for chat window/drawer

                    # 3. Check if we have history (Optional safety check)
                    # If there are previous messages from 'You', maybe skip?
                    # For now, we proceed to generate message.

                    # 4. Generate AI Message
                    message = self.generate_message(name, company, role, "", "") # Reusing existing logic

                    # 5. ============ APPROVAL FLOW ============
                    # Prepare preview data
                    contact_info = {'Name': name, 'Company': company, 'Role': role, 'LinkedIn_profile': 'Sales Nav List'}
                    
                    self.active_campaigns[campaign_id]['awaiting_confirmation'] = True
                    self.active_campaigns[campaign_id]['current_contact_preview'] = {
                        'contact': contact_info,
                        'message': message,
                        'contact_index': processed_count
                    }
                    
                    # Report to dashboard so UI updates
                    self.report_progress_to_dashboard(campaign_id)
                    logger.info(f"⏳ Waiting for approval for {name}...")

                    # Wait Loop
                    start_wait = time.time()
                    user_decision = None
                    while time.time() - start_wait < 300: # 5 min timeout
                         if self.active_campaigns[campaign_id].get('stop_requested'): break
                         user_decision = self.active_campaigns[campaign_id].get('user_action')
                         if user_decision: break
                         time.sleep(1)

                    # Reset Wait State
                    self.active_campaigns[campaign_id]['awaiting_confirmation'] = False
                    self.active_campaigns[campaign_id]['current_contact_preview'] = None
                    self.active_campaigns[campaign_id]['user_action'] = None

                    action = user_decision.get('action') if user_decision else 'skip'

                    if action in ['send', 'edit']:
                        final_msg = user_decision.get('message', message) if user_decision else message
                        
                        # 6. Type and Send
                        # Find the active message box in Sales Nav
                        # Sales Nav input is often textarea[name='message'] or div[contenteditable]
                        input_box = self.find_element_safe(driver, [
                            ("css", "textarea[name='message']"),
                            ("css", "div[role='textbox'][contenteditable='true']")
                        ])
                        
                        if input_box:
                            input_box.clear()
                            self.type_like_human(input_box, final_msg)
                            time.sleep(1)
                            
                            # Find Send Button
                            send_btn = self.find_element_safe(driver, [
                                ("css", "button[type='submit']"),
                                ("xpath", "//button[contains(text(), 'Send')]")
                            ])
                            
                            if send_btn:
                                self.safe_click(driver, send_btn)
                                self.active_campaigns[campaign_id]['successful'] += 1
                                logger.info(f"✅ Message sent to {name}")
                            else:
                                logger.error("Send button not found")
                        else:
                            logger.error("Input box not found")
                        
                        # Close chat window to clean up
                        try:
                            close_icon = driver.find_element(By.CSS_SELECTOR, "button[aria-label*='Close']")
                            close_icon.click()
                        except: pass

                    else:
                        logger.info(f"⏭️ Skipped {name}")
                        self.active_campaigns[campaign_id]['skipped'] += 1
                        # Close chat if opened
                        try:
                            close_icon = driver.find_element(By.CSS_SELECTOR, "button[aria-label*='Close']")
                            close_icon.click()
                        except: pass
                    
                except Exception as e:
                    logger.error(f"Error processing row {processed_count}: {e}")
                    self.active_campaigns[campaign_id]['failed'] += 1

                processed_count += 1
                self.active_campaigns[campaign_id]['progress'] = processed_count
                self.report_progress_to_dashboard(campaign_id)
                time.sleep(random.uniform(3, 6))

            # Final Report
            self.report_progress_to_dashboard(campaign_id, final=True)

        except Exception as e:
            logger.error(f"Critical error in Sales Nav campaign: {e}")
            self.active_campaigns[campaign_id]['status'] = 'failed'
            self.report_progress_to_dashboard(campaign_id, final=True)
        finally:
            self.browser_lock.release()

    def search_and_connect(self, driver, keywords, max_invites=20, search_id=None):
        """Search for profiles and send connection requests"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        from urllib.parse import quote_plus
        
        logger.info(f"🔍 Searching for: {keywords}")
        url = (f"https://www.linkedin.com/search/results/people/"
               f"?keywords={quote_plus(keywords)}&origin=GLOBAL_SEARCH_HEADER")
        
        driver.get(url)
        self.human_delay(4, 7)
        sent_count = 0
        page_loops = 0
        total_attempts = 0
        
        # --- FIX: Main loop now checks for stop_requested flag ---
        while sent_count < max_invites and page_loops < 10:
            if search_id and self.active_searches[search_id].get('stop_requested'):
                logger.info("🛑 Stop requested, halting search.")
                break
                
            logger.info(f"📊 Current status: {sent_count}/{max_invites} invitations sent")
            
            # Find connect buttons
            self.human_delay(2, 4)
            connect_buttons = self.find_connect_buttons_enhanced(driver)
            
            if not connect_buttons:
                logger.info("No connect buttons found on this page")
                if not self.go_to_next_page(driver):
                    break
                page_loops += 1
                self.human_delay(3, 5)
                continue
            
            for btn in connect_buttons:
                if search_id and self.active_searches[search_id].get('stop_requested'):
                    logger.info("🛑 Stop requested, halting connection loop.")
                    break
                    
                if sent_count >= max_invites:
                    logger.info(f"🎯 Target reached: {sent_count}/{max_invites} invitations sent")
                    return sent_count
                
                total_attempts += 1
                logger.info(f"🔄 Attempting connection #{total_attempts}")
                self.human_delay(1, 3)
                try:
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                    self.human_delay(0.5, 1.5)  # Wait after scrolling
                    if self.click_connect_and_validate(driver, btn):
                        sent_count += 1
                        logger.info(f"✅ Success! Sent invitation #{sent_count}/{max_invites}")
                        time.sleep(random.uniform(2, 4))
                    else:
                        logger.info(f"❌ Failed to send invitation (attempt #{total_attempts})")
                except Exception as e:
                    logger.debug(f"Exception during connection attempt: {e}")
                    continue
            
            # Check for stop before navigating to next page
            if search_id and self.active_searches[search_id].get('stop_requested'):
                break

            # Navigate to next page
            if not self.go_to_next_page(driver):
                logger.info("No more pages available")
                break
            page_loops += 1
            time.sleep(random.uniform(1, 5))
        
        logger.info(f"🏁 Final results: {sent_count}/{max_invites} invitations sent ({total_attempts} total attempts)")
        return sent_count

    def execute_keyword_search_task(self, search_id, search_params):
        """
        A thread-safe wrapper to execute the keyword search task.
        It handles locking, driver acquisition, execution, and reporting.
        """
        self.browser_lock.acquire()
        try:
            logger.info(f"🔑 Browser lock acquired for search task {search_id}")
            
            # --- FIX: Initialize the search state so it can be stopped ---
            self.active_searches[search_id] = {
                "status": "running",
                "stop_requested": False,
                "keywords": search_params.get('keywords', ''),
                "max_invites": search_params.get('max_invites', 10),
                "invites_sent": 0,
            }
            # --- End of Fix ---

            driver = self.get_shared_driver()
            if not driver:
                raise Exception("Failed to get a valid browser session for the task.")
            
            # Pass the validated driver and search_id to the actual logic function
            self.run_enhanced_keyword_search(driver, search_id, search_params)

        except Exception as e:
            logger.error(f"❌ A critical error occurred in search task {search_id}: {e}")
            self.report_search_results_to_dashboard(search_id, {
                "error": str(e),
                "message": "The search task failed due to a critical error.",
                "success": False
            })
        finally:
            logger.info(f"🔑 Browser lock released for search task {search_id}")
            # Clean up the active search entry
            if search_id in self.active_searches:
                del self.active_searches[search_id]
            self.browser_lock.release()

    def execute_outreach_task(self, campaign_id, user_config, campaign_data):
        """A thread-safe wrapper to execute an outreach campaign."""
        self.browser_lock.acquire()
        try:
            logger.info(f"🔑 Browser lock acquired for outreach campaign {campaign_id}")
            driver = self.get_shared_driver()
            if not driver:
                raise Exception("Failed to get a valid browser session for the campaign.")
            
            # Pass the shared driver to the campaign logic
            self.run_enhanced_outreach_campaign(driver, campaign_id, user_config, campaign_data)

        except Exception as e:
            logger.error(f"❌ A critical error occurred in outreach campaign {campaign_id}: {e}", exc_info=True)
            self.active_campaigns[campaign_id]['status'] = 'failed'
            self.active_campaigns[campaign_id]['error'] = str(e)
            self.report_progress_to_dashboard(campaign_id, final=True)
        finally:
            logger.info(f"🔑 Browser lock released for outreach campaign {campaign_id}")
            self.browser_lock.release()

    def execute_inbox_task(self, process_id, platform='linkedin'):
        """
        A thread-safe wrapper to execute the inbox processing task.
        Handles locking, driver acquisition, and reporting.
        """
        self.browser_lock.acquire()
        try:
            logger.info(f"🔑 Browser lock acquired for {platform} inbox task {process_id}")
            driver = self.get_shared_driver()
            
            if not driver:
                raise Exception("Failed to get a valid browser session for inbox processing.")

            # Get the user's name if not already cached
            if not hasattr(self, 'user_name') or not self.user_name:
                self.user_name = self.get_user_profile_name(driver)
            
            logger.info(f"👤 Proceeding with user name: {self.user_name}")

            # Execute the inbox processing ONE time with the correct platform
            results = self.enhanced_inbox.process_inbox_enhanced(
                driver, 
                user_name=self.user_name or "Me", 
                session_id=process_id,
                client_instance=self,
                platform_str=platform  # Pass the platform string correctly
            )

            # Report the final results
            self.report_task_result({
                'task_id': process_id,
                'type': 'process_inbox',
                'success': results.get('success', False),
                'payload': results,
                'error': results.get('error')
            })

        except Exception as e:
            logger.error(f"❌ A critical error occurred in inbox task {process_id}: {e}", exc_info=True)
            self.report_task_result({
                'task_id': process_id,
                'type': 'process_inbox',
                'success': False,
                'payload': None,
                'error': f"A critical client-side error occurred: {e}"
            })
        finally:
            logger.info(f"🔑 Browser lock released for inbox task {process_id}")
            self.browser_lock.release()

    def find_connect_buttons_enhanced(self, driver):
        """Find connect buttons with updated 2025 detection"""
        from selenium.webdriver.common.by import By
        
        buttons = []
        
        # Strategy 1: Find ALL buttons and filter by text content (most reliable)
        try:
            all_buttons = driver.find_elements(By.TAG_NAME, "button")
            for btn in all_buttons:
                try:
                    btn_text = btn.text.strip()
                    # Check if button text is exactly "Connect"
                    if btn_text == "Connect":
                        if btn.is_displayed() and btn.is_enabled():
                            # Verify it's not in a "Pending" state container
                            parent_text = ""
                            try:
                                parent = btn.find_element(By.XPATH, "./ancestor::*[contains(@class, 'entity-result__actions')]")
                                parent_text = parent.text
                            except:
                                pass
                            
                            if "Pending" not in parent_text:
                                buttons.append(btn)
                except Exception as e:
                    continue
        except Exception as e:
            logger.debug(f"Strategy 1 (all buttons) failed: {e}")
        
        # Strategy 2: Use aria-label selector
        if not buttons:
            try:
                aria_buttons = driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='Invite'][aria-label*='to connect']")
                for btn in aria_buttons:
                    if btn.is_displayed() and btn.is_enabled():
                        buttons.append(btn)
            except Exception as e:
                logger.debug(f"Strategy 2 (aria-label) failed: {e}")
        
        # Strategy 3: Look in entity-result__actions container
        if not buttons:
            try:
                action_containers = driver.find_elements(By.CSS_SELECTOR, ".entity-result__actions")
                for container in action_containers:
                    try:
                        connect_btns = container.find_elements(By.TAG_NAME, "button")
                        for btn in connect_btns:
                            if btn.text.strip() == "Connect" and btn.is_displayed():
                                buttons.append(btn)
                    except:
                        continue
            except Exception as e:
                logger.debug(f"Strategy 3 (action containers) failed: {e}")
        
        unique_buttons = list(dict.fromkeys(buttons))
        logger.info(f"Found {len(unique_buttons)} available connect buttons")
        return unique_buttons

    def click_connect_and_validate(self, driver, button):
        """Click connect button and validate success"""
        self.human_delay(0.5, 1.5)
        driver.execute_script("arguments[0].scrollIntoView(true);", button)
        driver.execute_script("arguments[0].click();", button)
        self.human_delay(1, 2)
        return self.handle_connect_modal(driver)

    def handle_connect_modal(self, driver):
        """Handle connection modal with updated 2025 selectors"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        
        self.human_delay(1, 2)
        
        # Try to find and click "Send without a note" or "Send now" button
        send_selectors = [
            # Updated 2025 selectors
            "button[aria-label='Send without a note']",
            "button[aria-label='Send now']",
            "button[aria-label='Send invitation']",
        ]
        
        for selector in send_selectors:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                btn.click()
                logger.info(f"Clicked send button with selector: {selector}")
                break
            except TimeoutException:
                continue
        else:
            # Fallback: Find by button text
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                for btn in all_buttons:
                    btn_text = btn.text.strip()
                    if btn_text in ["Send without a note", "Send now", "Send"]:
                        if btn.is_displayed() and btn.is_enabled():
                            btn.click()
                            logger.info(f"Clicked send button with text: {btn_text}")
                            break
            except Exception as e:
                logger.warning(f"Fallback button click failed: {e}")
        
        self.human_delay(1, 2)
        
        # Check for success - look for "Pending" state
        try:
            WebDriverWait(driver, 5).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Pending')]")),
                    EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Pending')]")),
                    # Modal closed successfully indicator
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, ".artdeco-modal"))
                )
            )
            return True
        except TimeoutException:
            # Try to close modal if still open
            try:
                close_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Dismiss']")
                close_btn.click()
            except:
                pass
            return False

    def go_to_next_page(self, driver):
        """Navigate to next page of search results"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        
        try:
            wait = WebDriverWait(driver, 5)
            next_button = wait.until(EC.element_to_be_clickable((
                By.XPATH,
                "//button[@aria-label='Next' and not(@disabled)] | //a[@aria-label='Next']"
            )))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
            next_button.click()
            return True
        except TimeoutException:
            return False
        except Exception as e:
            return False

    
    def process_inbox_replies_enhanced(self, driver, max_replies=10):
        """Enhanced inbox processing with LinkedIn Helper 2-like features"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        logger.info("🤖 Starting enhanced inbox processing (LinkedIn Helper 2 style)...")
        results = []
        
        if not self.navigate_to_messaging(driver):
            return {"success": False, "error": "Messaging navigation failed"}
        
        try:
            # Find unread conversations with better selectors
            unread_selectors = [
                "li.msg-conversations-container__conversation-list-item--is-unread",
                "li.conversation-list-item--unread",
                "li.msg-conversation-listitem--unread",
                "li[data-test-unread-message='true']"
            ]
            
            unread_items = []
            for selector in unread_selectors:
                try:
                    unread_items = driver.find_elements(By.CSS_SELECTOR, selector)
                    if unread_items:
                        break
                except:
                    continue
            
            logger.info(f"Found {len(unread_items)} unread conversations")
            
            for idx, item in enumerate(unread_items[:max_replies]):
                try:
                    # Extract participant name from list item
                    name_selectors = [
                        ".msg-conversation-listitem__participant-names",
                        ".conversation-list-item__participant-names",
                        ".conversation-list-item__title"
                    ]
                    
                    name = "Unknown"
                    for n_selector in name_selectors:
                        try:
                            name_elem = item.find_element(By.CSS_SELECTOR, n_selector)
                            name = name_elem.text.strip()
                            if name:
                                break
                        except:
                            continue
                    
                    logger.info(f"Processing conversation with {name} ({idx+1}/{len(unread_items)})")
                    
                    # Open conversation
                    driver.execute_script("arguments[0].click();", item)
                    self.human_delay(2, 4)
                    
                    # Wait for conversation to load
                    WebDriverWait(driver, 10).until(
                        EC.any_of(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.msg-s-message-list-content")),
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.msg-thread"))
                        )
                    )
                    
                    # Extract conversation details
                    conversation_details = self.extract_conversation_details(driver)
                    
                    # Get complete conversation history
                    conversation_history = self.get_complete_conversation_history(driver)
                    
                    if not conversation_history:
                        logger.warning("No messages found, skipping")
                        results.append({"name": name, "status": "skipped", "reason": "empty history"})
                        self.navigate_to_messaging(driver)
                        continue
                    
                    # Check if last message is from user
                    if conversation_history and conversation_history[-1]["sender"] == "You":
                        logger.info("Last message was from user, skipping")
                        results.append({"name": name, "status": "skipped", "reason": "already replied"})
                        self.navigate_to_messaging(driver)
                        continue
                    
                    # Generate AI response with full context
                    ai_reply = self.generate_contextual_ai_response(conversation_history, conversation_details)
                    
                    logger.info(f"Generated AI response: {ai_reply}")
                    
                    # Send response
                    if self.send_chat_message(driver, ai_reply):
                        logger.info(f"✅ Replied to {name}")
                        results.append({
                            "name": name, 
                            "status": "replied", 
                            "message": ai_reply,
                            "context": {
                                "message_count": len(conversation_history),
                                "participant_info": conversation_details
                            }
                        })
                    else:
                        logger.error(f"❌ Failed to reply to {name}")
                        results.append({"name": name, "status": "failed", "reason": "send error"})
                    
                    # Return to inbox with delay
                    self.navigate_to_messaging(driver)
                    self.human_delay(3, 6)  # Longer delay between conversations
                    
                except Exception as e:
                    logger.error(f"Error processing conversation: {e}")
                    results.append({"name": f"Unknown{idx}", "status": "error", "reason": str(e)})
                    try:
                        self.navigate_to_messaging(driver)
                    except:
                        driver.refresh()
                        self.human_delay(3, 5)
            
            return {"success": True, "results": results, "processed_count": len(results)}
            
        except Exception as e:
            logger.error(f"Inbox processing failed: {e}")
            return {"success": False, "error": str(e)}

    

    def generate_contextual_ai_response(self, conversation_history: List[Dict[str, str]], 
                                   conversation_details: Dict[str, Any]) -> str:
        """Generate a highly contextual AI response based on conversation history and details"""
        if not self.model:
            return "I appreciate your message. I'll get back to you soon."
        
        # Format conversation history for the prompt
        formatted_history = "\n".join([
            f"{msg['sender']}: {msg['message']}" for msg in conversation_history[-10:]  # Last 10 messages
        ])
        
        # Get participant info for personalization
        participant_name = conversation_details.get('participant_name', 'there')
        participant_headline = conversation_details.get('participant_headline', '')
        
        prompt = f"""You are a professional LinkedIn assistant. Craft a thoughtful response to this conversation.

    Conversation Context:
    - Participant: {participant_name}
    - Headline: {participant_headline}

    Recent Messages:
    {formatted_history}

    Guidelines for your response:
    1. Be professional yet approachable
    2. Match the tone and formality of the conversation
    3. Keep it concise (1-3 sentences max)
    4. Address any questions or points raised
    5. If appropriate, suggest a next step or call to action
    6. Sign with just your first name if needed

    Craft your response:"""
        
        try:
            response = self.model.generate_content(prompt)
            ai_message = response.text.strip()
            
            # Clean up the response
            ai_message = re.sub(r'^(Response:|AI:|Assistant:)\s*', '', ai_message, flags=re.IGNORECASE)
            ai_message = ai_message.strip('"\'')
            
            # Ensure it's not too long
            if len(ai_message) > 300:
                ai_message = ai_message[:297] + "..."
                
            return ai_message
            
        except Exception as e:
            logger.error(f"AI response generation failed: {e}")
            return "Thank you for your message. I'll review this and respond properly soon."


    # ==============================================
    # ENHANCED CAMPAIGN RUNNERS
    # ==============================================

    # Replace your run_enhanced_outreach_campaign method with this corrected version

    def run_enhanced_outreach_campaign(self, driver, campaign_id, user_config, campaign_data):
        """
        Run outreach campaign with AI generation, user preview, and confirmation.
        """
        try:
            # Initialize campaign status
            self.active_campaigns[campaign_id] = {
                'status': 'running',
                'progress': 0,
                'total': len(campaign_data.get('contacts', [])[:campaign_data.get('max_contacts', 0)]),
                'successful': 0,
                'failed': 0,
                'skipped': 0,
                'already_messaged': 0,
                'stop_requested': False,
                'awaiting_confirmation': False,
                'current_contact_preview': None,  # Changed from current_contact
                'start_time': datetime.now().isoformat(),
                'contacts_processed': [],
                'user_action': None
            }

            # Load previously messaged profiles to avoid duplicates
            tracked_profiles = set()
            tracked_profiles_file = 'messaged_profiles.json'
            if os.path.exists(tracked_profiles_file):
                try:
                    with open(tracked_profiles_file, 'r', encoding='utf-8') as f:
                        tracked_profiles = set(json.load(f))
                except Exception:
                    pass

            contacts = campaign_data.get('contacts', [])[:campaign_data.get('max_contacts', 20)]
            
            for idx, contact in enumerate(contacts):
                # Check for stop request
                if self.active_campaigns[campaign_id].get('stop_requested'):
                    self.active_campaigns[campaign_id]['status'] = 'stopped'
                    break

                try:
                    # Basic contact validation
                    linkedin_url = contact.get('LinkedIn_profile', '')
                    if not linkedin_url or 'linkedin.com/in/' not in linkedin_url:
                        self.active_campaigns[campaign_id]['failed'] += 1
                        self.active_campaigns[campaign_id]['progress'] += 1
                        continue

                    # Skip if already messaged
                    if linkedin_url in tracked_profiles:
                        logger.info(f"⭐ Skipping {contact['Name']} - already messaged")
                        self.active_campaigns[campaign_id]['already_messaged'] += 1
                        self.active_campaigns[campaign_id]['progress'] += 1
                        continue

                    # --- AUTOMATION LOGIC ---
                    logger.info(f"🌐 Navigating to {contact['Name']}'s profile...")
                    driver.get(linkedin_url)
                    time.sleep(random.uniform(3, 5))

                    profile_data = self.extract_profile_data(driver)

                    logger.info(f"🤖 Generating personalized message for {contact['Name']}...")
                    message = self.generate_message(
                        contact.get('Name'),
                        contact.get('Company'),
                        contact.get('Role'),
                        contact.get('services and products_1', ''),
                        contact.get('services and products_2', ''),
                        profile_data
                    )

                    # ========== PAUSE & WAIT FOR USER DECISION ==========
                    
                    # 1. Set up awaiting confirmation state
                    self.active_campaigns[campaign_id]['awaiting_confirmation'] = True
                    self.active_campaigns[campaign_id]['current_contact_preview'] = {
                        'contact': contact,
                        'message': message,
                        'contact_index': idx
                    }
                    
                    # 2. Report to dashboard immediately
                    self.report_progress_to_dashboard(campaign_id)
                    
                    # 3. Wait for user decision with timeout
                    logger.info(f"⏳ Waiting for user decision for {contact['Name']}... (Timeout: 5 minutes)")
                    start_wait_time = time.time()
                    user_decision = None
                    
                    while time.time() - start_wait_time < 300:  # 5 minute timeout
                        if self.active_campaigns[campaign_id].get('stop_requested'):
                            break
                        
                        try:
                            self.send_heartbeat_ping()
                        except Exception as e:
                            pass

                        user_decision = self.active_campaigns[campaign_id].get('user_action')
                        if user_decision:
                            logger.info(f"👍 Received user action: {user_decision.get('action')}")
                            break
                        time.sleep(2)  # Poll every 2 seconds

                    # 4. Process the decision
                    # Reset state immediately
                    self.active_campaigns[campaign_id]['awaiting_confirmation'] = False
                    self.active_campaigns[campaign_id]['current_contact_preview'] = None
                    self.active_campaigns[campaign_id]['user_action'] = None
                    
                    # Determine action (default to skip on timeout)
                    action_to_take = user_decision.get('action') if user_decision else 'skip'
                    
                    if action_to_take in ['send', 'edit']:
                        final_message = user_decision.get('message', message) if user_decision else message
                        logger.info(f"▶️ Sending message to {contact['Name']} as per user confirmation.")
                        
                        success = self.send_message_with_priority(driver, final_message, 
                                                                contact.get('Name'), contact.get('Company'))
                        
                        if success:
                            self.active_campaigns[campaign_id]['successful'] += 1
                            tracked_profiles.add(linkedin_url)
                            
                            # Save tracked profiles
                            try:
                                with open(tracked_profiles_file, 'w', encoding='utf-8') as f:
                                    json.dump(list(tracked_profiles), f, indent=2)
                            except Exception as e:
                                logger.warning(f"Could not save tracked profile: {e}")
                            
                            logger.info(f"✅ Successfully sent message to {contact['Name']}")
                            time.sleep(random.uniform(45, 90))  # Long delay after successful send
                        else:
                            self.active_campaigns[campaign_id]['failed'] += 1
                            logger.error(f"❌ Failed to send message to {contact['Name']}")
                    else:
                        # Skip action
                        logger.info(f"⏭️ Skipping {contact['Name']} based on user decision or timeout.")
                        self.active_campaigns[campaign_id]['skipped'] += 1

                    # Update progress
                    self.active_campaigns[campaign_id]['progress'] += 1
                    self.report_progress_to_dashboard(campaign_id)

                except Exception as e:
                    logger.error(f"❌ Error processing {contact.get('Name', 'Unknown')}: {e}", exc_info=True)
                    self.active_campaigns[campaign_id]['failed'] += 1
                    self.active_campaigns[campaign_id]['progress'] += 1

            # Final campaign status update
            self.active_campaigns[campaign_id]['status'] = 'completed' if not self.active_campaigns[campaign_id].get('stop_requested') else 'stopped'
            self.active_campaigns[campaign_id]['end_time'] = datetime.now().isoformat()
            self.report_progress_to_dashboard(campaign_id, final=True)

        except Exception as e:
            logger.error(f"❌ Campaign {campaign_id} failed critically: {e}", exc_info=True)
            self.active_campaigns[campaign_id]['status'] = 'failed'
            self.active_campaigns[campaign_id]['error'] = str(e)
            self.report_progress_to_dashboard(campaign_id, final=True)

    def run_enhanced_keyword_search(self, driver, search_id, search_params):
        """
        Run keyword-based LinkedIn search and connect using a shared driver.
        This function now contains only the core automation logic.
        """
        try:
            keywords = search_params.get('keywords', '')
            max_invites = search_params.get('max_invites', 10)

            logger.info(f"🔍 Starting keyword search for: '{keywords}' with driver {driver.session_id}")

            # --- FIX: Pass the search_id to search_and_connect ---
            sent_count = self.search_and_connect(driver, keywords, max_invites=max_invites, search_id=search_id)

            logger.info(f"✅ Keyword search completed. Invitations sent: {sent_count}/{max_invites}")
            
            # Check if it was stopped
            if self.active_searches.get(search_id, {}).get('stop_requested'):
                 logger.info(f"Search task {search_id} was stopped by user. Final count: {sent_count}")
                 # Report as 'stopped' (which is a form of success)
                 payload = {
                    'keywords': keywords,
                    'max_invites': max_invites,
                    'invites_sent': sent_count,
                    'status': 'stopped',
                    'timestamp': datetime.now().isoformat()
                }
                 self.report_task_result({
                    'task_id': search_id, 'type': 'keyword_search',
                    'success': True, 'payload': payload, 'error': 'Stopped by user.'
                })
            else:
                # Report successful completion
                payload = {
                    'keywords': keywords,
                    'max_invites': max_invites,
                    'invites_sent': sent_count,
                    'status': 'completed',
                    'timestamp': datetime.now().isoformat()
                }
                self.report_task_result({
                    'task_id': search_id,
                    'type': 'keyword_search',
                    'success': True,
                    'payload': payload,
                    'error': None
                })

        except Exception as e:
            logger.error(f"❌ Keyword search logic for {search_id} failed: {e}", exc_info=True)
            self.report_task_result({
                'task_id': search_id,
                'type': 'keyword_search',
                'success': False,
                'payload': None,
                'error': str(e)
            })

        
    # ─── add to client_bot.py – right after run_enhanced_keyword_search() ─────────
    def run_search_connect_campaign(self, task_id: str, user_cfg: dict, params: dict) -> None:
        """
        Full keyword *search & connect* flow with live progress reporting
        and graceful shutdown on user request.
        """
        try:
            kw = params.get("keywords", "")
            max_invites = int(params.get("max_invites", 15))
            
            self.active_searches[task_id].update({
                "status": "initializing",
                "keywords": kw,
                "max_invites": max_invites,
                "start_time": datetime.now().isoformat(),
                "invites_sent": 0,
                "progress": 0
            })

            logger.info(f"🚀 Starting search-and-connect campaign: {task_id}")
            logger.info(f"🔍 Keywords: {kw}, Max invites: {max_invites}")

            # Initialize LinkedIn automation instance (same as campaign flow)
            automation = LinkedInAutomation(
                email=user_cfg.get('linkedin_email', self.config['linkedin_email']),
                password=user_cfg.get('linkedin_password', self.config['linkedin_password']),
                api_key=user_cfg.get('gemini_api_key', self.config['gemini_api_key'])
            )

            # Login to LinkedIn
            self.active_searches[task_id]["status"] = "logging_in"
            logger.info("🔐 Attempting LinkedIn login...")
            
            if not automation.login():
                logger.error("❌ LinkedIn login failed")
                self.active_searches[task_id]["status"] = "failed"
                self.active_searches[task_id]["driver_errors"] += 1
                self.report_search_results_to_dashboard(task_id, {
                    "error": "login_failed",
                    "message": "LinkedIn login failed"
                })
                automation.close()
                return

            logger.info("✅ LinkedIn login successful")
            self.active_searches[task_id]["status"] = "running"

            # Perform search and connect
            logger.info(f"🔍 Starting search and connect for: '{kw}'")
            
            # FIXED: Use the correct method - search_profiles instead of search_and_connect
            sent_count = automation.search_profiles(kw, max_invites=max_invites)

            # Update final status
            self.active_searches[task_id]["invites_sent"] = sent_count
            self.active_searches[task_id]["progress"] = sent_count
            self.active_searches[task_id]["status"] = "completed"
            self.active_searches[task_id]["end_time"] = datetime.now().isoformat()

            logger.info(f"✅ Search-and-connect completed: {sent_count}/{max_invites} invitations sent")

            # Report final results to dashboard
            self.report_search_results_to_dashboard(task_id, {
                "keywords": kw,
                "max_invites": max_invites,
                "invites_sent": sent_count,
                "timestamp": datetime.now().isoformat(),
                "success": True,
                "completion_rate": f"{sent_count}/{max_invites}",
                "message": f"Successfully sent {sent_count} connection requests"
            })

            # Clean up
            automation.close()

        except Exception as exc:
            logger.error(f"❌ Search-connect task {task_id} failed: {exc}")
            self.active_searches[task_id]["status"] = "failed"
            self.active_searches[task_id]["end_time"] = datetime.now().isoformat()
            
            self.report_search_results_to_dashboard(task_id, {
                "error": str(exc),
                "keywords": kw,
                "timestamp": datetime.now().isoformat(),
                "success": False
            })

            # Ensure cleanup
            try:
                if 'automation' in locals():
                    automation.close()
            except Exception as cleanup_error:
                logger.error(f"❌ Cleanup error: {cleanup_error}")


    def run_enhanced_inbox_processing(self, process_id, user_config):
        """Enhanced inbox processing with LinkedHelper 2 features"""
        try:
            # Initialize LinkedIn automation
            automation = LinkedInAutomation(
                email=user_config.get('linkedin_email', self.config['linkedin_email']),
                password=user_config.get('linkedin_password', self.config['linkedin_password']),
                api_key=user_config.get('gemini_api_key', self.config['gemini_api_key'])
            )

            # Login to LinkedIn
            logger.info("🔐 Logging into LinkedIn for enhanced inbox processing...")
            if not automation.login():
                logger.error("❌ Login failed - cannot process inbox")
                self.report_inbox_results_to_dashboard(process_id, {
                    "success": False, 
                    "error": "LinkedIn login failed"
                })
                return

            driver = automation.driver

            # Navigate to messaging
            if not automation.navigate_to_messaging():
                logger.warning("⚠️ Messaging did not load properly")
                self.report_inbox_results_to_dashboard(process_id, {
                    "success": False, 
                    "error": "Failed to navigate to messaging"
                })
                return

            # Use the enhanced inbox system
            results = self.enhanced_inbox.process_inbox_enhanced(automation.driver, max_replies=20)

            
            # Add processing ID to results
            results['process_id'] = process_id
            results['processing_completed_at'] = datetime.now().isoformat()

            logger.info(f"📬 Enhanced inbox processing completed:")
            logger.info(f"  - Total processed: {results.get('total_processed', 0)}")
            logger.info(f"  - Auto-replied: {results.get('auto_replied', 0)}")  
            logger.info(f"  - High priority leads: {results.get('high_priority', 0)}")
            logger.info(f"  - Hot leads identified: {results.get('leads_identified', 0)}")
            logger.info(f"  - Average lead score: {results.get('summary', {}).get('avg_lead_score', 0):.1f}")

            # Report comprehensive results to dashboard
            self.report_inbox_results_to_dashboard(process_id, results)

            # Keep browser open for manual inspection
            logger.info("✅ Enhanced inbox processing complete. Browser kept open for inspection.")

        except Exception as e:
            logger.error(f"❌ Enhanced inbox processing failed: {e}")
            self.report_inbox_results_to_dashboard(process_id, {
                "success": False, 
                "error": str(e),
                "process_id": process_id
            })
            
    def extract_conversation_details(self, driver) -> Dict[str, Any]:
        """Extract detailed conversation information including participant info"""
        from selenium.webdriver.common.by import By
        
        conversation_details = {}
        # FIX: Removed the incorrect recursive call to itself
        try:
            name_selectors = [".msg-thread-headline__title-text", ".msg-conversation-container__participant-names", ".thread__header-title", "h1.conversation-title"]
            for selector in name_selectors:
                try:
                    name_element = driver.find_element(By.CSS_SELECTOR, selector)
                    conversation_details['participant_name'] = name_element.text.strip()
                    break
                except: continue
            
            headline_selectors = [".msg-thread-headline__subtitle-text", ".msg-conversation-container__participant-headline", ".thread__header-subtitle"]
            for selector in headline_selectors:
                try:
                    headline_element = driver.find_element(By.CSS_SELECTOR, selector)
                    conversation_details['participant_headline'] = headline_element.text.strip()
                    break
                except: continue
            
            info_selectors = [".msg-thread-headline__info-text", ".thread__header-info"]
            for selector in info_selectors:
                try:
                    info_element = driver.find_element(By.CSS_SELECTOR, selector)
                    conversation_details['additional_info'] = info_element.text.strip()
                    break
                except: continue
                    
        except Exception as e:
            logger.error(f"Error extracting conversation details: {e}")

        return conversation_details
    
    def get_complete_conversation_history(self, driver) -> List[Dict[str, str]]:
        """Get the complete conversation history with improved extraction"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        conversation = []
        
        try:
            # Wait for messages to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".msg-s-message-list-content, .msg-thread"))
            )
            
            # Different selectors for messages in different UI versions
            message_selectors = [
                "li.msg-s-message-list__event",  # New UI
                "div.msg-s-event-listitem",      # Alternate new UI
                "li.message",                    # Old UI
                "div.msg-conversation-container__message"  # Another variant
            ]
            
            for selector in message_selectors:
                try:
                    message_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if message_elements:
                        break
                except:
                    continue
            
            for msg_element in message_elements:
                try:
                    # Extract sender name
                    sender_selectors = [
                        ".msg-s-message-group__name",
                        ".msg-s-event-listitem__sender-name",
                        ".message-sender",
                        ".msg-s-message-group__profile-link"
                    ]
                    
                    sender = "Unknown"
                    for s_selector in sender_selectors:
                        try:
                            sender_elem = msg_element.find_element(By.CSS_SELECTOR, s_selector)
                            sender = sender_elem.text.strip()
                            if sender:
                                break
                        except:
                            continue
                    
                    # Check if message is from current user
                    if "you" in sender.lower() or "your" in sender.lower():
                        sender = "You"
                    
                    # Extract message content
                    content_selectors = [
                        ".msg-s-event-listitem__body",
                        ".msg-s-message-group__message",
                        ".message-content",
                        ".msg-s-message-group__bubble"
                    ]
                    
                    content = ""
                    for c_selector in content_selectors:
                        try:
                            content_elem = msg_element.find_element(By.CSS_SELECTOR, c_selector)
                            content = content_elem.text.strip()
                            if content:
                                break
                        except:
                            continue
                    
                    # Extract timestamp if available
                    time_selectors = [
                        ".msg-s-message-group__timestamp",
                        ".msg-s-event-listitem__timestamp",
                        ".message-time"
                    ]
                    
                    timestamp = ""
                    for t_selector in time_selectors:
                        try:
                            time_elem = msg_element.find_element(By.CSS_SELECTOR, t_selector)
                            timestamp = time_elem.text.strip()
                            if timestamp:
                                break
                        except:
                            continue
                    
                    if content:
                        conversation.append({
                            "sender": sender,
                            "message": content,
                            "timestamp": timestamp
                        })
                        
                except Exception as e:
                    logger.debug(f"Error extracting individual message: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error getting conversation history: {e}")
        
        return conversation
    
    def process_non_responders(self, campaign_id):
        """
        Checks for contacts who were messaged >3 days ago and haven't replied.
        Extracts email and sends follow-up via Gmail.
        """
        import json
        from datetime import datetime, timedelta

        # 1. Load Campaign Data
        # (Assuming you are tracking campaign state locally or pulling from server)
        # For this example, we'll assume self.active_campaigns holds the state
        campaign = self.active_campaigns.get(campaign_id)
        if not campaign:
            logger.error("Campaign not found")
            return

        driver = self.get_shared_driver()
        
        for contact in campaign.get('contacts_processed', []):
            # Check criteria: Sent message, No Reply, Time elapsed
            last_msg_time = datetime.fromisoformat(contact.get('last_message_time'))
            days_elapsed = (datetime.now() - last_msg_time).days
            
            has_replied = contact.get('has_replied', False) # You need to update this flag from Inbox scanner
            already_emailed = contact.get('emailed', False)

            if not has_replied and not already_emailed and days_elapsed >= 3:
                logger.info(f"📉 No reply from {contact['Name']} after {days_elapsed} days. Attempting email fallback.")
                
                # 1. Extract Email
                email = self.extract_email_from_profile(driver, contact['LinkedIn_profile'])
                
                if email:
                    # 2. Prepare Email Content
                    subject = f"Following up - {contact['Company']}"
                    body = f"Hi {contact['Name'].split()[0]},\n\nI sent you a note on LinkedIn a few days ago regarding {contact['Company']}..."
                    
                    # 3. Send via Gmail (Server API)
                    details = {
                        "to_email": email,
                        "subject": subject,
                        "body": body
                    }
                    
                    result = self.send_email(details)
                    
                    if result.get('success'):
                        logger.info(f"✅ Cold email sent to {email}")
                        contact['emailed'] = True
                        contact['email_sent_time'] = datetime.now().isoformat()
                        # Update database/server state here
                    else:
                        logger.error(f"❌ Failed to send email: {result.get('error')}")
                else:
                    logger.warning(f"Could not find email for {contact['Name']}")

    def send_chat_message(self, driver, message):
        """Types and sends a message in the currently active chat window"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException, NoSuchElementException
        
        logger.info(f"Sending message: '{message[:50]}...'")
        
        try:
            # Wait for message box to be ready
            message_box_selector = "div.msg-form__contenteditable[role='textbox']"
            message_box = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, message_box_selector))
            )
            
            # Wait for any previous messages to clear
            self.human_delay(1, 2)
            
            # Clear any existing text
            driver.execute_script("arguments[0].innerText = '';", message_box)
            message_box.send_keys(" ")  # Trigger any required events
            self.human_delay(0.5, 1)
            
            # Type message
            self.type_like_human(message_box, message)
            self.human_delay(1, 2)
            
            # Find and click the send button
            send_button = driver.find_element(
                By.CSS_SELECTOR,
                "button.msg-form__send-button[type='submit'], button.msg-form-send-button"
            )
            
            # Ensure button is enabled
            if send_button.is_enabled():
                self.safe_click(driver, send_button)
                logger.info("Message sent successfully.")
                self.human_delay(2, 4)
                return True
            else:
                logger.error("Send button is disabled.")
                return False
                
        except TimeoutException:
            logger.error("Message input box not found or not interactable.")
            return False
        except NoSuchElementException:
            logger.error("Send button not found.")
            return False
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
        
    def navigate_to_messaging(self, driver, retries=3):
        """Navigate to LinkedIn messaging with retries and broader selectors"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        logger.info("📨 Navigating to LinkedIn messaging...")

        for attempt in range(1, retries + 1):
            try:
                driver.get("https://www.linkedin.com/messaging")
                WebDriverWait(driver, 20).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "ul.msg-conversations-container__conversations-list")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div.msg-threads")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".msg-conversation-listitem")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, "aside.msg-s-message-list-container"))
                    )
                )
                logger.info("✅ Successfully loaded messaging page.")
                self.human_delay(2, 3)
                return True
            except Exception as e:
                logger.warning(f"⚠️ Attempt {attempt}/{retries} failed to load messaging: {e}")
                self.human_delay(3, 6)

        logger.error("❌ Failed to load messaging page after retries. Staying on current page.")
        return False
    

    def report_progress_to_dashboard(self, campaign_id, final=False):
        """Report campaign progress back to dashboard with better error handling"""
        try:
            dashboard_url = self.config.get('dashboard_url')
            if not dashboard_url:
                logger.debug("No dashboard URL configured")
                return

            progress_data = self.active_campaigns.get(campaign_id, {})
            
            # Include current contact info if awaiting confirmation
            if progress_data.get('awaiting_confirmation') and progress_data.get('current_contact_preview'):
                progress_data['awaiting_action'] = True
            
            endpoint = f"{dashboard_url}/api/campaign_progress"
            
            # Add authentication headers
            headers = self._get_auth_headers()
            
            response = requests.post(endpoint, json={
                'campaign_id': campaign_id,
                'progress': progress_data,
                'final': final
            }, headers=headers, timeout=30, verify=True)
            
            if response.status_code == 200:
                logger.debug(f"✅ Successfully reported progress for campaign {campaign_id}")
            else:
                logger.warning(f"⚠️ Dashboard progress report returned status {response.status_code}")
                logger.warning(f"Response text: {response.text}")

            if final:
                logger.info(f"💾 Saving final campaign results for {campaign_id} to database...")
                self.report_task_result({
                    "task_id": campaign_id,
                    "type": "outreach_campaign",
                    "success": progress_data.get('status') in ['completed', 'stopped'],
                    "error": progress_data.get('error'),
                    "payload": progress_data,  # Save the entire progress dict as the result
                    "end_time": datetime.now().isoformat()
                })
        except requests.exceptions.Timeout:
            logger.warning(f"⚠️ Timeout reporting progress to dashboard for campaign {campaign_id}")
        except requests.exceptions.ConnectionError:
            logger.warning(f"⚠️ Connection error reporting progress to dashboard for campaign {campaign_id}")
        except Exception as e:
            logger.error(f"Could not report progress for campaign {campaign_id}: {e}")

    def report_search_results_to_dashboard(self, search_id, results):
        """Report search results back to dashboard with better error handling"""
        try:
            dashboard_url = self.config.get('dashboard_url')
            if not dashboard_url:
                return

            endpoint = f"{dashboard_url}/api/search_results"
            
            response = requests.post(endpoint, json={
                'search_id': search_id,
                'results': results
            }, timeout=30, verify=True)
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully reported search results for {search_id}")
            else:
                logger.warning(f"⚠️ Dashboard search report returned status {response.status_code}")

        except Exception as e:
            logger.debug(f"Could not report search results for {search_id}: {e}")



    def get_calendar_slots(self, duration_minutes: int = 30, days_ahead: int = 7) -> List[str]:
        """Fetch available calendar slots from the server."""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            if not SERVER_BASE:
                logger.warning("No dashboard URL, cannot fetch calendar slots.")
                return []
                
            endpoint = f"{SERVER_BASE.rstrip('/')}/api/google/free-slots"
            params = {'duration_minutes': duration_minutes, 'days_ahead': days_ahead}
            
            resp = requests.get(
                endpoint, 
                headers=self._get_auth_headers(), 
                params=params, 
                timeout=20
            )
            
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Successfully fetched {len(data.get('slots', []))} free slots.")
                return data.get('slots', [])
            else:
                logger.error(f"Error fetching calendar slots: {resp.status_code} - {resp.text}")
                return []
        except Exception as e:
            logger.error(f"Exception fetching calendar slots: {e}")
            return []

    def book_calendar_event(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Request the server to book a calendar event."""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            if not SERVER_BASE:
                return {'success': False, 'error': 'No dashboard URL configured'}
                
            endpoint = f"{SERVER_BASE.rstrip('/')}/api/google/book-meeting"
            
            resp = requests.post(
                endpoint, 
                headers=self._get_auth_headers(), 
                json=details, 
                timeout=30
            )
            
            if resp.status_code == 200:
                logger.info("Successfully booked meeting.")
                return resp.json()
            else:
                logger.error(f"Error booking meeting: {resp.status_code} - {resp.text}")
                return {'success': False, 'error': resp.text}
        except Exception as e:
            logger.error(f"Exception booking meeting: {e}")
            return {'success': False, 'error': str(e)}
    

    def extract_email_from_profile(self, driver, profile_url):
        """
        Navigates to profile, clicks Contact Info, and scrapes email.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import re

        email = None
        try:
            if driver.current_url != profile_url:
                driver.get(profile_url)
                time.sleep(3)

            # Click "Contact info" link
            try:
                contact_info_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "top-card-text-details-contact-info"))
                )
                contact_info_btn.click()
                time.sleep(2)
            except Exception:
                logger.warning("Could not find or click Contact Info button")
                return None

            # Scrape Email from the modal
            try:
                # Look for the email section in the modal
                email_section = driver.find_element(By.CSS_SELECTOR, ".pv-contact-info__contact-type--email")
                email_link = email_section.find_element(By.TAG_NAME, "a")
                email = email_link.text.strip()
                logger.info(f"📧 Extracted email: {email}")
            except Exception:
                logger.info("No email listed in Contact Info")

            # Close modal
            try:
                close_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Dismiss']")
                close_btn.click()
            except:
                driver.find_element(By.TAG_NAME, "body").click()

        except Exception as e:
            logger.error(f"Error extracting email: {e}")

        return email

    def send_email(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Request the server to send an email."""
        try:
            SERVER_BASE = self.config.get('dashboard_url')
            if not SERVER_BASE:
                return {'success': False, 'error': 'No dashboard URL configured'}
                
            endpoint = f"{SERVER_BASE.rstrip('/')}/api/google/send-email"
            
            resp = requests.post(
                endpoint, 
                headers=self._get_auth_headers(), 
                json=details, 
                timeout=30
            )
            
            if resp.status_code == 200:
                logger.info("Successfully sent email.")
                return resp.json()
            else:
                logger.error(f"Error sending email: {resp.status_code} - {resp.text}")
                return {'success': False, 'error': resp.text}
        except Exception as e:
            logger.error(f"Exception sending email: {e}")
            return {'success': False, 'error': str(e)}
                
    def show_profile_info(self):
        """Show information about the persistent profile"""
        if hasattr(self, 'persistent_profile_dir'):
            profile_size = 0
            try:
                for dirpath, dirnames, filenames in os.walk(self.persistent_profile_dir):
                    for filename in filenames:
                        profile_size += os.path.getsize(os.path.join(dirpath, filename))
                profile_size_mb = profile_size / (1024 * 1024)
                
                logger.info(f"📁 Profile directory: {self.persistent_profile_dir}")
                logger.info(f"💾 Profile size: {profile_size_mb:.1f} MB")
                logger.info("🔄 This profile will be reused for future sessions")
            except Exception as e:
                logger.debug(f"Could not calculate profile size: {e}")

    
    def add_contact_to_hubspot(self, contact):
        """
        Adds a contact to HubSpot CRM when a positive reply is detected.
        Requires 'hubspot_api_key' in client_config.json.
        """
        api_key = self.config.get('hubspot_api_key')
        if not api_key:
            logger.warning("⚠️ HubSpot API key not found in config. Skipping sync.")
            return False

        endpoint = "https://api.hubapi.com/crm/v3/objects/contacts"
        
        # Split name into First/Last
        names = contact.name.split(' ')
        first_name = names[0]
        last_name = ' '.join(names[1:]) if len(names) > 1 else ''

        # Extract email if available in profile_data
        email = contact.profile_data.get('email', '')
        
        # Prepare payload
        properties = {
            "firstname": first_name,
            "lastname": last_name,
            "company": contact.company,
            "jobtitle": contact.title,
            "linkedinbio": contact.linkedin_url, # Standard HubSpot property for LinkedIn
            "lifecyclestage": "lead",            # Mark as Lead
            "lead_status": "New"
        }
        
        # Only add email if we actually found one (avoids validation errors)
        if email:
            properties["email"] = email

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"🚀 Syncing {contact.name} to HubSpot...")
            response = requests.post(endpoint, json={"properties": properties}, headers=headers, timeout=10)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Successfully added {contact.name} to HubSpot as a Lead.")
                return True
            elif response.status_code == 409:
                logger.info(f"⚠️ Contact {contact.name} already exists in HubSpot. (Duplicate)")
                return True
            else:
                logger.error(f"❌ HubSpot Sync Failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error syncing to HubSpot: {e}")
            return False
                
    def _run_flask_app(self):
        """Run Flask app"""
        try:
            self.flask_app.run(
                host='127.0.0.1',
                port=self.config['local_port'],
                debug=False,
                use_reloader=False
            )
        except Exception as e:
            logger.error(f"❌ Flask app error: {e}")

    def cleanup_safe(self):
        """Safe cleanup method - DON'T delete persistent profile"""
        try:
            if hasattr(self, 'driver') and self.driver:
                logger.info("🔧 Closing browser (keeping profile for next session)")
                self.driver.quit()
        except Exception as e:
            logger.error(f"Error during driver cleanup: {e}")
        
        # DON'T delete persistent_profile_dir - we want to keep it!
        # Only clean up if it was actually a temp directory
        if hasattr(self, 'temp_profile_dir') and self.temp_profile_dir and os.path.exists(self.temp_profile_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_profile_dir, ignore_errors=True)
                logger.info("🧹 Cleaned up temporary files")
            except Exception as e:
                logger.error(f"Error during temp cleanup: {e}")

    def cleanup(self):
        """Cleanup resources"""
        self.running = False

        # Close any active automation instances
        for automation in self.automation_instances.values():
            try:
                automation.close()
            except:
                pass