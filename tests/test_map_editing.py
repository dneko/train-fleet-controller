import math
import tempfile
import unittest
from pathlib import Path
from map_editing import insert_checkpoint
from map_geometry import STUD_M, update_distances
from fleet_setup import save_config

class CheckpointPlacementTests(unittest.TestCase):
    def config(self):
        return {'trains':[], 'map_m_per_unit':STUD_M,'readers':[
            {'id':'a','name':'A','x':0,'y':0,'via':[{'x':40,'y':0},{'x':80,'y':0}]},
            {'id':'b','name':'B','x':100,'y':0},
            {'id':'c','name':'C','x':100,'y':100}]}

    def test_split_straight_keeps_order_and_shape_points(self):
        source=self.config()
        config,ident=insert_checkpoint(source,60,0)
        a,sensor,b,c=config['readers']
        self.assertEqual(sensor['id'],ident)
        self.assertEqual(sensor['node'],'')
        self.assertEqual(a['via'],[{'x':40,'y':0}])
        self.assertEqual(sensor['via'],[{'x':80,'y':0}])
        self.assertAlmostEqual(a['distance_to_next_m']/STUD_M,60)
        self.assertAlmostEqual(sensor['distance_to_next_m']/STUD_M,40)
        self.assertEqual(len(source['readers']),3)
        config,other=insert_checkpoint(config,90,0)
        self.assertNotEqual(ident,other)
        with tempfile.TemporaryDirectory() as folder:
            saved=save_config(Path(folder)/'fleet.json',config)
            self.assertEqual(len(saved['readers']),5)

    def test_split_curve_retains_arc_length(self):
        config=self.config()
        config['readers'][0].update(via=[],curve_to_next=1)
        config['readers'][1].update(x=40,y=40)
        # Midpoint of a clockwise arc from (0,0) to (40,40).
        config,ident=insert_checkpoint(config,40/math.sqrt(2),40-40/math.sqrt(2))
        a,sensor,*_=config['readers']
        self.assertAlmostEqual(a['curve_sweep'],45)
        self.assertAlmostEqual(sensor['curve_sweep'],45)
        self.assertAlmostEqual((a['distance_to_next_m']+sensor['distance_to_next_m'])/STUD_M,20*math.pi)

    def test_uncalibrated_preserves_manual_total(self):
        config=self.config()
        config.pop('map_m_per_unit')
        config['readers'][0]['distance_to_next_m']=10
        config,_=insert_checkpoint(config,60,0)
        self.assertAlmostEqual(config['readers'][0]['distance_to_next_m'],6)
        self.assertAlmostEqual(config['readers'][1]['distance_to_next_m'],4)

    def test_empty_and_duplicate(self):
        config,ident=insert_checkpoint({'trains':[],'readers':[]},10,20)
        self.assertEqual(config['readers'][0]['id'],ident)
        with self.assertRaises(ValueError):
            insert_checkpoint(config,10,20)

    def test_two_reader_return_path_survives(self):
        config=self.config()
        config['readers']=config['readers'][:2]
        config['readers'][0]['via']=[]
        update_distances(config)
        total=sum(r['distance_to_next_m'] for r in config['readers'])
        config,_=insert_checkpoint(config,25,32.5)
        self.assertAlmostEqual(sum(r['distance_to_next_m'] for r in config['readers']),total)
