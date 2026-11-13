"""Small desktop UI helpers."""
import tkinter as tk


class ThrottleScale(tk.Scale):
    """Display controller updates without invoking the manual-change callback."""
    def __init__(self, master, **kwargs):
        self.throttle_value = tk.IntVar(master=master, value=0)
        super().__init__(master, variable=self.throttle_value, **kwargs)

    def set(self, value):
        # Scale.set() schedules its command at idle, which can mistake an old
        # controller update for a manual change. Variable updates do not.
        self.throttle_value.set(value)


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.pending = self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(450, self.show)

    def show(self):
        self.pending = None
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()+12}+{self.widget.winfo_rooty()+self.widget.winfo_height()+5}')
        text = self.text() if callable(self.text) else self.text
        tk.Label(self.window, text=text, bg='#0f172a', fg='white', padx=10, pady=7,
                 justify='left', wraplength=380).pack()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def rounded_button_styles(style, icon_backgrounds=None, icon_size=(44, 40)):
    """Rounded image-backed ttk buttons retain native focus and keyboard behavior."""
    from PIL import Image, ImageDraw, ImageTk
    images = []
    palettes = {
        'TButton': ('#e2e8f0', '#cbd5e1', '#94a3b8', '#0f172a'),
        'Stop.TButton': ('#fee2e2', '#fecaca', '#fca5a5', '#991b1b'),
        'Force.TButton': ('#fed7aa', '#fdba74', '#fb923c', '#7c2d12'),
        'Connect.Icon.TButton': ('#dbeafe', '#bfdbfe', '#93c5fd', '#1d4ed8'),
        'Stop.Icon.TButton': ('#fee2e2', '#fecaca', '#fca5a5', '#b91c1c'),
        'Position.Icon.TButton': ('#ccfbf1', '#99f6e4', '#5eead4', '#0f766e'),
        'Remove.Icon.TButton': ('#fef3c7', '#fde68a', '#fcd34d', '#92400e'),
        'Details.Icon.TButton': ('#ede9fe', '#ddd6fe', '#c4b5fd', '#6d28d9'),
    }
    import math

    def draw_icon(draw, name, color, factor):
        def line(points, width=2.5):
            draw.line([(round(x*factor),round(y*factor)) for x,y in points],
                      fill=color,width=round(width*factor),joint='curve')
        def box(values):
            return tuple(round(v*factor) for v in values)
        if name.startswith('Connect.'):
            for start,end in ((-45,145),(135,325)):
                draw.arc(box((11,9,33,31)),start=start,end=end,fill=color,width=3*factor)
                angle=math.radians(end)
                x,y=22+11*math.cos(angle),20+11*math.sin(angle)
                tx,ty=-math.sin(angle),math.cos(angle)
                nx,ny=math.cos(angle),math.sin(angle)
                draw.polygon([(round(px*factor),round(py*factor)) for px,py in
                              ((x+tx*2,y+ty*2),(x-tx*5+nx*4,y-ty*5+ny*4),
                               (x-tx*5-nx*4,y-ty*5-ny*4))],fill=color)
        elif name.startswith('Stop.'):
            draw.rounded_rectangle(box((13,11,31,29)),radius=factor,fill=color)
        elif name.startswith('Position.'):
            draw.ellipse(box((14,12,30,28)),outline=color,width=2*factor)
            line(((22,6),(22,34)),2)
            line(((8,20),(36,20)),2)
        elif name.startswith('Remove.'):
            line(((12,30),(32,10)),3)
            line(((19,10),(32,10),(32,23)),3)
        elif name.startswith('Details.'):
            draw.ellipse(box((20,8,24,12)),fill=color)
            line(((19,17),(23,17),(23,30),(27,30)),3)

    variants = [(name, name, palette, 'white' if '.Icon.' in name else '#f8fafc')
                for name, palette in palettes.items()]
    for prefix, backing in (icon_backgrounds or {}).items():
        variants.extend((f'{prefix}.{name}', name, palette, backing)
                        for name, palette in palettes.items() if '.Icon.' in name)
    for name, icon_name, (normal,hover,pressed,foreground), backing in variants:
        icon = '.Icon.' in name
        width,height = (44,40) if icon else (24,24)
        states = []
        for index,color in enumerate((normal,hover,pressed,normal)):
            # Opaque corners prevent ttk's native background/focus box leaking through.
            image = Image.new('RGB',(width*4,height*4),backing)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((2,2,width*4-3,height*4-3),radius=36,fill=color,
                                   outline=foreground if index == 3 else None,
                                   width=6 if index == 3 else 1)
            if icon:
                draw_icon(draw,icon_name,foreground,4)
            photo = ImageTk.PhotoImage(image.resize(icon_size if icon else (width,height),Image.Resampling.LANCZOS))
            images.append(photo)
            states.append(photo)
        element = 'Rounded.'+name
        style.element_create(element,'image',states[0],('pressed',states[2]),('active',states[1]),
                             ('focus',states[3]),border=0 if icon else 10,sticky='nsew')
        children = [] if icon else [('Button.padding', {'sticky':'nsew','children':[
            ('Button.label', {'sticky':'nsew'})]})]
        style.layout(name, [(element, {'sticky':'nsew', **({'children':children} if children else {})})])
        style.configure(name,foreground=foreground,anchor='center',background=backing)
        style.map(name,foreground=[('disabled','#64748b'),('active',foreground),('pressed',foreground)],
                  background=[('active',backing),('pressed',backing)])
        if icon:
            style.configure(name,padding=0)
    return images
