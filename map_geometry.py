"""Polyline shaping and interpolation, independent of Tk."""
import math


STUD_M = 0.008
INCH_M = 0.0254


def control_path(readers, index):
    a, b = readers[index], readers[(index+1) % len(readers)]
    start, end = (a.get('x',100),a.get('y',100)), (b.get('x',100),b.get('y',100))
    via = [(p['x'],p['y']) for p in a.get('via',[])]
    if not via and len(readers) == 2 and 'curve_to_next' not in a:
        via = [((start[0]+end[0])/2,(start[1]+end[1])/2+(65 if index == 0 else -65))]
    return [start, *via, end]


def arc_points(point, scale):
    """A quarter circle beginning at x/y, tangent to heading (screen degrees)."""
    radius = point['radius_studs'] * STUD_M / scale
    angle = math.radians(point.get('heading', 0))
    sign = point.get('turn', 1)
    x, y = point['x'], point['y']
    cx, cy = x - sign * radius * math.sin(angle), y + sign * radius * math.cos(angle)
    start = angle - sign * math.pi / 2
    return [(cx + radius * math.cos(start + sign * math.radians(point.get('arc_sweep', 90)) * i / 90),
             cy + radius * math.sin(start + sign * math.radians(point.get('arc_sweep', 90)) * i / 90)) for i in range(91)]


def endpoint_arc(start, end, turn=1, sweep=90):
    """Circular arc joining endpoints; partial sweeps support sensor splits."""
    x, y = start
    dx, dy = end[0]-x, end[1]-y
    radius = math.hypot(dx, dy) / (2 * math.sin(math.radians(sweep)/2))
    if radius < 1e-6:
        raise ValueError('Curve endpoints must be different points')
    heading = math.degrees(math.atan2(dy, dx)) - turn*sweep/2
    points = arc_points(dict(x=x, y=y, radius_studs=radius,
                            heading=heading, turn=turn, arc_sweep=sweep), STUD_M)
    points[0], points[-1] = start, end
    return points, radius * math.radians(sweep)


def geometry(readers, index, scale=None):
    controls = control_path(readers, index)
    path, length = [controls[0]], 0.
    owners = []
    via = readers[index].get('via', [])
    objects = [readers[index], *via, readers[(index+1) % len(readers)]]
    if len(objects) != len(controls):
        objects = [objects[0], {}, objects[-1]]  # Legacy schematic return point.
    for leg, destination in enumerate(controls[1:]):
        source = objects[leg]
        if 'curve_to_next' in source:
            arc, arc_length = endpoint_arc(path[-1], destination, source['curve_to_next'], source.get('curve_sweep',90))
            length += arc_length
            path.extend(arc[1:])
            owners.extend([leg] * (len(arc)-1))
        else:
            length += math.dist(path[-1], destination)
            path.append(destination)
            owners.append(leg)
        if leg < len(via) and 'radius_studs' in via[leg]:
            if not scale:
                raise ValueError('Calibrate the map before adding curves')
            arc = arc_points(via[leg], scale)
            path.extend(arc[1:])
            owners.extend([leg + 1] * (len(arc) - 1))
            length += via[leg]['radius_studs'] * STUD_M / scale * math.pi / 2
    return path, length, owners


def path_for(readers, index, scale=None):
    return geometry(readers, index, scale)[0]


def calibration_scale(start, end, inches):
    if not math.isfinite(inches) or inches <= 0:
        raise ValueError('Known distance must be positive inches')
    pixels = math.dist(start, end)
    if not math.isfinite(pixels) or pixels < 1e-6:
        raise ValueError('Choose two different reference points')
    return inches * INCH_M / pixels


def update_distances(config):
    scale = config.get('map_m_per_unit')
    if scale is None:
        return
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Map calibration must be positive')
    readers = config['readers']
    if len(readers) < 2:
        return
    distances = [geometry(readers, i, scale)[1] * scale for i in range(len(readers))]
    if any(not math.isfinite(d) or d <= 0 for d in distances):
        raise ValueError('Track blocks must have positive lengths')
    for reader, distance in zip(readers, distances):
        reader['distance_to_next_m'] = distance


def interpolate(points, fraction):
    lengths = [math.dist(a,b) for a,b in zip(points,points[1:])]
    remaining = max(0,min(1,fraction))*sum(lengths)
    for (a,b), length in zip(zip(points,points[1:]),lengths):
        if remaining <= length and length:
            f=remaining/length
            return a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f
        remaining -= length
    return points[-1]


def nearest_leg(points, x, y):
    best = (float('inf'),0)
    for i,(a,b) in enumerate(zip(points,points[1:])):
        dx,dy=b[0]-a[0],b[1]-a[1]
        f=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        distance=math.hypot(x-a[0]-f*dx,y-a[1]-f*dy)
        best=min(best,(distance,i))
    return best


def track_heading(points, fraction):
    """Tangent of the nonzero path segment containing this distance fraction."""
    legs = [(a, b, math.dist(a,b)) for a,b in zip(points,points[1:]) if math.dist(a,b)>1e-9]
    if not legs:
        return 0.
    remaining = max(0., min(1., fraction))*sum(length for _,_,length in legs)
    for a,b,length in legs:
        if remaining < length:
            return math.atan2(b[1]-a[1],b[0]-a[0])
        remaining -= length
    a,b,_ = legs[-1]
    return math.atan2(b[1]-a[1],b[0]-a[0])


def oriented_marker(x, y, heading, length=26, width=12):
    c,s = math.cos(heading), math.sin(heading)
    return [(x+along*c-across*s, y+along*s+across*c)
            for along,across in ((-length/2,-width/2),(length/2,-width/2),
                                 (length/2,width/2),(-length/2,width/2))]
