# gui.py
from logging import root
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# This will hold the result from the config GUI
_tk_config_result = None

def create_config_gui(self):
    global _tk_config_result
    """Create configuration GUI implemented using Tkinter"""
    import tkinter as tk
    from tkinter import ttk, messagebox

    _tk_config_result = None

    root = tk.Tk()
    root.title('LinkedIn Automation Client Setup')
    root.resizable(False, False)
        
        # Center the window
    root.geometry('600x600') # Increased height slightly to fit new fields

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill='both', expand=True)

    row = 0
    ttk.Label(frm, text='LinkedIn Automation Client Setup', 
            font=('Helvetica', 16, 'bold')).grid(row=row, column=0, columnspan=2, pady=(0,10))
    row += 1

        # LinkedIn credentials
    ttk.Label(frm, text='LinkedIn Credentials:', 
                 font=('Helvetica', 12, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text='Email:').grid(row=row, column=0, sticky='e', padx=(0,5))
    linkedin_email = tk.StringVar()
    ttk.Entry(frm, textvariable=linkedin_email, width=40).grid(row=row, column=1, sticky='w')
    row += 1
    ttk.Label(frm, text='Password:').grid(row=row, column=0, sticky='e', padx=(0,5))
    linkedin_password = tk.StringVar()
    ttk.Entry(frm, textvariable=linkedin_password, show='*', width=40).grid(row=row, column=1, sticky='w')
    row += 1

    # AI Config
    ttk.Label(frm, text='').grid(row=row, column=0)
    row += 1
    ttk.Label(frm, text='AI Configuration:', 
            font=('Helvetica', 12, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text='Gemini API Key:').grid(row=row, column=0, sticky='e', padx=(0,5))
    gemini_api_key = tk.StringVar()
    ttk.Entry(frm, textvariable=gemini_api_key, width=40).grid(row=row, column=1, sticky='w')
    row += 1

    # ---------------------------------------------------------
    # NEW: HubSpot Configuration
    # ---------------------------------------------------------
    ttk.Label(frm, text='').grid(row=row, column=0)
    row += 1
    ttk.Label(frm, text='Integrations:', 
            font=('Helvetica', 12, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text='HubSpot API Key:').grid(row=row, column=0, sticky='e', padx=(0,5))
    hubspot_api_key = tk.StringVar()
    ttk.Entry(frm, textvariable=hubspot_api_key, width=40).grid(row=row, column=1, sticky='w')
    row += 1
    # ---------------------------------------------------------

    # Client Settings
    ttk.Label(frm, text='').grid(row=row, column=0)
    row += 1
    ttk.Label(frm, text='Client Settings:', 
            font=('Helvetica', 12, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text='Local Port:').grid(row=row, column=0, sticky='e', padx=(0,5))
    local_port = tk.StringVar(value='5001')
    ttk.Entry(frm, textvariable=local_port, width=10).grid(row=row, column=1, sticky='w')
    row += 1
    
    # Dashboard connection
    ttk.Label(frm, text='').grid(row=row, column=0)
    row += 1
    ttk.Label(frm, text='Dashboard Connection:', 
            font=('Helvetica', 12, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1

    URL_RENDER = 'https://linkedin-automation-dashboard.onrender.com'
    URL_EB_AWS = 'http://linksprig-dev.ap-south-1.elasticbeanstalk.com/'
    URL_LOCAL = 'http://127.0.0.1:5000'
    default_dashboard = URL_RENDER
    dashboard_choice = tk.StringVar(value="render")
    def update_dashboard_url():
            choice = dashboard_choice.get()
            if choice == "render":
                dashboard_url.set(URL_RENDER)
            elif choice == "AWS":
                dashboard_url.set(URL_EB_AWS)
            else:
                dashboard_url.set(URL_LOCAL)

    ttk.Radiobutton(frm, text='Online Dashboard (Render)', 
                       variable=dashboard_choice, value="render", 
                       command=update_dashboard_url).grid(row=row, column=0, sticky='w')
    ttk.Radiobutton(frm, text='Local Dashboard (127.0.0.1)', 
                       variable=dashboard_choice, value="local",
                       command=update_dashboard_url).grid(row=row, column=1, sticky='w')
    row += 1
    ttk.Radiobutton(frm, text='Dev Dashboard (Elastic Beanstalk)', 
                       variable=dashboard_choice, value="eb_AWS",
                       command=update_dashboard_url).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text='Dashboard URL:').grid(row=row, column=0, sticky='e', padx=(0,5))
    dashboard_url = tk.StringVar(value=default_dashboard)
    ttk.Entry(frm, textvariable=dashboard_url, width=50).grid(row=row, column=1, sticky='w')
    row += 1
    ttk.Label(frm, text=f'(Render: {URL_RENDER})', 
                 font=('Helvetica', 8)).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text=f'(AWS: {URL_EB_AWS})', 
                 font=('Helvetica', 8)).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1
    ttk.Label(frm, text=f'(Local: {URL_LOCAL})', 
                 font=('Helvetica', 8)).grid(row=row, column=0, columnspan=2, sticky='w')
    row += 1

        # Buttons
    btn_frame = ttk.Frame(frm)
    btn_frame.grid(row=row, column=0, columnspan=2, pady=(20,0))
    
    def on_cancel():
            global _tk_config_result
            _tk_config_result = None
            root.destroy()
        
    def on_save():
            global _tk_config_result
            # Validation
            req = {
                'linkedin_email': linkedin_email.get().strip(), 
                'linkedin_password': linkedin_password.get().strip(), 
                'gemini_api_key': gemini_api_key.get().strip()
            }
            missing = [k for k,v in req.items() if not v]
            if missing:
                messagebox.showerror('Validation Error', 
                                   f'Please fill in all required fields: {", ".join(missing)}')
                return

            dash = dashboard_url.get().strip()
            if not dash:
                messagebox.showerror('Validation Error', 'Please enter a dashboard URL!')
                return
            if not (dash.startswith('http://') or dash.startswith('https://')):
                messagebox.showerror('Validation Error', 
                                   'Dashboard URL must start with http:// or https://')
                return

            try:
                lp = int(local_port.get())
                if lp < 1 or lp > 65535:
                    raise ValueError
            except Exception:
                messagebox.showerror('Validation Error', 
                                   'Please enter a valid port number (1-65535)!')
                return

            cfg = {
                'linkedin_email': req['linkedin_email'],
                'linkedin_password': req['linkedin_password'],
                'gemini_api_key': req['gemini_api_key'],
                # NEW: Save HubSpot Key to config
                'hubspot_api_key': hubspot_api_key.get().strip(),
                'local_port': lp,
                'dashboard_url': dash,
                'use_online_dashboard': dashboard_choice.get(),
                'created_at': __import__('datetime').datetime.now().isoformat()
            }

            # Test connection
            try:
                import requests
                test_url = dash if dash.endswith('/') else f"{dash}/"
                resp = requests.get(test_url, timeout=15)
                if resp.status_code != 200:
                    cont = messagebox.askyesno('Connection Test Failed', 
                                         f'Dashboard connection test to {test_url} returned status {resp.status_code}.\n\nDo you want to continue anyway?')
                    if not cont:
                        return
            except requests.exceptions.Timeout:
                cont = messagebox.askyesno('Connection Timeout', 
                                         'Dashboard connection timeout. Do you want to continue anyway?')
                if not cont:
                    return
            except requests.exceptions.ConnectionError:
                cont = messagebox.askyesno('Connection Error', 
                                         'Cannot connect to dashboard. Do you want to continue anyway?')
                if not cont:
                    return
            except Exception as e:
                cont = messagebox.askyesno('Connection Test Error', 
                                         f'Connection test failed: {e}\n\nDo you want to continue anyway?')
                if not cont:
                    return

            # Save config
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    import json
                    json.dump(cfg, f, indent=2)
                _tk_config_result = cfg
                root.destroy()
            except Exception as e:
                messagebox.showerror('Save Error', f'Error saving configuration: {e}')

    ttk.Button(btn_frame, text='Save & Start', command=on_save).grid(row=0, column=0, padx=6)
    ttk.Button(btn_frame, text='Cancel', command=on_cancel).grid(row=0, column=1, padx=6)
        # Start mainloop
    root.mainloop()
    return _tk_config_result


def show_status_gui(self):
    """Show client status GUI using Tkinter"""
    import tkinter as tk
    from tkinter import ttk, scrolledtext

    root = tk.Tk()
    root.title('LinkedIn Automation Client - Enhanced Status')
    root.geometry('900x600')

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='LinkedIn Automation Client', 
                 font=('Helvetica', 16, 'bold')).pack(anchor='w')
    status_label = ttk.Label(frame, text=f"Status: Running on port {self.config.get('local_port', 'N/A')}")
    status_label.pack(anchor='w', pady=(4,0))
    dashboard_label = ttk.Label(frame, text=f"Dashboard: {self.config.get('dashboard_url', 'N/A')}")
    dashboard_label.pack(anchor='w', pady=(0,8))

    ttk.Label(frame, text='Active Campaigns:', 
                 font=('Helvetica', 12, 'bold')).pack(anchor='w')
    campaigns_text = scrolledtext.ScrolledText(frame, height=15, wrap='word')
    campaigns_text.pack(fill='both', expand=True, pady=(0,8))
    campaigns_text.insert('1.0', 'No active campaigns')
    campaigns_text.config(state='disabled')

    ttk.Label(frame, text='Active Searches:', 
                 font=('Helvetica', 12, 'bold')).pack(anchor='w')
    searches_text = scrolledtext.ScrolledText(frame, height=6, wrap='word')
    searches_text.pack(fill='both', expand=False, pady=(0,8))
    searches_text.insert('1.0', 'No active searches')
    searches_text.config(state='disabled')

    ttk.Label(frame, text='Active Profile Collections:', 
                 font=('Helvetica', 12, 'bold')).pack(anchor='w')
    collections_text = scrolledtext.ScrolledText(frame, height=5, wrap='word')
    collections_text.pack(fill='both', expand=False, pady=(0,8))
    collections_text.insert('1.0', 'No active collections')
    collections_text.config(state='disabled')

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(anchor='e', pady=(8,0))

    def stop_client():
        self.running = False
        try:
            root.destroy()
        except:
            pass

    def do_refresh():
        # Update campaigns
        campaigns_lines = []
        if self.active_campaigns:
            for cid, status in self.active_campaigns.items():
                campaigns_lines.append(f"Campaign {cid[:8]}...")
                campaigns_lines.append(f"  Status: {status.get('status', 'unknown')}")
                campaigns_lines.append(f"  Progress: {status.get('progress', 0)}/{status.get('total', 0)}")
                campaigns_lines.append(f"  Success: {status.get('successful', 0)}, Failed: {status.get('failed', 0)}")
                campaigns_lines.append(f"  Skipped: {status.get('skipped', 0)}, Already Messaged: {status.get('already_messaged', 0)}")
                if status.get('awaiting_confirmation'):
                    campaigns_lines.append('  ⏳ AWAITING USER CONFIRMATION')
                    current_contact = status.get('current_contact', {}).get('contact', {})
                    campaigns_lines.append(f"  Contact: {current_contact.get('Name', 'Unknown')}")      

        else:
            campaigns_lines = ['No active campaigns']

        campaigns_text.config(state='normal')
        campaigns_text.delete('1.0', tk.END)
        campaigns_text.insert('1.0', ''.join(campaigns_lines))
        campaigns_text.config(state='disabled')

            # Update searches
        search_lines = []
        for sid, s in self.active_searches.items():
            search_lines.append(f"Search {sid[:8]}... | Keywords: {s.get('keywords', 'N/A')} | Progress: {s.get('invites_sent', 0)}/{s.get('max_invites', 0)} | Status: {s.get('status', 'unknown')}")
        if not search_lines:
            search_lines = ['No active searches']

        searches_text.config(state='normal')
        searches_text.delete('1.0', tk.END)
        searches_text.insert('1.0', ''.join(search_lines))
        searches_text.config(state='disabled')

        collection_lines = []
        if self.active_collections:
            for cid, status in self.active_collections.items():
                collection_lines.append(f"Collection {cid[:8]}...")
                collection_lines.append(f"  Status: {status.get('status', 'unknown')}")
                collection_lines.append(f"  Progress: {status.get('progress', 0)}/{status.get('total', 0)}")
                collection_lines.append(f"  URL: {status.get('url', 'N/A')[:50]}...")
        else:
            collection_lines = ['No active collections']

        collections_text.config(state='normal')
        collections_text.delete('1.0', tk.END)
        collections_text.insert('1.0', '\n'.join(collection_lines))
        collections_text.config(state='disabled')

    def periodic_update():
        if not self.running:
            try:
                root.destroy()
            except:
                pass
            return
        do_refresh()
        root.after(3000, periodic_update)

    ttk.Button(btn_frame, text='Refresh', command=do_refresh).grid(row=0, column=0, padx=6)
    ttk.Button(btn_frame, text='Stop Client', command=stop_client).grid(row=0, column=1, padx=6)

        # Start periodic updates and enter mainloop
    root.after(100, periodic_update)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass

    try:
        root.destroy()
    except:
        pass