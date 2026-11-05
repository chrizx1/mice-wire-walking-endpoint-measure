"""Calibration, timestamp input, endpoint measurements and project files."""
from __future__ import annotations
import csv
import json
import math
import os
import re
from pathlib import Path

METHOD = 'sum_of_start_to_end_rod_axis_separations_per_movement'
UNIT_CM = {'inches': 2.54, 'cm': 1.0, 'mm': 0.1}


def parse_time(value):
    parts = str(value).strip().split(':')
    if len(parts) > 3 or not parts or any(not re.fullmatch(r'\d+(?:\.\d+)?', p) for p in parts):
        raise ValueError(f'Invalid timestamp: {value!r}. Use seconds, MM:SS, or HH:MM:SS.')
    nums = [float(p) for p in parts]
    if any(not p.isdigit() for p in parts[:-1]):
        raise ValueError('Only the seconds component may contain decimals.')
    if any(p >= 60 for p in nums[1:]):
        raise ValueError(f'Invalid timestamp: {value!r}. Seconds/minutes after a colon must be below 60.')
    result = sum(p * 60**i for i, p in enumerate(reversed(nums)))
    if not math.isfinite(result):
        raise ValueError('Timestamp must be finite.')
    return result


def validate_windows(windows, duration=None):
    windows = sorted((float(a), float(b)) for a, b in windows)
    if not windows:
        raise ValueError('Add at least one movement interval.')
    for a, b in windows:
        if not math.isfinite(a+b) or not 0 <= a < b:
            raise ValueError('Each interval must have a nonnegative start and a later end.')
        if duration is not None and b > duration + .06:
            raise ValueError(f'Interval ending at {b:g} s exceeds the video (last frame: {duration:.3f} s).')
    if any(b > c for (_, b), (c, _) in zip(windows, windows[1:])):
        raise ValueError('Movement intervals overlap. Split or correct the times before applying.')
    return windows


def parse_intervals(text, duration=None):
    text = str(text).replace('\u2013', '-').replace('\u2014', '-').replace('\u2212', '-')
    windows = []
    for token in re.split(r'[,;\n]+', text):
        if not token.strip():
            continue
        pieces = token.strip().split('-')
        if len(pieces) != 2:
            raise ValueError(f'Invalid interval: {token!r}. Example: 16-18, 0:22-0:29')
        windows.append(tuple(parse_time(p) for p in pieces))
    return validate_windows(windows, duration)


def read_interval_csv(path, video_path, duration=None):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not {'start_s', 'end_s'}.issubset(reader.fieldnames or []):
            raise ValueError('CSV requires start_s and end_s columns; video is optional.')
        windows, skipped = [], 0
        for line, row in enumerate(reader, 2):
            video = str(row.get('video') or '').strip()
            # Accept basenames written with either Windows or Unix separators.
            if video and video.replace('\\', '/').rsplit('/', 1)[-1].casefold() != Path(video_path).name.casefold():
                skipped += 1
                continue
            try:
                windows.append((parse_time(row['start_s']), parse_time(row['end_s'])))
            except ValueError as exc:
                raise ValueError(f'CSV line {line}: {exc}') from exc
    if not windows:
        raise ValueError('CSV has no movement intervals matching the current video.')
    return validate_windows(windows, duration), skipped


def format_time(seconds):
    return f'{seconds:.9f}'.rstrip('0').rstrip('.')


def safe_id(value):
    result = re.sub(r'[^\w.-]+', '_', str(value), flags=re.UNICODE).strip('._')
    if not result:
        raise ValueError('Enter a recording identifier containing letters or numbers.')
    # Avoid reserved device names on Windows, including when an extension exists.
    if result.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}:
        result = 'recording_' + result
    return result


def rod_projection(point, a, b, known_cm):
    dx, dy = b[0]-a[0], b[1]-a[1]
    denominator = dx*dx + dy*dy
    if not all(math.isfinite(v) for v in (*point, *a, *b, known_cm)) or denominator < 1 or known_cm <= 0:
        raise ValueError('Calibration requires two distinct endpoints and a positive physical length.')
    fraction = ((point[0]-a[0])*dx + (point[1]-a[1])*dy)/denominator
    return fraction*known_cm, (a[0]+fraction*dx, a[1]+fraction*dy)


def image_read(path):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f'Cannot read image: {path}')
    return image


def video_info(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError('Cannot open this video. Try an MP4 supported by OpenCV.')
        ok, first = cap.read()
        if not ok:
            raise ValueError('Video has no readable first frame.')
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps = cap.get(cv2.CAP_PROP_FPS)
        if count < 2 or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Cannot read video timing metadata.')
        cap.set(cv2.CAP_PROP_POS_FRAMES, count-1)
        ok, _ = cap.read()
        duration = cap.get(cv2.CAP_PROP_POS_MSEC)/1000 if ok else -1
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Video decoder timestamps are unavailable.')
        return dict(width=int(first.shape[1]), height=int(first.shape[0]), fps=fps,
                    last_frame_s=duration, frame_count=count), first
    finally:
        cap.release()


def read_frame(path, seconds):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0, seconds)*1000)
        ok, image = cap.read()
        if not ok:
            raise ValueError(f'No video frame near {seconds:.3f} seconds.')
        actual = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        if not math.isfinite(actual) or (seconds > 1 and actual <= 0) or abs(actual-seconds) > .06:
            raise ValueError(f'Decoder cannot resolve {seconds:.3f} s within 0.06 s. Check the timestamp/video encoding.')
        return image, actual
    finally:
        cap.release()


def map_screenshot(screenshot, frame, endpoints):
    import cv2
    import numpy as np
    factor = min(1.0, 1100/max(screenshot.shape[:2]))
    sf = cv2.resize(screenshot, None, fx=factor, fy=factor)
    vf = min(1.0, 1100/max(frame.shape[:2]))
    target = cv2.resize(frame, None, fx=vf, fy=vf)
    orb = cv2.ORB_create(nfeatures=7000)
    ka, da = orb.detectAndCompute(cv2.cvtColor(sf, cv2.COLOR_BGR2GRAY), None)
    kb, db = orb.detectAndCompute(cv2.cvtColor(target, cv2.COLOR_BGR2GRAY), None)
    if da is None or db is None:
        raise ValueError('Too few scene features. Select the rod directly in the video preview.')
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < .72*p[1].distance]
    if len(good) < 15:
        raise ValueError('Too few reliable matches. Select the rod directly in the video preview.')
    src = np.float32([ka[m.queryIdx].pt for m in good])/factor
    dst = np.float32([kb[m.trainIdx].pt for m in good])/vf
    matrix, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.5/vf)
    if matrix is None or mask is None:
        raise ValueError('Screenshot transfer failed. Select the rod in the video preview.')
    mask = mask.ravel().astype(bool)
    if mask.sum() < 15 or mask.mean() < .35 or np.ptp(src[mask, 0]) < screenshot.shape[1]*.35:
        raise ValueError('Matching does not cover enough of the scene. Select the rod in the video preview.')
    points = np.c_[np.asarray(endpoints), np.ones(2)] @ matrix.T
    if not all(0 <= x < frame.shape[1] and 0 <= y < frame.shape[0] for x, y in points):
        raise ValueError('Transferred endpoints lie outside the video. Select them directly.')
    return points.tolist(), dict(method='ORB/RANSAC similarity transform', inliers=int(mask.sum()), matrix=matrix.tolist())


class Project:
    def __init__(self, video, info):
        self.video = str(Path(video).resolve());self.info = info
        try:self.recording_id = safe_id(Path(video).stem)
        except ValueError:self.recording_id = 'recording'
        self.animal = '';self.date = '';self.run = ''
        self.length = 24.0;self.unit = 'inches'
        self.calibration = dict(source_type='', screenshot='', source_points=[], video_points=[],
                                reference_time_s=0.0, source_frame_time_s=0.0, mapping={}, confirmed=False)
        self.movements = []
        self.movement_detection = {}

    @property
    def known_cm(self):
        return self.length*UNIT_CM[self.unit]

    def invalidate_calibration(self):
        self.calibration['confirmed'] = False
        for movement in self.movements:
            movement['reviewed'] = False

    def set_length(self, value, unit):
        value = float(value)
        if not math.isfinite(value) or value <= 0 or unit not in UNIT_CM:
            raise ValueError('Enter a positive rod length and select inches, cm, or mm.')
        if not math.isclose(value*UNIT_CM[unit], self.known_cm, abs_tol=1e-12):
            self.invalidate_calibration()
        self.length, self.unit = value, unit

    def set_windows(self, windows):
        windows = validate_windows(windows, self.info['last_frame_s'])
        old = self.movements
        same=(len(old)==len(windows) and all(abs(m['start_s']-a)<1e-8 and abs(m['end_s']-b)<1e-8 for m,(a,b) in zip(old,windows)))
        if self.movement_detection and not same:
            self.movement_detection['edited_after_verification']=True
        self.movements = [next((m for m in old if abs(m['start_s']-a)<1e-8 and abs(m['end_s']-b)<1e-8),
                               dict(start_s=a,end_s=b,points={},reviewed=False)) for a,b in windows]

    def confirm_calibration(self):
        points = self.calibration['video_points']
        if len(points) != 2 or math.dist(*points) < 10:
            raise ValueError('Select two rod endpoints in original video coordinates first.')
        if not all(0 <= x < self.info['width'] and 0 <= y < self.info['height'] for x, y in points):
            raise ValueError('Rod endpoints lie outside the video.')
        rod_projection(points[0], *points, self.known_cm)
        self.calibration['confirmed'] = True

    def mark_head(self, index, role, x, y, actual_time):
        if not self.calibration['confirmed']:
            raise ValueError('Confirm the video calibration first.')
        if role not in ('start', 'end') or not all(math.isfinite(v) for v in (x, y, actual_time)):
            raise ValueError('Invalid head point.')
        if not (0 <= x < self.info['width'] and 0 <= y < self.info['height']):
            raise ValueError('Head point is outside the image.')
        movement = self.movements[index]
        requested = movement[role+'_s']
        if abs(actual_time-requested) > .06:
            raise ValueError('The displayed frame does not match this movement boundary.')
        movement['points'][role] = dict(x=x, y=y, t=actual_time)
        movement['reviewed'] = False

    def rows(self):
        rows = [];rod = self.calibration['video_points']
        for i, m in enumerate(self.movements):
            start, end = m['points'].get('start'), m['points'].get('end')
            def position(p):
                return rod_projection((p['x'],p['y']),*rod,self.known_cm)[0] if p and len(rod)==2 else None
            a, b = position(start), position(end)
            signed = b-a if a is not None and b is not None else None
            separation = abs(signed) if signed is not None else None
            confirmed = self.calibration['confirmed'] and m['reviewed'] and separation is not None
            time = m['end_s']-m['start_s']
            rows.append(dict(interval=i+1,start_s=m['start_s'],end_s=m['end_s'],moving_time_s=time,
                start_frame_s=start['t'] if start else None,end_frame_s=end['t'] if end else None,
                start_head_x_px=start['x'] if start else None,start_head_y_px=start['y'] if start else None,
                end_head_x_px=end['x'] if end else None,end_head_y_px=end['y'] if end else None,
                start_position_cm=a,end_position_cm=b,signed_displacement_toward_B_cm=signed,
                measured_separation_cm=separation,distance_cm=separation if confirmed else None,
                distance_inches=separation/2.54 if confirmed else None,speed_cm_s=separation/time if confirmed else None,
                reviewed=bool(confirmed),status='confirmed' if confirmed else 'needs_endpoint_review'))
        return rows

    def confirm_movement(self, index):
        if not self.calibration['confirmed'] or self.rows()[index]['measured_separation_cm'] is None:
            raise ValueError('Confirm calibration and select the head at both movement boundaries.')
        self.movements[index]['reviewed'] = True

    def summary(self):
        rows = self.rows();complete = bool(rows) and all(r['reviewed'] for r in rows)
        time = sum(r['moving_time_s'] for r in rows)
        distance = sum(r['distance_cm'] for r in rows) if complete else None
        timing_source=('auto_detected_verified' if self.movement_detection.get('verified') and not self.movement_detection.get('edited_after_verification')
                       else 'manual_edit_after_detection' if self.movement_detection else 'manual')
        return dict(recording_id=safe_id(self.recording_id),animal=self.animal,date=self.date,run=self.run,
            video=Path(self.video).name,workbook='',source_cell='manual_timestamp_input',
            calibration_image=Path(self.calibration['screenshot']).name if self.calibration['screenshot'] else self.calibration['source_type'],
            measurement=METHOD,interval_input_method=timing_source,
            interval_detector=self.movement_detection.get('method',''),
            known_length_inches=self.known_cm/2.54,known_length_cm=self.known_cm,
            moving_time_s=time,movement_intervals=len(rows),confirmed_intervals=sum(r['reviewed'] for r in rows),
            distance_cm=distance,distance_inches=distance/2.54 if complete else None,
            speed_cm_s=distance/time if complete else None,status='confirmed' if complete else 'incomplete_endpoint_review')

    def validate_metadata(self):
        import datetime
        self.recording_id = safe_id(self.recording_id)
        if self.date:
            datetime.date.fromisoformat(self.date)
        if str(self.run).strip():
            if not str(self.run).isdigit() or int(self.run) < 1:
                raise ValueError('Trial/run must be a positive integer or blank.')

    def save(self, path):
        self.validate_metadata();path = Path(path)
        def relative(value):
            if not value:return ''
            try:return Path(os.path.relpath(value,path.parent)).as_posix()
            except ValueError:return str(value)
        calibration = dict(self.calibration)
        calibration['screenshot'] = relative(calibration['screenshot'])
        data = dict(kind='wirewalk_studio',version=1,video=relative(self.video),info=self.info,
                    recording_id=self.recording_id,animal=self.animal,date=self.date,run=self.run,
                    length=self.length,unit=self.unit,calibration=calibration,movements=self.movements,
                    movement_detection=self.movement_detection)
        path.write_text(json.dumps(data,indent=2),encoding='utf-8')

    @classmethod
    def load(cls, path, video_override=None):
        path = Path(path);data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('kind') != 'wirewalk_studio' or data.get('version') != 1:
            raise ValueError('This is not a WireWalk Studio project.')
        resolve = lambda value: str((path.parent/Path(value)).resolve()) if value else ''
        video = str(video_override) if video_override else resolve(data['video'])
        if not Path(video).is_file():
            raise FileNotFoundError(video)
        info,_ = video_info(video)
        if any(info[k] != data['info'][k] for k in ('width','height','frame_count')):
            raise ValueError('Video dimensions or frame count differ from the saved project.')
        if abs(info['last_frame_s']-data['info']['last_frame_s'])>.06:
            raise ValueError('Video timing differs from the saved project. Locate the original recording.')
        project = cls(video,info)
        for key in ('recording_id','animal','date','run'):
            setattr(project,key,data.get(key,''))
        project.set_length(data['length'],data['unit'])
        project.calibration = data['calibration']
        project.calibration['screenshot'] = resolve(project.calibration.get('screenshot',''))
        project.set_windows([(m['start_s'],m['end_s']) for m in data['movements']]) if data['movements'] else None
        if project.calibration['confirmed']:
            project.confirm_calibration()
        for target,saved in zip(project.movements,data['movements']):
            for role,p in saved.get('points',{}).items():
                # Validate saved points even when calibration is not confirmed.
                if role not in ('start','end') or not all(math.isfinite(float(p[k])) for k in ('x','y','t')):
                    raise ValueError('Saved head point is invalid.')
                if not(0<=p['x']<info['width'] and 0<=p['y']<info['height']) or abs(p['t']-target[role+'_s'])>.06:
                    raise ValueError('Saved point does not match the video or timestamp.')
                target['points'][role] = p
            target['reviewed'] = bool(saved.get('reviewed')) and project.calibration['confirmed'] and len(target['points'])==2
        project.validate_metadata()
        project.movement_detection=data.get('movement_detection',{})
        return project

    def export(self, directory):
        if not self.movements:
            raise ValueError('Add movement intervals before exporting.')
        self.validate_metadata();directory = Path(directory);directory.mkdir(parents=True,exist_ok=True)
        project_file = directory/(self.recording_id+'_project.json')
        if project_file.exists():
            previous = json.loads(project_file.read_text(encoding='utf-8'))
            previous_video = (project_file.parent/previous['video']).resolve()
            if previous_video != Path(self.video).resolve():
                raise ValueError('This export ID already belongs to another video. Change the recording identifier.')
        for suffix,rows in [('endpoint_summary',[self.summary()]),('endpoint_intervals',self.rows())]:
            with (directory/f'{self.recording_id}_{suffix}.csv').open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        self.save(project_file)
        return self.summary()
