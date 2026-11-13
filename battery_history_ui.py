"""Battery history charts using Tk's canvas, with no plotting dependency."""
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from battery_history import BatteryHistory
from battery_monitor import BATTERY_STALE_SECONDS
from train_controllers import controller_label, supports

WINDOWS = {'1 minute': 60, '5 minutes': 300, '15 minutes': 900,
           '1 hour': 3600, '4 hours': 14400, 'All recorded': None}
COLORS = {'volts': '#2563eb', 'amps': '#08916c', 'watts': '#9333ea',
          'demand': '#d97706', 'sent': '#475569'}


class BatteryHistoryDialog(tk.Toplevel):
    def __init__(self, parent, app, train_id=None):
        super().__init__(parent)
        self.app = app
        self.title('Battery history and demand')
        self.geometry('1020x850')
        self.minsize(760, 740)
        self.transient(parent)
        self.timer = None
        self.rows = []
        self.plot_bounds = None
        self.recording = tk.BooleanVar(value=app.battery_recording)
        bar = ttk.Frame(self, padding=(14, 10))
        bar.pack(fill='x')
        self.record_button = ttk.Checkbutton(bar, text='Record power history (supported controllers)',
                                            variable=self.recording, command=self.toggle_recording)
        self.record_button.pack(side='left')
        if app.demo:
            self.record_button.configure(state='disabled')
        self.export_button = ttk.Button(bar, text='Export visible samples…', command=self.export)
        self.export_button.pack(side='right')
        self.record_status = tk.StringVar()
        ttk.Label(self, textvariable=self.record_status, padding=(14, 0)).pack(fill='x')
        controls = ttk.Frame(self, padding=(14, 10))
        controls.pack(fill='x')
        ttk.Label(controls, text='Train').pack(side='left', padx=(0, 6))
        self.choice = ttk.Combobox(controls, state='readonly', width=32)
        self.choice.pack(side='left', fill='x', expand=True)
        self.choice.bind('<<ComboboxSelected>>', lambda _: self.refresh())
        ttk.Label(controls, text='Time range').pack(side='left', padx=(16, 6))
        self.window = ttk.Combobox(controls, state='readonly', values=list(WINDOWS), width=14)
        self.window.set('5 minutes')
        self.window.pack(side='left')
        self.window.bind('<<ComboboxSelected>>', lambda _: self.refresh())
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, padding=(14, 0), wraplength=960).pack(fill='x')
        profile_bar = ttk.Frame(self, padding=(14, 6))
        profile_bar.pack(fill='x')
        self.alerts = tk.BooleanVar(value=app.power_alerts_enabled)
        self.alert_button = ttk.Checkbutton(profile_bar, text='Current advisories while recording', variable=self.alerts,
                                           command=lambda: app.set_power_alerts(self.alerts.get()))
        self.alert_button.pack(side='left')
        self.reset_button = ttk.Button(profile_bar, text='Reset train history & profile', command=self.reset_data)
        self.reset_button.pack(side='right')
        setpoint_bar = ttk.Frame(self, padding=(14, 4))
        setpoint_bar.pack(fill='x')
        self.high_percent, self.low_percent, self.confirm_samples = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.setpoint_entries = []
        for label, variable in (('High: above expected %', self.high_percent),
                                ('Low: below expected %', self.low_percent),
                                ('Readings', self.confirm_samples)):
            ttk.Label(setpoint_bar, text=label).pack(side='left', padx=(0, 4))
            entry = ttk.Entry(setpoint_bar, textvariable=variable, width=4)
            entry.pack(side='left', padx=(0, 10))
            self.setpoint_entries.append(entry)
        self.apply_setpoints_button = ttk.Button(setpoint_bar, text='Apply setpoints', command=self.apply_setpoints)
        self.apply_setpoints_button.pack(side='right')
        self.setpoint_status = tk.StringVar()
        ttk.Label(self, textvariable=self.setpoint_status, padding=(14, 0), wraplength=960).pack(fill='x')
        self.loaded_setpoints = None
        alarm_bar = ttk.Frame(self, padding=(14, 4))
        alarm_bar.pack(fill='x')
        self.alarm_status = tk.StringVar()
        ttk.Label(alarm_bar, textvariable=self.alarm_status, foreground='#991b1b').pack(side='left')
        self.clear_alarm_button = ttk.Button(alarm_bar, text='Clear alarm', command=self.clear_alarm)
        self.clear_alarm_button.pack(side='right')
        self.view_alarm_button = ttk.Button(alarm_bar, text='View alarm',
                                           command=lambda: app.show_power_alert(self.choices.get(self.choice.get())))
        self.view_alarm_button.pack(side='right', padx=8)
        self.profile_status = tk.StringVar()
        ttk.Label(self, textvariable=self.profile_status, padding=(14, 0), wraplength=960).pack(fill='x')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=14, pady=10)
        self.timeline = tk.Canvas(self.tabs, background='white', highlightthickness=0)
        self.scatter = tk.Canvas(self.tabs, background='white', highlightthickness=0)
        self.tabs.add(self.timeline, text='Over time')
        self.tabs.add(self.scatter, text='Current vs demand')
        self.tabs.bind('<<NotebookTabChanged>>', lambda _: self.refresh())
        self.timeline.bind('<Configure>', lambda _: self.refresh())
        self.scatter.bind('<Configure>', lambda _: self.refresh())
        self.timeline.bind('<Motion>', self.hover)
        self.timeline.bind('<Leave>', lambda _: self.clear_hover())
        self.hover_text = tk.StringVar(value='Move over the time graph to inspect a reading.')
        ttk.Label(self, textvariable=self.hover_text, padding=(14, 0), wraplength=960).pack(fill='x')
        ttk.Label(self, text='Demand = requested throttle after safety limits. Sent = last command written to the controller; '
                  'neither is measured speed. Load alarms compare amps at similar demand. Watts = volts × amps.\n'
                  'History is kept in memory for up to 4 hours while recording; export to keep a copy after closing the app.',
                  wraplength=960, padding=(14, 10), justify='left').pack(fill='x')
        self.choices = {}
        self.bind('<Configure>', self.resize_text)
        self.sync_trains(train_id)
        self.refresh()
        self.tick()

    def resize_text(self, event):
        if event.widget is self:
            for child in self.winfo_children():
                if isinstance(child, ttk.Label):
                    child.configure(wraplength=max(400, event.width-28))

    def sync_trains(self, train_id=None):
        selected = train_id or self.choices.get(self.choice.get())
        self.choices = {f"{t['name']} — {controller_label(t)} ({t['id']})": t['id']
                        for t in self.app.config['trains'] if supports(t, 'power_telemetry') or t.get('power_alarm')}
        self.choice.configure(values=list(self.choices))
        self.choice.set(next((label for label, tid in self.choices.items() if tid == selected),
                             next(iter(self.choices), '')))

    def select_train(self, tid):
        self.sync_trains(tid)
        self.refresh()
        self.lift()

    def toggle_recording(self):
        try:
            self.app.set_battery_recording(self.recording.get())
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot save recording preference', str(exc), parent=self)
        self.refresh()

    def reset_data(self):
        self.app.reset_battery_data(self.choices.get(self.choice.get()))
        self.refresh()
        self.hover_text.set('Selected train history and learned current profile reset. Recording setting is unchanged.')

    def apply_setpoints(self):
        tid = self.choices.get(self.choice.get())
        if tid is None:
            return
        try:
            try:
                values = dict(high_percent=float(self.high_percent.get()), low_percent=float(self.low_percent.get()),
                              confirm_samples=int(self.confirm_samples.get()))
            except ValueError as exc:
                raise ValueError('Enter numeric percentages and a whole number of consecutive readings.') from exc
            self.app.set_power_setpoints(tid, values)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot save current setpoints', str(exc), parent=self)
            return
        self.refresh()
        self.hover_text.set('Setpoints saved for this train. Learning is retained; an active alarm still needs Clear alarm.')

    def clear_alarm(self):
        self.app.clear_power_alarm(self.choices.get(self.choice.get()))
        self.refresh()

    def tick(self):
        self.sync_trains()
        self.refresh()
        self.timer = self.after(1000, self.tick)

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        super().destroy()

    def refresh(self):
        if not hasattr(self, 'choices'):
            return
        self.recording.set(self.app.battery_recording)
        self.alerts.set(self.app.power_alerts_enabled)
        self.alert_button.configure(state='normal' if self.app.battery_recording else 'disabled')
        self.reset_button.configure(state='normal' if self.choices else 'disabled')
        tid = self.choices.get(self.choice.get())
        telemetry = tid is not None and supports(self.app.supervisor.trains[tid], 'power_telemetry')
        settings = self.app.power_setpoints_for(tid)
        signature = (tid, settings)
        if signature != self.loaded_setpoints:
            self.high_percent.set(f'{settings.high_percent:g}')
            self.low_percent.set(f'{settings.low_percent:g}')
            self.confirm_samples.set(str(settings.confirm_samples))
            self.loaded_setpoints = signature
        self.apply_setpoints_button.configure(state='normal' if telemetry else 'disabled')
        for entry in self.setpoint_entries:
            entry.configure(state='normal' if telemetry else 'disabled')
        self.setpoint_status.set(f'Current limits: above +{settings.high_percent:g}% / below −{settings.low_percent:g}%; '
                                 f'{settings.confirm_samples} consecutive reading(s). Saved with the configuration.')
        alarm = self.app.power_alarm_for(tid)
        self.alarm_status.set(f'{alarm.label} LATCHED · {alarm.direction} than expected' if alarm else 'No latched load alarm')
        for button in (self.clear_alarm_button, self.view_alarm_button):
            button.configure(state='normal' if alarm else 'disabled')
        self.record_status.set('Demo mode: no hardware history is collected. Your saved recording preference is unchanged.' if self.app.demo else
                               'RECORDING · this choice is saved and resumes when you reopen this configuration in Live or Motor only mode.'
                               if self.app.battery_recording else
                               'Recording OFF · your choice is remembered. Enable the checkbox to collect data; existing history remains available.')
        self.history = self.app.battery_history_for(self.choices.get(self.choice.get()))
        self.profile = self.app.power_profile_for(self.choices.get(self.choice.get()))
        self.profile_status.set(('Profile: ' + self.profile.status) if self.profile else
                                'Profile unavailable: this controller has no power telemetry.' if not telemetry else
                                'Current profile: learn during normal running; needs 12 steady readings per demand band. '
                                'Reset after an intentional load change.')
        self.end = time.monotonic() if self.app.battery_recording else self.app.battery_recording_end
        seconds = WINDOWS[self.window.get()]
        first = self.history.samples[0][0].received_at if self.history and self.history.samples else self.end
        self.start = self.end - seconds if seconds else min(first, self.end - 10)
        self.rows = self.history.rows(self.start, self.end) if self.history else []
        self.export_button.configure(state='normal' if self.rows else 'disabled')
        if self.rows:
            last = self.rows[-1]
            summary = BatteryHistory.summary(self.rows)
            age = max(0, self.end - last.at)
            self.summary.set(f'Last sample: {last.volts:.2f} V · {last.amps:.3f} A · {last.watts:.2f} W'
                             + (f' · {age:.0f}s old' if age >= BATTERY_STALE_SECONDS else '')
                             + f"   |   Peak {summary['peak']:.2f} W   |   Sampled usage: {summary['wh']:.4f} Wh / "
                             f"{summary['mah']:.2f} mAh over {summary['seconds']:.0f}s (gaps excluded)")
        else:
            self.summary.set('No battery samples in this range. Enable recording and connect a hub running the battery-reporting program.')
        self.draw_timeline()
        self.draw_scatter()

    def draw_timeline(self):
        canvas = self.timeline
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(240, canvas.winfo_height())
        left, right = 72, width - 22
        top, bottom = 26, height - 32
        row_height = (bottom - top) / 4
        self.plot_bounds = (left, top, right, bottom)
        x = lambda at: left + (at-self.start) / max(1, self.end-self.start) * (right-left)
        for index, (kind, title) in enumerate((('volts', 'Volts / V'), ('amps', 'Amps / A'),
                                                ('watts', 'Watts / W'), ('demand', 'Throttle / %'))):
            ytop, ybottom = top + index*row_height, top + (index+1)*row_height - 24
            values = [getattr(row, kind) for row in self.rows] if kind != 'demand' else []
            if kind == 'demand':
                low, high = -100, 100
            elif kind == 'volts' and values:
                low, high = max(0, min(values)-.25), max(values)+.25
            else:
                low, high = 0, max(.1, max(values, default=0)*1.1)
            y = lambda value: ybottom - (value-low)/(high-low)*(ybottom-ytop)
            canvas.create_text(8, ytop-8, text=title, anchor='w', fill=COLORS[kind], font=('Helvetica', 10, 'bold'))
            for value in (low, (low+high)/2, high):
                canvas.create_line(left, y(value), right, y(value), fill='#e2e8f0')
                canvas.create_text(left-7, y(value), text=f'{value:g}' if kind == 'demand' else f'{value:.2f}',
                                   anchor='e', fill='#64748b', font=('Helvetica', 9))
            for step in range(6):
                at = self.start + (self.end-self.start)*step/5
                canvas.create_line(x(at), ytop, x(at), ybottom, fill='#f1f5f9')
                if index == 3:
                    seconds_ago = self.end-at
                    label = f'−{seconds_ago/60:.1f}m' if seconds_ago >= 60 else f'−{seconds_ago:.0f}s'
                    canvas.create_text(x(at), bottom-5, text=label if step < 5 else 'Latest', anchor='n', fill='#64748b')
            if kind == 'demand':
                if self.history:
                    for signal in ('sent', 'demand'):
                        points = self.history.steps(signal, self.start, self.end)
                        options = dict(fill=COLORS[signal], width=2, tags=f'series-{signal}')
                        if signal == 'demand':
                            options['dash'] = (5, 3)
                        line = []
                        for before, after in zip(points, points[1:]):
                            if before[1] is None:
                                continue
                            if not line:
                                line.extend((x(before[0]), y(before[1])))
                            line.extend((x(after[0]), y(before[1])))
                            if after[1] is not None:
                                line.extend((x(after[0]), y(after[1])))
                            else:
                                canvas.create_line(*line, **options)
                                line = []
                        if len(line) >= 4:
                            canvas.create_line(*line, **options)
                canvas.create_text(right, ytop-8, text='Demand (orange dashed)   Sent (gray)', anchor='e', fill='#475569')
                continue
            previous = None
            line = []
            def finish_line():
                if len(line) >= 4:
                    canvas.create_line(*line, fill=COLORS[kind], width=2, tags=f'series-{kind}')
                elif line:
                    canvas.create_oval(line[0]-2, line[1]-2, line[0]+2, line[1]+2,
                                       outline='', fill=COLORS[kind], tags=f'sample-{kind}')
            for row in self.rows:
                if previous and (row.segment != previous.segment or row.at-previous.at >= BATTERY_STALE_SECONDS):
                    finish_line()
                    line = []
                line.extend((x(row.at), y(getattr(row, kind))))
                previous = row
            finish_line()
        if not self.rows:
            canvas.create_text((left+right)/2, top+35, text='Waiting for recorded battery samples', fill='#64748b')

    def draw_scatter(self):
        canvas = self.scatter
        canvas.delete('all')
        width, height = max(300, canvas.winfo_width()), max(240, canvas.winfo_height())
        left, right, top, bottom = 72, width-30, 35, height-55
        points = [row for row in self.rows if row.demand is not None]
        bands = {key: self.profile.expected(key) for key in self.profile.bands} if self.profile else {}
        bands = {key: value for key, value in bands.items() if value is not None}
        maximum = max(.1, max([row.amps for row in points] + [value[2] for value in bands.values()], default=0)*1.1)
        x = lambda value: left + abs(value)/100*(right-left)
        y = lambda value: bottom-value/maximum*(bottom-top)
        for value in range(0, 101, 20):
            canvas.create_line(x(value), top, x(value), bottom, fill='#e2e8f0')
            canvas.create_text(x(value), bottom+12, text=str(value), anchor='n', fill='#64748b')
        for step in range(5):
            value = maximum*step/4
            canvas.create_line(left, y(value), right, y(value), fill='#e2e8f0')
            canvas.create_text(left-8, y(value), text=f'{value:.3f}', anchor='e', fill='#64748b')
        canvas.create_text(left, 15, text='Current / A', anchor='w', fill=COLORS['amps'])
        canvas.create_text((left+right)/2, height-10, text='Controller demand magnitude / %', fill='#475569')
        canvas.create_text(right, 15, text='Forward / stopped: purple     Reverse: teal', anchor='e', fill='#475569')
        for (direction, band), (center, lower, upper) in bands.items():
            color = COLORS['watts'] if direction == 1 else COLORS['amps']
            canvas.create_rectangle(x(band*10), y(upper), x((band+1)*10), y(lower),
                                    outline=color, dash=(4, 3), tags='expected-current')
            canvas.create_line(x(band*10), y(center), x((band+1)*10), y(center),
                               fill=color, dash=(4, 3), tags='expected-current')
        if bands:
            canvas.create_text(left+8, top+10, text='Dashed boxes: alarm limits around learned current', anchor='w', fill='#475569')
        drawn = set()
        for row in points:
            color = COLORS['watts'] if row.demand >= 0 else COLORS['amps']
            pixel = (round(x(row.demand)), round(y(row.amps)), color)
            if pixel in drawn:
                continue
            drawn.add(pixel)
            canvas.create_oval(x(row.demand)-3, y(row.amps)-3, x(row.demand)+3, y(row.amps)+3,
                               fill=color, outline='', tags='current-sample')
        if not points:
            canvas.create_text((left+right)/2, (top+bottom)/2, text='No paired current and demand samples in this range', fill='#64748b')

    def hover(self, event):
        if not self.rows or self.plot_bounds is None:
            return
        left, top, right, bottom = self.plot_bounds
        if not left <= event.x <= right:
            self.clear_hover()
            return
        at = self.start + (event.x-left)/(right-left)*(self.end-self.start)
        row = min(self.rows, key=lambda row: abs(row.at-at))
        self.timeline.delete('cursor')
        if abs(row.at-at) >= BATTERY_STALE_SECONDS:
            self.hover_text.set('No recorded battery sample here — telemetry or recording gap.')
            return
        x = left + (row.at-self.start)/(self.end-self.start)*(right-left)
        self.timeline.create_line(x, top, x, bottom, fill='#94a3b8', dash=(3, 3), tags='cursor')
        stamp = time.strftime('%H:%M:%S', time.localtime(self.history.wall_offset+row.at))
        demand = 'unknown' if row.demand is None else f'{row.demand:+g}%'
        sent = 'unknown' if row.sent is None else f'{row.sent:+g}%'
        self.hover_text.set(f'{stamp} · {row.volts:.3f} V · {row.amps:.3f} A · {row.watts:.3f} W'
                            f' · Demand {demand} · Sent {sent}')

    def clear_hover(self):
        self.timeline.delete('cursor')
        self.hover_text.set('Move over the time graph to inspect a reading.')

    def export(self):
        if not self.rows or self.history is None:
            return
        path = filedialog.asksaveasfilename(parent=self, title='Export visible battery samples',
                                           initialfile='battery-history.csv', defaultextension='.csv',
                                           filetypes=[('CSV data', '*.csv')])
        if path:
            try:
                count = self.history.export_csv(path, self.start, self.end)
            except OSError as exc:
                messagebox.showerror('Cannot export battery history', str(exc), parent=self)
                return
            self.hover_text.set(f'Exported {count} battery samples with volts, amps, watts, demand, and sent throttle.')
