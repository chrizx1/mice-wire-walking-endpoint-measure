"""Synthetic tests only; no experimental videos or account-specific paths."""
import math
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from movement_detector import Settings,detect,propose_intervals
import core


def sample(t,x,valid=True):
    return dict(t=t,position_cm=x,valid=valid,status='detected' if valid else 'no_candidate')


class MotionSignalTests(unittest.TestCase):
    def test_small_stationary_jitter_is_not_a_bout(self):
        rows=[sample(i/8,.02*math.sin(i)) for i in range(80)]
        self.assertEqual(propose_intervals(rows,Settings()),[])

    def test_unknown_samples_split_bouts_even_with_long_merge_setting(self):
        rows=[sample(i/8,i/8,not 2<=i/8<2.25) for i in range(40)]
        bouts=propose_intervals(rows,Settings(merge_gap_s=3))
        self.assertEqual(len(bouts),2)
        self.assertLess(bouts[0][1],2.25)
        self.assertGreaterEqual(bouts[1][0],2.25)

    def test_speed_threshold_is_respected(self):
        rows=[sample(i/8,i/8) for i in range(40)]
        self.assertEqual(propose_intervals(rows,Settings(speed_cm_s=10)),[])
        self.assertTrue(propose_intervals(rows,Settings(speed_cm_s=.5)))

    def test_head_endpoint_calculation_is_unchanged(self):
        p=core.Project('synthetic.mp4',dict(width=640,height=360,fps=30,last_frame_s=10,frame_count=301))
        p.set_length(52,'cm');p.calibration['video_points']=[[60,100],[580,100]]
        p.confirm_calibration();p.set_windows([(1,2)])
        p.mark_head(0,'start',200,120,1);p.mark_head(0,'end',270,180,2)
        p.confirm_movement(0)
        self.assertAlmostEqual(p.rows()[0]['distance_cm'],7)
        self.assertAlmostEqual(p.rows()[0]['speed_cm_s'],7)


class SyntheticVideoTests(unittest.TestCase):
    def test_visible_translation_and_cooperative_cancellation(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.mp4'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),30,(640,360))
            if not writer.isOpened():self.skipTest('MP4 writer is unavailable in this OpenCV build.')
            for i in range(181):
                t=i/30;image=np.full((360,640,3),220,dtype=np.uint8)
                cv2.line(image,(60,100),(580,100),(130,130,130),3)
                x=500 if t<2 else 500-20*min(t-2,2)
                cv2.ellipse(image,(round(x),124),(23,16),0,0,360,(20,20,20),-1)
                writer.write(image)
            writer.release()
            result=detect(path,[[60,100],[580,100]],52,Settings(scan_end_s=6))
            self.assertFalse(result['cancelled'])
            self.assertEqual(len(result['windows']),1)
            start,end=result['windows'][0]
            self.assertLess(abs(start-2),.7)
            self.assertLess(abs(end-4),.7)
            reversed_result=detect(path,[[580,100],[60,100]],52,Settings(scan_end_s=6))
            self.assertEqual(reversed_result['windows'],result['windows'])
            stop=threading.Event();stop.set()
            result=detect(path,[[60,100],[580,100]],52,Settings(),stop)
            self.assertTrue(result['cancelled']);self.assertEqual(result['windows'],[])
            project=core.Project(path,core.video_info(path)[0])
            project_path=Path(directory)/'legacy_project.json';project.save(project_path)
            old=json.loads(project_path.read_text());old.pop('movement_detection')
            project_path.write_text(json.dumps(old),encoding='utf-8')
            self.assertEqual(core.Project.load(project_path).movement_detection,{})


if __name__=='__main__':unittest.main()
