"""Dedicated live sensor/gate management and tag/train assignment screen."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
from fleet_setup import register_tag
from fleet_model import all_trains
from sensor_registry import bind_sensor


class SensorDialog(tk.Toplevel):
    def __init__(self, parent, registry, get_config, apply_config, live):
        super().__init__(parent)
        self.title('Sensors, gates & train tags')
        self.geometry('1120x650')
        self.registry, self.get_config, self.apply_config = registry, get_config, apply_config
        self.live = live
        self.notice = tk.StringVar(value=('Sensors appear automatically after joining Wi-Fi. Select one to identify or assign it.'
                                           if live else 'Live receiver is disabled. Start without --demo and set TRAIN_READER_TOKEN.'))
        ttk.Label(self, textvariable=self.notice, wraplength=1060).pack(fill='x', padx=12, pady=12)
        notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True, padx=12)
        sensors = ttk.Frame(notebook)
        tags = ttk.Frame(notebook)
        notebook.add(sensors, text='Sensors & gates')
        notebook.add(tags, text='Tags & trains')
        self.sensors = self.table(sensors, [('node','Sensor ID',170), ('gate','Gate',130), ('status','Status',135),
                                          ('ip','IP address',125), ('rssi','Signal',70), ('hz','Reports/s',75),
                                          ('age','Last report',80), ('led','LED command',125)])
        actions = ttk.Frame(sensors)
        actions.pack(fill='x', pady=12)
        self.identify_button = ttk.Button(actions, text='Blink LED for 10 seconds', command=self.identify,
                                         state='normal' if live else 'disabled')
        self.identify_button.pack(side='left', padx=4)
        ttk.Label(actions, text='Gate:').pack(side='left', padx=6)
        self.gate = ttk.Combobox(actions, state='readonly', width=26)
        self.gate.pack(side='left')
        ttk.Button(actions, text='Assign selected sensor', command=self.assign_gate).pack(side='left', padx=6)
        ttk.Button(actions, text='Unassign sensor', command=self.unassign_gate).pack(side='left')
        ttk.Label(sensors, text='Status targets 10 reports/second. Offline means no report for 1.5 seconds.\n'
                  'Gate changes stop/disconnect trains. Tag assignments save live without restarting the fleet.').pack(anchor='w', pady=8)
        self.tags = self.table(tags, [('uid','Tag UID',200), ('train','Assigned train',180),
                                     ('sensor','Last sensor',180), ('age','Last scan',120)])
        actions = ttk.Frame(tags)
        actions.pack(fill='x', pady=12)
        ttk.Label(actions, text='Train:').pack(side='left', padx=4)
        self.train = ttk.Combobox(actions, state='readonly', width=28)
        self.train.pack(side='left')
        self.tag_role = ttk.Combobox(actions, state='readonly', width=10, values=['Front','Rear'])
        self.tag_role.set('Front')
        self.tag_role.pack(side='left', padx=4)
        ttk.Button(actions, text='Assign selected tag', command=self.assign_tag).pack(side='left', padx=6)
        ttk.Button(actions, text='Unassign tag', command=self.unassign_tag).pack(side='left')
        ttk.Label(tags, text='Scan a tag at any discovered sensor, even before assigning that sensor to a gate.\n'
                  'Select its UID above, choose a train, and assign it. A tag can belong to only one train.').pack(anchor='w', pady=8)
        self.after_id = None
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    def table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=50)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)
        return tree

    @staticmethod
    def sync_rows(tree, rows):
        wanted = set()
        for key, values in rows:
            wanted.add(key)
            if tree.exists(key):
                tree.item(key, values=values)
            else:
                tree.insert('', 'end', iid=key, values=values)
        for key in tree.get_children():
            if key not in wanted:
                tree.delete(key)

    @staticmethod
    def choices(combo, items):
        selected = combo.get()
        values = [f"{item['name']} ({item['id']})" for item in items]
        combo.configure(values=values)
        if selected not in values:
            combo.set(values[0] if values else '')
        return dict(zip(values, [item['id'] for item in items]))

    def refresh(self):
        config = self.get_config()
        self.gate_ids = self.choices(self.gate, config['readers'])
        collection = [*config['trains'], *[dict(t, name=t['name']+' · saved') for t in config.get('saved_trains', [])]]
        self.train_ids = self.choices(self.train, collection)
        names = {r['id']: r['name'] for r in config['readers']}
        sensors, recent = self.registry.snapshot()
        rows = []
        for s in sensors:
            status = ('Offline' if not s['online'] else 'PN532 missing' if not s['pn532']
                      else 'Reader stalled' if s['poll_age_ms'] > 2000 else 'Online')
            led = ('Blinking' if s['identifying'] and s['online'] else 'Command pending' if s['identify_pending']
                   else 'Not configured' if s.get('led') == 'none'
                   else 'On — occupied' if s['occupied'] else 'Off')
            rows.append((s['node'], (s['node'], names.get(s['gate'], 'Unassigned'), status, s.get('ip','—'),
                                    f"{s['rssi']} dBm" if s['online'] else '—',
                                    f"{s.get('hz',0):.1f}" if s['online'] else '—',
                                    f"{s['age']:.1f}s" if s['age'] is not None else 'Never', led)))
        self.sync_rows(self.sensors, rows)
        owners = {uid: t['name']+' · front' for t in collection for uid in t.get('tags', [])}
        owners.update({t['rear_tag']:t['name']+' · rear' for t in collection if t.get('rear_tag')})
        scans = {t['uid']: t for t in recent}
        uids = list(scans) + sorted(set(owners) - set(scans))
        self.sync_rows(self.tags, [(uid, (uid, owners.get(uid, 'Unassigned'), scans.get(uid, {}).get('node', '—'),
                                        f"{scans[uid]['age']:.1f}s" if uid in scans else 'Not scanned')) for uid in uids])
        self.after_id = self.after(100, self.refresh)

    def selected(self, table):
        selection = table.selection()
        if not selection:
            raise ValueError('Select a row first')
        return selection[0]

    def perform(self, action):
        try:
            action()
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot apply change', str(exc), parent=self)

    def identify(self):
        def action():
            node = self.selected(self.sensors)
            self.registry.identify(node)
            self.notice.set(f'Blink requested for {node}; waiting for acknowledgment from the sensor.')
        self.perform(action)

    def assign_gate(self):
        def action():
            node = self.selected(self.sensors)
            gate = self.gate_ids.get(self.gate.get())
            if not gate:
                raise ValueError('Add a gate in Train & loop setup first')
            config = self.get_config()
            old = next(r for r in config['readers'] if r['id'] == gate).get('node')
            if old and old != node and not messagebox.askyesno(
                    'Replace gate sensor?', f'This gate is assigned to {old}. Replace it with {node}?', parent=self):
                return
            self.apply_config(bind_sensor(config, node, gate, replace=True))
            self.notice.set(f'Saving {node} → {self.gate.get()}. Reconnect trains after setup applies.')
        self.perform(action)

    def unassign_gate(self):
        def action():
            node = self.selected(self.sensors)
            self.apply_config(bind_sensor(self.get_config(), node, ''))
            self.notice.set(f'Unassigned {node}. It will still report status and scanned tags.')
        self.perform(action)

    def assign_tag(self):
        def action():
            uid = self.selected(self.tags)
            config = copy.deepcopy(self.get_config())
            register_tag(config, self.train_ids.get(self.train.get()), uid, self.tag_role.get().lower())
            self.apply_config(config)
            self.notice.set(f'Saving tag {uid} → {self.train.get()}.')
        self.perform(action)

    def unassign_tag(self):
        def action():
            uid = self.selected(self.tags)
            config = copy.deepcopy(self.get_config())
            for train in all_trains(config):
                train['tags'] = [tag for tag in train.get('tags', []) if tag != uid]
                if train.get('rear_tag') == uid:
                    train.pop('rear_tag')
            self.apply_config(config)
            self.notice.set(f'Tag {uid} is unassigned.')
        self.perform(action)

    def close(self):
        if self.after_id:
            self.after_cancel(self.after_id)
        self.destroy()
