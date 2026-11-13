"""View/copy/export the bundled source and upload it to a chosen active City Hub."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from train_controllers import supports
from hub_program import compiler_available, read_program, source_revision


class HubProgramDialog(tk.Toplevel):
    def __init__(self, parent, config, on_upload, demo=False, train_id=None, on_update=None):
        source = read_program()  # Fail before creating a partly initialized window.
        super().__init__(parent)
        self.title('City Hub program')
        self.geometry('900x700')
        self.minsize(650, 500)
        self.source, self.on_upload, self.demo = source, on_upload, demo
        self.on_update = on_update
        self.busy = False
        self.pending = set()
        self.results = {}
        self.batch = False
        self.trains = {f"{t['name']} — {t['hub_name']} ({t['id']})": dict(t)
                       for t in config['trains'] if supports(t, 'city_hub_program')}
        self.status = tk.StringVar(value='Select a train to upload. Uses saved train settings; apply hub-name changes first.')
        ttk.Label(self, text='City Hub motor program', font=('Helvetica', 15, 'bold')).pack(anchor='w', padx=14, pady=(12,4))
        ttk.Label(self, text=f'Bundled city_hub.py · source {source_revision(source)} · motor on port A').pack(anchor='w', padx=14)
        ttk.Label(self, text='An empty Pybricks hub works; factory LEGO firmware must be replaced using Pybricks Code first.',
                  wraplength=840).pack(anchor='w', padx=14, pady=(4,8))
        batch_actions = ttk.Frame(self)
        batch_actions.pack(fill='x', padx=14, pady=(0,8))
        self.update_button = ttk.Button(batch_actions, text='Update hubs', command=self.update_hubs)
        self.update_button.pack(side='left')
        ttk.Label(batch_actions, text='Stop active City Hubs → scan names → connect → upload → verify zero throttle',
                  wraplength=600).pack(side='left', padx=10)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=14)
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text='Bundled code')
        self.code = tk.Text(frame, wrap='none', font=('Menlo', 11), background='white', foreground='black')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.code.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.code.xview)
        self.code.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        self.code.pack(fill='both', expand=True)
        self.code.insert('1.0', source)
        self.code.configure(state='disabled')
        self.debug_frame = ttk.Frame(self.tabs)
        self.tabs.add(self.debug_frame, text='Update progress / debug')
        self.progress = ttk.Treeview(self.debug_frame, columns=('hub', 'status'), show='headings', height=4)
        self.progress.heading('hub', text='Active train / hub name')
        self.progress.heading('status', text='Update status')
        self.progress.column('hub', width=250)
        self.progress.column('status', width=560)
        self.progress.pack(fill='x')
        for train in self.trains.values():
            self.progress.insert('', 'end', iid=train['id'],
                                 values=(f"{train['name']} / {train['hub_name']}", 'Not updated'))
        debug_actions = ttk.Frame(self.debug_frame)
        debug_actions.pack(fill='x', pady=5)
        ttk.Button(debug_actions, text='Copy debug log', command=self.copy_debug).pack(side='left')
        ttk.Button(debug_actions, text='Save debug log…', command=self.save_debug).pack(side='left', padx=6)
        debug_body = ttk.Frame(self.debug_frame)
        debug_body.pack(fill='both', expand=True)
        self.debug_log = tk.Text(debug_body, wrap='word', font=('Menlo', 10), state='disabled',
                                 background='white', foreground='black')
        debug_scroll = ttk.Scrollbar(debug_body, command=self.debug_log.yview)
        self.debug_log.configure(yscrollcommand=debug_scroll.set)
        debug_scroll.pack(side='right', fill='y')
        self.debug_log.pack(fill='both', expand=True)
        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=14, pady=8)
        ttk.Button(actions, text='Copy code', command=self.copy_code).pack(side='left')
        ttk.Button(actions, text='Save a copy…', command=self.save_copy).pack(side='left', padx=6)
        ttk.Button(actions, text='Close', command=self.destroy).pack(side='right')
        target = ttk.Frame(self)
        target.pack(fill='x', padx=14)
        ttk.Label(target, text='Upload to:').pack(side='left', padx=(0,6))
        self.choice = ttk.Combobox(target, state='readonly', values=list(self.trains))
        self.choice.pack(side='left', fill='x', expand=True)
        self.upload_button = ttk.Button(target, text='Upload hub program…', command=self.upload)
        self.upload_button.pack(side='right', padx=(8,0))
        self.choice.bind('<<ComboboxSelected>>', lambda _: self.refresh())
        if train_id is None and self.trains:
            self.choice.current(0)
        else:
            self.choice.set(next((name for name, t in self.trains.items() if t['id'] == train_id), ''))
        ttk.Label(self, textvariable=self.status, wraplength=840, justify='left').pack(fill='x', padx=14, pady=(8,14))
        self.refresh()
        self.transient(parent)

    def refresh(self):
        available = compiler_available()
        enabled = not self.demo and not self.busy and available and self.choice.get() in self.trains
        self.upload_button.configure(state='normal' if enabled else 'disabled')
        update_enabled = not self.demo and not self.busy and available and self.trains and self.on_update
        self.update_button.configure(state='normal' if update_enabled else 'disabled')
        self.choice.configure(state='disabled' if self.busy else 'readonly')
        if self.demo:
            self.status.set('Demo mode: code viewing, copying and saving are available. Upload in Live or Motor only mode.')
        elif not available:
            self.status.set('Upload compiler is missing. Install requirements.txt, then reopen the controller.')
        elif not self.trains:
            self.status.set('Add a fleet-controlled City Hub train to the active list to upload. External trains use their own controller.')

    def copy_code(self):
        self.clipboard_clear()
        self.clipboard_append(self.source)

    def save_copy(self):
        filename = filedialog.asksaveasfilename(parent=self, title='Save City Hub program',
                    initialfile='city_hub.py', defaultextension='.py', filetypes=[('Python program', '*.py')])
        if filename:
            try:
                Path(filename).write_text(self.source, encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('Cannot save program', str(exc), parent=self)

    def upload(self):
        train = self.trains.get(self.choice.get())
        if not train or self.demo or self.busy:
            return
        if not messagebox.askyesno('Upload City Hub program?',
                f"Replace the saved program on {train['name']} — hub {train['hub_name']}?\n\n"
                'This train will stop and remain stopped after upload. Its motor must be on port A. '
                'Disconnect Pybricks Code from this hub before continuing.', parent=self):
            return
        try:
            self.on_upload(train['id'], train['hub_name'], self.source)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror('Cannot upload program', str(exc), parent=self)
            return
        self.busy = True
        self.pending = {train['id']}
        self.batch = False
        self.tabs.select(self.debug_frame)
        self.status.set('Preparing upload…')
        self.refresh()

    def update_hubs(self):
        if self.demo or self.busy or not self.trains or self.on_update is None:
            return
        try:
            self.on_update(list(self.trains.values()), self.source)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror('Cannot update hubs', str(exc), parent=self)
            return
        self.batch = True
        self.pending = {train['id'] for train in self.trains.values()}
        self.busy = True
        for tid in self.pending:
            self.results[tid] = ('progress', 'Waiting for Bluetooth scan…')
            self.progress.set(tid, 'status', 'Waiting for Bluetooth scan…')
        self.status.set('Scanning the active train list, then connecting and updating each matching hub…')
        self.tabs.select(self.debug_frame)
        self.refresh()

    def receive_debug(self, text):
        self.debug_log.configure(state='normal')
        self.debug_log.insert('end', text + '\n')
        lines = int(self.debug_log.index('end-1c').split('.')[0])
        if lines > 10000:
            self.debug_log.delete('1.0', f'{lines-10000+1}.0')
        self.debug_log.see('end')
        self.debug_log.configure(state='disabled')

    def copy_debug(self):
        self.clipboard_clear()
        self.clipboard_append(self.debug_log.get('1.0', 'end-1c'))

    def save_debug(self):
        filename = filedialog.asksaveasfilename(parent=self, title='Save hub update debug log',
                    initialfile='hub-update-debug.txt', defaultextension='.txt', filetypes=[('Text log', '*.txt')])
        if filename:
            try:
                Path(filename).write_text(self.debug_log.get('1.0', 'end-1c'), encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('Cannot save debug log', str(exc), parent=self)

    def receive_status(self, train_id, phase, text):
        self.results[train_id] = (phase, text)
        if phase == 'progress':
            self.pending.add(train_id)
        else:
            self.pending.discard(train_id)
        self.busy = bool(self.pending)
        if self.progress.exists(train_id):
            self.progress.set(train_id, 'status', text)
        selected = self.trains.get(self.choice.get())
        if self.batch:
            if self.busy:
                self.status.set(f'{len(self.pending)} hub(s) remaining. {text}')
            else:
                complete = sum(phase == 'complete' for phase, _ in self.results.values())
                self.status.set(f'Update finished: {complete} succeeded; {len(self.trains)-complete} failed. See debug log.')
        elif selected is not None and selected['id'] == train_id:
            self.status.set(text)
        self.refresh()
