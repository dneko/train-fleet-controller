"""Desktop fleet setup, controls, and a live schematic loop map."""
import json
import copy
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path
import shutil
import uuid
from ui_helpers import Tooltip, ThrottleScale, rounded_button_styles
from map_editing import insert_checkpoint
from fleet_automation import Supervisor, DEFAULT_MAX_SPEED, DEFAULT_TARGET_SPEED
from block_style import block_name, block_style
from fleet_model import all_trains, load_config, is_external
from train_controllers import connection_identity, controller_label, history_identity, supports
from configuration_files import save_configuration
from train_photos import load_train_photo, save_train_photo
from train_alarm import TrainAlarm
from alarm_audio import AlarmSound, ALARM_CHOICES
from alarm_locations import alarm_locations
from train_speed import card_speed, SpeedWarnings
from train_settings import can_apply_live, merge_train_edits, update_train_runtime
from map_geometry import (path_for, interpolate, nearest_leg, geometry, control_path,
                          calibration_scale, update_distances, STUD_M, track_heading, oriented_marker)
from fleet_setup import SetupDialog, save_config
from loop_tracking import LoopTracker, motor_speed
from reader_server import create_server
from reader_discovery import ReceiverDiscovery
from reader_secret import load_reader_token, SecretStoreError
from sensor_registry import SensorRegistry
from sensor_ui import SensorDialog
from hub_program_ui import HubProgramDialog
from battery_history import BatteryHistory
from battery_history_ui import BatteryHistoryDialog
from power_profile import PowerAlert, PowerProfile, PowerSetpoints, SETTLE_SECONDS

COLORS = ['#2563eb', '#9333ea', '#d97706', '#08916c', '#db2777', '#475569']
CARD_BACKGROUNDS = ['#dbeafe', '#f3e8ff', '#fef3c7', '#d1fae5', '#fce7f3', '#e2e8f0']
CARD_TITLES = ['#1d4ed8', '#7e22ce', '#92400e', '#065f46', '#be185d', '#334155']
OFF_TRACK_BACKGROUND = '#374151'
VIEW_OPTIONS = (
    ('distances', 'Block distances', False), ('radii', 'Curve radii', False),
    ('sensor_names', 'Checkpoint names', True), ('sensor_status', 'Sensor status labels', False),
    ('edit_handles', 'Track editing points', False), ('speed_details', 'Checkpoint read age', True),
    ('map_help', 'Map editing help', False), ('activity_log', 'Activity log', False),
)


class FleetApp:
    def __init__(self, root, config, path, demo, backend_class, motor_only=False):
        self.root, self.config, self.path, self.demo = root, config, path, demo
        self.motor_only = motor_only
        self.configure_theme()
        self.events = queue.Queue(maxsize=2000)
        self.backend = backend_class(config, self.events, demo)
        self.server = None
        self.discovery = None
        self.setup_dialog = None
        self.sensor_dialog = None
        self.program_dialog = None
        self.program_uploads = set()
        self.program_results = {}
        self.program_debug = []
        self.battery_history_dialog = None
        self.battery_histories = {}
        self.battery_recording = False
        self.battery_recording_start = None
        self.battery_recording_end = time.monotonic()
        self.power_profiles = {}
        self.power_alerts_enabled = True
        self.power_alert_dialogs = {}
        self.sensors = SensorRegistry(config)
        self.allowed_readers = {r['id'] for r in config['readers']}
        self.applying = False
        self.closed = False
        self.alarm_sound = AlarmSound(self.root.bell, config.get('alerts', {}).get('sound', 'siren'))
        self.alarm = TrainAlarm(self.alarm_sound.play, stop=self.alarm_sound.stop)
        self.alarm_title_active = False
        self.last_history_save = 0.
        self.demo_running = False
        self.demo_sequence = 0
        self.demo_positions = {}
        self.root.title('LEGO Train Fleet' + (' — DEMO' if demo else ''))
        self.root.geometry('940x600' if motor_only else '1320x900')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        # Run card bindings before widget defaults, especially Scale's arrow keys.
        self.root.bind_class('FleetTrainCard', '<Button-1>', self.select_train_card)
        for key, step in (('Up', 1), ('Right', 1), ('Down', -1), ('Left', -1)):
            self.root.bind_class('FleetTrainCard', f'<{key}>',
                                 lambda event, delta=step: self.keyboard_throttle(event, delta))
        self.build()
        self.set_battery_recording(self.config.get('battery_recording', False), persist=False)
        if not demo:
            self.start_receiver()
        self.root.after(100, self.poll)

    def configure_theme(self):
        # Use a consistent light theme so black text stays legible in macOS dark mode.
        self.root.configure(background='#f8fafc')
        for option,value in (('*foreground','black'),('*background','#f8fafc'),
                             ('*insertBackground','black'),('*selectForeground','black'),
                             ('*selectBackground','#cbd5e1'),('*disabledForeground','black')):
            self.root.option_add(option,value)
        style=ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.',foreground='black',background='#f8fafc',fieldbackground='white')
        for name in ('TLabel','TButton','TCheckbutton','TLabelframe.Label','TEntry',
                     'TCombobox','TNotebook.Tab'):
            style.configure(name,foreground='black')
            style.map(name,foreground=[('disabled','black'),('active','black'),('selected','black')])
        style.map('TCombobox',fieldbackground=[('readonly','white')],selectforeground=[('readonly','black')])
        style.configure('TButton', padding=(9,5), background='#e2e8f0', borderwidth=0)
        style.map('TButton', background=[('active','#cbd5e1')])
        style.configure('Title.TLabel', font=('Helvetica',16,'bold'), foreground='#0f172a')
        style.configure('Badge.TLabel', foreground='#2563eb', font=('Helvetica',10,'bold'))
        style.configure('Muted.TLabel', foreground='#64748b', font=('Helvetica',10))
        style.configure('Section.TLabel', foreground='#64748b', font=('Helvetica',10,'bold'))
        style.configure('Card.TFrame', background='white')
        style.configure('CardTitle.TLabel', background='white', font=('Helvetica',11,'bold'))
        style.configure('CardSmall.TLabel', background='white', foreground='#475569', font=('Helvetica',10))
        style.configure('Card.TCheckbutton', background='white', font=('Helvetica',10))
        style.configure('Icon.TButton', padding=(4,5), font=('Helvetica',16), background='#eef2f7')
        style.configure('Stop.TButton', background='#fee2e2', foreground='#991b1b', font=('Helvetica',11,'bold'))
        style.configure('Force.TButton', font=('Helvetica',10,'bold'), padding=(6,3))
        style.map('Stop.TButton', background=[('active','#fecaca')],foreground=[('active','#991b1b')])
        icon_backgrounds = {f'TrainColor{i}': color for i, color in enumerate(CARD_BACKGROUNDS)}
        icon_backgrounds['OffTrack'] = OFF_TRACK_BACKGROUND
        icon_backgrounds['Waiting'] = '#fef3c7'
        icon_backgrounds['Ready'] = '#dcfce7'
        icon_backgrounds['Alarm'] = '#b91c1c'
        icon_backgrounds['Force'] = '#ffedd5'
        compact_layout = style.layout('TButton')
        self.button_images = rounded_button_styles(style, icon_backgrounds, icon_size=(32,28))
        style.layout('Emergency.TButton', compact_layout)
        style.configure('Emergency.TButton', background='#b91c1c', foreground='white',
                        font=('Helvetica',12,'bold'), padding=(14,9))
        style.map('Emergency.TButton', background=[('pressed','#7f1d1d'),('active','#991b1b')],
                  foreground=[('active','white'),('pressed','white')])
        for name, background, foreground in (('Compact.TButton', '#e2e8f0', '#0f172a'),
                ('Compact.Force.TButton', '#fed7aa', '#7c2d12'),
                ('Compact.Stop.TButton', '#fee2e2', '#991b1b')):
            style.layout(name, compact_layout)
            style.configure(name, padding=(4,1), font=('Helvetica',10),
                            background=background, foreground=foreground)
            style.map(name, background=[('active', background)], foreground=[('active', foreground)])
        style.configure('Auto.Toolbutton',font=('Helvetica',12,'bold'),padding=(7,3),
                        background='#e2e8f0',foreground='#0f172a')
        style.map('Auto.Toolbutton',background=[('selected','#bbf7d0'),('active','#dbeafe')],
                  foreground=[('selected','#14532d'),('disabled','#64748b')])

    def start_receiver(self):
        if self.motor_only:
            return
        try:
            token = load_reader_token()
        except SecretStoreError as exc:
            self.append(f'Wi-Fi receiver disabled: {exc} Enter the shared secret in the launcher or set TRAIN_READER_TOKEN.')
            return
        if len(token) < 16:
            self.append('Wi-Fi receiver disabled: enter the shared secret in the launcher or set TRAIN_READER_TOKEN (16+ characters) and restart. Train controls remain available.')
            return
        net = self.config.get('network', {})
        try:
            self.server = create_server(net.get('host', '0.0.0.0'), net.get('port', 8765), token,
                                        self.allowed_readers, self.events, self.sensors)
        except OSError as exc:
            self.append(f'Wi-Fi receiver unavailable: {exc}')
            return
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.append(f"Wi-Fi receiver listening on port {self.server.server_port}")
        self.discovery = ReceiverDiscovery(
            self.server.server_address[0], self.server.server_port,
            net.get('controller_id', 'train-fleet'),
            lambda text: self.events.put_nowait(('discovery', text)))

    def build(self):
        for child in self.root.winfo_children():
            if not isinstance(child, tk.Toplevel):
                child.destroy()
        self.tracker = LoopTracker(self.config)
        self.tracker.learning.persist = not self.demo
        self.supervisor = Supervisor(self.config, self.tracker, motor_only=self.motor_only)
        self.sync_battery_histories()
        self.alarm_sound.set_mode(self.config.get('alerts', {}).get('sound', 'siren'))
        self.supervisor.ready = {tid: self.demo for tid in self.supervisor.trains}
        self.auto_vars, self.service_vars, self.dispatch_labels = {}, {}, {}
        self.background = None
        self.background_source = None
        self.zoom = 1.0
        self.offset = [0., 0.]
        self.calibrating = False
        self.reference_points = []
        self.statuses, self.locations, self.sliders = {}, {}, {}
        self.active_train = None
        self.batteries, self.battery_labels = {}, {}
        self.train_titles = {}
        self.speed_warnings = SpeedWarnings()
        self.speed_warning_text = {}
        self.reason_labels = {}
        self.demo_positions = {}
        self.demo_initialized = False
        self.update_title()
        header = ttk.Frame(self.root, padding=(12, 10))
        header.pack(fill='x')
        # Reserve the emergency control's space before less critical header items.
        self.emergency_stop_button = ttk.Button(header, text='■  EMERGENCY STOP ALL',
                                                style='Emergency.TButton', command=self.stop_all)
        self.emergency_stop_button.pack(side='right', padx=(8,0))
        Tooltip(self.emergency_stop_button,
                'Immediately stop all fleet-controlled trains and cancel Auto, speed matching, and Force Run.'
                + (' Stop external trains with their own controller.' if self.supervisor.external else ''))
        ttk.Label(header, text='RAIL / CONTROL', style='Title.TLabel').pack(side='left', padx=(0,14))
        ttk.Label(header, text='MOTOR ONLY' if self.motor_only else 'DEMO' if self.demo else 'LIVE',
                  style='Badge.TLabel').pack(side='left', padx=(0,12))
        file_button = ttk.Menubutton(header, text='File ▾', width=0)
        file_menu = tk.Menu(file_button, tearoff=False)
        file_menu.add_command(label='Save configuration', command=self.save_map)
        file_menu.add_command(label='Save configuration as…', command=self.save_as)
        file_menu.add_command(label='Load configuration…', command=self.load_configuration)
        file_menu.add_separator()
        file_menu.add_command(label='Update hubs', command=self.update_hubs)
        file_menu.add_command(label='City Hub program…', command=self.show_hub_program)
        file_menu.add_command(label='Battery history…', command=self.show_battery_history)
        file_button.configure(menu=file_menu)
        file_button.pack(side='left')
        self.view_options = {key: tk.BooleanVar(value=self.config.get('view', {}).get(key, default))
                             for key, _, default in VIEW_OPTIONS}
        view_button = ttk.Menubutton(header, text='View ▾', width=0)
        view_menu = tk.Menu(view_button, tearoff=False)
        view_menu.add_command(label='Hide all extra information', command=lambda: self.set_view_details(False))
        view_menu.add_command(label='Show all details', command=lambda: self.set_view_details(True))
        view_menu.add_separator()
        for key, label, _ in VIEW_OPTIONS:
            view_menu.add_checkbutton(label=label, variable=self.view_options[key], command=self.update_view)
        view_button.configure(menu=view_menu)
        if not self.motor_only:
            view_button.pack(side='left', padx=4)
            ttk.Button(header, text='Fleet setup', width=0, command=self.setup).pack(side='left', padx=4)
            ttk.Button(header, text='Sensors', width=0, command=self.manage_sensors).pack(side='left')
        ttk.Button(header, text='App settings', width=0, command=self.app_settings).pack(side='left', padx=4)
        self.config_filename = tk.StringVar(value=Path(self.path).name)
        filename_label = ttk.Label(header, textvariable=self.config_filename, style='Muted.TLabel', width=20)
        filename_label.pack(side='left', padx=12)
        Tooltip(filename_label, self.config_filename.get)
        if self.demo and not self.motor_only:
            self.demo_button = ttk.Button(header, text='▶ Start demo', width=0, command=self.toggle_demo)
            self.demo_button.pack(side='right', padx=8)
            self.demo_badge = tk.Label(header, text='Ⅱ DEMO PAUSED', font=('Helvetica',11,'bold'),
                                       fg='#92400e', bg='#fef3c7', padx=10, pady=6)
            self.demo_badge.pack(side='right', padx=4)
        body = ttk.Panedwindow(self.root, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=(0,10))
        sidebar = ttk.Frame(body)
        map_panel = ttk.Frame(body)
        body.add(sidebar, weight=0)
        if not self.motor_only:
            body.add(map_panel, weight=1)
        else:
            ttk.Label(sidebar, text='Manual motor control · No position or block checks',
                      font=('Helvetica',12,'bold')).pack(anchor='w',padx=10,pady=10)
        self.auto_readiness = tk.StringVar()
        self.auto_readiness_label = tk.Label(map_panel, textvariable=self.auto_readiness,
            font=('Helvetica',11,'bold'), justify='left', anchor='w', padx=10, pady=8,
            bg='#fef3c7', fg='#78350f', wraplength=780)
        self.auto_readiness_label.pack(fill='x',padx=10,pady=(0,4))
        self.auto_readiness_label.bind('<Configure>',lambda e:self.auto_readiness_label.configure(wraplength=max(120,e.width-20)))
        pace_bar = ttk.Frame(map_panel, padding=(10,4))
        pace_bar.pack(fill='x')
        self.match_external = tk.BooleanVar(value=False)
        self.pace_choices = {f"{t['name']} ({t['id']})": t['id'] for t in self.config['trains'] if is_external(t)}
        self.match_button = ttk.Checkbutton(pace_bar, text='Match external speed', variable=self.match_external,
                                           command=self.set_follow_mode,
                                           state='normal' if self.pace_choices else 'disabled')
        self.match_button.pack(side='left')
        self.pace_choice = ttk.Combobox(pace_bar, values=list(self.pace_choices), width=22,
                                       state='readonly' if self.pace_choices else 'disabled')
        self.pace_choice.pack(side='left', padx=6)
        saved_pace = self.config.get('automation', {}).get('pace_train_id')
        selected = next((label for label, tid in self.pace_choices.items() if tid == saved_pace),
                        next(iter(self.pace_choices), ''))
        self.pace_choice.set(selected)
        self.pace_choice.bind('<<ComboboxSelected>>', self.change_pace_train)
        ttk.Button(pace_bar, text='Auto timing…', command=self.timing_settings).pack(side='left', padx=4)
        self.pace_status = tk.StringVar()
        ttk.Label(map_panel, textvariable=self.pace_status, wraplength=780).pack(anchor='w', padx=12)
        self.alarm_panel = tk.Frame(map_panel, bg='#b91c1c', padx=12, pady=10,
                                    highlightthickness=3, highlightbackground='#7f1d1d')
        self.alarm_text = tk.StringVar()
        self.alarm_heading = tk.Label(self.alarm_panel,text='TRAIN ALARM',font=('Helvetica',20,'bold'),
                                      bg='#b91c1c',fg='white',anchor='w')
        self.alarm_heading.grid(row=0,column=0,sticky='ew')
        self.alarm_label = tk.Label(self.alarm_panel, textvariable=self.alarm_text, fg='white',bg='#b91c1c',
                                    font=('Helvetica',12,'bold'),wraplength=600,justify='left',anchor='w')
        self.alarm_label.grid(row=1,column=0,sticky='ew')
        self.alarm_panel.columnconfigure(0,weight=1)
        self.alarm_label.bind('<Configure>',lambda e:self.alarm_label.configure(wraplength=max(100,e.width-4)))
        ttk.Button(self.alarm_panel, text='Silence alarm', command=self.silence_alarm).grid(row=0,column=1,rowspan=2,padx=(12,0))
        auto_bar = ttk.Frame(sidebar, padding=(8,4))
        if not self.motor_only:
            auto_bar.pack(fill='x')
        self.auto_enabled = tk.BooleanVar(value=False)
        self.auto_master = ttk.Checkbutton(auto_bar, text='Auto fleet →', variable=self.auto_enabled,
                        style='Auto.Toolbutton', command=self.enable_auto)
        self.auto_master.grid(row=0,column=0,sticky='w')
        ttk.Label(auto_bar, text='Target · studs/s', style='Muted.TLabel').grid(row=1,column=0,sticky='w')
        automation = self.config.get('automation', {})
        maximum = automation.get('max_speed_studs_s', DEFAULT_MAX_SPEED)
        self.target_value = tk.DoubleVar()
        self.target_speed = tk.Scale(auto_bar, from_=maximum, to=0, resolution=.5, orient='vertical',
                                    length=65, width=9, sliderlength=16, highlightthickness=0,
                                    variable=self.target_value, command=self.set_target)
        self.target_speed.grid(row=0,column=1,rowspan=2,padx=(8,0))
        Tooltip(self.target_speed, 'Forward-only automatic target, in studs per second')
        target = min(maximum, automation.get('target_speed_studs_s', DEFAULT_TARGET_SPEED))
        self.target_value.set(target)
        self.set_target(target)
        self.target_speed.bind('<ButtonRelease-1>', lambda _: self.save_map())
        ttk.Label(auto_bar, text='Auto max · studs/s', style='Muted.TLabel').grid(row=0,column=2,sticky='w',padx=(8,0))
        self.auto_max = tk.StringVar(value=f'{maximum:g}')
        maximum_controls = ttk.Frame(auto_bar)
        maximum_controls.grid(row=1,column=2,sticky='w',padx=(8,0))
        maximum_entry = ttk.Entry(maximum_controls, textvariable=self.auto_max, width=6)
        maximum_entry.pack(side='left')
        Tooltip(maximum_entry, 'Maximum estimated auto speed. Each train learns its load after two checkpoints; tune its initial starting throttle in Fleet setup.')
        ttk.Button(maximum_controls, text='Set', width=4, command=self.set_auto_max).pack(side='left',padx=(4,0))
        roster_bar = ttk.Frame(sidebar)
        roster_bar.pack(fill='x', padx=10, pady=(8,4))
        ttk.Label(roster_bar, text=f"ACTIVE TRAINS  /  {len(self.config['trains'])}", style='Section.TLabel').pack(side='left')
        ttk.Button(roster_bar, text='Train library…', style='Compact.TButton',
                   command=lambda: self.setup(library_only=True)).pack(side='right')
        self.keyboard_hint = tk.StringVar(value='Click a train card · ↑/→ +1 · ↓/← −1')
        ttk.Label(sidebar, textvariable=self.keyboard_hint, style='Muted.TLabel').pack(anchor='w',padx=10)
        self.control_cards = {}
        self.auto_checks = {}
        self.card_locations = {}
        self.card_speeds, self.card_speed_details, self.speed_labels = {}, {}, {}
        self.card_buttons = {}
        self.resume_buttons = {}
        self.force_buttons = {}
        self.card_on_track = {}
        self.photo_buttons, self.train_photo_images = {}, {}
        self.control_area = ttk.Frame(sidebar)
        cards = self.control_area
        cards.pack(fill='x')
        # Only configured trains get cards. Additional columns keep larger rosters visible.
        for index, train in enumerate(self.config['trains']):
            tid = train['id']
            prefix = f'Train{index}'
            card = ttk.Frame(cards, padding=(8,6), width=380, height=170, style=f'{prefix}.ControlCard.TFrame')
            rows = 2 if self.motor_only else 4
            card.grid(row=index % rows, column=index // rows, sticky='ew', padx=4, pady=4)
            card.grid_propagate(False)
            self.control_cards[tid] = card
            card.columnconfigure(0, weight=1)
            title_row = ttk.Frame(card, style=f'{prefix}.Card.TFrame')
            title_row.grid(row=0,column=0,sticky='ew')
            title_row.columnconfigure(0, weight=1)
            title = ttk.Label(title_row, text='●  '+train['name'], style=f'{prefix}.CardTitle.TLabel',
                              width=12)
            title.grid(row=0,column=0,sticky='ew')
            self.train_titles[tid] = title
            Tooltip(title, lambda t=tid:self.supervisor.trains[t]['name'])
            ttk.Button(title_row, text='Settings', width=7, style='Compact.TButton',
                       command=lambda t=tid:self.setup(t)).grid(row=0,column=1,sticky='e')
            self.batteries[tid] = None
            battery_label = ttk.Label(card, style=f'{prefix}.CardSmall.TLabel', anchor='w')
            battery_label.grid(row=1,column=0,sticky='ew')
            self.battery_labels[tid] = battery_label
            Tooltip(battery_label, lambda t=tid:self.battery_details(t))
            if not is_external(train):
                battery_label.configure(cursor='hand2')
                battery_label.bind('<Button-1>', lambda _, t=tid:self.show_battery_history(t))
            self.statuses[tid] = tk.StringVar(value='External control · RFID tracking' if is_external(train)
                                            else 'Demo' if self.demo else 'Disconnected')
            self.locations[tid] = tk.StringVar(value='Block unknown — awaiting first detection')
            self.dispatch_labels[tid] = tk.StringVar()
            self.card_locations[tid] = tk.StringVar(value='Awaiting checkpoint')
            self.card_speeds[tid] = tk.StringVar(value='— studs/s · no reads')
            self.card_speed_details[tid] = ''
            info = ttk.Frame(card, style=f'{prefix}.Card.TFrame')
            info.grid(row=2,column=0,rowspan=3,sticky='ew')
            info.columnconfigure(1, weight=1)
            photo_slot = ttk.Frame(info, width=80, height=52, style=f'{prefix}.Card.TFrame')
            photo_slot.grid(row=0,column=0,rowspan=3,padx=(0,8))
            photo_button = tk.Button(photo_slot, text='Add\nphoto', font=('Helvetica',9),
                                     borderwidth=0, highlightthickness=1, relief='flat',
                                     cursor='hand2', command=lambda t=tid:self.edit_train_photo(t))
            photo_button.place(x=0,y=0,relwidth=1,relheight=1)
            self.photo_buttons[tid] = photo_button
            self.refresh_train_photo(tid)
            Tooltip(photo_button, 'Add, change, or remove this train’s photo')
            for row,variable in ((1,self.card_speeds[tid]), (2,self.locations[tid])):
                # A single clipped line keeps card size stable; hover shows full details.
                label = ttk.Label(info, textvariable=self.card_locations[tid] if row == 2 else variable,
                                  style=f'{prefix}.CardSpeed.TLabel' if row == 1 else f'{prefix}.CardSmall.TLabel',
                                  width=27, anchor='w')
                height = 21 if row == 1 else 17
                slot = ttk.Frame(info, height=height, style=f'{prefix}.Card.TFrame')
                slot.grid(row=row-1,column=1,sticky='ew')
                slot.grid_propagate(False)
                label.place(in_=slot,x=0,y=0,relwidth=1,height=height)
                if row == 1:
                    self.speed_labels[tid] = label
                    Tooltip(label,lambda t=tid:self.card_speed_details[t]+'\n'+self.statuses[t].get())
                else:
                    Tooltip(label, variable.get)
            reason_slot = ttk.Frame(card, height=30, style=f'{prefix}.Card.TFrame')
            reason_slot.grid(row=6,column=0,sticky='ew')
            reason_slot.grid_propagate(False)
            reason_slot.columnconfigure(0, weight=1)
            reason_slot.rowconfigure(0, weight=1)
            reason_label = ttk.Label(reason_slot,textvariable=self.dispatch_labels[tid],
                                     style=f'{prefix}.CardReason.TLabel',font=('Helvetica',10,'bold'),
                                     wraplength=320,anchor='w',justify='left')
            reason_label.grid(row=0,column=0,sticky='nsew')
            self.reason_labels[tid] = reason_label
            reason_label.bind('<Configure>',lambda e,l=reason_label:l.configure(wraplength=max(80,e.width-4)))
            Tooltip(reason_label,lambda t=tid: '\n'.join(filter(None, (
                self.dispatch_labels[t].get(), self.speed_warning_text.get(t),
                self.supervisor.automatic_status(t), self.statuses[t].get()))))
            resume_button = ttk.Button(reason_slot, text='Resume', width=7, style='Compact.TButton',
                                       command=lambda t=tid:self.resume(t))
            resume_button.grid(row=0,column=1,padx=(4,0),sticky='e')
            resume_button.grid_remove()
            self.resume_buttons[tid] = resume_button
            toggles = ttk.Frame(info, style=f'{prefix}.Card.TFrame')
            toggles.grid(row=2,column=1,sticky='w')
            self.auto_vars[tid] = tk.BooleanVar(value=False)
            self.service_vars[tid] = tk.BooleanVar(value=self.supervisor.service[tid])
            auto_check = ttk.Checkbutton(toggles,text='AUTO',style='Auto.Toolbutton',width=8,variable=self.auto_vars[tid],
                                        command=lambda t=tid:self.set_auto(t))
            if not self.motor_only:
                auto_check.pack(side='left',padx=(0,6))
            self.auto_checks[tid] = auto_check
            ttk.Checkbutton(toggles,text='In service',style=f'{prefix}.Card.TCheckbutton',variable=self.service_vars[tid],
                            command=lambda t=tid:self.set_service(t)).pack(side='left')
            actions = ttk.Frame(card, style=f'{prefix}.Card.TFrame')
            actions.grid(row=5,column=0,sticky='w',pady=(2,2))
            self.card_buttons[tid] = []
            for icon, button_style, hint, action in (
                ('↻', 'Connect.Icon.TButton', 'Connect / retry controller', lambda t=tid: None if self.applying else self.backend.connect(t)),
                ('■', 'Stop.Icon.TButton', 'Stop this train', lambda t=tid:self.stop(t)),
                ('⌖', 'Position.Icon.TButton', 'Set initial checkpoint position', lambda t=tid:self.place_train(t)),
                ('↗', 'Remove.Icon.TButton', 'Mark train physically removed from the track', lambda t=tid:self.off_track(t)),
                ('i', 'Details.Icon.TButton', 'Show train status and tracking details', lambda t=tid:self.train_details(t))):
                if self.motor_only and button_style in ('Position.Icon.TButton', 'Remove.Icon.TButton'):
                    continue
                button_slot = ttk.Frame(actions, width=32, height=28, style=f'{prefix}.Card.TFrame')
                button_slot.pack(side='left', padx=(0,4))
                button_slot.pack_propagate(False)
                button = ttk.Button(button_slot,text=icon,style=button_style,command=action)
                if is_external(train) and button_style in ('Connect.Icon.TButton', 'Stop.Icon.TButton'):
                    button.configure(state='disabled')
                self.card_buttons[tid].append((button, button_style))
                button.pack(fill='both', expand=True)
                Tooltip(button,hint)
            force_button = ttk.Button(actions,text='Force run',width=8,style='Compact.Force.TButton',
                                      command=lambda t=tid:self.toggle_force(t))
            if not self.motor_only:
                force_button.pack(side='left',padx=(4,0))
            self.force_buttons[tid] = force_button
            Tooltip(force_button,lambda t=tid: ('End Force: stop this train and restore automatic protection.'
                    if t in self.supervisor.forced else
                    f'Force Run at {self.supervisor.force_value(t):+d}%: bypass block, speed and tracking holds. Stop or End force cancels it.'))
            slider = ThrottleScale(card,from_=100,to=-100,orient='vertical',length=146,resolution=1,
                              width=26,sliderlength=32,highlightthickness=0,bg='white',font=('Helvetica',12,'bold'),
                              activebackground='#2563eb',troughcolor='#e2e8f0',
                              command=lambda value,t=tid:self.command(t,int(value)))
            slider.grid(row=0,column=1,rowspan=7,sticky='ns',padx=(8,0))
            self.sliders[tid] = slider
            Tooltip(slider,'Throttle: + forward · 0 stop · − reverse\nSelect this card, then ↑/→ +1 or ↓/← −1.')
            pending = [card]
            while pending:
                widget = pending.pop()
                widget.bindtags(('FleetTrainCard',) + widget.bindtags())
                pending.extend(widget.winfo_children())
        if not self.config['trains']:
            ttk.Label(cards,text='No active trains.\nOpen Train library to add one.',style='Muted.TLabel').pack(padx=12,pady=20)
        if self.motor_only:
            self.log = tk.Text(sidebar, height=3, state='disabled')
            self.log.pack(fill='x',padx=10,pady=10)
            self.refresh_card_styles()
            self.refresh_batteries()
            self.refresh_run_status()
            return
        self.add_point_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(map_panel, text='Add points (A)',
                        variable=self.add_point_mode).pack(anchor='w', padx=12)
        map_bar = ttk.Frame(map_panel, padding=(0,4))
        map_bar.pack(fill='x', padx=12)
        for label, action in [('Image…', self.choose_background), ('Clear image', self.clear_background),
                              ('Calibrate', self.start_calibration),
                              ('Curve (C)', self.curve_selected),
                              ('Zoom +', lambda: self.zoom_map(1.25)),
                              ('Zoom −', lambda: self.zoom_map(.8)), ('Reset view', self.reset_view), ('Save map', self.save_map)]:
            ttk.Button(map_bar, text=label, command=action).pack(side='left')
        self.map_status = tk.StringVar()
        self.map_status_label = ttk.Label(map_panel, textvariable=self.map_status)
        self.map_status_label.pack(anchor='w', padx=12)
        self.map_help_label = ttk.Label(map_panel, text='A: point • S: sensor • Shift-click: select multiple • C: curve / flip curve • Drag empty map: pan • Esc: cancel')
        self.map_help_label.pack()
        self.curve_pending = None
        self.canvas = tk.Canvas(map_panel, width=850, height=600, bg='#edf2f7', highlightthickness=1, highlightbackground='#dbe3ed')
        self.canvas.pack(fill='both', expand=True, padx=10, pady=8)
        ttk.Label(map_panel, text='Colored blocks · thick = occupied · dashed = reserved · train positions are estimates').pack()
        self.log = tk.Text(map_panel, height=3, state='disabled')
        self.log.pack(fill='x', padx=10)
        self.dragging = None
        self.pan_last = None
        self.map_items = {}
        self.selected_points = []
        self.add_point_once = False
        self.add_sensor_once = False
        self.canvas.bind('<Button-1>', self.pick)
        modifier = 'Option' if self.root.tk.call('tk', 'windowingsystem') == 'aqua' else 'Alt'
        self.canvas.bind(f'<{modifier}-Button-1>', self.add_waypoint)
        self.canvas.bind('<Button-3>', self.remove_waypoint)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', self.end_drag)
        self.canvas.bind('<Double-Button-1>', self.edit_curve)
        self.canvas.bind('<Shift-Button-1>', self.pick)
        self.canvas.bind('<KeyPress-a>', self.arm_point)
        self.canvas.bind('<KeyPress-A>', self.arm_point)
        self.canvas.bind('<KeyPress-s>', self.arm_sensor)
        self.canvas.bind('<KeyPress-S>', self.arm_sensor)
        self.canvas.bind('<KeyPress-c>', self.curve_selected)
        self.canvas.bind('<KeyPress-C>', self.curve_selected)
        self.canvas.bind('<MouseWheel>', lambda e: self.zoom_map(1.1 if e.delta > 0 else 1/1.1, e.x, e.y))
        self.canvas.bind('<Button-4>', lambda e: self.zoom_map(1.1, e.x, e.y))
        self.canvas.bind('<Button-5>', lambda e: self.zoom_map(1/1.1, e.x, e.y))
        self.root.bind('<Escape>', lambda e: self.cancel_placement())
        self.load_background()
        self.refresh_card_styles()
        self.refresh_batteries()
        self.refresh_run_status()
        self.update_view(save=False)
        self.draw()

    def train_for_widget(self, widget):
        while widget is not None:
            for tid, card in self.control_cards.items():
                if widget is card:
                    return tid
            widget = getattr(widget, 'master', None)
        return None

    def select_train_card(self, event):
        tid = self.train_for_widget(event.widget)
        if tid is None:
            return
        self.active_train = tid
        self.control_cards[tid].focus_set()
        self.keyboard_hint.set('External controller · use its own throttle' if tid in self.supervisor.external
                               else 'Selected train · ↑/→ +1 · ↓/← −1')
        self.refresh_card_styles()

    def keyboard_throttle(self, event, step):
        tid = self.active_train
        # Only card focus controls trains; entries, map tools, and dialogs keep their keys.
        if (tid is not None and self.train_for_widget(event.widget) == tid
                and not self.closed and not self.applying and self.root.grab_current() is None
                and str(self.sliders[tid]['state']) != 'disabled'):
            value = max(-100, min(100, self.supervisor.throttle[tid] + step))
            self.command(tid, value)
        return 'break'

    def refresh_train_photo(self, tid):
        from PIL import ImageTk
        train = self.supervisor.trains[tid]
        photo = None
        text = 'Add\nphoto'
        if train.get('photo'):
            try:
                photo = ImageTk.PhotoImage(load_train_photo(train['photo'], self.path, (76, 48)), master=self.root)
            except (OSError, ValueError):
                text = 'Photo\nunavailable'
        self.train_photo_images[tid] = photo
        self.photo_buttons[tid].configure(image=photo or '', text='' if photo else text)

    def edit_train_photo(self, tid):
        if not self.configuration_available():
            return
        from PIL import ImageTk
        train = self.supervisor.trains[tid]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Train photo — {train['name']}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        ttk.Label(dialog, text=train['name'], style='Title.TLabel').pack(padx=16,pady=(16,8))
        preview_slot = ttk.Frame(dialog, width=360, height=210)
        preview_slot.pack(padx=16)
        preview = ttk.Label(preview_slot, anchor='center', justify='center')
        preview.place(x=0,y=0,relwidth=1,relheight=1)
        ttk.Label(dialog, text='Choose a picture from your computer. Changes save automatically.',
                  wraplength=350, justify='center').pack(padx=16,pady=10)
        buttons = ttk.Frame(dialog)
        buttons.pack(padx=12,pady=(0,16))

        def refresh():
            preview.image = None
            text = 'No photo yet'
            if train.get('photo'):
                try:
                    preview.image = ImageTk.PhotoImage(
                        load_train_photo(train['photo'], self.path, (360, 210)), master=self.root)
                except (OSError, ValueError):
                    text = 'Photo unavailable\nChoose another picture to replace it.'
            preview.configure(image=preview.image or '', text='' if preview.image else text)
            remove.configure(state='normal' if train.get('photo') else 'disabled')

        def update(source=None):
            try:
                save_train_photo(self.path, self.config, tid, source)
            except (OSError, ValueError) as exc:
                messagebox.showerror('Cannot save train photo', str(exc), parent=dialog)
                return
            self.refresh_train_photo(tid)
            refresh()

        def choose():
            source = filedialog.askopenfilename(parent=dialog, title=f"Choose photo for {train['name']}",
                filetypes=[('Train photos', '*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff'),
                           ('All files', '*')])
            if source:
                update(source)

        ttk.Button(buttons, text='Choose photo…', command=choose).pack(side='left',padx=4)
        remove = ttk.Button(buttons, text='Remove photo', command=update)
        remove.pack(side='left',padx=4)
        ttk.Button(buttons, text='Done', command=dialog.destroy).pack(side='left',padx=4)
        refresh()
        dialog.grab_set()

    def refresh_card_styles(self):
        style = ttk.Style(self.root)
        changed = False
        for index, train in enumerate(self.config['trains']):
            tid = train['id']
            on_track = self.supervisor.on_track[tid]
            forced = tid in self.supervisor.forced
            alarmed = any(key[0] == tid for key in self.supervisor.alarms)
            held = on_track and self.supervisor.waiting[tid] and self.supervisor.throttle[tid] == 0
            clear = held and self.supervisor.can_resume(tid)
            pulse = (held or alarmed) and bool(int(time.monotonic() * 2) % 2)
            selected = tid == self.active_train
            appearance = (on_track, pulse, clear, alarmed, forced, selected)
            if self.card_on_track.get(tid) == appearance:
                continue
            self.card_on_track[tid] = appearance
            changed = True
            color_index = index % len(COLORS)
            prefix = f'Train{index}'
            background = CARD_BACKGROUNDS[color_index] if on_track else OFF_TRACK_BACKGROUND
            if forced:
                background = '#ffedd5'
            if pulse:
                background = '#b91c1c' if alarmed else '#dcfce7' if clear else '#fef3c7'
            foreground = '#0f172a' if on_track else '#f8fafc'
            muted = '#475569' if on_track else '#e2e8f0'
            warning = '#92400e' if on_track else '#fde68a'
            if clear:
                warning = '#166534'
            title = CARD_TITLES[color_index] if on_track else '#f8fafc'
            if forced:
                foreground = title = '#7c2d12'
                muted = warning = '#9a3412'
            if pulse and alarmed:
                foreground = muted = title = 'white'
                warning = '#fef08a'
            style.configure(f'{prefix}.Card.TFrame', background=background)
            style.configure(f'{prefix}.ControlCard.TFrame', background=background, borderwidth=2,
                            relief='solid', bordercolor='#2563eb' if selected else background)
            self.photo_buttons[tid].configure(bg=background, fg=foreground,
                                              activebackground=background, activeforeground=foreground,
                                              highlightbackground=muted, highlightcolor=title)
            for suffix, color in (('CardTitle.TLabel', title), ('CardSmall.TLabel', muted), ('CardSpeed.TLabel', foreground),
                                  ('CardReason.TLabel', warning), ('Card.TCheckbutton', foreground)):
                name = f'{prefix}.{suffix}'
                style.configure(name, background=background, foreground=color)
                style.map(name, background=[('active', background), ('disabled', background)],
                          foreground=[('disabled', color), ('active', color), ('selected', color)])
            style.configure(f'{prefix}.CardSpeed.TLabel',font=('Helvetica',12,'bold'))
            self.sliders[tid].configure(bg=background, fg=foreground,
                                       activebackground=COLORS[color_index] if on_track else '#64748b',
                                       troughcolor='#cbd5e1' if on_track else '#64748b')
            button_prefix = f'TrainColor{color_index}' if on_track else 'OffTrack'
            if forced:
                button_prefix = 'Force'
            if pulse:
                button_prefix = 'Alarm' if alarmed else 'Ready' if clear else 'Waiting'
            for button, base_style in self.card_buttons[tid]:
                button.configure(style=f'{button_prefix}.{base_style}')
        if changed:
            self.refresh_batteries()

    def battery_history_for(self, tid, create=False):
        train = self.supervisor.trains.get(tid)
        if train is None or not supports(train, 'power_telemetry'):
            return None
        key = history_identity(train)
        if create and key not in self.battery_histories:
            self.battery_histories[key] = BatteryHistory()
        return self.battery_histories.get(key)

    def power_profile_for(self, tid, create=False):
        train = self.supervisor.trains.get(tid)
        if train is None or not supports(train, 'power_telemetry'):
            return None
        key = history_identity(train)
        if create and key not in self.power_profiles:
            self.power_profiles[key] = PowerProfile(self.power_setpoints_for(tid))
        profile = self.power_profiles.get(key)
        if profile is not None:
            profile.configure(self.power_setpoints_for(tid))
            if self.power_alarm_for(tid) is not None:
                profile.latched = True
        return profile

    def power_setpoints_for(self, tid):
        train = self.supervisor.trains.get(tid, {})
        return PowerSetpoints.from_config(train.get('power_setpoints'))

    def set_power_setpoints(self, tid, values):
        settings = PowerSetpoints.from_config(values)
        train = self.supervisor.trains.get(tid)
        if train is None or is_external(train):
            raise ValueError('Select an active fleet-controlled train')
        edited = copy.deepcopy(self.config)
        target = next(t for t in edited['trains'] if t['id'] == tid)
        target['power_setpoints'] = settings.to_config()
        self.apply_setup(edited)
        self.power_profile_for(tid)  # Update existing limits without discarding learning.

    def power_alarm_for(self, tid):
        train = next((t for t in all_trains(self.config) if t['id'] == tid), None)
        return PowerAlert.from_config(train['power_alarm']) if train and 'power_alarm' in train else None

    def clear_power_alarm(self, tid):
        train = next((t for t in all_trains(self.config) if t['id'] == tid), None)
        if train is None:
            return False
        if 'power_alarm' in train:
            edited = copy.deepcopy(self.config)
            next(t for t in all_trains(edited) if t['id'] == tid).pop('power_alarm', None)
            try:
                save_config(self.path, edited)
            except (OSError, ValueError) as exc:
                messagebox.showerror('Cannot clear load alarm', f'The alarm remains latched because it could not be saved:\n{exc}',
                                     parent=self.root)
                return False
            train.pop('power_alarm', None)
        # Also clear any retained profile for this train's earlier hub/polarity.
        for key, profile in self.power_profiles.items():
            if key[0] == tid:
                profile.clear_alarm()
        dialog = self.power_alert_dialogs.pop(tid, None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        self.refresh_batteries()
        self.append(f"Load alarm manually cleared — {train['name']}.")
        return True

    def sync_battery_histories(self):
        keys = {history_identity(t) for t in all_trains(self.config) if supports(t, 'power_telemetry')}
        self.battery_histories = {key: history for key, history in self.battery_histories.items() if key in keys}
        self.power_profiles = {key: profile for key, profile in self.power_profiles.items() if key in keys}
        for profile in self.power_profiles.values():
            profile.break_observation()
        if self.battery_recording:
            now = time.monotonic()
            for tid in self.supervisor.trains:
                history = self.battery_history_for(tid, create=True)
                if history is not None:
                    history.gap(now)
                    history.command('demand', self.supervisor.throttle[tid], now)

    def set_battery_recording(self, enabled, *, persist=True):
        # Demo never records hardware data or overwrites the saved Live choice.
        if self.demo:
            return
        enabled = bool(enabled)
        if persist:
            if self.applying:
                raise ValueError('Wait for the configuration change to finish before changing recording')
            if self.config.get('battery_recording') is not enabled:
                edited = copy.deepcopy(self.config)
                edited['battery_recording'] = enabled
                save_config(self.path, edited)
                self.config['battery_recording'] = enabled
        if enabled == self.battery_recording:
            return
        now = time.monotonic()
        self.battery_recording = enabled
        self.battery_recording_end = now
        if enabled:
            self.battery_recording_start = now
            self.sync_battery_histories()
        else:
            for history in self.battery_histories.values():
                history.gap(now)
                history.command('demand', None, now)
            for profile in self.power_profiles.values():
                profile.break_observation()
        self.backend.record_history(enabled)

    def set_power_alerts(self, enabled):
        self.power_alerts_enabled = bool(enabled)
        for profile in self.power_profiles.values():
            profile.break_observation()

    def reset_battery_data(self, tid):
        train = self.supervisor.trains.get(tid)
        if train is None or is_external(train):
            return
        if not self.clear_power_alarm(tid):
            return
        key = history_identity(train)
        self.battery_histories.pop(key, None)
        self.power_profiles.pop(key, None)
        dialog = self.power_alert_dialogs.pop(tid, None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
        if self.battery_recording:
            now = time.monotonic()
            history = self.battery_history_for(tid, create=True)
            if history is not None:
                history.accept_after = now
                history.command('demand', self.supervisor.throttle[tid], now)
                self.backend.record_history(True)  # Seed the current sent command again.

    def check_power_profile(self, tid, history, at):
        rows = history.rows(at, at)
        if not rows:
            return
        row = rows[0]
        changes = history.steps('demand', at-SETTLE_SECONDS, at)
        demands = [value for _, value in changes]
        stable = bool(demands) and None not in demands and max(demands)-min(demands) <= 2
        profile = self.power_profile_for(tid, create=True)
        alert = profile.observe(row, stable_demand=stable, alerting=self.power_alerts_enabled)
        if alert is not None and self.power_alerts_enabled:
            self.show_power_alert(tid, alert)

    def show_power_alert(self, tid, alert=None):
        train = self.supervisor.trains[tid]
        saved_alarm = self.power_alarm_for(tid)
        if saved_alarm is not None:
            alert = saved_alarm
        elif alert is not None:
            train['power_alarm'] = alert.to_config()
            try:
                save_config(self.path, copy.deepcopy(self.config))
            except (OSError, ValueError) as exc:
                self.append(f'Could not save load alarm; it remains latched in this session: {exc}')
            self.append(f"{alert.label} — {train['name']}: {alert.formatted(alert.measured)}, "
                        f"{alert.direction} than expected {alert.formatted(alert.expected)}.")
        else:
            return
        profile = self.power_profile_for(tid)
        if profile is not None:
            profile.latched = True
        text = (f"LATCHED: {train['name']} drew {alert.formatted(alert.measured)} — {alert.direction} than expected "
                f"at {alert.demand:+g}% demand.\n\n"
                f'Learned expectation: {alert.formatted(alert.expected)}; '
                f'alarm limits {alert.formatted(alert.lower)}–{alert.formatted(alert.upper)}.\n'
                f'Triggered after {alert.confirm_samples} consecutive battery reading(s).\n\n'
                + ('This saved alarm used watts before current-based monitoring. It remains latched until you clear it.\n\n'
                   if alert.metric == 'watts' else '')
                + ('Extra load could mean dragging wheels, a derailment, pushing another train, or a changed consist.'
                   if alert.direction == 'higher' else
                   'Reduced load could mean wheels off the track, slipping, a disconnected motor, or a lighter consist.')
                + '\n\nInspect the train, then use Clear alarm. Normal readings and changed settings do not clear it. '
                  'This alarm does not change throttle. Closing this window only hides it.')
        existing = self.power_alert_dialogs.get(tid)
        if existing is not None and existing.winfo_exists():
            existing.advisory.set(text)
            existing.lift()
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Latched {alert.label.lower()} — {train['name']}")
        dialog.transient(self.root)
        dialog.advisory = tk.StringVar(value=text)
        ttk.Label(dialog, text='ⓘ  Unexpected current draw' if alert.metric == 'amps' else 'ⓘ  Legacy power alarm',
                  font=('Helvetica', 14, 'bold')).pack(anchor='w', padx=18, pady=(16, 8))
        ttk.Label(dialog, textvariable=dialog.advisory, wraplength=530, justify='left').pack(padx=18, pady=8)
        actions = ttk.Frame(dialog, padding=12)
        actions.pack(fill='x')
        ttk.Button(actions, text='View history', command=lambda: self.show_battery_history(tid)).pack(side='left')
        ttk.Button(actions, text='Reset train data', command=lambda: self.reset_battery_data(tid)).pack(side='left', padx=8)
        ttk.Button(actions, text='Clear alarm', command=lambda: self.clear_power_alarm(tid)).pack(side='right')
        self.power_alert_dialogs[tid] = dialog
        self.refresh_batteries()

    def show_battery_history(self, tid=None):
        dialog = self.battery_history_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.select_train(tid)
        else:
            self.battery_history_dialog = BatteryHistoryDialog(self.root, self, tid)

    def battery_details(self, tid):
        alarm = self.power_alarm_for(tid)
        if alarm is not None:
            return (f'{alarm.label} LATCHED: {alarm.formatted(alarm.measured)}, expected {alarm.formatted(alarm.expected)}.\n'
                    'Click to view the alarm and setpoints. It remains active until you explicitly clear it.')
        if tid in self.supervisor.external:
            return 'Externally controlled train: RFID tracking only. Use its own controller to change speed or stop.'
        if not supports(self.supervisor.trains[tid], 'power_telemetry'):
            return 'This controller does not provide voltage/current telemetry.'
        if self.demo:
            return 'Battery readings are unavailable in demo mode.'
        reading = self.batteries[tid]
        if reading is None and not supports(self.supervisor.trains[tid], 'city_hub_program'):
            return 'Power: no reading. Connect the controller to receive telemetry. Click to open history.'
        if reading is None:
            return ('Battery: no reading. Connect the hub and run the updated city_hub.py program.\n'
                    'Readings arrive every two seconds while the program is running.\nClick to open battery history.')
        return reading.details() + '\nClick to open battery history and optional recording.'

    def refresh_batteries(self):
        now = time.monotonic()
        for tid, label in self.battery_labels.items():
            reading = self.batteries[tid]
            text = ('External control' if tid in self.supervisor.external else
                    'Power telemetry unavailable' if not supports(self.supervisor.trains[tid], 'power_telemetry') else
                    'Battery: demo' if self.demo else reading.card_text(now) if reading else 'Battery —')
            off_track = not self.supervisor.on_track[tid] and tid not in self.supervisor.forced
            stale = not self.demo and reading and reading.stale(now)
            color = ('#fde68a' if off_track else '#92400e') if stale else ('#e2e8f0' if off_track else '#475569')
            level = reading.warning_level if reading and not self.demo and tid not in self.supervisor.external else ''
            background = ttk.Style(self.root).lookup(label['style'], 'background')
            if level:
                color, background = ('#991b1b', '#fee2e2') if level == 'critical' else ('#713f12', '#fef08a')
            alarm = self.power_alarm_for(tid)
            if alarm is not None:
                text = alarm.label + ' · ' + text
                color, background = '#991b1b', '#fee2e2'
            label.configure(text=text, foreground=color, background=background)

    def train_details(self, tid):
        train = next(t for t in self.config['trains'] if t['id'] == tid)
        if self.motor_only:
            messagebox.showinfo(train['name'],f"{self.statuses[tid].get()}\n\n{self.battery_details(tid)}\n\n"
                f"Motor: {self.supervisor.throttle[tid]:+d}% · polarity {train.get('forward_sign',1):+d}",parent=self.root)
            return
        history = [f"{block_name(self.config,key[1])} ({'forward' if key[2] > 0 else 'reverse'}): "
                   f"{sample['seconds']:.1f}s moving, {sample['samples']} samples"
                   for key,sample in self.tracker.learning.samples.items() if key[0] == tid]
        pending = self.tracker.clearance.pending.get(tid, [])
        clearance = ('\nWaiting for rear at: ' + ', '.join(self.tracker.readers[p['reader']] for p in pending)
                     if pending else '\nRear clearance confirmed' if train.get('rear_tag') else '')
        messagebox.showinfo(train['name'],
            f"{self.statuses[tid].get()}\n\n{self.battery_details(tid)}\n\n{self.locations[tid].get()}\n\n"
            f"{self.dispatch_labels[tid].get()}\nMotor polarity: {train.get('forward_sign',1):+d}"
            + clearance + '\n\nLearned section times:\n' + ('\n'.join(history) or 'No completed sections yet'), parent=self.root)

    def timing_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('Adaptive cruise and timing')
        ttk.Label(dialog, text='Alarm at 2× expected travel time. Time stopped by the controller is excluded.',
                  wraplength=440).grid(row=0,column=0,columnspan=2,padx=12,pady=12)
        factor = tk.StringVar(value=f'{self.supervisor.stop_time_factor:g}')
        gap = tk.StringVar(value=f'{self.supervisor.following_seconds:g}')
        for row, label, var in ((1,'Power down at × expected time',factor),
                                (2,'Following gap (seconds)',gap)):
            ttk.Label(dialog,text=label).grid(row=row,column=0,sticky='w',padx=12,pady=6)
            ttk.Entry(dialog,textvariable=var,width=8).grid(row=row,column=1,padx=12)
        def apply():
            try:
                stop, headway = float(factor.get()), float(gap.get())
                if not (math.isfinite(stop) and 2.1 <= stop <= 10 and math.isfinite(headway) and 1 <= headway <= 30):
                    raise ValueError()
            except ValueError:
                messagebox.showerror('Invalid timing','Power-down factor: 2.1–10; following gap: 1–30 seconds.',parent=dialog)
                return
            self.supervisor.stop_time_factor, self.supervisor.following_seconds = stop, headway
            self.config.setdefault('automation',{}).update(stop_time_factor=stop,following_seconds=headway)
            self.save_map()
            dialog.destroy()
        def reset():
            self.stop_all()
            self.tracker.learning.clear()
            self.tracker.speed_per_throttle.clear()
            self.tracker.passages.clear()
            self.save_map()
            dialog.destroy()
        ttk.Button(dialog,text='Reset learned times and stop fleet',command=reset).grid(row=3,column=0,padx=12,pady=12)
        ttk.Button(dialog,text='Save',command=apply).grid(row=3,column=1,padx=12,pady=12)
        ttk.Button(dialog,text='Test alarm sound',command=self.alarm_sound.play).grid(row=4,column=0,padx=12,pady=8)

    def app_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('App settings')
        ttk.Label(dialog,text='Alarm sound',font=('Helvetica',12,'bold')).pack(anchor='w',padx=16,pady=(16,6))
        labels = {label: key for key, label in ALARM_CHOICES.items()}
        selection = ttk.Combobox(dialog,state='readonly',width=34,values=list(labels))
        selection.set(ALARM_CHOICES[self.alarm_sound.mode])
        selection.pack(padx=16,pady=6)
        ttk.Label(dialog,text='Visual alerts and map circles remain active with sound off.').pack(padx=16,pady=6)
        preview = AlarmSound(self.root.bell)
        def test_sound():
            preview.set_mode(labels[selection.get()])
            preview.play()
        def close():
            preview.stop()
            dialog.destroy()
        def save():
            try:
                self.set_alarm_sound(labels[selection.get()])
            except (ValueError, OSError) as exc:
                messagebox.showerror('Cannot save settings',str(exc),parent=dialog)
                return
            close()
        buttons = ttk.Frame(dialog)
        buttons.pack(padx=16,pady=16)
        ttk.Button(buttons,text='Preview',command=test_sound).pack(side='left',padx=4)
        ttk.Button(buttons,text='Save',command=save).pack(side='left',padx=4)
        ttk.Button(buttons,text='Cancel',command=close).pack(side='left',padx=4)
        if not self.motor_only:
            ttk.Button(dialog,text='Auto timing settings…',command=self.timing_settings).pack(pady=(0,12))
        dialog.protocol('WM_DELETE_WINDOW',close)
        dialog.bind('<Destroy>',lambda e:preview.stop() if e.widget is dialog else None)
        return dialog

    def set_alarm_sound(self, mode):
        updated = copy.deepcopy(self.config)
        updated.setdefault('alerts', {})['sound'] = mode
        save_config(self.path, updated)
        self.config['alerts'] = updated['alerts']
        self.alarm_sound.set_mode(mode)
        self.alarm.last_ring = float('-inf')
        if not self.motor_only:
            self.refresh_alarms()

    def silence_alarm(self):
        self.alarm.silence()
        self.refresh_alarms()

    def refresh_alarms(self):
        if self.closed:
            return
        incidents = self.supervisor.alarms
        self.alarm.update(incidents)
        if incidents:
            self.alarm_text.set('ALARM: ' + '\n'.join(dict.fromkeys(incidents.values())))
            audible = self.alarm_sound.mode != 'none' and bool(self.alarm.active-self.alarm.silenced)
            flash = audible and bool(int(time.monotonic()*2) % 2)
            background, foreground = ('#fef08a','#7f1d1d') if flash else ('#b91c1c','white')
            self.alarm_panel.configure(bg=background)
            for label in (self.alarm_heading,self.alarm_label):
                label.configure(bg=background,fg=foreground)
            self.alarm_heading.configure(text='TRAIN ALARM · SOUND OFF' if self.alarm_sound.mode == 'none' else
                                         'TRAIN ALARM' if audible else 'TRAIN ALARM · SOUND SILENCED')
            if not self.alarm_panel.winfo_manager():
                self.alarm_panel.pack(before=self.canvas, fill='x')
        else:
            self.alarm_panel.pack_forget()
        if bool(incidents) != self.alarm_title_active:
            self.alarm_title_active = bool(incidents)
            self.update_title()

    def refresh_speeds(self):
        for tid in self.supervisor.trains:
            text, details = card_speed(self.supervisor,tid)
            warning = self.speed_warnings.warning(self.supervisor, tid)
            self.speed_warning_text[tid] = warning
            if warning:
                text = '⚠ ' + text.split(' · ')[0] + ' · slow'
                details = warning + '\n' + details
            label = self.speed_labels[tid]
            style = ttk.Style(self.root)
            label.configure(background='#fef08a' if warning else style.lookup(label['style'], 'background'),
                            foreground='#713f12' if warning else style.lookup(label['style'], 'foreground'))
            self.card_speeds[tid].set(text)
            self.card_speed_details[tid] = details

    def save_learning(self, force=False):
        if self.motor_only or self.demo or self.applying or not self.tracker.learning.dirty:
            return
        now = time.monotonic()
        if not force and now-self.last_history_save < 5:
            return
        self.last_history_save = now
        try:
            saved = load_config(self.path)
            geometry = lambda config: [(r['id'],r.get('distance_to_next_m',1.)) for r in config['readers']]
            if geometry(saved) != geometry(self.config):
                return  # Wait for an explicit map save before persisting its new history.
            current = {t['id']:t for t in self.config['trains']}
            identity = ('hub_name','controller_type','controller_options','control_mode','tags','rear_tag','length_m','forward_sign')
            for train in saved['trains']:
                live = current.get(train['id'])
                if live is None or any(train.get(k) != live.get(k) for k in identity):
                    continue
                if 'section_history' in live:
                    train['section_history'] = live['section_history']
                else:
                    train.pop('section_history',None)
            save_config(self.path, saved)
            self.tracker.learning.dirty = False
        except (OSError, ValueError) as exc:
            self.append(f'Could not save learned section times: {exc}')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 200:
            self.log.delete('1.0', '2.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def send(self, tid, value):
        if tid in self.program_uploads and value != 0:
            self.supervisor.request(tid, 0)
            return
        if tid in self.supervisor.external:
            return
        train = next(t for t in self.config['trains'] if t['id'] == tid)
        if self.battery_recording:
            history = self.battery_history_for(tid, create=True)
            if history is not None:
                history.command('demand', value, time.monotonic())
        if not self.motor_only:
            self.tracker.set_command(tid, value)
        self.backend.speed(tid, motor_speed(train, value))
        self.backend.waiting(tid, self.supervisor.waiting[tid])
        self.sliders[tid].set(value)

    def command(self, tid, value):
        if self.applying or tid in self.program_uploads:
            return
        if tid in self.supervisor.forced:
            if value == 0:
                self.stop(tid)
            else:
                self.send(tid,self.supervisor.request(tid,value))
            self.refresh_run_status()
            return
        if tid in self.supervisor.external or self.supervisor.pace_train is not None:
            self.sliders[tid].set(self.supervisor.throttle[tid])
            return
        if self.applying or value == self.supervisor.throttle[tid]:
            return
        self.auto_vars[tid].set(False)
        self.supervisor.automatic[tid] = False
        # A fresh manual request replaces the throttle saved by an earlier hold.
        self.supervisor.held_throttle.pop(tid, None)
        previous_reason = self.supervisor.reasons.get(tid)
        actual = self.supervisor.request(tid, value)
        self.send(tid, actual)
        reason = self.supervisor.reasons.get(tid)
        if value != 0 and actual == 0 and reason and reason != previous_reason:
            self.append(f"{self.supervisor.trains[tid]['name']}: manual throttle held — {reason}")
        self.refresh_run_status()

    def toggle_force(self, tid):
        if self.motor_only or self.applying or tid in self.supervisor.external or tid in self.program_uploads:
            return
        if tid in self.supervisor.forced:
            value = self.supervisor.end_force(tid)
            message = 'Force Run ended; train stopped'
        else:
            value = self.supervisor.force_run(tid)
            message = (f'FORCE RUN {value:+d}%: automatic holds bypassed' if value else
                       self.supervisor.reasons.get(tid,'Cannot force run'))
        self.send(tid,value)
        self.refresh_run_status()
        self.append(f"{self.supervisor.trains[tid]['name']}: {message}")

    def stop(self, tid):
        if tid in self.supervisor.external:
            return
        if self.supervisor.pace_train is not None:
            self.supervisor.pause(tid)
            self.send(tid, 0)
            self.refresh_run_status()
            return
        self.supervisor.automatic[tid] = False
        self.auto_vars[tid].set(False)
        self.supervisor.request(tid, 0)
        self.send(tid, 0)
        self.refresh_run_status()

    def resume(self, tid):
        if (self.applying or tid in self.program_uploads or not self.supervisor.can_resume(tid)
                or (self.supervisor.automatic[tid] and tid not in self.supervisor.operator_paused)):
            return
        actual = self.supervisor.resume(tid)
        self.send(tid, actual)
        self.refresh_run_status()

    def stop_all(self):
        if self.demo:
            for tid in self.supervisor.external:
                self.tracker._advance(tid)
                self.tracker.timing_enabled[tid] = False
        self.demo_running = False
        self.match_external.set(False)
        self.supervisor.set_pace_train(None)
        self.auto_enabled.set(False)
        self.supervisor.enabled = False
        for tid in self.sliders:
            self.stop(tid)
        self.target_value.set(self.supervisor.target / STUD_M)
        self.refresh_run_status()

    def set_target(self, value):
        if self.supervisor.pace_train is not None:
            return
        target = min(float(value), self.supervisor.max_speed / STUD_M)
        self.supervisor.target = target * STUD_M
        self.config.setdefault('automation', {})['target_speed_studs_s'] = target

    def set_auto_max(self):
        try:
            maximum = float(self.auto_max.get())
            if not math.isfinite(maximum) or maximum <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror('Invalid maximum speed', 'Enter a positive number of studs per second.', parent=self.root)
            return
        self.supervisor.max_speed = maximum * STUD_M
        self.config.setdefault('automation', {})['max_speed_studs_s'] = maximum
        self.supervisor.target = min(self.supervisor.target, self.supervisor.max_speed)
        self.config['automation']['target_speed_studs_s'] = self.supervisor.target / STUD_M
        self.target_speed.configure(from_=maximum)
        self.target_value.set(min(self.target_speed.get(), maximum))
        self.set_target(self.target_speed.get())
        self.save_map()

    def change_pace_train(self, event=None):
        tid = self.pace_choices.get(self.pace_choice.get())
        if tid:
            self.config.setdefault('automation', {})['pace_train_id'] = tid
        if self.match_external.get():
            self.set_follow_mode()
        else:
            self.save_map()

    def set_follow_mode(self):
        if self.motor_only:
            return
        tid = self.pace_choices.get(self.pace_choice.get()) if self.match_external.get() else None
        if self.match_external.get() and tid is None:
            self.match_external.set(False)
        self.supervisor.set_pace_train(tid)
        if tid is not None:
            self.config.setdefault('automation', {})['pace_train_id'] = tid
            self.auto_enabled.set(True)
        else:
            self.auto_enabled.set(False)
            self.supervisor.enabled = False
            for controlled in self.supervisor.trains.keys() - self.supervisor.external:
                self.supervisor.request(controlled, 0)
                self.send(controlled, 0)
            self.target_value.set(self.supervisor.target / STUD_M)
        self.refresh_run_status()
        self.save_map()

    def set_view_details(self, visible):
        for variable in self.view_options.values():
            variable.set(visible)
        if not visible:
            self.selected_points.clear()
        self.update_view()

    def update_view(self, save=True):
        self.config['view'] = {key: variable.get() for key, variable in self.view_options.items()}
        for widget in (self.map_status_label, self.map_help_label):
            widget.pack_forget()
            if self.view_options['map_help'].get():
                widget.pack(before=self.canvas, anchor='w', padx=12)
        self.log.pack_forget()
        if self.view_options['activity_log'].get():
            self.log.pack(fill='x', padx=10)
        if save:
            self.save_map()
        self.draw()

    def enable_auto(self):
        if self.motor_only:
            return
        if not self.auto_enabled.get() and self.supervisor.pace_train is not None:
            self.match_external.set(False)
            self.set_follow_mode()
            return
        self.supervisor.enabled = self.auto_enabled.get()
        if not self.supervisor.enabled:
            for tid in self.sliders:
                if self.supervisor.automatic[tid]:
                    self.supervisor.request(tid, 0)
                    self.send(tid, 0)
        self.refresh_run_status()

    def set_auto(self, tid):
        if self.motor_only or tid in self.supervisor.external or tid in self.program_uploads:
            return
        if self.supervisor.pace_train is not None and tid not in self.supervisor.forced:
            self.supervisor.force_automatic()
            self.auto_vars[tid].set(self.supervisor.automatic[tid])
            return
        if self.auto_vars[tid].get() or tid in self.supervisor.forced:
            self.supervisor.select_auto(tid)
            self.auto_enabled.set(True)
            # Evaluate protection immediately when handing an override to Auto.
            for train, value in self.supervisor.tick().items():
                self.send(train, value)
            self.refresh_run_status()
            return
        self.supervisor.automatic[tid] = self.auto_vars[tid].get()
        if not self.supervisor.automatic[tid]:
            self.supervisor.request(tid, 0)
            self.send(tid, 0)
        self.refresh_run_status()

    def set_service(self, tid):
        active = self.service_vars[tid].get()
        self.supervisor.service[tid] = active
        if active:
            self.supervisor.operator_paused.discard(tid)
        if not active:
            self.stop(tid)
        for train in self.config['trains']:
            if train['id'] == tid:
                train['in_service'] = active
        self.save_map()

    def off_track(self, tid):
        self.stop(tid)
        self.supervisor.remove_from_track(tid)
        self.service_vars[tid].set(False)
        self.locations[tid].set('Physically off track')
        self.card_locations[tid].set('Off track')
        self.refresh_run_status()
        self.append(f'{tid}: marked physically removed; occupancy released')

    def place_train(self, tid):
        if not self.config['readers']:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Initial position')
        ttk.Label(dialog, text='Place the train tag at the selected reader, then confirm.').pack(padx=15,pady=10)
        selection = ttk.Combobox(dialog, state='readonly', width=40,
            values=[f"{r['name']} — {block_name(self.config, r['id'])}" for r in self.config['readers']])
        selection.pack()
        selection.current(0)
        def apply():
            self.stop(tid)
            checkpoint = self.config['readers'][selection.current()]['id']
            self.tracker.last_seen[tid] = (checkpoint, self.tracker.clock())
            self.tracker.passages.pop(tid,None)
            self.tracker.blocks.pop(tid,None)
            self.tracker.block_entries.pop(tid,None)
            self.tracker.directions.pop(tid,None)
            self.tracker.frozen.pop(tid,None)
            self.tracker.speeds.pop(tid,None)
            self.tracker.reset_motion(tid, clear_model=True)
            self.tracker.journeys.pop(tid,None)
            self.tracker.clearance.reset(tid)
            self.tracker.clearance.front(tid,checkpoint,{'unix_ms':time.time_ns()//1_000_000})
            self.demo_positions.pop(tid,None)
            self.supervisor.on_track[tid] = True
            self.supervisor.faults.pop(tid,None)
            self.supervisor.alarms = {key:value for key,value in self.supervisor.alarms.items() if key[0] != tid}
            self.refresh_run_status()
            dialog.destroy()
        ttk.Button(dialog, text='Train is at this checkpoint', command=apply).pack(pady=10)

    def show_hub_program(self, train_id=None):
        if train_id is None and self.program_uploads:
            train_id = next(iter(self.program_uploads))
        if self.program_dialog is not None and self.program_dialog.winfo_exists():
            if train_id is not None and not self.program_dialog.busy:
                dialog = self.program_dialog
                dialog.choice.set(next((name for name, train in dialog.trains.items() if train['id'] == train_id), ''))
                dialog.refresh()
            self.program_dialog.lift()
            return
        try:
            dialog = HubProgramDialog(self.root, self.config, self.upload_hub_program,
                                      demo=self.demo, train_id=train_id, on_update=self.upload_active_hubs)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror('Cannot open City Hub program', str(exc), parent=self.root)
            return
        self.program_dialog = dialog
        # Keep fleet Stop controls accessible while the upload runs.
        previous_grab = self.root.grab_current()
        if previous_grab is not None:
            previous_grab.grab_release()
            def restore_grab(event):
                if event.widget is dialog and previous_grab.winfo_exists():
                    previous_grab.grab_set()
            dialog.bind('<Destroy>', restore_grab)
        for tid, (phase, text) in self.program_results.items():
            dialog.receive_status(tid, phase, text)
        for line in self.program_debug:
            dialog.receive_debug(line)
        if self.program_debug or self.program_uploads:
            dialog.tabs.select(dialog.debug_frame)

    def update_hubs(self):
        self.show_hub_program()
        if self.program_dialog is not None and self.program_dialog.winfo_exists():
            self.program_dialog.update_hubs()

    def upload_active_hubs(self, targets, source):
        if self.applying or self.program_uploads:
            raise ValueError('Wait for the current configuration change or program upload to finish')
        if self.demo:
            raise ValueError('Update hubs in Live or Motor only mode')
        current = {tid: t['hub_name'] for tid, t in self.supervisor.trains.items() if supports(t, 'city_hub_program')}
        if not current or {t['id']: t['hub_name'] for t in targets} != current:
            raise ValueError('The active train list changed. Close and reopen City Hub program before updating.')
        for tid in current:
            self.prepare_hub_upload(tid)
        try:
            self.backend.update_hubs(source)
        except Exception:
            for tid in current:
                self.finish_hub_upload(tid)
                self.program_results[tid] = ('failed', 'Update could not start. Train remains stopped.')
            raise
        self.refresh_run_status()

    def prepare_hub_upload(self, tid):
        self.stop(tid)
        self.supervisor.pause(tid)
        self.supervisor.automatic[tid] = False
        self.auto_vars[tid].set(False)
        self.supervisor.connection_state(tid, False)
        self.program_uploads.add(tid)
        self.program_results[tid] = ('progress', 'Preparing scan and connection…')
        self.statuses[tid].set('Preparing hub update…')

    def upload_hub_program(self, tid, hub_name, source):
        if self.applying or self.program_uploads:
            raise ValueError('Wait for the current configuration change or program upload to finish')
        if self.demo:
            raise ValueError('Upload in Live or Motor only mode')
        train = self.supervisor.trains.get(tid)
        if train is None or not supports(train, 'city_hub_program') or train.get('hub_name') != hub_name:
            raise ValueError('The selected train settings changed. Reopen City Hub program and select an active fleet-controlled train.')
        self.prepare_hub_upload(tid)
        try:
            self.backend.upload_program(tid, source)
        except Exception:
            self.finish_hub_upload(tid)
            self.program_results[tid] = ('failed', 'Upload could not start. Train remains stopped.')
            raise
        self.statuses[tid].set('Uploading hub program…')
        self.refresh_run_status()

    def finish_hub_upload(self, tid):
        self.program_uploads.discard(tid)
        if tid in self.supervisor.trains:
            self.supervisor.pause(tid)
            if self.supervisor.pace_train is None:
                # Manual controls can be used again, but old Auto selections never restart.
                self.supervisor.automatic[tid] = False
                self.auto_vars[tid].set(False)
                self.supervisor.operator_paused.discard(tid)

    def setup(self, train_id=None, library_only=False):
        if not self.applying:
            if self.sensor_dialog is not None and self.sensor_dialog.winfo_exists():
                messagebox.showinfo('Setup', 'Close Sensors & tags before editing fleet setup.', parent=self.sensor_dialog)
                return
            if self.setup_dialog is not None and self.setup_dialog.winfo_exists():
                self.setup_dialog.lift()
                return
            original = copy.deepcopy(self.config)
            self.setup_dialog = SetupDialog(self.root, self.config,
                lambda edited:self.apply_setup(merge_train_edits(self.config, original, edited)),
                can_scan=self.server is not None and not self.demo, train_id=train_id, library_only=library_only,
                on_program=self.show_hub_program)

    def manage_sensors(self):
        if self.setup_dialog is not None and self.setup_dialog.winfo_exists():
            self.setup_dialog.lift()
            return
        if self.sensor_dialog is not None and self.sensor_dialog.winfo_exists():
            self.sensor_dialog.lift()
            return
        self.sensor_dialog = SensorDialog(self.root, self.sensors, lambda: self.config,
                                          self.apply_setup, live=self.server is not None and not self.demo)

    def apply_setup(self, config):
        if self.program_uploads:
            raise ValueError('Wait for the hub program upload to finish before changing setup')
        if self.applying:
            raise ValueError('Wait for the previous setup change to finish')
        config = copy.deepcopy(config)
        live = can_apply_live(self.config, config)
        originals = {t['id']: t for t in all_trains(self.config)}
        for train in all_trains(config):
            old = originals.get(train['id'])
            if old and (connection_identity(old) != connection_identity(train) or
                    old.get('forward_sign', 1) != train.get('forward_sign', 1) or
                    is_external(old) != is_external(train)):
                train.pop('section_history', None)
        saved = save_config(self.path, config)
        if live:
            self.apply_live_trains(saved)
            return
        self.stop_all()
        self.demo_running = False
        self.applying = True
        self.append('Applying setup: stopping and disconnecting trains…')
        self.backend.reconfigure(saved)

    def apply_live_trains(self, saved):
        for train in saved['trains']:
            tid = train['id']
            old = self.supervisor.trains[tid]
            controller_changed = connection_identity(old) != connection_identity(train)
            if controller_changed or old.get('forward_sign', 1) != train.get('forward_sign', 1):
                self.stop(tid)  # Send zero with the OLD polarity/connection first.
                if controller_changed and not self.demo and tid not in self.supervisor.external:
                    self.supervisor.connection_state(tid, False)
                    self.batteries[tid] = None
                    self.statuses[tid].set('Controller changed — reconnecting if previously connected')
        update_train_runtime(self.supervisor, saved)
        if 'saved_trains' in saved:
            self.config['saved_trains'] = saved['saved_trains']
        else:
            self.config.pop('saved_trains', None)
        # Retain history only for the same train/hub/polarity identity. Do not
        # insert a gap for an unrelated library or display-name edit.
        keys = {history_identity(t) for t in all_trains(self.config) if supports(t, 'power_telemetry')}
        self.battery_histories = {key: history for key, history in self.battery_histories.items() if key in keys}
        self.power_profiles = {key: profile for key, profile in self.power_profiles.items() if key in keys}
        self.backend.update_trains(self.config)
        for train in self.config['trains']:
            tid = train['id']
            self.train_titles[tid].configure(text='●  ' + train['name'])
            # Apply a lowered limit immediately, without touching other trains.
            old = self.supervisor.throttle[tid]
            if tid not in self.supervisor.forced and abs(old) > train.get('max_throttle', 70):
                self.send(tid, self.supervisor.request(tid, old))
        source = self.supervisor.pace_train or self.pace_choices.get(self.pace_choice.get())
        self.pace_choices = {f"{t['name']} ({t['id']})": t['id'] for t in self.config['trains'] if is_external(t)}
        self.pace_choice.configure(values=list(self.pace_choices))
        self.pace_choice.set(next((name for name, tid in self.pace_choices.items() if tid == source), ''))
        self.sensors.configure(self.config)
        self.append('Train settings saved live; connections and confirmed positions retained.')
        for tid in self.supervisor.trains:
            if tid not in self.tracker.clearance.enabled and self.tracker.clearance.pending.get(tid):
                self.append(f'{tid}: rear tag removed; previously held blocks stay occupied until verified repositioning.')
        self.refresh_run_status()
        self.refresh_batteries()

    def update_title(self):
        if hasattr(self, 'config_filename'):
            self.config_filename.set(Path(self.path).name)
        prefix = 'TRAIN ALARM — ' if self.supervisor.alarms else ''
        mode = 'MOTOR ONLY' if self.motor_only else 'DEMO' if self.demo else 'LIVE'
        self.root.title(f"{prefix}LEGO Train Fleet — {mode} — {Path(self.path).name}")

    def configuration_available(self):
        if self.applying or self.program_uploads:
            return False
        for dialog in (self.setup_dialog, self.sensor_dialog):
            if dialog is not None and dialog.winfo_exists():
                messagebox.showinfo('Configuration', 'Finish or close the open setup window first.', parent=dialog)
                dialog.lift()
                return False
        return True

    def save_as(self):
        if not self.configuration_available():
            return
        destination = filedialog.asksaveasfilename(parent=self.root, title='Save configuration as',
            initialdir=Path(self.path).parent, initialfile=Path(self.path).name,
            defaultextension='.json', filetypes=[('Fleet configuration', '*.json')])
        if not destination:
            return
        try:
            saved = save_configuration(destination, self.config, self.path)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot save configuration', str(exc), parent=self.root)
            return
        # Preserve the live objects used by the supervisor; only image paths change.
        if 'map_image' in saved:
            self.config['map_image'] = saved['map_image']
        saved_trains = {train['id']: train for train in all_trains(saved)}
        for train in all_trains(self.config):
            if 'photo' in saved_trains[train['id']]:
                train['photo'] = saved_trains[train['id']]['photo']
        self.path = Path(destination).resolve()
        self.update_title()
        self.append(f'Configuration saved: {self.path}')

    def load_configuration(self):
        if not self.configuration_available():
            return
        source = filedialog.askopenfilename(parent=self.root, title='Load configuration',
            initialdir=Path(self.path).parent, filetypes=[('Fleet configuration', '*.json')])
        if not source:
            return
        try:
            config = load_config(source)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot load configuration', str(exc), parent=self.root)
            return
        try:
            changed = self.config != load_config(self.path)
        except (ValueError, OSError):
            changed = True
        if changed:
            answer = messagebox.askyesnocancel('Unsaved configuration',
                'Save your current configuration before loading another?', parent=self.root)
            if answer is None or (answer and not self.save_map()):
                return
            # Loading the current file should include any changes just saved.
            if Path(source).resolve() == Path(self.path).resolve():
                config = load_config(source)
        self.stop_all()
        self.demo_running = False
        self.applying = True
        self.pending_config_path = Path(source).resolve()
        # Network settings and sensor bindings belong to the selected configuration.
        if self.discovery:
            self.discovery.close()
            self.discovery = None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.append(f'Loading {source}: stopping and disconnecting trains…')
        self.backend.reconfigure(config)

    def save_map(self):
        if self.applying:
            return
        try:
            save_config(self.path, self.config)
            self.append(f'Configuration saved: {self.path}')
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot save map', str(exc), parent=self.root)
            return False

    def load_background(self):
        source = self.config.get('map_image')
        if not source:
            self.background = None
            self.background_source = None
            return
        try:
            from PIL import Image, ImageOps
            path = Path(source)
            if not path.is_absolute():
                path = Path(self.path).parent/path
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                self.background_source = image.copy()
                self.render_background()
        except (ImportError, OSError, ValueError) as exc:
            self.append(f'Cannot load map image: {exc}')

    def choose_background(self):
        source = filedialog.askopenfilename(filetypes=[('Map images','*.png *.jpg *.jpeg *.gif *.webp')])
        if source:
            try:
                from PIL import Image
                with Image.open(source) as image:
                    image.verify()
                assets = Path(self.path).parent/'map_images'
                assets.mkdir(exist_ok=True)
                dest = assets/(uuid.uuid4().hex+Path(source).suffix.lower())
                shutil.copyfile(source,dest)
                self.cancel_placement()
                self.config['map_image'] = str(dest.relative_to(Path(self.path).parent))
                self.load_background()
                self.save_map()
            except (ImportError,OSError,ValueError) as exc:
                messagebox.showerror('Cannot load image',str(exc),parent=self.root)

    def clear_background(self):
        self.config.pop('map_image',None)
        self.background=None
        self.background_source=None
        self.save_map()

    def world(self, x, y):
        return ((x-self.offset[0])/self.zoom, (y-self.offset[1])/self.zoom)

    def screen(self, x, y):
        return x*self.zoom+self.offset[0], y*self.zoom+self.offset[1]

    def track_path(self, index):
        return path_for(self.config['readers'], index, self.config.get('map_m_per_unit'))

    def render_background(self):
        if self.background_source is not None:
            from PIL import Image, ImageTk
            w, h = self.background_source.size
            fit = min(1., 1080/w, 450/h)
            w, h = round(w*fit), round(h*fit)
            self.background = ImageTk.PhotoImage(self.background_source.resize(
                (max(1, round(w*self.zoom)), max(1, round(h*self.zoom))), Image.Resampling.LANCZOS))

    def zoom_map(self, factor, x=None, y=None):
        x = self.canvas.winfo_width()/2 if x is None else x
        y = self.canvas.winfo_height()/2 if y is None else y
        wx, wy = self.world(x, y)
        self.zoom = max(.2, min(6., self.zoom*factor))
        self.offset = [x-wx*self.zoom, y-wy*self.zoom]
        self.render_background()
        self.draw()
        return 'break'

    def reset_view(self):
        self.zoom, self.offset = 1., [0., 0.]
        self.render_background()
        self.draw()

    def end_drag(self, event=None):
        self.dragging = None
        self.pan_last = None
        self.canvas.configure(cursor='')

    def pan_start(self, event):
        self.dragging = None
        self.pan_last = (event.x, event.y)
        self.canvas.configure(cursor='fleur')
        return 'break'

    def pan_move(self, event):
        if self.pan_last is None:
            return 'break'
        x, y = self.pan_last
        self.offset[0] += event.x-x
        self.offset[1] += event.y-y
        self.pan_last = (event.x, event.y)
        self.draw()
        return 'break'

    def cancel_placement(self):
        self.add_sensor_once = False
        self.add_point_once = False
        self.add_point_mode.set(False)
        self.calibrating, self.reference_points, self.curve_pending = False, [], None
        self.draw()

    def start_calibration(self):
        self.cancel_placement()
        self.add_point_mode.set(False)
        self.calibrating = True
        self.draw()

    def arm_sensor(self, event=None):
        if not self.configuration_available():
            return 'break'
        self.cancel_placement()
        self.add_sensor_once = True
        self.canvas.focus_set()
        self.draw()
        return 'break'

    def add_sensor(self, event):
        if not self.configuration_available():
            return 'break'
        x,y = self.world(event.x,event.y)
        try:
            config, ident = insert_checkpoint(self.config,x,y)
        except ValueError as exc:
            messagebox.showerror('Cannot add sensor',str(exc),parent=self.root)
            return 'break'
        self.stop_all()
        self.demo_running = False
        ready = dict(self.supervisor.ready)
        present = dict(self.supervisor.on_track)
        service = dict(self.supervisor.service)
        self.config.clear()
        self.config.update(config)
        self.tracker = LoopTracker(self.config)
        self.tracker.learning.persist = not self.demo
        self.supervisor = Supervisor(self.config,self.tracker)
        self.set_target(self.target_speed.get())
        self.supervisor.ready.update(ready)
        self.supervisor.on_track.update(present)
        self.supervisor.service.update(service)
        self.demo_positions.clear()
        self.demo_initialized = False
        self.allowed_readers.clear()
        self.allowed_readers.update(r['id'] for r in self.config['readers'])
        self.sensors.configure(self.config)
        for tid in self.locations:
            self.locations[tid].set('Block unknown — track changed; set position or detect train')
            self.card_locations[tid].set('Awaiting checkpoint')
        self.selected_points = [next(r for r in self.config['readers'] if r['id']==ident)]
        self.add_sensor_once = False
        self.append(f'{ident} added. Save map to keep it; assign hardware in Sensors. Tracking reset for the new block layout.')
        self.draw()
        return 'break'

    def arm_point(self, event=None):
        self.cancel_placement()
        self.add_point_once = True
        self.canvas.focus_set()
        self.draw()
        return 'break'

    def curve_selected(self, event=None):
        if self.applying:
            return 'break'
        if len(self.selected_points) != 2:
            messagebox.showinfo('Select two points', 'Click one point, then Shift-click another point on the same track block.', parent=self.root)
            return 'break'
        if not self.config.get('map_m_per_unit'):
            messagebox.showinfo('Calibrate map', 'Set a known distance before adding curves.', parent=self.root)
            return 'break'
        readers = self.config['readers']
        for i, reader in enumerate(readers):
            points = [reader, *reader.get('via', []), readers[(i+1) % len(readers)]]
            indices = [j for j,p in enumerate(points) if any(p is selected for selected in self.selected_points)]
            if len(indices) != 2:
                continue
            first, last = indices
            start, end = points[first], points[last]
            if math.dist((start.get('x',100),start.get('y',100)), (end.get('x',100),end.get('y',100))) < 1e-6:
                messagebox.showerror('Cannot create curve', 'Choose two points at different positions.', parent=self.root)
                return 'break'
            # A second press flips the bend; endpoints and travel order stay fixed.
            turn = -start.get('curve_to_next', -1)
            for key in ('radius_studs', 'heading', 'turn'):
                start.pop(key, None)
            start['curve_to_next'] = turn
            start.pop('curve_sweep',None)
            # Replace only shaping points between endpoints, never checkpoints.
            reader['via'] = [p for p in reader.get('via', []) if not any(p is q for q in points[first+1:last])]
            self.cancel_placement()
            self.geometry_changed()
            self.canvas.focus_set()
            return 'break'
        messagebox.showinfo('Different blocks', 'Choose two points within the same checkpoint-to-checkpoint block. A curve cannot skip a checkpoint.', parent=self.root)
        return 'break'

    def curve_parameters(self, point=None):
        point = point or {}
        radius = simpledialog.askfloat('90° curve', 'Radius (LEGO studs):',
                     initialvalue=point.get('radius_studs', 40), minvalue=.01, parent=self.root)
        if radius is None:
            return
        heading = simpledialog.askfloat('90° curve', 'Entry heading in degrees: 0 = right, 90 = down, 180 = left, 270 = up',
                     initialvalue=point.get('heading', 0), parent=self.root)
        if heading is None:
            return
        if not math.isfinite(radius) or not math.isfinite(heading):
            messagebox.showerror('Invalid curve', 'Radius and heading must be finite numbers.', parent=self.root)
            return
        right = messagebox.askyesnocancel('90° curve', 'Turn right? Choose No for a left turn.', parent=self.root)
        if right is None:
            return
        return dict(radius_studs=radius, heading=heading % 360, turn=1 if right else -1)

    def start_curve(self):
        if not self.config.get('map_m_per_unit'):
            messagebox.showinfo('Calibrate map', 'Set a known distance before adding curves.', parent=self.root)
            return
        self.cancel_placement()
        self.add_point_mode.set(False)
        self.curve_pending = self.curve_parameters()
        self.draw()

    def edit_curve(self, event):
        self.pick(event)
        x, y = self.world(event.x, event.y)
        for reader in self.config['readers']:
            for point in reader.get('via', []):
                if 'radius_studs' in point and math.dist((x,y), (point['x'],point['y'])) < 10/self.zoom:
                    parameters = self.curve_parameters(point)
                    if parameters:
                        point.update(parameters)
                        self.geometry_changed()
                    return 'break'

    def geometry_changed(self):
        update_distances(self.config)
        self.tracker.distances = {r['id']: r.get('distance_to_next_m',1) for r in self.config['readers']}
        # Previous samples were measured against different geometry.
        self.stop_all()
        self.tracker.speeds.clear()
        self.tracker.passages.clear()
        self.tracker.motion.clear()
        self.tracker.effort.clear()
        self.tracker.speed_per_throttle.clear()
        self.tracker.learning.clear()
        self.tracker.journeys.clear()
        self.demo_positions.clear()
        self.draw()

    def add_waypoint(self, event):
        self.dragging = None
        self.canvas.focus_set()
        readers = self.config['readers']
        if len(readers) < 2:
            return 'break'
        x,y = self.world(event.x,event.y)
        candidates = []
        for i in range(len(readers)):
            path, _, owners = geometry(readers, i, self.config.get('map_m_per_unit'))
            distance, leg = nearest_leg(path, x, y)
            candidates.append((distance, owners[leg], i))
        _, leg, index = min(candidates)
        reader = readers[index]
        if not reader.get('via') and len(readers) == 2 and 'curve_to_next' not in reader:
            reader['via'] = [{'x': px, 'y': py} for px,py in control_path(readers,index)[1:-1]]
        point = {'x':x, 'y':y}
        if self.curve_pending:
            point.update(self.curve_pending)
            self.curve_pending = None
        reader.setdefault('via', []).insert(leg, point)
        self.selected_points = [point]
        self.add_point_once = False
        self.geometry_changed()
        return 'break'

    def remove_waypoint(self, event):
        x,y = self.world(event.x,event.y)
        for reader in self.config['readers']:
            for point in list(reader.get('via', [])):
                if math.dist((point['x'],point['y']), (x,y)) < 10/self.zoom:
                    reader['via'].remove(point)
                    self.selected_points = [p for p in self.selected_points if p is not point]
                    self.geometry_changed()
                    return

    def pick(self, event):
        self.canvas.focus_set()
        self.end_drag()
        if self.add_sensor_once:
            return self.add_sensor(event)
        if self.calibrating:
            self.reference_points.append(self.world(event.x,event.y))
            self.draw()
            if len(self.reference_points) == 2:
                inches = simpledialog.askfloat('Known distance', 'Distance between the two marks (inches):', minvalue=.001, parent=self.root)
                if inches is not None:
                    try:
                        scale = calibration_scale(*self.reference_points, inches)
                        self.config['map_m_per_unit'] = scale
                        self.config['map_reference'] = {'points': self.reference_points[:], 'inches': inches}
                        self.geometry_changed()
                        self.save_map()
                    except ValueError as exc:
                        messagebox.showerror('Invalid calibration', str(exc), parent=self.root)
                self.cancel_placement()
            return 'break'
        if self.add_point_mode.get() or self.add_point_once or self.curve_pending:
            return self.add_waypoint(event)
        for item in reversed(self.canvas.find_overlapping(event.x-10,event.y-10,event.x+10,event.y+10)):
            if item in self.map_items:
                point = self.map_items[item]
                if getattr(event, 'state', 0) & 1:
                    if any(point is p for p in self.selected_points):
                        self.selected_points = [p for p in self.selected_points if p is not point]
                    else:
                        self.selected_points.append(point)
                else:
                    self.selected_points = [point]
                self.dragging = point
                self.draw()
                return 'break' 
        return self.pan_start(event)

    def move(self, event):
        if self.pan_last is not None:
            return self.pan_move(event)
        if self.dragging:
            x,y = self.world(event.x,event.y)
            old = dict(self.dragging)
            self.dragging.update(x=x,y=y)
            try:
                self.geometry_changed()
            except ValueError:
                self.dragging.clear()
                self.dragging.update(old)

    def draw(self):
        if self.motor_only:
            return
        c = self.canvas
        view = {key: variable.get() for key, variable in self.view_options.items()}
        editing = view['edit_handles'] or self.add_point_once or self.add_point_mode.get() or self.curve_pending
        c.delete('all')
        self.map_items.clear()
        if self.background:
            c.create_image(*self.offset,anchor='nw',image=self.background)
        status = ('Click to place a sensor checkpoint' if self.add_sensor_once else
                  'Click to add a point' if self.add_point_once else
                  'Click two ends of a known distance' if self.calibrating else
                  'Click the curve entry point' if self.curve_pending else
                  'Calibrated · save map after edits' if self.config.get('map_m_per_unit') else 'Uncalibrated · manual distances')
        self.map_status.set(f'{self.zoom:.0%} · {len(self.selected_points)} selected · {status}')
        for point in self.reference_points:
            x,y = self.screen(*point)
            c.create_oval(x-5,y-5,x+5,y+5,fill='#e11d48')
        if len(self.reference_points) == 2:
            c.create_line(*[v for p in self.reference_points for v in self.screen(*p)], fill='#e11d48', width=2)
        readers = self.config['readers']
        sensor_rows, _ = self.sensors.snapshot()
        sensor_status = {s['node']: s for s in sensor_rows}
        points = {r['id']: r for r in readers}
        positions = {t['id']: self.tracker.position(t['id']) for t in self.config['trains']}
        for i, a in enumerate(readers):
            if len(readers) < 2:
                break
            b = readers[(i+1) % len(readers)]
            path = [self.screen(x,y) for x,y in self.track_path(i)]
            occupied = self.supervisor.occupied().get(a['id'])
            name, color = block_style(self.config, a['id'])
            reserved = a['id'] in self.supervisor.reservations and not occupied
            c.create_line(*[v for point in path for v in point], fill=color,
                          width=9 if occupied else 5, dash=(8,4) if reserved else (), arrow='last',
                          tags=('block', f"block:{a['id']}"))
            mx,my = interpolate(path,.5)
            label = name + ' block'
            if view['distances']:
                label += f" · {a.get('distance_to_next_m',1)/STUD_M:.1f} studs"
            c.create_text(mx,my-12,fill=color,text=label, font=('TkDefaultFont', 11, 'bold'))
            for point in a.get('via',[]):
                x,y=self.screen(point['x'],point['y'])
                if editing:
                    item=c.create_rectangle(x-5,y-5,x+5,y+5,fill='white',outline='#64748b')
                    self.map_items[item]=point
                if view['radii'] and 'radius_studs' in point:
                    c.create_text(x,y-14,fill='black',text=f"R{point['radius_studs']:g} studs · 90°")
        for r in readers:
            x, y = self.screen(r.get('x', 100), r.get('y', 100))
            sensor = sensor_status.get(r.get('node'))
            if self.demo:
                status, color = 'DEMO', '#2563eb'
            elif not r.get('node'):
                status, color = 'NO SENSOR', '#64748b'
            elif not sensor or not sensor['online']:
                status, color = 'OFFLINE', '#dc2626'
            elif not sensor.get('pn532'):
                status, color = 'READER MISSING', '#b45309'
            elif sensor.get('poll_age_ms', 0) > 2000:
                status, color = 'READER STALLED', '#b45309'
            else:
                status, color = 'ONLINE', '#15803d'
            item = c.create_oval(x-9,y-9,x+9,y+9,fill=color,outline='white',width=2)
            self.map_items[item] = r
            if view['sensor_names']:
                label = c.create_text(x,y+23,fill='black',text=r['name'])
                self.map_items[label] = r
            if view['sensor_status']:
                badge = c.create_text(x,y+40,fill=color,text=status,font=('TkDefaultFont', 10, 'bold'),
                                      tags=('sensor-status', f"sensor-status:{r['id']}"))
                left, top, right, bottom = c.bbox(badge)
                backing = c.create_rectangle(left-4,top-2,right+4,bottom+2,
                                             fill='white',outline=color,width=1)
                c.tag_lower(backing, badge)
                self.map_items[badge] = self.map_items[backing] = r
        for point in self.selected_points:
            x,y = self.screen(point.get('x',100), point.get('y',100))
            c.create_oval(x-13,y-13,x+13,y+13,outline='#7c3aed',width=3)
        for i, reader in enumerate(readers):
            controls = [reader, *reader.get('via', []), readers[(i+1) % len(readers)]]
            for a,b in zip(controls,controls[1:]):
                if view['radii'] and 'curve_to_next' in a:
                    radius = math.dist((a.get('x',100),a.get('y',100)),(b.get('x',100),b.get('y',100))) / (2*math.sin(math.radians(a.get('curve_sweep',90))/2))
                    radius *= self.config['map_m_per_unit']/STUD_M
                    x,y = self.screen(a.get('x',100),a.get('y',100))
                    c.create_text(x,y-22,fill='#7c3aed',text=f"R{radius:.1f} studs · {a.get('curve_sweep',90):g}°")
        marker_corners = {}
        for index, train in enumerate(self.config['trains']):
            tid = train['id']
            p = positions[tid]
            if not p:
                continue
            a, b = points[p['from']], points[p['to']]
            simulated = self.demo_positions.get(tid) if self.demo else None
            f = simulated['fraction'] if simulated and self.tracker.order[simulated['index']] == p['from'] else p['fraction']
            path = self.track_path(readers.index(a))
            x,y = self.screen(*interpolate(path, f))
            heading = track_heading(path, f) + (math.pi if p.get('direction',1)<0 else 0)
            corners = oriented_marker(x,y,heading)
            marker_corners[tid] = corners
            color = '#f59e0b' if p['overdue'] else COLORS[index % len(COLORS)]
            waiting = self.supervisor.waiting[tid]
            if waiting:
                color = '#f59e0b' if int(time.monotonic()*2) % 2 else '#fde68a'
            c.create_polygon(*[v for point in corners for v in point],fill=color,
                             outline='white',width=2,tags=('train-marker',f'train:{tid}'))
            # Front stripe shows travel direction, including reverse.
            nose = oriented_marker(x+8*math.cos(heading),y+8*math.sin(heading),heading,3,8)
            c.create_polygon(*[v for point in nose for v in point],fill='white',outline='')
            c.create_text(x,y-18-index*10,fill='black',text=train['name'] + (' · waiting' if waiting else ' ?' if p['overdue'] else ''))
            block = block_name(self.config, a['id'])
            speed = f"{p['speed']*p.get('direction',1)/STUD_M:.2f} studs/s last average" if p['speed'] else 'Speed not yet measured'
            self.card_locations[tid].set(block + (f" · read {int(p['age'])}s ago" if view['speed_details'] else '') +
                                         (' · overdue' if p['overdue'] else ''))
            self.locations[tid].set(f"{'Last confirmed block' if p['overdue'] else 'Detected block'}: {block}\n"
                                    f"{speed} · {int(p['age'])}s since read" +
                                    ('\nCheckpoint overdue — position uncertain' if p['overdue'] else ''))
        for kind, location in alarm_locations(self.supervisor):
            pulse = 4 if int(time.monotonic()*2) % 2 else 0
            if kind == 'trains':
                corners = [point for tid in location for point in marker_corners.get(tid, ())]
                if not corners:
                    continue
                xs, ys = zip(*corners)
                x, y = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
                radius = max(math.hypot(px-x, py-y) for px, py in corners) + 12 + pulse
                tag = 'alarm-trains:' + ':'.join(location)
            else:
                if location not in points:
                    continue
                reader = points[location]
                position = (interpolate(self.track_path(readers.index(reader)), .5) if kind == 'block'
                            else (reader.get('x',100), reader.get('y',100)))
                x, y = self.screen(*position)
                radius = 20 + pulse
                tag = f'alarm-{kind}:{location}'
            color = '#f97316' if kind == 'trains' and pulse else '#dc2626'
            c.create_oval(x-radius,y-radius,x+radius,y+radius,outline=color,width=4,
                          tags=('alarm-marker', tag))
            c.create_text(x,y-radius-10,text='COLLISION RISK' if kind == 'trains' else 'ALARM',
                          fill=color,font=('Helvetica',11,'bold'),
                          tags=('alarm-caption',))

    def refresh_run_status(self):
        if self.motor_only:
            for tid in self.supervisor.trains:
                external = tid in self.supervisor.external
                throttle = self.supervisor.throttle[tid]
                self.sliders[tid].configure(state='disabled' if external or self.applying or tid in self.program_uploads else 'normal')
                self.card_speeds[tid].set('External controller' if external else f'Motor {throttle:+d}%' if throttle else 'Motor stopped')
                self.card_speed_details[tid] = 'Manual motor power; no speed or position measurements in this mode.'
                self.card_locations[tid].set(self.statuses[tid].get())
                reading = self.batteries[tid]
                self.dispatch_labels[tid].set((reading.warning() if reading else '') or self.supervisor.reasons.get(tid, '') or
                                             ('External controller' if external else 'Manual motor control'))
            self.refresh_card_styles()
            return
        matching = self.supervisor.pace_train is not None
        self.supervisor.force_automatic()
        self.target_speed.configure(state='disabled' if matching else 'normal')
        if matching:
            speed, reason = self.supervisor.pace_speed()
            self.target_value.set(min(speed, self.supervisor.max_speed) / STUD_M)
            source = self.supervisor.trains[self.supervisor.pace_train]['name']
            learned = self.tracker.learning.get(self.supervisor.pace_train,
                       self.tracker.block_for(self.supervisor.pace_train)) if self.supervisor.pace_train in self.tracker.last_seen else None
            basis = 'section estimate' if learned else 'measured'
            self.pace_status.set(reason or f"Matching {source}: {speed/STUD_M:.1f} studs/s {basis} · "
                                 f"fleet target {min(speed,self.supervisor.max_speed)/STUD_M:.1f} studs/s")
        else:
            self.pace_status.set('Matching off · external trains use their own controller' if self.pace_choices else
                                 'To follow an external train, choose External controller in Fleet setup.')
        if self.demo:
            self.demo_badge.configure(text='● DEMO RUNNING' if self.demo_running else 'Ⅱ DEMO PAUSED',
                                      fg='#166534' if self.demo_running else '#92400e',
                                      bg='#dcfce7' if self.demo_running else '#fef3c7')
            self.demo_button.configure(text='Ⅱ Pause demo' if self.demo_running else '▶ Start demo')
        for tid,var in self.dispatch_labels.items():
            locked = matching or tid in self.supervisor.external
            forced = tid in self.supervisor.forced
            self.auto_vars[tid].set(self.supervisor.automatic[tid] and not forced)
            uploading = tid in self.program_uploads
            self.auto_checks[tid].configure(state='disabled' if uploading or tid in self.supervisor.external or (matching and not forced) else 'normal')
            self.sliders[tid].configure(state='disabled' if uploading or (locked and not forced) else 'normal')
            self.force_buttons[tid].configure(text='End force' if forced else 'Force run',
                    style='Compact.Stop.TButton' if forced else 'Compact.Force.TButton',
                    state='disabled' if self.applying or uploading or tid in self.supervisor.external
                           or (not self.supervisor.ready[tid] and not forced) else 'normal')
            if uploading:
                reason = 'Stopped: uploading hub program'
            elif forced:
                reason = self.supervisor.automatic_status(tid)
            elif not self.supervisor.on_track[tid]:
                reason = 'Off track — physically removed'
            elif self.applying:
                reason = 'Stopped: applying configuration'
            elif self.supervisor.automatic[tid] and self.demo and not self.demo_running:
                reason = 'Auto stopped: demo is paused'
            else:
                reason = self.supervisor.automatic_status(tid)
            reading = self.batteries[tid] if not self.demo and tid not in self.supervisor.external else None
            battery_warning = reading.warning() if reading else ''
            speed_warning = self.speed_warnings.warning(self.supervisor, tid)
            battery_summary = ''
            if battery_warning:
                battery_summary = ('Battery critical — power loss may be imminent' if reading.warning_level == 'critical'
                                   else 'Low battery — power is getting low')
                if reading.stale():
                    battery_summary = 'Last reading: ' + battery_summary
            var.set('\n'.join(filter(None, (battery_summary, 'Speed low — unable to maintain speed' if speed_warning else '')))
                    or reason or self.statuses[tid].get())
            label = self.reason_labels[tid]
            level = reading.warning_level if reading else ''
            style = ttk.Style(self.root)
            label.configure(background='#fee2e2' if level == 'critical' else '#fef08a' if level or speed_warning
                            else style.lookup(label['style'], 'background'),
                            foreground='#991b1b' if level == 'critical' else '#713f12' if level or speed_warning
                            else style.lookup(label['style'], 'foreground'))
            button = self.resume_buttons[tid]
            if ((self.supervisor.waiting[tid] and not self.supervisor.automatic[tid])
                    or tid in self.supervisor.operator_paused):
                button.grid()
                button.configure(state='normal' if not self.applying and not uploading and self.supervisor.can_resume(tid) else 'disabled')
            else:
                button.grid_remove()
        self.refresh_card_styles()
        self.refresh_speeds()
        self.refresh_alarms()
        issues = self.supervisor.auto_issues()
        if self.demo and not self.demo_running:
            issues.insert(0, 'Demo is paused — Start demo')
        elif not self.demo:
            if self.server is None:
                issues.append('RFID receiver is disabled — no live position updates')
            else:
                rows, _ = self.sensors.snapshot()
                nodes = {row['node']: row for row in rows}
                unavailable = []
                for reader in self.config['readers']:
                    node = reader.get('node', reader['id'])
                    state = nodes.get(node, {})
                    if not node or not state.get('online') or not state.get('pn532') or state.get('poll_age_ms',0) > 2000:
                        unavailable.append(reader['name'])
                if unavailable:
                    issues.append('Check RFID sensors: ' + ', '.join(unavailable))
        self.auto_readiness.set('AUTO · ' + ('\n'.join(issues) if issues else 'Ready / running — all prerequisites met'))
        self.auto_readiness_label.configure(bg='#fef3c7' if issues else '#dcfce7', fg='#78350f' if issues else '#166534')

    def toggle_demo(self):
        if self.applying:
            return
        self.demo_running = not self.demo_running
        for tid in self.supervisor.external:
            self.tracker._advance(tid)
            self.tracker.timing_enabled[tid] = self.demo_running
        if self.demo_running:
            for state in self.demo_positions.values():
                state['time'] = time.monotonic()
            readers=self.config['readers']
            if len(readers)<2:
                self.demo_running = False
                self.refresh_run_status()
                self.append('Demo paused: configure at least two checkpoints.')
                return
            if not self.demo_initialized:
                candidates=[t for t in self.config['trains'] if self.supervisor.on_track[t['id']]
                            and self.supervisor.service[t['id']]]
                selected_source = self.pace_choices.get(self.pace_choice.get())
                candidates.sort(key=lambda t: (t['id'] != selected_source, not is_external(t)))
                capacity=max(1,len(readers)//2)
                selected=candidates[:capacity]
                for i,train in enumerate(selected):
                    tid=train['id']
                    checkpoint=readers[i*len(readers)//len(selected)]['id']
                    self.tracker.last_seen[tid]=(checkpoint,self.tracker.clock())
                    self.tracker.clearance.front(tid,checkpoint,{})
                selected_ids = {t['id'] for t in selected}
                for train in self.config['trains']:
                    if train['id'] not in selected_ids:
                        self.off_track(train['id'])
                self.demo_initialized=True
                if len(candidates)>capacity:
                    self.append(f'Demo spacing: {capacity} trains placed; remaining simulated trains are off track. Configuration unchanged.')
            for train in self.config['trains']:
                tid=train['id']
                if tid not in self.supervisor.external and self.supervisor.on_track[tid] and self.sliders[tid].get()==0:
                    self.command(tid,40)
            for tid in self.supervisor.trains:
                if tid not in self.supervisor.external and self.supervisor.on_track[tid] and self.supervisor.service[tid]:
                    self.auto_vars[tid].set(True)
                    self.supervisor.automatic[tid]=True
            self.auto_enabled.set(True)
            self.supervisor.enabled=True
            self.append('Demo running in automatic mode with spaced trains and block protection.')
        else:
            # Pause retains Auto selections so the reason remains visible.
            for tid in self.sliders:
                self.supervisor.request(tid,0)
                self.send(tid,0)
            self.append('Demo paused. Press Start demo to resume.')
        self.refresh_run_status()

    def demo_tick(self):
        if self.motor_only or not self.demo_running or self.applying or len(self.config['readers']) < 2:
            return
        now = time.monotonic()
        readers = self.config['readers']
        for i, train in enumerate(self.config['trains']):
            tid = train['id']
            external = tid in self.supervisor.external
            if (not self.supervisor.on_track[tid] or (not self.supervisor.service[tid] and tid not in self.supervisor.forced)
                    or (not external and self.sliders[tid].get() == 0)):
                if tid in self.demo_positions:
                    self.demo_positions[tid]['time']=now
                continue
            state = self.demo_positions.get(tid)
            if state is None:
                current=self.tracker.last_seen.get(tid,(readers[i % len(readers)]['id'],now))[0]
                state={'index':self.tracker.order.index(current),'fraction':0.,'time':now,'distance':0.,
                       'rear_passages':[(train.get('length_m',.5),current)] if train.get('rear_tag') else []}
                self.demo_positions[tid]=state
            delta=max(0,now-state['time'])
            state['time']=now
            # The external demo train has its own fixed speed; fleet commands
            # and the matching target cannot change it.
            speed=(DEFAULT_TARGET_SPEED * STUD_M if external else
                   .3*self.sliders[tid].get()/40*(1+i*.1))
            state['distance'] = state.get('distance',0.) + delta*abs(speed)
            state['fraction'] += delta*speed/readers[state['index']].get('distance_to_next_m',1)
            for due, checkpoint in list(state.get('rear_passages', [])):
                if state['distance'] >= due:
                    r = readers[self.tracker.order.index(checkpoint)]
                    self.demo_read(tid,r,train['rear_tag'],now)
                    state['rear_passages'].remove((due,checkpoint))
            if 0 <= state['fraction'] < 1:
                continue
            if speed >= 0:
                state['fraction']=0
                state['index']=(state['index']+1)%len(readers)
                r=readers[state['index']]
            else:
                r=readers[state['index']]
                state['fraction']=1.-1e-9
                state['index']=(state['index']-1)%len(readers)
            uid=next(iter(train['tags']),f'{i+1:08X}')
            self.demo_read(tid,r,uid,now)
            if train.get('rear_tag'):
                state.setdefault('rear_passages',[]).append((state['distance']+train.get('length_m',.5),r['id']))

    def demo_read(self, tid, reader, uid, now):
        self.tracker.tags[uid]=tid
        self.demo_sequence+=1
        payload={'reader':reader['id'],'uid':uid,'node':reader.get('node'),'boot':'demo',
                 'seq':self.demo_sequence,'t_ms':int(now*1000)%2**32,'unix_ms':int(time.time()*1000)}
        self.events.put(('rfid',reader['id'],json.dumps(payload)))

    def poll(self):
        if self.closed:
            return
        self.demo_tick()
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'discovery':
                self.append(event[1])
            elif event[0] == 'configured':
                self.config = event[1]
                loaded = hasattr(self, 'pending_config_path')
                if loaded:
                    self.path = self.pending_config_path
                    del self.pending_config_path
                self.allowed_readers.clear()
                self.allowed_readers.update(r['id'] for r in self.config['readers'])
                self.sensors.configure(self.config)
                self.applying = False
                self.build()
                self.set_battery_recording(self.config.get('battery_recording', False), persist=False)
                if loaded and not self.demo:
                    self.start_receiver()
                self.append('Setup applied. Reconnect trains; tracking starts unknown.')
            elif event[0] == 'ready' and event[1] in self.supervisor.ready:
                tid, ready = event[1:]
                if tid in self.program_uploads and ready:
                    continue
                self.supervisor.connection_state(tid, ready)
                if self.battery_recording and not ready:
                    history = self.battery_history_for(tid, create=True)
                    if history is not None:
                        history.command('demand', 0, time.monotonic())
                self.sliders[tid].set(self.supervisor.throttle[tid])
            elif event[0] == 'program_debug':
                _, tid, text = event
                train = self.supervisor.trains.get(tid)
                label = f"{train['name']} / {controller_label(train)}" if train else 'Update Hubs'
                line = f'{time.strftime("%H:%M:%S")} [{label}] {text}'
                self.program_debug.append(line)
                del self.program_debug[:-5000]
                if self.program_dialog is not None and self.program_dialog.winfo_exists():
                    self.program_dialog.receive_debug(line)
            elif event[0] == 'program_upload':
                _, tid, phase, text = event
                self.program_results[tid] = (phase, text)
                if phase != 'progress':
                    self.finish_hub_upload(tid)
                    if tid in self.supervisor.trains:
                        self.append(f'{self.supervisor.trains[tid]["name"]}: {text}')
                if tid in self.statuses:
                    self.statuses[tid].set(text)
                if self.program_dialog is not None and self.program_dialog.winfo_exists():
                    self.program_dialog.receive_status(tid, phase, text)
            elif event[0] == 'status' and event[1] in self.statuses:
                self.statuses[event[1]].set(event[2])
            elif event[0] == 'battery' and event[1] in self.batteries:
                self.batteries[event[1]] = event[2]
                if (self.battery_recording and event[2] is not None
                        and event[2].received_at >= self.battery_recording_start):
                    history = self.battery_history_for(event[1], create=True)
                    if history is not None and history.add(event[2]):
                        self.check_power_profile(event[1], history, event[2].received_at)
            elif event[0] == 'sent_throttle' and self.battery_recording:
                _, tid, at, value = event
                history = self.battery_history_for(tid, create=True)
                if history is not None and at >= self.battery_recording_start:
                    if value is None:
                        history.gap(at)
                    else:
                        history.command('sent', value, at)
            elif event[0] == 'hub_output' and event[1] in self.statuses:
                name = self.supervisor.trains[event[1]]['name']
                self.append(f"{name} hub: {event[2]}")
            elif event[0] == 'rfid' and not self.applying and not self.motor_only:
                try:
                    previous = {tid:p[0] for tid,p in self.tracker.last_seen.items()}
                    result = self.tracker.ingest(event[2], event[1])
                    if result:
                        tid, uid, checkpoint = result
                        if self.setup_dialog is not None and self.setup_dialog.winfo_exists() and not self.demo:
                            self.setup_dialog.receive_tag(uid, checkpoint)
                        self.supervisor.observe(tid, previous.get(tid), rear=uid in self.tracker.rear_tags)
                        if tid:
                            self.service_vars[tid].set(self.supervisor.service[tid])
                        saved_owner = next((t['name'] for t in self.config.get('saved_trains', [])
                                            if uid in t.get('tags', []) or uid == t.get('rear_tag')), None)
                        label = tid or (f'{saved_owner} (saved, inactive)' if saved_owner else 'UNASSIGNED TAG')
                        self.append(f"{label}: {uid} at {checkpoint}" +
                                    (' — noted and ignored; trains continue' if tid is None else ''))
                except (ValueError, UnicodeError, KeyError) as exc:
                    self.append(f'Ignored reader input: {exc}')
        if not self.demo or self.demo_running:
            for tid,value in self.supervisor.tick().items():
                self.send(tid,value)
        for tid in self.supervisor.trains:
            if tid not in self.supervisor.external:
                self.backend.waiting(tid, self.supervisor.waiting[tid])
        self.sensors.set_occupancy(self.supervisor.occupied())
        self.refresh_run_status()
        self.refresh_batteries()
        self.save_learning()
        self.draw()
        self.root.after(100, self.poll)

    def close(self):
        self.closed = True
        self.alarm.close()
        self.stop_all()
        self.save_learning(force=True)
        self.backend.close()
        if self.discovery:
            threading.Thread(target=self.discovery.close, daemon=True).start()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        self.root.destroy()
