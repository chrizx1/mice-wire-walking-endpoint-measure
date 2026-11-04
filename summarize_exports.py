"""Combine endpoint-measurer CSV exports. Python standard library only."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

METHOD='sum_of_start_to_end_rod_axis_separations_per_movement'


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames is None:raise ValueError('CSV has no header.')
        return list(reader)


def number(row,key,optional=False):
    text=str(row.get(key,'')).strip()
    if not text and optional:return None
    try:value=float(text)
    except ValueError:raise ValueError(f'Missing or invalid {key}: {text!r}') from None
    if not math.isfinite(value) or value<0:raise ValueError(f'{key} must be finite and nonnegative.')
    return value


def integer(row,key):
    value=number(row,key)
    if not value.is_integer():raise ValueError(f'{key} must be an integer.')
    return int(value)


def recording_info(recording_id, metadata=None):
    metadata=metadata or {}
    match=re.fullmatch(r'(.+)_r(\d+)_(\d{6})',recording_id,re.IGNORECASE)
    parsed_animal,parsed_run,parsed_date=('',None,'')
    if match:
        parsed_animal,run,date=match.groups()
        try:
            parsed_run=int(run);parsed_date=dt.datetime.strptime(date,'%m%d%y').date().isoformat()
            if parsed_run<1:raise ValueError('Not a legacy run identifier.')
        except ValueError:
            parsed_animal,parsed_run,parsed_date=('',None,'')
    animal=str(metadata.get('animal') or parsed_animal or 'Unspecified').strip()
    date=str(metadata.get('date') or parsed_date).strip()
    if date:dt.date.fromisoformat(date)
    run=metadata.get('run')
    run=int(run) if run is not None and str(run).strip() else parsed_run
    if run is not None and run<1:raise ValueError('Trial must be positive or blank.')
    return animal,run,date



def near(a,b):return math.isclose(a,b,rel_tol=1e-7,abs_tol=1e-6)


def write_csv(path,rows,fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        out=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        out.writeheader();out.writerows(rows)


def canonical_rows(rows):
    # Identical duplicate exports count once. Changed exports are conflicts.
    return tuple(tuple(sorted((k,str(v).strip()) for k,v in row.items())) for row in rows)


def aggregate(rows,group_keys):
    groups=defaultdict(list)
    for row in rows:
        if row['animal'] and (group_keys == ['animal'] or row['date']):
            groups[tuple(row[k] for k in group_keys)].append(row)
    result=[]
    for key,items in sorted(groups.items()):
        valid=[r for r in items if r['validation_status']=='valid']
        distances=[r['distance_cm'] for r in valid];times=[r['moving_time_s'] for r in valid]
        speeds=[r['speed_cm_s'] for r in valid]
        result.append(dict(zip(group_keys,key)) | dict(
            recordings_found=len(items),recordings_included=len(valid),recordings_excluded=len(items)-len(valid),
            total_distance_cm=sum(distances) if valid else None,
            mean_distance_per_recording_cm=statistics.mean(distances) if valid else None,
            sd_distance_per_recording_cm=statistics.stdev(distances) if len(valid)>1 else None,
            total_moving_time_s=sum(times) if valid else None,
            mean_moving_time_per_recording_s=statistics.mean(times) if valid else None,
            mean_recording_speed_cm_s=statistics.mean(speeds) if valid else None,
            sd_recording_speed_cm_s=statistics.stdev(speeds) if len(valid)>1 else None,
            pooled_speed_cm_s=sum(distances)/sum(times) if valid else None))
    return result


def summarize(input_dir,output_dir):
    input_dir=Path(input_dir).resolve();output_dir=Path(output_dir).resolve()
    if not input_dir.is_dir():raise ValueError(f'Input folder does not exist: {input_dir}')
    sources=sorted(input_dir.rglob('*_endpoint_summary.csv'))
    if not sources:raise ValueError('No *_endpoint_summary.csv exports found. Select the endpoint export folder (subfolders are included).')
    issues=[];groups=defaultdict(list);records=[];movements=[]
    def issue(recording,path,message,severity='error'):
        issues.append(dict(recording_id=recording,severity=severity,file=str(path),message=message))
    for path in sources:
        rid=path.name.removesuffix('_endpoint_summary.csv')
        try:
            rows=read_csv(path)
            if len(rows)!=1:raise ValueError(f'Expected one recording row; found {len(rows)}.')
            row=rows[0]
            if row.get('recording_id')!=rid:raise ValueError('Recording ID differs from filename.')
            groups[rid.casefold()].append((path,row))
        except (ValueError,OSError,csv.Error) as exc:
            issue(rid,path,str(exc))
            groups[rid.casefold()].append((path,None))

    for _,copies in sorted(groups.items()):
        path,source=copies[0];rid=path.name.removesuffix('_endpoint_summary.csv')
        rec=dict(recording_id=rid,animal='',date='',run=None,moving_time_s=None,distance_cm=None,distance_inches=None,
                 speed_cm_s=None,speed_inches_s=None,
                 movement_intervals=None,confirmed_intervals=None,validation_status='invalid',problem_summary='',
                 source_summary_csv=str(path),source_intervals_csv='')
        records.append(rec)
        problems=[]
        try:
            animal,run,date=recording_info(rid,source or {});rec.update(animal=animal,run=run,date=date)
            if any(row is None for _,row in copies):raise ValueError('An export for this recording could not be read.')
            summary_signatures={canonical_rows([row]) for _,row in copies}
            if len(summary_signatures)>1:raise ValueError('Conflicting duplicate recording exports. Keep only the intended version in the input folder.')
            if len(copies)>1:issue(rid,path,f'{len(copies)} identical summary copies found; counted once.','info')
            if source.get('measurement')!=METHOD:raise ValueError('Measurement method is not endpoint separation.')
            total_time=number(source,'moving_time_s');n=integer(source,'movement_intervals');confirmed=integer(source,'confirmed_intervals')
            dist=number(source,'distance_cm',optional=True)
            rec.update(moving_time_s=total_time,movement_intervals=n,confirmed_intervals=confirmed)
            if n<1 or total_time<=0:raise ValueError('At least one movement interval with positive moving time is required.')
            if confirmed>n:raise ValueError('Confirmed interval count exceeds movement interval count.')
            if not near(number(source,'known_length_cm'),number(source,'known_length_inches')*2.54):raise ValueError('Known rod length units do not agree.')
            confirmed_record=(source.get('status')=='confirmed' and confirmed==n and dist is not None)
            if not confirmed_record:problems.append('Endpoint measurements are not fully confirmed.')
            if dist is not None and str(source.get('distance_inches') or '').strip() and not near(dist/2.54,number(source,'distance_inches')):
                problems.append('Recording distance in inches does not match centimeters.')

            details=[]
            for copy_path,_ in copies:
                candidate=copy_path.with_name(copy_path.name.replace('_endpoint_summary.csv','_endpoint_intervals.csv'))
                if candidate.is_file():details.append((candidate,read_csv(candidate)))
            if not details:raise ValueError('Matching endpoint interval CSV is missing.')
            detail_signatures={canonical_rows(rows) for _,rows in details}
            if len(detail_signatures)>1:raise ValueError('Duplicate exports have conflicting interval data.')
            detail_path,rows=details[0];rec['source_intervals_csv']=str(detail_path)
            if len(rows)!=n:problems.append(f'Expected {n} movement intervals; found {len(rows)}.')
            intervals=[];seen=set();distance_sum=0.0;time_sum=0.0;confirmed_rows=0
            for raw in rows:
                interval=integer(raw,'interval')
                if interval in seen or interval<1:raise ValueError('Duplicate or invalid interval number.')
                seen.add(interval)
                a,b,t=number(raw,'start_s'),number(raw,'end_s'),number(raw,'moving_time_s')
                if b<=a or not near(b-a,t):raise ValueError(f'Interval {interval}: movement time differs from end minus start.')
                d=number(raw,'distance_cm',optional=True)
                reviewed=raw.get('reviewed','').strip().lower()=='true' and raw.get('status')=='confirmed'
                if reviewed:
                    if d is None:raise ValueError(f'Interval {interval}: confirmed distance is blank.')
                    # Positions can legitimately be negative if the head extends beyond A.
                    start_pos=float(raw['start_position_cm']);end_pos=float(raw['end_position_cm'])
                    if not math.isfinite(start_pos) or not math.isfinite(end_pos) or not near(abs(end_pos-start_pos),d):
                        raise ValueError(f'Interval {interval}: distance is not the start-to-end rod separation.')
                    if str(raw.get('distance_inches') or '').strip() and not near(d/2.54,number(raw,'distance_inches')):
                        raise ValueError(f'Interval {interval}: distance units do not agree.')
                    confirmed_rows+=1;distance_sum+=d
                time_sum+=t;intervals.append((a,b))
                movements.append(dict(recording_id=rid,animal=animal,date=date,run=run,**raw,
                                      recording_validation_status='pending',source_intervals_csv=str(detail_path)))
            if seen!=set(range(1,n+1)):problems.append('Interval numbers do not match the expected interval count.')
            ordered=sorted(intervals)
            if any(b>c for (_,b),(c,_) in zip(ordered,ordered[1:])):problems.append('Movement intervals overlap.')
            if not near(time_sum,total_time):problems.append('Interval movement times do not sum to recording movement time.')
            if confirmed_rows!=confirmed:problems.append('Confirmed interval count disagrees with the detail CSV.')
            if confirmed_record and (confirmed_rows!=n or not near(distance_sum,dist)):
                problems.append('Interval distances do not sum to the confirmed recording distance.')
            if problems:
                for problem in problems:issue(rid,path,problem)
                rec['validation_status']='needs_review'
            else:
                rec.update(distance_cm=dist,distance_inches=dist/2.54,
                           speed_cm_s=dist/total_time,speed_inches_s=dist/2.54/total_time,
                           validation_status='valid')
        except (ValueError,KeyError,TypeError,OSError,csv.Error) as exc:
            problems.append(str(exc));issue(rid,path,str(exc));rec['validation_status']='invalid'
        rec['problem_summary']='; '.join(problems)

    records.sort(key=lambda r:(r['animal'].casefold(),r['date'],r['run'] or 0,r['recording_id']))
    statuses={r['recording_id']:r['validation_status'] for r in records}
    for row in movements:
        row['recording_validation_status']=statuses[row['recording_id']]
        valid=row['recording_validation_status']=='valid' and row.get('reviewed','').lower()=='true' and row.get('status')=='confirmed'
        row['speed_cm_s']=number(row,'distance_cm')/number(row,'moving_time_s') if valid else None
        row['speed_inches_s']=row['speed_cm_s']/2.54 if valid else None
    movements.sort(key=lambda r:(r['animal'].casefold(),r['date'],r['run'] or 0,int(r['interval'])))
    output_dir.mkdir(parents=True,exist_ok=True)
    write_csv(output_dir/'recording_summary.csv',records,list(records[0]))
    detail_fields=['recording_id','animal','date','run','interval','start_s','end_s','moving_time_s',
                   'start_position_cm','end_position_cm','signed_displacement_toward_B_cm',
                   'measured_separation_cm','distance_cm','distance_inches','speed_cm_s','speed_inches_s','reviewed','status',
                   'recording_validation_status','start_frame_s','end_frame_s','start_head_x_px',
                   'start_head_y_px','end_head_x_px','end_head_y_px','source_intervals_csv']
    write_csv(output_dir/'movement_intervals.csv',movements,detail_fields)
    group_fields=['recordings_found','recordings_included','recordings_excluded','total_distance_cm',
                  'mean_distance_per_recording_cm','sd_distance_per_recording_cm','total_moving_time_s',
                  'mean_moving_time_per_recording_s','mean_recording_speed_cm_s',
                  'sd_recording_speed_cm_s','pooled_speed_cm_s']
    write_csv(output_dir/'animal_day_summary.csv',aggregate(records,['animal','date']),['animal','date']+group_fields)
    write_csv(output_dir/'animal_summary.csv',aggregate(records,['animal']),['animal']+group_fields)
    write_csv(output_dir/'validation_issues.csv',issues,['recording_id','severity','file','message'])
    valid=sum(r['validation_status']=='valid' for r in records)
    report=f'Recordings found: {len(records)}\nValidated and included: {valid}\nExcluded pending review: {len(records)-valid}\nMovement interval rows: {len(movements)}\n'
    (output_dir/'summary_notes.txt').write_text(report+\
        '\nDistances are endpoint separations summed across annotated movement intervals, not cumulative head travel.\n'
        'Group totals and means use only validated, fully confirmed recordings.\n'
        'Speed (cm/s) = endpoint separation (cm) / annotated moving time (s). Pauses outside the annotated intervals are excluded.\n'
        'Recording speed = sum of interval separations / sum of interval moving times.\n'
        'Mean recording speed gives each recording equal weight; pooled speed divides combined distance by combined moving time.\n'
        'Speeds use endpoint progress, not cumulative path length. Speed is blank for recordings that have not passed validation.\n'
        'SD is sample standard deviation and is blank for fewer than two included recordings.\n'
        'Blank values indicate unavailable results, not zero. Input files were not modified.\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_folder',nargs='?',type=Path,help='Endpoint export folder; all subfolders are searched.')
    parser.add_argument('--output',type=Path,help='Defaults to combined_endpoint_summary beside the input folder.')
    args=parser.parse_args();root=None
    try:
        if args.input_folder is None:
            import tkinter as tk
            from tkinter import filedialog,messagebox
            root=tk.Tk();root.withdraw()
            selected=filedialog.askdirectory(title='Select the folder containing all endpoint CSV exports')
            if not selected:return
            args.input_folder=Path(selected)
        output=args.output or args.input_folder.resolve().parent/'combined_endpoint_summary'
        report=summarize(args.input_folder,output)
        print(report);print(f'Saved to: {output.resolve()}')
        if root is not None:messagebox.showinfo('Summary complete',report+f'\nSaved to:\n{output.resolve()}')
    except Exception as exc:
        if root is not None:messagebox.showerror('Summary failed',str(exc))
        else:raise SystemExit(str(exc))
    finally:
        if root is not None:root.destroy()


if __name__=='__main__':main()
