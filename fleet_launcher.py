"""Graphical startup options for the double-click command launcher."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from fleet_model import load_config
from reader_secret import (load_reader_token, save_reader_token, forget_reader_token,
                           SecretStoreError)


def choose_options():
    root = tk.Tk()
    root.title('Launch LEGO Train Fleet')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    configuration = tk.StringVar(value=str(Path(__file__).with_name('fleet.json')))
    mode = tk.StringVar(value='demo')
    secret_notice = 'The shared secret is saved in Mac Keychain when you launch Live.'
    try:
        initial_token = load_reader_token()
    except SecretStoreError as exc:
        initial_token = ''
        secret_notice = str(exc) + ' Enter the secret to use it for this launch.'
    token = tk.StringVar(value=initial_token)
    result = []
    ttk.Label(frame, text='LEGO Train Fleet', font=('TkDefaultFont', 18, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0,15))
    ttk.Label(frame, text='Configuration').grid(row=1, column=0, sticky='w')
    ttk.Entry(frame, textvariable=configuration, width=65).grid(row=2, column=0, sticky='ew')
    def browse():
        path = filedialog.askopenfilename(parent=root, title='Choose configuration',
                    initialdir=Path(configuration.get()).parent, filetypes=[('Fleet configuration', '*.json')])
        if path:
            configuration.set(path)
    ttk.Button(frame, text='Browse…', command=browse).grid(row=2, column=1, padx=(8,0))
    for row, value, label in ((3,'demo','Demo — simulated trains, no hardware connections'),
                              (4,'live','Live — Bluetooth trains, map, and Wi-Fi readers'),
                              (5,'motor','Motor only — manual Bluetooth control, no map or sensors')):
        ttk.Radiobutton(frame,text=label,variable=mode,value=value).grid(row=row,column=0,columnspan=2,sticky='w',pady=4)
    ttk.Label(frame, text='Live reader token (optional; not used in Motor only)').grid(row=6,column=0,columnspan=2,sticky='w',pady=(15,4))
    token_entry = ttk.Entry(frame, textvariable=token, show='•', width=65)
    token_entry.grid(row=7,column=0,sticky='ew')
    mode.trace_add('write',lambda *_:token_entry.configure(state='normal' if mode.get() == 'live' else 'disabled'))
    token_entry.configure(state='disabled')
    def forget():
        try:
            forget_reader_token()
        except SecretStoreError as exc:
            messagebox.showerror('Could not forget shared secret', str(exc), parent=root)
            return
        token.set('')
        os.environ.pop('TRAIN_READER_TOKEN', None)
    ttk.Button(frame, text='Forget saved secret', command=forget).grid(row=7,column=1,padx=(8,0))
    ttk.Label(frame, text=secret_notice + '\nLeave the field blank to disable readers for this launch.\nLive mode opens with automatic control disabled; connect each train when ready.',
              wraplength=610).grid(row=8,column=0,columnspan=2,sticky='w',pady=12)
    def launch():
        try:
            path = Path(configuration.get()).expanduser().resolve()
            config = load_config(path)
            if mode.get() == 'live' and token.get() and len(token.get()) < 16:
                raise ValueError('Enter at least 16 characters for the reader token, or leave it blank.')
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot launch', str(exc), parent=root)
            return
        if mode.get() == 'live':
            if token.get():
                try:
                    save_reader_token(token.get())
                except SecretStoreError as exc:
                    messagebox.showwarning('Shared secret not saved',
                        str(exc) + '\nThis launch will use the entered secret, but the saved secret was not updated.',
                        parent=root)
            os.environ['TRAIN_READER_TOKEN'] = token.get()
        result.append((path, config, mode.get() == 'demo', mode.get() == 'motor'))
        root.destroy()
    ttk.Button(frame, text='Launch', command=launch).grid(row=9,column=0,sticky='e')
    ttk.Button(frame, text='Cancel', command=root.destroy).grid(row=9,column=1,padx=(8,0))
    root.mainloop()
    return result[0] if result else None


def main():
    options = choose_options()
    if options is None:
        return
    from fleet_controller import Backend
    from fleet_ui import FleetApp
    path, config, demo, motor_only = options
    root = tk.Tk()
    app = FleetApp(root, config, path, demo, Backend, motor_only=motor_only)
    root.mainloop()
    app.backend.thread.join(timeout=5)


if __name__ == '__main__':
    main()
