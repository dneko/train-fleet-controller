"""Topology edits for checkpoint placement, independent of Tk."""
import copy
import math
from map_geometry import control_path, geometry, nearest_leg, endpoint_arc, update_distances


def insert_checkpoint(config, x, y):
    result = copy.deepcopy(config)
    readers = result['readers']
    used = {r['id'] for r in readers}
    number = 1
    while f'sensor_{number}' in used:
        number += 1
    sensor = dict(id=f'sensor_{number}', name=f'Sensor {number}', node='',
                  x=x, y=y, distance_to_next_m=1., via=[])
    if any(math.dist((x,y),(r.get('x',100),r.get('y',100))) < 1e-6 for r in readers):
        raise ValueError('A checkpoint already occupies that position')
    if len(readers) < 2:
        readers.append(sensor)
        update_distances(result)
        return result, sensor['id']
    scale = result.get('map_m_per_unit')
    # Make the old two-reader return paths explicit before adding a third reader.
    if len(readers) == 2:
        for i,r in enumerate(readers):
            if not r.get('via') and 'curve_to_next' not in r:
                r['via'] = [dict(x=px,y=py) for px,py in control_path(readers,i)[1:-1]]
    paths = [geometry(readers,i,scale) for i in range(len(readers))]
    _, sampled_leg, index = min((*nearest_leg(path,x,y),i) for i,(path,_,_) in enumerate(paths))
    reader = readers[index]
    path, _, owners = paths[index]
    # Legacy explicit-radius curves are sampled only in the edited block.
    if any('radius_studs' in p for p in reader.get('via',[])):
        reader['via'] = [dict(x=px,y=py) for px,py in path[1:-1]]
        reader.pop('curve_to_next',None)
        reader.pop('curve_sweep',None)
        leg = sampled_leg
    else:
        leg = owners[sampled_leg]
    via = reader.get('via',[])
    controls = [reader,*via,readers[(index+1)%len(readers)]]
    source, end = controls[leg:leg+2]
    if 'curve_to_next' in source:
        start_xy = (source.get('x',100),source.get('y',100))
        end_xy = (end.get('x',100),end.get('y',100))
        sweep = source.get('curve_sweep',90)
        arc,_ = endpoint_arc(start_xy,end_xy,source['curve_to_next'],sweep)
        _, arc_leg = nearest_leg(arc,x,y)
        a,b = arc[arc_leg:arc_leg+2]
        dx,dy = b[0]-a[0],b[1]-a[1]
        along = max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
        fraction = max(.001,min(.999,(arc_leg+along)/90))
        source['curve_sweep'] = sweep*fraction
        sensor.update(curve_to_next=source['curve_to_next'],curve_sweep=sweep*(1-fraction))
    sensor['via'] = via[leg:]
    reader['via'] = via[:leg]
    old_distance = reader.get('distance_to_next_m',1.)
    readers.insert(index+1,sensor)
    if not scale:
        first = geometry(readers,index,scale)[1]
        second = geometry(readers,index+1,scale)[1]
        ratio = first/(first+second) if first+second else .5
        reader['distance_to_next_m'] = old_distance*max(.001,min(.999,ratio))
        sensor['distance_to_next_m'] = old_distance-reader['distance_to_next_m']
    update_distances(result)
    return result,sensor['id']
