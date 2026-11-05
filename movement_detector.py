"""Conservative proposals for dark-mouse axial motion; human verification required.

This is image segmentation, not a trained animal detector. Missing/ambiguous
detections and nearby cyan/blue gloves are unknown intervals, never stationary
observations. Unknown gaps are not bridged when forming movement proposals.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import time


@dataclass
class Settings:
    sample_hz: float = 8.0
    speed_cm_s: float = 0.5
    min_bout_s: float = 0.5
    merge_gap_s: float = 0.3
    min_excursion_cm: float = 0.3
    dark_threshold: int = 100
    above_cm: float = 4.0
    below_cm: float = 8.0
    scan_start_s: float = 0.0
    scan_end_s: float | None = None
    require_below_rod: bool = True

    def validate(self):
        limits={'sample_hz':(2,20),'speed_cm_s':(.05,20),'min_bout_s':(.1,10),
                'merge_gap_s':(0,3),'min_excursion_cm':(.05,5),
                'dark_threshold':(20,200),'above_cm':(1,15),'below_cm':(1,20)}
        for key,(lo,hi) in limits.items():
            value=float(getattr(self,key))
            if not math.isfinite(value) or not lo<=value<=hi:
                raise ValueError(f'{key} must be between {lo:g} and {hi:g}.')
        if not math.isfinite(self.scan_start_s) or self.scan_start_s<0:
            raise ValueError('Scan start must be nonnegative.')
        if self.scan_end_s is not None and (not math.isfinite(self.scan_end_s) or self.scan_end_s<=self.scan_start_s):
            raise ValueError('Scan end must be later than scan start.')
        if not isinstance(self.require_below_rod,bool):raise ValueError('Posture option must be true or false.')
        return self


def _true_runs(flags):
    runs=[];start=None
    for i,value in enumerate([*flags,False]):
        if value and start is None:start=i
        if not value and start is not None:runs.append((start,i-1));start=None
    return runs


def propose_intervals(samples,settings):
    """Smooth each continuous known run, estimate local axial slope, find bouts."""
    import numpy as np
    settings.validate();groups=[];previous=-1
    for i,row in enumerate(samples):
        row['smoothed_position_cm']=None;row['axial_speed_cm_s']=None
        if not row['valid']:continue
        if (not groups or previous!=i-1 or
                row['t']-samples[previous]['t']>max(.4,2.5/settings.sample_hz)):
            groups.append([])
        groups[-1].append(i);previous=i
    proposals=[]
    for group in groups:
        if len(group)<3:continue
        times=np.array([samples[i]['t'] for i in group],dtype=float)
        positions=np.array([samples[i]['position_cm'] for i in group],dtype=float)
        smooth=np.array([np.median(positions[np.abs(times-t)<=.20+1e-9]) for t in times])
        slopes=np.zeros(len(group));moving=[]
        for j,t in enumerate(times):
            nearby=np.abs(times-t)<=.35+1e-9
            ts=times[nearby];ps=smooth[nearby];centered=ts-ts.mean()
            denom=float(centered@centered)
            if len(ts)>=3 and denom>0:
                slopes[j]=float(centered@(ps-ps.mean()))/denom
            samples[group[j]]['smoothed_position_cm']=float(smooth[j])
            samples[group[j]]['axial_speed_cm_s']=float(abs(slopes[j]))
            moving.append(abs(slopes[j])>=settings.speed_cm_s and
                          float(np.ptp(ps))>=settings.min_excursion_cm/2)
        # Fill only brief quiet gaps inside one continuously observed run.
        runs=_true_runs(moving)
        for (_,a),(b,_) in zip(runs,runs[1:]):
            if times[b]-times[a]<=settings.merge_gap_s+1/settings.sample_hz:
                moving[a+1:b]=[True]*(b-a-1)
        for start,end in _true_runs(moving):
            a=(times[start-1]+times[start])/2 if start else times[start]
            b=(times[end]+times[end+1])/2 if end+1<len(times) else times[end]
            if b-a<settings.min_bout_s or float(np.ptp(smooth[start:end+1]))<settings.min_excursion_cm:
                continue
            # Small boundary padding stays inside the observed run, not unknown gaps.
            a=max(float(times[0]),float(a)-.10);b=min(float(times[-1]),float(b)+.10)
            a,b=round(a,3),round(b,3)
            if a<b:proposals.append((a,b))
    # Padding may make adjacent detections within the same run touch/overlap.
    # Merge overlaps only: unknown gaps always lie outside the run's bounds.
    combined=[]
    for a,b in sorted(proposals):
        if combined and a<=combined[-1][1]:combined[-1]=(combined[-1][0],max(b,combined[-1][1]))
        else:combined.append((a,b))
    return combined


def detect(video,rod_points,known_cm,settings=None,cancel=None,progress=None):
    import cv2
    import numpy as np
    settings=(settings or Settings()).validate()
    points=np.asarray(rod_points,dtype=float)
    if points.shape!=(2,2) or not np.all(np.isfinite(points)) or not math.isfinite(known_cm) or known_cm<=0:
        raise ValueError('A confirmed, two-endpoint rod calibration is required.')
    rod_length=float(np.linalg.norm(points[1]-points[0]))
    if rod_length<10:raise ValueError('Rod endpoints are too close.')
    u=(points[1]-points[0])/rod_length;n=np.array([-u[1],u[0]])
    if n[1]<0:n=-n
    # Work in a horizontal, physically scaled band around the rod, without upsampling.
    padding=1.0;scale=min(24.,1000/(known_cm+2*padding),rod_length/known_cm)
    width=max(16,math.ceil((known_cm+2*padding)*scale))
    height=max(16,math.ceil((settings.above_cm+settings.below_cm)*scale))
    # Original pixels -> cm -> work pixels. Keep both axes in the same physical scale.
    input_px_per_cm=rod_length/known_cm
    matrix=np.array([[u[0]*scale/input_px_per_cm,u[1]*scale/input_px_per_cm,
                      padding*scale-float(points[0]@u)*scale/input_px_per_cm],
                     [n[0]*scale/input_px_per_cm,n[1]*scale/input_px_per_cm,
                      settings.above_cm*scale-float(points[0]@n)*scale/input_px_per_cm]],dtype=np.float32)
    inverse=cv2.invertAffineTransform(matrix)
    rod_y=settings.above_cm*scale
    cap=cv2.VideoCapture(str(video));samples=[];previous=None;last_progress=0.0
    try:
        if not cap.isOpened():raise ValueError('Cannot open video for motion detection.')
        fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps<=0 or not math.isfinite(fps):raise ValueError('Video frame rate is unavailable.')
        stride=max(1,round(fps/settings.sample_hz))
        duration=(count-1)/fps
        end=settings.scan_end_s if settings.scan_end_s is not None else duration+.06
        if settings.scan_start_s>=end:raise ValueError('Scan range is empty.')
        cap.set(cv2.CAP_PROP_POS_MSEC,settings.scan_start_s*1000)
        index=0;last_t=None
        while cap.grab():
            if cancel is not None and cancel.is_set():
                return dict(cancelled=True,windows=[],samples=[],settings=asdict(settings))
            if index%stride:
                index+=1;continue
            index+=1;ok,frame=cap.retrieve()
            if not ok:raise ValueError('Could not decode a sampled frame.')
            t=cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            if not math.isfinite(t) or (last_t is not None and t<=last_t):
                raise ValueError('Video decoder timestamps are not increasing.')
            last_t=t
            if t>end+.001:break
            if t<settings.scan_start_s-.06:continue
            roi=cv2.warpAffine(frame,matrix,(width,height),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
            hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
            blue=cv2.inRange(hsv,np.array([70,65,30]),np.array([135,255,255]))
            gray=cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)
            mask=cv2.inRange(gray,0,int(settings.dark_threshold))
            mask[blue>0]=0
            stripe=max(1,round(.16*scale));yy=round(rod_y)
            mask[max(0,yy-stripe):min(height,yy+stripe+1),:]=0
            kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(3,3))
            mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,kernel)
            nc,labels,stats,centers=cv2.connectedComponentsWithStats(mask,connectivity=8)
            candidates=[]
            for i in range(1,nc):
                x,y,w,h,area=(int(v) for v in stats[i]);area_cm=area/(scale*scale)
                wc,hc=w/scale,h/scale
                if not(.8<=area_cm<=45 and .65<=wc<=11 and .6<=hc<=9 and .2<=wc/hc<=4.5):continue
                if area/(w*h)<.18 or y/scale>settings.above_cm+2 or (y+h)/scale<settings.above_cm-2:continue
                cx,cy=centers[i];position=cx/scale-padding
                if settings.require_below_rod and cy<rod_y+.1*scale:continue
                distance=abs(position-previous['position_cm']) if previous and t-previous['t']<=.5 else None
                candidates.append(dict(position_cm=float(position),area_cm2=float(area_cm),
                    box=(x,y,w,h),center=(float(cx),float(cy)),distance=distance))
            row=dict(t=float(t),position_cm=None,area_cm2=None,body_x_px=None,body_y_px=None,valid=False,status='no_candidate')
            if candidates:
                candidates.sort(key=lambda c:(c['distance']+.6*abs(math.log(c['area_cm2']/previous['area_cm2']))-.15*math.sqrt(c['area_cm2'])
                                             if c['distance'] is not None else -math.sqrt(c['area_cm2'])))
                best=candidates[0]
                ambiguous=(len(candidates)>1 and best['distance'] is None and
                           candidates[1]['area_cm2']>.65*best['area_cm2'] and
                           abs(candidates[1]['position_cm']-best['position_cm'])>3)
                x,y,w,h=best['box'];pad=round(1.0*scale)
                nearby=blue[max(0,y-pad):min(height,y+h+pad),max(0,x-pad):min(width,x+w+pad)]
                blue_area=float(np.count_nonzero(nearby))/(scale*scale)
                handling=blue_area>.4 and np.count_nonzero(nearby)/max(1,nearby.size)>.04
                jump=best['distance'] is not None and best['distance']>max(2.,40*(t-previous['t']))
                row.update(position_cm=best['position_cm'],area_cm2=best['area_cm2'])
                original=inverse@np.array([*best['center'],1.])
                row.update(body_x_px=float(original[0]),body_y_px=float(original[1]))
                row['status']='handling_or_glove' if handling else 'ambiguous' if ambiguous else 'position_jump' if jump else 'detected'
                row['valid']=row['status']=='detected'
            samples.append(row)
            previous=row if row['valid'] else None
            if progress is not None and time.monotonic()-last_progress>.15:
                fraction=(t-settings.scan_start_s)/max(.001,end-settings.scan_start_s)
                progress(min(1.,max(0.,fraction)),t);last_progress=time.monotonic()
        if not samples:raise ValueError('No readable samples in this scan range.')
        windows=propose_intervals(samples,settings)
        counts={status:sum(r['status']==status for r in samples) for status in sorted({r['status'] for r in samples})}
        return dict(cancelled=False,windows=windows,samples=samples,settings=asdict(settings),
                    method='dark_body_axial_motion_v1',sample_counts=counts,
                    sampled_start_s=samples[0]['t'],sampled_end_s=samples[-1]['t'])
    finally:cap.release()
