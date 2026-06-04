import os
import json
import time
import datetime
import requests
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Page
from simplepush import send
import yaml

WSF_ENDPOINT = 'https://secureapps.wsdot.wa.gov/ferries/reservations/vehicle/SailingSchedule.aspx'

TERMINAL_MAP = {
    'anacortes': '1',
    'friday harbor': '10',
    'coupeville': '11',
    'lopez island': '13',
    'orcas island': '15',
    'port townsend': '17',
    'shaw island': '18'
}

VEHICLE_SIZE_MAP = {
    'under_22': '3',  # Legacy support
    'normal': '3'     # New intuitive name
}

VEHICLE_HEIGHT_MAP = {
    'up_to_7_2': '1000',    # Legacy support
    '7_2_to_7_6': '1001',   # Legacy support
    '7_6_to_13': '6',       # Legacy support
    'normal': '1000',       # Up to 7'2" tall
    'tall': '1001',         # 7'2" to 7'6" tall
    'tallxl': '6'           # 7'6" to 13' tall
}

TIME_FORMAT = '%I:%M %p'

class FerryBot:
    def __init__(self, config: Dict):
        self.config = config
        
        # Notification settings
        self.notification_type = config.get('notification_type', 'simplepush')
        
        # SimplePush settings
        simplepush_config = config.get('simplepush', {})
        self.simplepush_key = simplepush_config.get('key')
        self.simplepush_password = simplepush_config.get('password')
        self.simplepush_salt = simplepush_config.get('salt')
        
        # Discord webhook settings
        discord_config = config.get('discord', {})
        self.discord_webhook_url = discord_config.get('webhook_url')
        
        self.acknowledgment_file = '/tmp/ferry_bot_ack.json'
        self.notification_state_file = '/tmp/ferry_bot_notifications.json'
        self.discord_test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ferry_bot_discord_tested.json')
        
    def load_notification_state(self) -> Dict:
        """Load the current notification state"""
        if os.path.exists(self.notification_state_file):
            with open(self.notification_state_file, 'r') as f:
                return json.load(f)
        return {}
    
    def save_notification_state(self, state: Dict):
        """Save the notification state"""
        with open(self.notification_state_file, 'w') as f:
            json.dump(state, f)
    
    def is_acknowledged(self, key: str) -> bool:
        """Check if a notification has been acknowledged"""
        if os.path.exists(self.acknowledgment_file):
            with open(self.acknowledgment_file, 'r') as f:
                acks = json.load(f)
                return acks.get(key, False)
        return False
    
    def check_discord_first_run(self) -> bool:
        """Check if this is the first run with Discord notifications"""
        if self.notification_type != 'discord':
            return False
            
        # Check if we've already sent a test message for this webhook URL
        if os.path.exists(self.discord_test_file):
            try:
                with open(self.discord_test_file, 'r') as f:
                    test_data = json.load(f)
                    # If webhook URL matches what we tested, skip test
                    if test_data.get('webhook_url') == self.discord_webhook_url:
                        return False
            except:
                pass  # If file is corrupted, treat as first run
        
        return True
    
    def mark_discord_tested(self):
        """Mark that Discord webhook has been tested"""
        try:
            test_data = {
                'webhook_url': self.discord_webhook_url,
                'tested_at': datetime.datetime.now().isoformat()
            }
            with open(self.discord_test_file, 'w') as f:
                json.dump(test_data, f)
        except Exception as e:
            print(f"Warning: Could not save Discord test state: {e}")

    def send_discord_test_message(self):
        """Send initial test message to Discord"""
        test_message = """🤖 **WSF Ferry Bot Setup Complete!**

This is a **one-time test message** to confirm your Discord webhook is working correctly.

✅ **Discord notifications are now active**
🚢 **You'll receive alerts here when ferries become available**
⏰ **Bot will check every 5 minutes for your configured routes**

*You will not see this test message again unless you change your webhook URL.*"""

        try:
            self._send_discord_notification("🧪 Discord Webhook Test", test_message, "discord_test")
            self.mark_discord_tested()
            print("✅ Discord webhook test message sent successfully")
        except Exception as e:
            print(f"❌ Discord webhook test failed: {e}")
            # If running in GitHub Actions, fail the workflow on test failure too
            if os.environ.get('GITHUB_ACTIONS'):
                print("❌ CRITICAL: Discord webhook test failed in GitHub Actions - failing workflow")
                raise SystemExit(1)
            raise

    def send_notification(self, title: str, message: str, key: str):
        """Send notification via configured method"""
        try:
            if self.notification_type == 'discord':
                self._send_discord_notification(title, message, key)
            else:
                self._send_simplepush_notification(title, message, key)
            print(f"Notification sent: {title}")
        except Exception as e:
            print(f"Failed to send notification: {e}")
            # If running in GitHub Actions and Discord is configured, fail the entire workflow
            if os.environ.get('GITHUB_ACTIONS') and self.notification_type == 'discord' and self.discord_webhook_url:
                print("❌ CRITICAL: Discord notification failure in GitHub Actions - failing workflow")
                raise SystemExit(1)
            raise
    
    def _send_simplepush_notification(self, title: str, message: str, key: str):
        """Send notification via SimplePush"""
        if not self.simplepush_key:
            raise ValueError("SimplePush key not configured")
        
        send(
            key=self.simplepush_key,
            title=title,
            message=message,
            event=key
        )
    
    def _send_discord_notification(self, title: str, message: str, key: str):
        """Send notification via Discord webhook"""
        if not self.discord_webhook_url:
            raise ValueError("Discord webhook URL not configured")
        
        # Create Discord embed
        embed = {
            "title": title,
            "description": message,
            "color": 0x00ff00,  # Green color
            "footer": {
                "text": f"Event: {key}"
            },
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        
        payload = {
            "embeds": [embed]
        }
        
        response = requests.post(self.discord_webhook_url, json=payload)
        response.raise_for_status()
    
    def check_availability(self, page: Page, request: Dict) -> List[Dict]:
        """Check ferry availability for a specific request"""
        terminal_from = request['terminal_from'].lower()
        terminal_to = request['terminal_to'].lower()
        sailing_date = request['sailing_date']
        vehicle_size = request.get('vehicle_size', 'normal')
        vehicle_height = request.get('vehicle_height', 'normal')
        
        if terminal_from not in TERMINAL_MAP:
            raise ValueError(f'Unknown terminal: {terminal_from}')
        if terminal_to not in TERMINAL_MAP:
            raise ValueError(f'Unknown terminal: {terminal_to}')
        
        # Navigate to the ferry schedule page
        page.goto(WSF_ENDPOINT)
        page.wait_for_timeout(2000)
        
        # Fill in the form
        page.locator('#MainContent_dlFromTermList').select_option(value=TERMINAL_MAP[terminal_from])
        page.locator('#MainContent_dlToTermList').select_option(value=TERMINAL_MAP[terminal_to])
        
        # Format date properly - use JavaScript to set value
        print(f"Filling date field with: {sailing_date}")
        page.evaluate(f"""
            document.getElementById('MainContent_txtDatePicker').value = '{sailing_date}';
            document.getElementById('MainContent_txtDatePicker').dispatchEvent(new Event('change', {{ bubbles: true }}));
        """)
        page.wait_for_timeout(1000)
        
        page.locator('#MainContent_dlVehicle').select_option(value=VEHICLE_SIZE_MAP[vehicle_size])
        page.locator('#MainContent_ddlCarTruck14To22').select_option(value=VEHICLE_HEIGHT_MAP[vehicle_height])
        
        # Click continue
        page.locator('#MainContent_linkBtnContinue').click()
        
        # Wait for page to load and check for schedule table
        page.wait_for_timeout(3000)  # Give page time to load
        
        # Check if schedule table exists
        schedule_table = page.locator('#MainContent_gvschedule')
        available_ferries = []
        
        if schedule_table.count() > 0:
            print("Schedule table found")
            # Parse results
            rows = page.locator('#MainContent_gvschedule tr').all()
        else:
            print("Schedule table not found - checking for errors")
            # Check for error messages
            error_selectors = ['.validation-summary-errors', '#MainContent_ValidationSummary1', '.alert-danger']
            for selector in error_selectors:
                error_msgs = page.locator(selector).all()
                for msg in error_msgs:
                    text = msg.inner_text().strip()
                    if text:
                        print(f"Error: {text}")
            return available_ferries  # Return empty list if no schedule found
        
        for i, row in enumerate(rows[1:]):  # Skip header
            text = row.inner_text()
            if "Space Available" in text:
                cells = row.locator('td').all()
                if len(cells) >= 3:
                    time_text = cells[0].inner_text().strip()
                    vessel = cells[-1].inner_text().strip()
                    
                    ferry_info = {
                        'time': time_text,
                        'vessel': vessel
                    }
                    
                    # Check if this time matches preferred times/range
                    ferry_time_str = time_text.split()[0] + ' ' + time_text.split()[1]
                    ferry_time = datetime.datetime.strptime(ferry_time_str, TIME_FORMAT).time()
                    
                    is_preferred = True  # Default to preferred
                    
                    # Check time range if specified
                    sailing_time_from = request.get('sailing_time_from')
                    sailing_time_to = request.get('sailing_time_to')
                    
                    if sailing_time_from or sailing_time_to:
                        try:
                            # Handle time range filtering
                            if sailing_time_from and sailing_time_to:
                                start_time = datetime.datetime.strptime(sailing_time_from, TIME_FORMAT).time()
                                end_time = datetime.datetime.strptime(sailing_time_to, TIME_FORMAT).time()
                                
                                if start_time <= end_time:
                                    # Normal range (same day)
                                    is_preferred = start_time <= ferry_time <= end_time
                                else:
                                    # Range crosses midnight
                                    is_preferred = ferry_time >= start_time or ferry_time <= end_time
                            elif sailing_time_from:
                                # Only start time specified - from this time onwards
                                start_time = datetime.datetime.strptime(sailing_time_from, TIME_FORMAT).time()
                                is_preferred = ferry_time >= start_time
                            elif sailing_time_to:
                                # Only end time specified - up to this time
                                end_time = datetime.datetime.strptime(sailing_time_to, TIME_FORMAT).time()
                                is_preferred = ferry_time <= end_time
                        except Exception as e:
                            print(f"Error parsing time range: {e}")
                            is_preferred = True  # Default to include if parsing fails
                    
                    # Also check legacy preferred_times format for backward compatibility
                    preferred_times = request.get('preferred_times', [])
                    if preferred_times and is_preferred:
                        try:
                            legacy_preferred = False
                            for pref_time in preferred_times:
                                if ' - ' in pref_time:
                                    # Handle time range (e.g., "8:00 AM - 12:00 PM")
                                    start_str, end_str = pref_time.split(' - ')
                                    start_time = datetime.datetime.strptime(start_str.strip(), TIME_FORMAT).time()
                                    end_time = datetime.datetime.strptime(end_str.strip(), TIME_FORMAT).time()
                                    
                                    if start_time <= end_time:
                                        # Normal range (same day)
                                        legacy_preferred = start_time <= ferry_time <= end_time
                                    else:
                                        # Range crosses midnight
                                        legacy_preferred = ferry_time >= start_time or ferry_time <= end_time
                                else:
                                    # Handle exact time
                                    exact_time = datetime.datetime.strptime(pref_time, TIME_FORMAT).time()
                                    legacy_preferred = ferry_time == exact_time
                                
                                if legacy_preferred:
                                    break
                            
                            is_preferred = legacy_preferred
                        except Exception as e:
                            print(f"Error parsing preferred time: {e}")
                            is_preferred = True
                    
                    ferry_info['is_preferred'] = is_preferred
                    
                    available_ferries.append(ferry_info)
        
        
        return available_ferries
    
    
    
    def run_check(self):
        """Run a single check for all configured routes"""
        # Check if this is first run with Discord and send test message
        if self.check_discord_first_run():
            print("🧪 First run with Discord notifications detected - sending test message...")
            self.send_discord_test_message()
            return  # Exit after test message
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            notification_state = self.load_notification_state()
            
            for request in self.config['requests']:
                route_key = f"{request['terminal_from']}_{request['terminal_to']}_{request['sailing_date']}"
                print(f"\nChecking {request['terminal_from']} to {request['terminal_to']} on {request['sailing_date']}")
                
                try:
                    available = self.check_availability(page, request)
                    
                    if available:
                        # Filter for preferred times if specified
                        preferred_available = [f for f in available if f.get('is_preferred', True)]
                        
                        if preferred_available:
                            print(f"Found {len(preferred_available)} available ferries at preferred times!")
                            ferries_to_try = preferred_available
                        else:
                            print(f"Found {len(available)} available ferries (none at preferred times)")
                            ferries_to_try = available
                        
                        # Just notify about availability - no cart interaction needed
                        
                        # Prepare notification
                        preferred_times = [f['time'] for f in preferred_available] if preferred_available else []
                        all_times = [f['time'] for f in available]
                        notification_key = f"{route_key}_{all_times[0]}"
                        
                        title = f"Ferry Available! {request['terminal_from']} → {request['terminal_to']}"
                        message = f"Date: {request['sailing_date']}\n"
                        
                        if preferred_times:
                            message += f"⭐ Preferred times: {', '.join(preferred_times)}\n"
                        message += f"🚢 All times: {', '.join(all_times)}"
                        message += f"\n🔗 Book now: {WSF_ENDPOINT}"
                        
                        # Check if we need to send notification
                        if not self.is_acknowledged(notification_key):
                            self.send_notification(title, message, notification_key)
                            
                            # Update notification state
                            if notification_key not in notification_state:
                                notification_state[notification_key] = {
                                    'first_sent': datetime.datetime.now().isoformat(),
                                    'last_sent': datetime.datetime.now().isoformat(),
                                    'count': 1
                                }
                            else:
                                notification_state[notification_key]['last_sent'] = datetime.datetime.now().isoformat()
                                notification_state[notification_key]['count'] += 1
                    else:
                        print("No available ferries found")
                        
                except Exception as e:
                    print(f"Error checking route: {e}")
                    import traceback
                    traceback.print_exc()
            
            self.save_notification_state(notification_state)
            browser.close()

def main():
    # Load ferry requests from file
    ferry_requests_file = 'ferry_requests.json'
    try:
        with open(ferry_requests_file, 'r') as f:
            ferry_requests = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {ferry_requests_file} not found!")
        print("Please create ferry_requests.json with your ferry requests.")
        print("Example format:")
        print('[{"terminal_from": "anacortes", "terminal_to": "friday harbor", "sailing_date": "12/25/2024", "vehicle_size": "under_22", "vehicle_height": "up_to_7_2", "preferred_times": ["8:30 AM", "10:45 AM"]}]')
        exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: {ferry_requests_file} contains invalid JSON: {e}")
        exit(1)
    
    # Check if running in GitHub Actions
    if os.environ.get('GITHUB_ACTIONS'):
        # Load credentials from environment variables
        notification_type = os.environ.get('NOTIFICATION_TYPE', 'simplepush')
        
        config = {
            'notification_type': notification_type,
            'simplepush': {
                'key': os.environ.get('SIMPLEPUSH_KEY'),
                'password': os.environ.get('SIMPLEPUSH_PASSWORD'),
                'salt': os.environ.get('SIMPLEPUSH_SALT')
            },
            'discord': {
                'webhook_url': os.environ.get('DISCORD_WEBHOOK_URL')
            },
            'requests': ferry_requests
        }
    else:
        # Load from config file for local testing
        config_file = os.environ.get('CONFIG_FILE', 'config.yaml')
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        # Override requests with ferry_requests.json
        config['requests'] = ferry_requests
    
    bot = FerryBot(config)
    bot.run_check()

if __name__ == '__main__':
    main()