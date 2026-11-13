import copy
import math
import tempfile
import unittest
from pathlib import Path
from map_geometry import calibration_scale, geometry, update_distances, STUD_M
from fleet_setup import save_config
from fleet_model import load_config


class MapGeometryTests(unittest.TestCase):
    def layout(self, turn=1):
        return {'trains': [], 'map_m_per_unit': STUD_M,
                'readers': [{'id':'a', 'name':'A', 'x':0, 'y':0,
                             'via':[{'x':10, 'y':0, 'radius_studs':40, 'heading':0, 'turn':turn}]},
                            {'id':'b', 'name':'B', 'x':50, 'y':turn*60}]}

    def test_inches_to_studs(self):
        self.assertAlmostEqual(calibration_scale((0,0),(100,0), 10)*100/STUD_M,31.75)
        for inches in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                calibration_scale((0,0),(100,0), inches)
        with self.assertRaises(ValueError):
            calibration_scale((1,1),(1,1), 10)

    def test_exact_quarter_circle_both_turns(self):
        for turn in (-1,1):
            config = self.layout(turn)
            path, length, owners = geometry(config['readers'],0,STUD_M)
            self.assertAlmostEqual(length, 30+40*math.pi/2)
            self.assertAlmostEqual(path[-2][0],50)
            self.assertAlmostEqual(path[-2][1],40*turn)
            self.assertEqual(len(owners), len(path)-1)
            update_distances(config)
            self.assertAlmostEqual(config['readers'][0]['distance_to_next_m']/STUD_M,length)

    def test_saved_calibration_and_curve_round_trip(self):
        config = self.layout()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fleet.json'
            saved = save_config(path,config)
            self.assertEqual(saved,load_config(path))
            self.assertEqual(saved['readers'][0]['via'][0]['radius_studs'],40)
            self.assertAlmostEqual(saved['readers'][0]['distance_to_next_m']/STUD_M,30+20*math.pi)

    def test_legacy_manual_distances_unchanged(self):
        config = self.layout()
        config.pop('map_m_per_unit')
        config['readers'][0]['via'] = []
        config['readers'][0]['distance_to_next_m'] = 7
        update_distances(config)
        self.assertEqual(config['readers'][0]['distance_to_next_m'],7)

    def test_recalibration_updates_straights_preserves_physical_radius(self):
        config = self.layout()
        update_distances(config)
        config['map_m_per_unit'] *= 2
        path,length,_ = geometry(config['readers'],0,config['map_m_per_unit'])
        self.assertAlmostEqual(path[-2][0],30)
        self.assertAlmostEqual(path[-2][1],20)
        update_distances(config)
        self.assertAlmostEqual(config['readers'][0]['distance_to_next_m'],length*2*STUD_M)

    def test_endpoint_curve_exact_length_and_moving_endpoint(self):
        from map_geometry import endpoint_arc
        for turn in (-1,1):
            points,length = endpoint_arc((10,20),(50,60),turn)
            self.assertEqual(points[0],(10,20))
            self.assertEqual(points[-1],(50,60))
            self.assertAlmostEqual(length,40*math.pi/2)
        config = {'trains':[], 'map_m_per_unit':STUD_M,
                  'readers':[{'id':'a','name':'A','x':0,'y':0,'curve_to_next':1},
                             {'id':'b','name':'B','x':40,'y':40}]}
        update_distances(config)
        self.assertAlmostEqual(config['readers'][0]['distance_to_next_m']/STUD_M,20*math.pi)
        with tempfile.TemporaryDirectory() as folder:
            saved = save_config(Path(folder)/'fleet.json',config)
            self.assertEqual(saved['readers'][0]['curve_to_next'],1)
        config['readers'][1].update(x=80,y=80)
        update_distances(config)
        self.assertAlmostEqual(config['readers'][0]['distance_to_next_m']/STUD_M,40*math.pi)

    def test_track_tangent_and_rotated_rectangle(self):
        from map_geometry import track_heading, oriented_marker, endpoint_arc
        self.assertEqual(track_heading([(0,0),(0,0),(10,0)],.5),0)
        self.assertAlmostEqual(track_heading([(0,0),(0,10)],.5),math.pi/2)
        self.assertAlmostEqual(track_heading([(0,0),(10,10)],1),math.pi/4)
        arc,_ = endpoint_arc((0,0),(40,40))
        self.assertLess(abs(track_heading(arc,0)),.02)
        self.assertLess(abs(track_heading(arc,1)-math.pi/2),.02)
        corners = oriented_marker(10,20,math.pi/2)
        self.assertAlmostEqual(sum(p[0] for p in corners)/4,10)
        self.assertAlmostEqual(sum(p[1] for p in corners)/4,20)
        self.assertAlmostEqual(math.dist(corners[0],corners[1]),26)
        self.assertAlmostEqual(math.dist(corners[1],corners[2]),12)
        self.assertAlmostEqual(corners[0][0],corners[1][0])
