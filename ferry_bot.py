     1|import os
     2|import json
     3|import time
     4|import datetime
     5|import requests
     6|from typing import List, Dict, Optional
     7|from playwright.sync_api import sync_playwright, Page
     8|from simplepush import send
     9|import yaml
    10|
    11|WSF_ENDPOINT = 'https://secureapps.wsdot.wa.gov/ferries/reservations/vehicle/SailingSchedule.aspx'
    12|
    13|TERMINAL_MAP = {
    14|    'anacortes': '1',
    15|    'friday harbor': '10',
    16|    'coupeville': '11',
    17|    'lopez island': '13',
    18|    'orcas island': '15',
    19|    'port townsend': '17',
    20|    'shaw island': '18'
    21|}
    22|
    23|VEHICLE_SIZE_MAP = {
    24|    'under_22': '3',  # Legacy support
    25|    'normal': '3'     # New intuitive name
    26|}
    27|
    28|VEHICLE_HEIGHT_MAP = {
    29|    'up_to_7_2': '1000',    # Legacy support
    30|    '7_2_to_7_6': '1001',   # Legacy support
    31|    '7_6_to_13': '6',       # Legacy support
    32|    'normal': '1000',       # Up to 7'2" tall
    33|    'tall': '1001',         # 7'2" to 7'6" tall
    34|    'tallxl': '6'           # 7'6" to 13' tall
    35|}
    36|
    37|TIME_FORMAT = '%I:%M %p'
    38|
    39|class FerryBot:
    40|    def __init__(self, config: Dict):
    41|        self.config = config
    42|        
    43|        # Notification settings
    44|        self.notification_type = config.get('notification_type', 'simplepush')
    45|        
    46|        # SimplePush settings
    47|        simplepush_config = config.get('simplepush', {})
    48|        self.simplepush_key = simplepush_config.get('key')
    49|        self.simplepush_password = simplepush_config.get('password')
    50|        self.simplepush_salt = simplepush_config.get('salt')
    51|        
    52|        # Discord webhook settings
    53|        discord_config = config.get('discord', {})
    54|        self.discord_webhook_url = discord_config.get('webhook_url')
    55|        
    56|        self.acknowledgment_file = '/tmp/ferry_bot_ack.json'
    57|        self.notification_state_file = '/tmp/ferry_bot_notifications.json'
    58|        self.discord_test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ferry_bot_discord_tested.json')
    59|        
    60|    def load_notification_state(self) -> Dict:
    61|        """Load the current notification state"""
    62|        if os.path.exists(self.notification_state_file):
    63|            with open(self.notification_state_file, 'r') as f:
    64|                return json.load(f)
    65|        return {}
    66|    
    67|    def save_notification_state(self, state: Dict):
    68|        """Save the notification state"""
    69|        with open(self.notification_state_file, 'w') as f:
    70|            json.dump(state, f)
    71|    
    72|    def is_acknowledged(self, key: str) -> bool:
    73|        """Check if a notification has been acknowledged"""
    74|        if os.path.exists(self.acknowledgment_file):
    75|            with open(self.acknowledgment_file, 'r') as f:
    76|                acks = json.load(f)
    77|                return acks.get(key, False)
    78|        return False
    79|    
    80|    def check_discord_first_run(self) -> bool:
    81|        """Check if this is the first run with Discord notifications"""
    82|        if self.notification_type != 'discord':
    83|            return False
    84|            
    85|        # Check if we've already sent a test message for this webhook URL
    86|        if os.path.exists(self.discord_test_file):
    87|            try:
    88|                with open(self.discord_test_file, 'r') as f:
    89|                    test_data = json.load(f)
    90|                    # If webhook URL matches what we tested, skip test
    91|                    if test_data.get('webhook_url') == self.discord_webhook_url:
    92|                        return False
    93|            except:
    94|                pass  # If file is corrupted, treat as first run
    95|        
    96|        return True
    97|    
    98|    def mark_discord_tested(self):
    99|        """Mark that Discord webhook has been tested"""
   100|        try:
   101|            test_data = {
   102|                'webhook_url': self.discord_webhook_url,
   103|                'tested_at': datetime.datetime.now().isoformat()
   104|            }
   105|            with open(self.discord_test_file, 'w') as f:
   106|                json.dump(test_data, f)
   107|        except Exception as e:
   108|            print(f"Warning: Could not save Discord test state: {e}")
   109|
   110|    def send_discord_test_message(self):
   111|        """Send initial test message to Discord"""
   112|        test_message = """🤖 **WSF Ferry Bot Setup Complete!**
   113|
   114|This is a **one-time test message** to confirm your Discord webhook is working correctly.
   115|
   116|✅ **Discord notifications are now active**
   117|🚢 **You'll receive alerts here when ferries become available**
   118|⏰ **Bot will check every 5 minutes for your configured routes**
   119|
   120|*You will not see this test message again unless you change your webhook URL.*"""
   121|
   122|        try:
   123|            self._send_discord_notification("🧪 Discord Webhook Test", test_message, "discord_test")
   124|            self.mark_discord_tested()
   125|            print("✅ Discord webhook test message sent successfully")
   126|        except Exception as e:
   127|            print(f"❌ Discord webhook test failed: {e}")
   128|            # If running in GitHub Actions, fail the workflow on test failure too
   129|            if os.environ.get('GITHUB_ACTIONS'):
   130|                print("❌ CRITICAL: Discord webhook test failed in GitHub Actions - failing workflow")
   131|                raise SystemExit(1)
   132|            raise
   133|
   134|    def send_notification(self, title: str, message: str, key: str):
   135|        """Send notification via configured method"""
   136|        try:
   137|            if self.notification_type == 'discord':
   138|                self._send_discord_notification(title, message, key)
   139|            else:
   140|                self._send_simplepush_notification(title, message, key)
   141|            print(f"Notification sent: {title}")
   142|        except Exception as e:
   143|            print(f"Failed to send notification: {e}")
   144|            # If running in GitHub Actions and Discord is configured, fail the entire workflow
   145|            if os.environ.get('GITHUB_ACTIONS') and self.notification_type == 'discord' and self.discord_webhook_url:
   146|                print("❌ CRITICAL: Discord notification failure in GitHub Actions - failing workflow")
   147|                raise SystemExit(1)
   148|            raise
   149|    
   150|    def _send_simplepush_notification(self, title: str, message: str, key: str):
   151|        """Send notification via SimplePush"""
   152|        if not self.simplepush_key:
   153|            raise ValueError("SimplePush key not configured")
   154|        
   155|        send(
   156|            key=self.simplepush_key,
   157|            title=title,
   158|            message=message,
   159|            event=key
   160|        )
   161|    
   162|    def _send_discord_notification(self, title: str, message: str, key: str):
   163|        """Send notification via Discord webhook"""
   164|        if not self.discord_webhook_url:
   165|            raise ValueError("Discord webhook URL not configured")
   166|        
   167|        # Create Discord embed
   168|        embed = {
   169|            "title": title,
   170|            "description": message,
   171|            "color": 0x00ff00,  # Green color
   172|            "footer": {
   173|                "text": f"Event: {key}"
   174|            },
   175|            "timestamp": datetime.datetime.utcnow().isoformat()
   176|        }
   177|        
   178|        payload = {
   179|            "embeds": [embed]
   180|        }
   181|        
   182|        response = requests.post(self.discord_webhook_url, json=payload)
   183|        response.raise_for_status()
   184|    
   185|    def check_availability(self, page: Page, request: Dict) -> List[Dict]:
   186|        """Check ferry availability for a specific request"""
   187|        terminal_from = request['terminal_from'].lower()
   188|        terminal_to = request['terminal_to'].lower()
   189|        sailing_date = request['sailing_date']
   190|        vehicle_size = request.get('vehicle_size', 'normal')
   191|        vehicle_height = request.get('vehicle_height', 'normal')
   192|        
   193|        if terminal_from not in TERMINAL_MAP:
   194|            raise ValueError(f'Unknown terminal: {terminal_from}')
   195|        if terminal_to not in TERMINAL_MAP:
   196|            raise ValueError(f'Unknown terminal: {terminal_to}')
   197|        
   198|        # Navigate to the ferry schedule page
   199|        page.goto(WSF_ENDPOINT)
   200|        page.wait_for_timeout(2000)
   201|        
   202|        # Fill in the form
   203|        page.locator('#MainContent_dlFromTermList').select_option(value=TERMINAL_MAP[terminal_from])
   204|        page.locator('#MainContent_dlToTermList').select_option(value=TERMINAL_MAP[terminal_to])
   205|        
   206|        # Format date properly - use JavaScript to set value
   207|        print(f"Filling date field with: {sailing_date}")
   208|        page.evaluate(f"""
   209|            document.getElementById('MainContent_txtDatePicker').value = '{sailing_date}';
   210|            document.getElementById('MainContent_txtDatePicker').dispatchEvent(new Event('change', {{ bubbles: true }}));
   211|        """)
   212|        page.wait_for_timeout(1000)
   213|        
   214|        page.locator('#MainContent_dlVehicle').select_option(value=VEHICLE_SIZE_MAP[vehicle_size])
   215|        page.locator('#MainContent_ddlCarTruck14To22').select_option(value=VEHICLE_HEIGHT_MAP[vehicle_height])
   216|        
   217|        # Click continue
   218|        page.locator('#MainContent_linkBtnContinue').click()
   219|        
   220|        # Wait for page to load and check for schedule table
   221|        page.wait_for_timeout(3000)  # Give page time to load
   222|        
   223|        # Check if schedule table exists
   224|        schedule_table = page.locator('#MainContent_gvschedule')
   225|        available_ferries = []
   226|        
   227|        if schedule_table.count() > 0:
   228|            print("Schedule table found")
   229|            # Parse results
   230|            rows = page.locator('#MainContent_gvschedule tr').all()
   231|        else:
   232|            print("Schedule table not found - checking for errors")
   233|            # Check for error messages
   234|            error_selectors = ['.validation-summary-errors', '#MainContent_ValidationSummary1', '.alert-danger']
   235|            for selector in error_selectors:
   236|                error_msgs = page.locator(selector).all()
   237|                for msg in error_msgs:
   238|                    text = msg.inner_text().strip()
   239|                    if text:
   240|                        print(f"Error: {text}")
   241|            return available_ferries  # Return empty list if no schedule found
   242|        
   243|        for i, row in enumerate(rows[1:]):  # Skip header
   244|            text = row.inner_text()
   245|            if "Space Available" in text:
   246|                cells = row.locator('td').all()
   247|                if len(cells) >= 3:
   248|                    time_text = cells[0].inner_text().strip()
   249|                    vessel = cells[-1].inner_text().strip()
   250|                    
   251|                    ferry_info = {
   252|                        'time': time_text,
   253|                        'vessel': vessel
   254|                    }
   255|                    
   256|                    # Check if this time matches preferred times/range
   257|                    ferry_time_str = time_text.split()[0] + ' ' + time_text.split()[1]
   258|                    ferry_time = datetime.datetime.strptime(ferry_time_str, TIME_FORMAT).time()
   259|                    
   260|                    is_preferred = True  # Default to preferred
   261|                    
   262|                    # Check time range if specified
   263|                    sailing_time_from = request.get('sailing_time_from')
   264|                    sailing_time_to = request.get('sailing_time_to')
   265|                    
   266|                    if sailing_time_from or sailing_time_to:
   267|                        try:
   268|                            # Handle time range filtering
   269|                            if sailing_time_from and sailing_time_to:
   270|                                start_time = datetime.datetime.strptime(sailing_time_from, TIME_FORMAT).time()
   271|                                end_time = datetime.datetime.strptime(sailing_time_to, TIME_FORMAT).time()
   272|                                
   273|                                if start_time <= end_time:
   274|                                    # Normal range (same day)
   275|                                    is_preferred = start_time <= ferry_time <= end_time
   276|                                else:
   277|                                    # Range crosses midnight
   278|                                    is_preferred = ferry_time >= start_time or ferry_time <= end_time
   279|                            elif sailing_time_from:
   280|                                # Only start time specified - from this time onwards
   281|                                start_time = datetime.datetime.strptime(sailing_time_from, TIME_FORMAT).time()
   282|                                is_preferred = ferry_time >= start_time
   283|                            elif sailing_time_to:
   284|                                # Only end time specified - up to this time
   285|                                end_time = datetime.datetime.strptime(sailing_time_to, TIME_FORMAT).time()
   286|                                is_preferred = ferry_time <= end_time
   287|                        except Exception as e:
   288|                            print(f"Error parsing time range: {e}")
   289|                            is_preferred = True  # Default to include if parsing fails
   290|                    
   291|                    # Also check legacy preferred_times format for backward compatibility
   292|                    preferred_times = request.get('preferred_times', [])
   293|                    if preferred_times and is_preferred:
   294|                        try:
   295|                            legacy_preferred = False
   296|                            for pref_time in preferred_times:
   297|                                if ' - ' in pref_time:
   298|                                    # Handle time range (e.g., "8:00 AM - 12:00 PM")
   299|                                    start_str, end_str = pref_time.split(' - ')
   300|                                    start_time = datetime.datetime.strptime(start_str.strip(), TIME_FORMAT).time()
   301|                                    end_time = datetime.datetime.strptime(end_str.strip(), TIME_FORMAT).time()
   302|                                    
   303|                                    if start_time <= end_time:
   304|                                        # Normal range (same day)
   305|                                        legacy_preferred = start_time <= ferry_time <= end_time
   306|                                    else:
   307|                                        # Range crosses midnight
   308|                                        legacy_preferred = ferry_time >= start_time or ferry_time <= end_time
   309|                                else:
   310|                                    # Handle exact time
   311|                                    exact_time = datetime.datetime.strptime(pref_time, TIME_FORMAT).time()
   312|                                    legacy_preferred = ferry_time == exact_time
   313|                                
   314|                                if legacy_preferred:
   315|                                    break
   316|                            
   317|                            is_preferred = legacy_preferred
   318|                        except Exception as e:
   319|                            print(f"Error parsing preferred time: {e}")
   320|                            is_preferred = True
   321|                    
   322|                    ferry_info['is_preferred'] = is_preferred
   323|                    
   324|                    available_ferries.append(ferry_info)
   325|        
   326|        
   327|        return available_ferries
   328|    
   329|    
   330|    
   331|    def run_check(self):
   332|        """Run a single check for all configured routes"""
   333|        # Check if this is first run with Discord and send test message
   334|        if self.check_discord_first_run():
   335|            print("🧪 First run with Discord notifications detected - sending test message...")
   336|            self.send_discord_test_message()
   337|            return  # Exit after test message
   338|        
   339|        with sync_playwright() as p:
   340|            browser = p.chromium.launch(headless=True)
   341|            context = browser.new_context()
   342|            page = context.new_page()
   343|            
   344|            notification_state = self.load_notification_state()
   345|            
   346|            for request in self.config['requests']:
   347|                route_key = f"{request['terminal_from']}_{request['terminal_to']}_{request['sailing_date']}"
   348|                print(f"\nChecking {request['terminal_from']} to {request['terminal_to']} on {request['sailing_date']}")
   349|                
   350|                try:
   351|                    available = self.check_availability(page, request)
   352|                    
   353|                    if available:
   354|                        # Filter for preferred times if specified
   355|                        preferred_available = [f for f in available if f.get('is_preferred', True)]
   356|                        
   357|                        if preferred_available:
   358|                            print(f"Found {len(preferred_available)} available ferries at preferred times!")
   359|                            ferries_to_try = preferred_available
   360|                        else:
   361|                            print(f"Found {len(available)} available ferries (none at preferred times)")
   362|                            ferries_to_try = available
   363|                        
   364|                        # Just notify about availability - no cart interaction needed
   365|                        
   366|                        # Prepare notification
   367|                        preferred_times = [f['time'] for f in preferred_available] if preferred_available else []
   368|                        all_times = [f['time'] for f in available]
   369|                        notification_key = f"{route_key}_{all_times[0]}"
   370|                        
   371|                        title = f"Ferry Available! {request['terminal_from']} → {request['terminal_to']}"
   372|                        message = f"Date: {request['sailing_date']}\n"
   373|                        
   374|                        if preferred_times:
   375|                            message += f"⭐ Preferred times: {', '.join(preferred_times)}\n"
   376|                        message += f"🚢 All times: {', '.join(all_times)}"
   377|                        message += f"\n🔗 Book now: {WSF_ENDPOINT}"
   378|                        
   379|                        # Check if we need to send notification
   380|                        if not self.is_acknowledged(notification_key):
   381|                            self.send_notification(title, message, notification_key)
   382|                            
   383|                            # Update notification state
   384|                            if notification_key not in notification_state:
   385|                                notification_state[notification_key] = {
   386|                                    'first_sent': datetime.datetime.now().isoformat(),
   387|                                    'last_sent': datetime.datetime.now().isoformat(),
   388|                                    'count': 1
   389|                                }
   390|                            else:
   391|                                notification_state[notification_key]['last_sent'] = datetime.datetime.now().isoformat()
   392|                                notification_state[notification_key]['count'] += 1
   393|                    else:
   394|                        print("No available ferries found")
   395|                        
   396|                except Exception as e:
   397|                    print(f"Error checking route: {e}")
   398|                    import traceback
   399|                    traceback.print_exc()
   400|            
   401|            self.save_notification_state(notification_state)
   402|            browser.close()
   403|
   404|def main():
   405|    # Load ferry requests from file
   406|    ferry_requests_file = 'ferry_requests.json'
   407|    try:
   408|        with open(ferry_requests_file, 'r') as f:
   409|            ferry_requests = json.load(f)
   410|    except FileNotFoundError:
   411|        print(f"ERROR: {ferry_requests_file} not found!")
   412|        print("Please create ferry_requests.json with your ferry requests.")
   413|        print("Example format:")
   414|        print('[{"terminal_from": "anacortes", "terminal_to": "friday harbor", "sailing_date": "12/25/2024", "vehicle_size": "under_22", "vehicle_height": "up_to_7_2", "preferred_times": ["8:30 AM", "10:45 AM"]}]')
   415|        exit(1)
   416|    except json.JSONDecodeError as e:
   417|        print(f"ERROR: {ferry_requests_file} contains invalid JSON: {e}")
   418|        exit(1)
   419|    
   420|    # Check if running in GitHub Actions
   421|    if os.environ.get('GITHUB_ACTIONS'):
   422|        # Load credentials from environment variables
   423|        notification_type = os.environ.get('NOTIFICATION_TYPE', 'simplepush')
   424|        
   425|        config = {
   426|            'notification_type': notification_type,
   427|            'simplepush': {
   428|                'key': os.environ.get('SIMPLEPUSH_KEY'),
   429|                'password': os.environ.get('SIMPLEPUSH_PASSWORD'),
   430|                'salt': os.environ.get('SIMPLEPUSH_SALT')
   431|            },
   432|            'discord': {
   433|                'webhook_url': os.environ.get('DISCORD_WEBHOOK_URL')
   434|            },
   435|            'requests': ferry_requests
   436|        }
   437|    else:
   438|        # Load from config file for local testing
   439|        config_file = os.environ.get('CONFIG_FILE', 'config.yaml')
   440|        with open(config_file, 'r') as f:
   441|            config = yaml.safe_load(f)
   442|        # Override requests with ferry_requests.json
   443|        config['requests'] = ferry_requests
   444|    
   445|    bot = FerryBot(config)
   446|    bot.run_check()
   447|
   448|if __name__ == '__main__':
   449|    main()