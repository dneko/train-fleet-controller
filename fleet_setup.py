"""Editable train roster and ordered loop checkpoints."""
import copy
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox
from fleet_model import all_trains, is_external, load_config, normalize_uid
from map_geometry import STUD_M, update_distances
from train_controllers import controller_spec, controller_types


def register_tag(config, train_id, uid, role='front'):
    """Assign a normalized UID without silently stealing another train's tag."""
    uid = normalize_uid(uid)
    target = next((t for t in all_trains(config) if t['id'] == train_id), None)
    if target is None:
        raise ValueError('Select a train first')
    for train in all_trains(config):
        assigned = [*train.get('tags', []), *([train['rear_tag']] if train.get('rear_tag') else [])]
        if uid in [normalize_uid(tag) for tag in assigned] and train is not target:
            raise ValueError(f"Tag already belongs to {train['name']}")
    if role == 'rear':
        target['tags'] = [tag for tag in target.get('tags', []) if normalize_uid(tag) != uid]
        target['rear_tag'] = uid
    elif role == 'front':
        if target.get('rear_tag') and normalize_uid(target['rear_tag']) == uid:
            target.pop('rear_tag')
        if uid not in [normalize_uid(tag) for tag in target.get('tags', [])]:
            target.setdefault('tags', []).append(uid)
    else:
        raise ValueError('Tag role must be front or rear')
    return uid


def move_train(config, train_id, active):
    """Move the whole saved record without losing tags, photos or learned times."""
    source, destination = ('saved_trains', 'trains') if active else ('trains', 'saved_trains')
    train = next((t for t in config.get(source, []) if t['id'] == train_id), None)
    if train is None:
        raise ValueError('Select a saved train' if active else 'Select an active train')
    config[source].remove(train)
    config.setdefault(destination, []).append(train)
    automation = config.get('automation', {})
    if not active and automation.get('pace_train_id') == train_id:
        automation.pop('pace_train_id')


def save_config(path, config):
    path = Path(path)
    update_distances(config)
    # Validate before replacing the saved configuration.
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as target:
            name = target.name
            json.dump(config, target, indent=2)
            target.write('\n')
        validated = load_config(name)
        os.replace(name, path)
        return validated
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


class ControllerSettingsDialog(tk.Toplevel):
    """Driver-declared fields; changes are staged in the train library."""
    def __init__(self, parent, train, on_apply):
        super().__init__(parent)
        self.train, self.on_apply = copy.deepcopy(train), on_apply
        self.spec = controller_spec(train)
        self.title(f'{self.spec.label} settings')
        self.variables = {}
        form = ttk.Frame(self, padding=16)
        form.pack(fill='both', expand=True)
        for row, field in enumerate(self.spec.fields):
            ttk.Label(form, text=field.label).grid(row=row, column=0, sticky='w', padx=(0, 14), pady=6)
            variable = tk.StringVar(value=self.spec.options(train)[field.key])
            self.variables[field.key] = variable
            ttk.Entry(form, textvariable=variable, width=36).grid(row=row, column=1, sticky='ew')
        if not self.spec.fields:
            ttk.Label(form, text='This controller has no connection settings.').grid(row=0, column=0)
        ttk.Button(form, text='Use these settings', command=self.apply).grid(
            row=len(self.spec.fields)+1, column=0, columnspan=2, pady=(14, 0))
        self.transient(parent)
        self.previous_grab = parent.grab_current()
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.grab_set()

    def apply(self):
        edited = copy.deepcopy(self.train)
        for field in self.spec.fields:
            value = self.variables[field.key].get().strip()
            if field.legacy_key:
                edited[field.legacy_key] = value
            else:
                edited.setdefault('controller_options', {})[field.key] = value
        try:
            self.spec.validate(edited)
        except ValueError as exc:
            messagebox.showerror('Invalid controller settings', str(exc), parent=self)
            return
        self.on_apply(edited)
        self.destroy()

    def destroy(self):
        super().destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set()


class SetupDialog(tk.Toplevel):
    def __init__(self, parent, config, on_apply, can_scan=False, train_id=None, library_only=False, on_program=None):
        super().__init__(parent)
        self.train_only = train_id is not None
        self.title('Train settings' if self.train_only else 'Train library' if library_only else 'Fleet and loop setup')
        self.geometry('900x720')
        self.config_data = copy.deepcopy(config)
        self.config_data.setdefault('saved_trains', [])
        self.original_readers = copy.deepcopy(config['readers'])
        self.displayed = {}
        self.on_apply = on_apply
        self.pages = {}
        self.controller_choices = {spec.label: spec.type_id for spec in controller_types()}
        self.controller_widgets = {}
        self.scan_target = None
        self.scan_section = 'trains'
        self.scan_status = tk.StringVar(value='Select a train, then scan its tag to register it.')
        ttk.Label(self, text='Train settings save live. Controller or polarity changes stop only that train.\n'
                  'Roster, control ownership, or loop changes stop and rebuild the fleet.', justify='center').pack(pady=8)
        notebook = self.notebook = ttk.Notebook(self)
        notebook.pack(fill='both', expand=True)
        train_fields = [('id','ID'), ('name','Display name'), ('control_mode','Controlled by'),
                                ('controller_type','Motor controller'), ('hub_name','Pybricks Bluetooth hub name'),
                                ('forward_sign','Forward polarity (+1 or -1)'), ('length_m','Train length (studs)'),
                                ('stop_margin_m','Stopping margin (studs)'), ('start_throttle','Auto starting throttle'),
                                ('max_throttle','Maximum throttle'), ('tags','Front RFID tags (comma separated)'),
                                ('rear_tag','Rear RFID tag (optional, forward only)')]
        for section, title, fields in (
            ('trains', 'Active trains', train_fields),
            ('saved_trains', 'Saved trains', train_fields),
            ('readers', 'Loop checkpoints', [('id','Gate ID'), ('name','Display name'),
                        ('node','Sensor ID (blank = unassigned)'), ('distance_to_next_m','Track distance to next point (studs)')])):
            if self.train_only and section != 'trains' or library_only and section == 'readers':
                continue
            page = ttk.Frame(notebook, padding=10)
            notebook.add(page, text=title)
            descriptions = {
                'trains': 'Active trains appear on the controls and map. Removing one keeps its RFIDs and settings in Saved trains.',
                'saved_trains': 'Saved trains stay inactive. Add one to active when ready, or delete it from this library.',
                'readers': 'List order is travel direction. Last point connects to first. Drag positions on the main map.',
            }
            tk.Label(page, text=descriptions[section], wraplength=850).pack()
            body = ttk.Frame(page)
            body.pack(fill='both', expand=True)
            listing = tk.Listbox(body, exportselection=False, width=30)
            listing.pack(side='left', fill='y', padx=8)
            form = ttk.Frame(body)
            form.pack(side='left', fill='both', expand=True)
            variables = {}
            for row, (key, label) in enumerate(fields):
                ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=6)
                var = tk.StringVar()
                if key == 'control_mode':
                    choice = ttk.Combobox(form, textvariable=var, values=['Fleet controller', 'External controller'],
                                         state='disabled' if self.train_only else 'readonly', width=28)
                    choice.grid(row=row,column=1,sticky='ew')
                    choice.bind('<<ComboboxSelected>>', lambda _, s=section: self.refresh_controller_controls(s))
                elif key == 'controller_type':
                    controls = ttk.Frame(form)
                    controls.grid(row=row, column=1, sticky='ew')
                    choice = ttk.Combobox(controls, textvariable=var, values=list(self.controller_choices),
                                          state='readonly', width=21)
                    choice.pack(side='left', fill='x', expand=True)
                    choice.bind('<<ComboboxSelected>>', lambda _, s=section: self.refresh_controller_controls(s))
                    button = ttk.Button(controls, text='Settings…', command=lambda s=section: self.controller_settings(s))
                    button.pack(side='left', padx=(4, 0))
                    self.controller_widgets[section] = [choice, button]
                else:
                    readonly = (key == 'id' and self.train_only) or (key == 'distance_to_next_m' and config.get('map_m_per_unit'))
                    entry = ttk.Entry(form, textvariable=var, width=30, state='readonly' if readonly else 'normal')
                    entry.grid(row=row, column=1, sticky='ew')
                    if key == 'hub_name':
                        self.controller_widgets[section].append(entry)
                variables[key] = var
            self.pages[section] = (listing, variables)
            listing.bind('<<ListboxSelect>>', lambda _, s=section: self.select(s))
            buttons = ttk.Frame(page)
            buttons.pack(fill='x', pady=10)
            actions = [('Add' if section == 'readers' else 'New train', lambda s=section: self.add(s)),
                       ('Update', lambda s=section: self.update_item(s)),
                       ('Remove from active…' if section == 'trains' else
                        'Add to active' if section == 'saved_trains' else 'Remove',
                        lambda s=section: self.remove(s))]
            if section == 'saved_trains':
                actions.append(('Delete train…', self.delete_saved_train))
            for label, callback in actions:
                if self.train_only and label != 'Update':
                    continue
                ttk.Button(buttons, text=label, command=callback).pack(side='left', padx=4)
            if section == 'readers':
                for label, amount in [('Earlier in loop', -1), ('Later in loop', 1)]:
                    ttk.Button(buttons, text=label, command=lambda a=amount: self.reorder(a)).pack(side='left')
            else:
                scan_buttons = ttk.Frame(page)
                scan_buttons.pack(fill='x', pady=(0, 6))
                ttk.Button(scan_buttons, text='Scan tag for selected train', command=lambda s=section: self.arm_scan(s),
                           state='normal' if can_scan else 'disabled').pack(side='left', padx=4)
                ttk.Button(scan_buttons, text='Cancel scan', command=self.cancel_scan).pack(side='left')
                ttk.Label(page, textvariable=self.scan_status, wraplength=800).pack(fill='x')
                if not can_scan:
                    self.scan_status.set('Live scanning requires the Wi-Fi receiver (not demo mode). You can still enter UIDs above.')
            self.refresh(section)
        if self.train_only:
            self.refresh('trains', next(i for i, t in enumerate(config['trains']) if t['id'] == train_id))
        if on_program is not None:
            def show_program():
                listing, _ = self.pages['trains']
                selected = listing.curselection()
                tid = self.config_data['trains'][selected[0]]['id'] if selected else None
                on_program(tid)
            ttk.Button(self, text='City Hub program…', command=show_program).pack(pady=(8,0))
        ttk.Button(self, text='Save train settings' if self.train_only else
                   'Save and apply library' if library_only else 'Save and apply setup', command=self.apply).pack(pady=12)
        self.transient(parent)
        self.grab_set()

    def refresh(self, section, selected=0):
        listing, _ = self.pages[section]
        listing.delete(0, 'end')
        for item in self.config_data[section]:
            suffix = ' · external' if section != 'readers' and is_external(item) else ''
            listing.insert('end', f"{item['name']} ({item['id']}){suffix}")
        if listing.size():
            listing.selection_set(min(selected, listing.size()-1))
            self.select(section)
        else:
            for var in self.pages[section][1].values():
                var.set('')
            self.displayed[section] = {}
            self.refresh_controller_controls(section)

    def select(self, section):
        if section != 'readers' and self.scan_target is not None:
            self.cancel_scan()
        listing, variables = self.pages[section]
        if not listing.curselection():
            return
        item = self.config_data[section][listing.curselection()[0]]
        for key, var in variables.items():
            if key == 'control_mode':
                var.set('External controller' if item.get(key) == 'external' else 'Fleet controller')
                continue
            if key == 'controller_type':
                var.set(next((label for label, ident in self.controller_choices.items()
                              if ident == item.get(key, 'pybricks')), item.get(key, 'pybricks')))
                continue
            value = item.get(key, {'forward_sign':1,'distance_to_next_m':1,'length_m':.5,'stop_margin_m':.3,'start_throttle':25,'max_throttle':70}.get(key,''))
            if key in ('length_m', 'stop_margin_m', 'distance_to_next_m'):
                value = round(value / STUD_M, 4)
            var.set(', '.join(value) if key == 'tags' else str(value))
        self.displayed[section] = {key: var.get() for key, var in variables.items()}
        self.refresh_controller_controls(section)

    def refresh_controller_controls(self, section):
        if section not in self.controller_widgets:
            return
        listing, variables = self.pages[section]
        choice, button, hub = self.controller_widgets[section]
        controlled = bool(listing.curselection()) and variables['control_mode'].get() != 'External controller'
        choice.configure(state='readonly' if controlled else 'disabled')
        button.configure(state='normal' if controlled else 'disabled')
        pybricks = self.controller_choices.get(variables['controller_type'].get()) == 'pybricks'
        hub.configure(state='normal' if controlled and pybricks else 'disabled')

    def controller_settings(self, section):
        if not self.update_item(section):
            return
        listing, _ = self.pages[section]
        if not listing.curselection():
            return
        index = listing.curselection()[0]
        train = self.config_data[section][index]
        if is_external(train):
            return
        def stage(edited):
            self.config_data[section][index] = edited
            self.refresh(section, index)
        self.controller_dialog = ControllerSettingsDialog(self, train, stage)

    def cancel_scan(self):
        self.scan_target = None
        self.scan_status.set('Scan cancelled. Select a train and click Scan tag to try again.')

    def arm_scan(self, section='trains'):
        if not self.update_item(section):
            return
        listing, _ = self.pages[section]
        if not listing.curselection():
            return
        train = self.config_data[section][listing.curselection()[0]]
        self.scan_target = train['id']
        self.scan_section = section
        self.scan_status.set(f"Waiting for {train['name']}: remove the tag for one second, then scan it at a configured reader.")

    def receive_tag(self, uid, checkpoint):
        if self.scan_target is None:
            return
        target, self.scan_target = self.scan_target, None
        try:
            uid = register_tag(self.config_data, target, uid)
        except ValueError as exc:
            self.scan_status.set(str(exc))
            return
        index = next(i for i, t in enumerate(self.config_data[self.scan_section]) if t['id'] == target)
        self.refresh(self.scan_section, index)
        self.scan_status.set(f'Tag {uid} captured at {checkpoint}. Save and apply to keep this assignment.')

    def add(self, section):
        if not self.update_item(section):
            return
        used = {item['id'] for item in (self.config_data[section] if section == 'readers' else all_trains(self.config_data))}
        number = 1
        prefix = 'point' if section == 'readers' else 'train'
        while f'{prefix}_{number}' in used:
            number += 1
        ident = f'{prefix}_{number}'
        item = {'id': ident, 'name': ident}
        if section != 'readers':
            item.update(hub_name=ident, forward_sign=1, tags=[])
        else:
            item.update(node='', distance_to_next_m=1.0, x=100+number*40, y=200)
        self.config_data[section].append(item)
        self.refresh(section, len(self.config_data[section])-1)

    def update_item(self, section):
        listing, variables = self.pages[section]
        if not listing.curselection():
            return True
        index = listing.curselection()[0]
        item = dict(self.config_data[section][index])
        try:
            for key, var in variables.items():
                text = var.get().strip()
                if text == self.displayed.get(section, {}).get(key):
                    continue
                if key == 'control_mode':
                    item[key] = 'external' if text == 'External controller' else 'fleet'
                    continue
                if key == 'controller_type':
                    item[key] = self.controller_choices[text]
                    # Options belong to one driver's schema; do not pass them to another.
                    item.pop('controller_options', None)
                    continue
                item[key] = ([tag.strip() for tag in text.split(',') if tag.strip()] if key == 'tags' else
                             int(text) if key == 'forward_sign' else
                             float(text) * STUD_M if key in ('distance_to_next_m','length_m','stop_margin_m') else
                             int(text) if key in ('start_throttle','max_throttle') else text)
            self.config_data[section][index] = item
            self.clean_pace_selection()
            self.refresh(section, index)
            return True
        except ValueError:
            messagebox.showerror('Invalid setup', 'Polarity must be +1 or -1; distance must be a number.', parent=self)
            return False

    def remove(self, section):
        listing, _ = self.pages[section]
        if listing.curselection():
            if section == 'readers':
                del self.config_data[section][listing.curselection()[0]]
                self.refresh(section)
                return
            destination = 'saved_trains' if section == 'trains' else 'trains'
            if not self.update_item(section) or not self.update_item(destination):
                return
            train = self.config_data[section][listing.curselection()[0]]
            if section == 'trains' and not messagebox.askyesno('Remove from active trains?',
                    f"Confirm {train['name']} is physically off the track.\n\n"
                    'Its RFID tags, settings, photo and learned times will stay in Saved trains. '
                    'It will no longer be controlled or tracked after you save and apply.', parent=self):
                return
            move_train(self.config_data, train['id'], active=section == 'saved_trains')
            self.clean_pace_selection()
            self.refresh(section)
            self.refresh(destination, len(self.config_data[destination])-1)
            self.notebook.select(self.pages[destination][0].master.master)

    def delete_saved_train(self):
        listing, _ = self.pages['saved_trains']
        selected = listing.curselection()
        if not selected:
            return
        index = selected[0]
        train = self.config_data['saved_trains'][index]
        if not messagebox.askyesno('Delete saved train?',
                f"Delete {train['name']} ({train['id']}) from Saved trains?\n\n"
                'Its saved settings, RFID assignments and learned times will be removed '
                'from this configuration when you save and apply.', parent=self):
            return
        if self.scan_target == train['id']:
            self.cancel_scan()
        del self.config_data['saved_trains'][index]
        self.refresh('saved_trains', index)

    def clean_pace_selection(self):
        automation = self.config_data.get('automation', {})
        selected = automation.get('pace_train_id')
        if selected and not any(t['id'] == selected and t.get('control_mode') == 'external'
                                for t in self.config_data['trains']):
            automation.pop('pace_train_id', None)

    def reorder(self, amount):
        listing, _ = self.pages['readers']
        if listing.curselection():
            index = listing.curselection()[0]
            other = index + amount
            items = self.config_data['readers']
            if 0 <= other < len(items):
                items[index], items[other] = items[other], items[index]
                self.refresh('readers', other)

    def apply(self):
        if not all(self.update_item(s) for s in self.pages):
            return
        # Directed loop supersedes the earlier undirected segment list.
        if self.config_data['readers'] != self.original_readers:
            self.config_data['segments'] = []
        try:
            self.on_apply(self.config_data)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot save setup', str(exc), parent=self)
            return
        self.destroy()
