"""Verify proposal rows are actually on top, not just present in the data model."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class ProposalLayoutTests(unittest.TestCase):
    def test_rendered_proposal_cell_is_not_covered_by_its_container(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest('Tkinter is not installed.')
        try:
            root=tk.Tk();root.withdraw()
        except tk.TclError:
            self.skipTest('No graphical display is available.')
        review=None
        try:
            import numpy as np
            from wirewalk import ImageView
            from motion_review import MotionReview
            app=SimpleNamespace(root=root,preview=ImageView(root),motion_review=None,error=lambda error:None,
                project=SimpleNamespace(video='synthetic.mp4',recording_id='synthetic',known_cm=52,
                    info={'last_frame_s':5,'fps':30},calibration={'video_points':[[60,100],[580,100]],'confirmed':True}))
            frame=np.full((360,640,3),220,dtype=np.uint8)
            with patch('motion_review.core.read_frame',return_value=(frame,0)):
                review=MotionReview(app)
            review.root.withdraw()
            review.proposals=[dict(start=1,end=3,verified=False)]
            review.refresh()
            # Map the test window outside the desktop. Tk can still resolve
            # stacking/hit-testing there without showing a window over user apps.
            review.root.overrideredirect(True)
            review.root.geometry('1240x860+30000+30000')
            review.root.deiconify();root.update()
            box=review.table.bbox('0','start')
            self.assertTrue(box,'The proposal row has no visible cell geometry.')
            x,y,w,h=box
            hit=root.winfo_containing(review.table.winfo_rootx()+x+w//2,
                                      review.table.winfo_rooty()+y+h//2)
            self.assertEqual(hit,review.table,'The proposal cell is covered by another widget.')
            self.assertEqual(review.table.identify_row(y+h//2),'0')
        finally:
            if review is not None:review.close()
            root.destroy()


if __name__=='__main__':unittest.main()
